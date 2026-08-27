import re
import random
import secrets
import time
from threading import Lock

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator

from app.services.status_store import get_status, set_status
from app.services.provisioner import is_slug_taken
from app.services.port_allocator import get_next_port
from app.services.main_site_sync import email_has_account, send_verification_email_via_odoo
from app.routers.provision import ProvisionRequest, CompanyInfo, SLUG_RE, _run_provisioning

router = APIRouter(prefix="/signup", tags=["signup"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Single-plan signup now - "business" is the only package the signup form
# offers (see signup-form-embed-v3.html). starter/enterprise still exist as
# valid Selection values on saas.instance for any pre-existing records, but
# new signups always land on business.
DEFAULT_PACKAGE = "business"

# ---------------------------------------------------------------------------
# Verification codes - in-memory, short-lived (10 min), so a uvicorn --reload
# restart just means the person re-requests a code rather than a broken
# login (unlike auth_store.py's password persistence, this doesn't need to
# survive a restart).
# ---------------------------------------------------------------------------
_lock = Lock()
_codes: dict[str, dict] = {}  # email -> {code, password, expires_at, attempts, sent_at}

CODE_TTL_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 30


def _can_resend(email: str) -> bool:
    with _lock:
        entry = _codes.get(email)
        if not entry:
            return True
        return (time.time() - entry["sent_at"]) >= RESEND_COOLDOWN_SECONDS


def _create_code(email: str, password: str) -> str:
    code = f"{random.randint(0, 999999):06d}"
    with _lock:
        _codes[email] = {
            "code": code,
            "password": password,
            "expires_at": time.time() + CODE_TTL_SECONDS,
            "attempts": 0,
            "sent_at": time.time(),
        }
    return code


def _check_code(email: str, code: str) -> str | None:
    """Returns the password submitted alongside this code if it matches and
    hasn't expired/been exhausted, consuming the entry on success so it
    can't be replayed."""
    with _lock:
        entry = _codes.get(email)
        if not entry:
            return None
        if time.time() > entry["expires_at"]:
            _codes.pop(email, None)
            return None
        if entry["attempts"] >= MAX_ATTEMPTS:
            _codes.pop(email, None)
            return None
        if entry["code"] != code:
            entry["attempts"] += 1
            return None
        _codes.pop(email, None)
        return entry["password"]


def _slug_base_from_email(email: str) -> str:
    local = re.sub(r"[^a-z0-9-]+", "-", email.split("@")[0].lower()).strip("-")
    return local[:28] or "customer"


def _unique_slug(email: str) -> str:
    """No company name at signup time anymore, so there's nothing human to
    slugify off of except the email - append a short random suffix so two
    people with the same local-part don't collide, and repeat signups from
    the same address get a fresh instance rather than fighting over one slug."""
    base = _slug_base_from_email(email)
    for _ in range(50):
        candidate = f"{base}-{secrets.token_hex(2)}"[:40]
        if not SLUG_RE.match(candidate):
            continue
        if is_slug_taken(candidate) or get_status(candidate) is not None:
            continue
        return candidate
    raise RuntimeError("Could not allocate an instance name, please try again")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
class RequestCodeBody(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class VerifyCodeBody(BaseModel):
    email: str
    code: str
    package: str = DEFAULT_PACKAGE

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/request-code")
def request_code(body: RequestCodeBody):
    # Refuse re-signup on an email that already has a portal login - they
    # should log in instead of getting a second account/instance under the
    # same address. Checked here, before sending a code, so nobody gets a
    # working OTP for an account they can't actually create.
    existing = email_has_account(body.email)
    if existing.get("exists"):
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Please log in instead.",
        )

    if not _can_resend(body.email):
        raise HTTPException(
            status_code=429,
            detail="A code was just sent - please wait before requesting another.",
        )

    code = _create_code(body.email, body.password)

    # Sent through Odoo's own configured mail server (main site), not raw
    # smtplib - same mechanism as the welcome and invoice emails, see
    # main_site_sync.send_verification_email_via_odoo().
    result = send_verification_email_via_odoo(body.email, code)
    if not result.get("ok", True) and result.get("error"):
        # send_transactional_email() returns {"ok": False, "error": ...} on
        # a real failure; a bare successful XML-RPC call with no "ok" key at
        # all is also treated as success (older/looser return shapes).
        raise HTTPException(status_code=502, detail=f"Could not send verification email: {result['error']}")

    return {"sent": True, "email": body.email}


@router.post("/verify-code")
def verify_code(body: VerifyCodeBody, background_tasks: BackgroundTasks):
    password = _check_code(body.email, body.code)
    if password is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    customer_slug = _unique_slug(body.email)
    host_port = get_next_port()

    # company_info is just the email for now - name/phone/address get filled
    # in later via the post-login onboarding flow (saas_pricing_business),
    # not at signup time anymore.
    req = ProvisionRequest(
        customer_slug=customer_slug,
        package=body.package or DEFAULT_PACKAGE,
        admin_password=password,
        company_info=CompanyInfo(email=body.email),
    )

    set_status(customer_slug, {"state": "queued", "portal_ready": False})
    background_tasks.add_task(_run_provisioning, req, host_port)

    return {
        "customer_slug": customer_slug,
        "state": "queued",
        "message": "Email verified. Provisioning started.",
    }
