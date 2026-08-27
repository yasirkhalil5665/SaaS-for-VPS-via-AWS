import os
import re
import random
import secrets
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from threading import Lock

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator

from app.services.status_store import get_status, set_status
from app.services.provisioner import is_slug_taken
from app.services.port_allocator import get_next_port
from app.routers.provision import ProvisionRequest, CompanyInfo, SLUG_RE, _run_provisioning

router = APIRouter(prefix="/signup", tags=["signup"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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


# ---------------------------------------------------------------------------
# Raw SMTP send - used ONLY for this verification code. Everything else in
# this project (welcome email, invoices) routes through Odoo's own mail
# server via saas.instance.send_transactional_email() (see main_site_sync.py)
# because it needs a saas.instance record to hang off of. At verification
# time no Odoo account exists yet, so this uses the SMTP_* env vars directly.
# ---------------------------------------------------------------------------
def _send_email(to_email: str, subject: str, body_text: str, body_html: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    from_email = os.environ.get("FROM_EMAIL", smtp_user)

    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP is not configured (set SMTP_USER / SMTP_PASSWORD in .env)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        if use_tls:
            server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(from_email, [to_email], msg.as_string())


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
    package: str = "starter"  # from ?package= on the pricing page, same default /provision used

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
    if not _can_resend(body.email):
        raise HTTPException(
            status_code=429,
            detail="A code was just sent - please wait before requesting another.",
        )

    code = _create_code(body.email, body.password)

    try:
        _send_email(
            to_email=body.email,
            subject="Your verification code",
            body_text=f"Your verification code is {code}. It expires in 10 minutes.",
            body_html=(
                f"<p>Your verification code is "
                f"<strong style='font-size:20px; letter-spacing:2px;'>{code}</strong></p>"
                f"<p style='color:#6b7280; font-size:13px;'>It expires in 10 minutes.</p>"
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not send verification email: {e}")

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
        package=body.package,
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
