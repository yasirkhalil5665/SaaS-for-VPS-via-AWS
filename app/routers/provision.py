import re

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator

from app.services.provisioner import provision_customer, is_slug_taken
from app.services.deprovisioner import deprovision_customer
from app.services.status_store import set_status, get_status, delete_status
from app.services.port_allocator import get_next_port
from app.services.main_site_sync import email_has_account

router = APIRouter()

# Matches Docker container/network/volume naming rules AND is safe as a
# single filesystem path segment (no '..', no '/', no leading dot). The
# client (signup.html) already runs its own slugify() before sending this,
# but that's cosmetic only - nothing here previously re-checked it
# server-side, so a hand-crafted request (or a bug in some future client)
# could send "../../etc" straight into CUSTOMERS_DIR / customer_slug, or a
# slug with characters that are invalid in a Docker Compose network name
# and make "docker compose up" fail outright.
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")


class CompanyInfo(BaseModel):
    name: str | None = None
    street: str | None = None
    city: str | None = None
    country_code: str | None = None
    phone: str | None = None
    email: str | None = None
    currency_code: str | None = None
    timezone: str | None = None


class ProvisionRequest(BaseModel):
    customer_slug: str
    package: str  # "starter" | "business" | "enterprise"
    admin_password: str = "admin123"
    modules: list[str] | None = None
    full_name: str | None = None  # the signing-up person's own name, distinct from company_info.name
    referral_token: str | None = None  # from /signup?ref=<token> - resolved against saas.referral in saas_dashboard
    company_info: CompanyInfo | None = None

    @field_validator("customer_slug")
    @classmethod
    def validate_customer_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "customer_slug must be 3-40 characters, lowercase letters/numbers/hyphens only, "
                "starting and ending with a letter or number"
            )
        return v


def _run_provisioning(req: ProvisionRequest, host_port: int):
    def _on_account_ready(info: dict):
        # Fires as soon as the portal login exists (before Docker starts) -
        # lets the frontend redirect to /web/login almost immediately
        # instead of waiting for the full container chain to finish.
        set_status(req.customer_slug, {
            "state": "in_progress",
            "portal_ready": True,
            "login_email": info.get("login_email"),
            "main_site_sync": info.get("main_site_sync"),
        })

    try:
        result = provision_customer(
            req.customer_slug, req.package, host_port,
            req.admin_password, req.modules,
            req.company_info.model_dump(exclude_none=True) if req.company_info else None,
            req.full_name,
            req.referral_token,
            on_account_ready=_on_account_ready,
        )
        result["state"] = "done"
        result["portal_ready"] = True
        set_status(req.customer_slug, result)
    except Exception as e:
        # portal_ready may already be True from _on_account_ready - a failure
        # here means Docker provisioning failed AFTER the account was
        # created, not that the account itself failed. Keep portal_ready as
        # whatever it already was rather than resetting it, so the frontend
        # doesn't get confused about whether login is possible.
        existing = get_status(req.customer_slug) or {}
        set_status(req.customer_slug, {
            **existing,
            "state": "failed",
            "error": str(e),
        })


@router.post("/provision")
def provision(req: ProvisionRequest, background_tasks: BackgroundTasks):
    if get_status(req.customer_slug) is not None:
        raise HTTPException(
            status_code=409,
            detail="This name is already taken. Please choose a different company name.",
        )
    if is_slug_taken(req.customer_slug):
        # Ground-truth filesystem check, independent of the in-memory status
        # store (which is wiped on restart) - catches real duplicates that
        # the check above would otherwise miss after any server restart.
        raise HTTPException(
            status_code=409,
            detail="This name is already taken. Please choose a different company name.",
        )

    signup_email = (req.company_info.email if req.company_info else None) or None
    if signup_email and email_has_account(signup_email):
        # The public /signup form is one-email-one-account only - repeat
        # purchases by an existing customer go through the backend/admin
        # flow instead, not this self-serve form. A structured "code" lets
        # the frontend distinguish this from a generic error and offer a
        # "log in instead" link rather than just showing red text.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "email_exists",
                "message": "An account with this email already exists. Please log in instead.",
            },
        )

    # Port is always allocated server-side now — client can no longer pick/spoof it
    host_port = get_next_port()

    set_status(req.customer_slug, {"state": "queued", "portal_ready": False})
    background_tasks.add_task(_run_provisioning, req, host_port)

    return {
        "customer_slug": req.customer_slug,
        "state": "queued",
        "message": "Provisioning started. Check /provision/status/{customer_slug} for progress.",
    }


@router.get("/provision/status/{customer_slug}")
def provision_status(customer_slug: str):
    status = get_status(customer_slug)
    if status is None:
        raise HTTPException(status_code=404, detail="No such provisioning job")
    return status


@router.delete("/provision/{customer_slug}")
def deprovision(customer_slug: str, remove_data: bool = True):
    result = deprovision_customer(customer_slug, remove_data=remove_data)
    delete_status(customer_slug)  # frees up the slug for reuse
    return result
