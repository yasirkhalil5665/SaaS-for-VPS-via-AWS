from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.services.provisioner import provision_customer, is_slug_taken
from app.services.deprovisioner import deprovision_customer
from app.services.status_store import set_status, get_status, delete_status
from app.services.port_allocator import get_next_port

router = APIRouter()


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
    company_info: CompanyInfo | None = None


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
