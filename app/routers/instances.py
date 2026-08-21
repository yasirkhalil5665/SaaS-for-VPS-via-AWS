import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from app.services.instance_manager import get_instance_status, control_instance
from app.services.auth_store import verify_credentials
from app.services.odoo_config import install_modules

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"


def _check_auth(customer_slug: str, x_instance_password: str | None):
    if not x_instance_password or not verify_credentials(customer_slug, x_instance_password):
        raise HTTPException(status_code=401, detail="Invalid or missing instance password")


@router.get("/instances/{customer_slug}")
def instance_status(customer_slug: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    status = get_instance_status(customer_slug)
    if not status.get("exists"):
        raise HTTPException(status_code=404, detail="No such instance")
    return status


@router.post("/instances/{customer_slug}/{action}")
def instance_control(customer_slug: str, action: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    try:
        result = control_instance(customer_slug, action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


class InstallModulesRequest(BaseModel):
    modules: list[str]


@router.post("/instances/{customer_slug}/install-modules")
def instance_install_modules(customer_slug: str, req: InstallModulesRequest, x_instance_password: str | None = Header(default=None)):
    """Installs modules into an ALREADY-RUNNING instance - the piece
    README_FLOW.txt flagged as missing for the package-first onboarding
    flow (business/apps chosen post-login, after the container already
    exists). Reuses the exact same install_modules() call used during
    initial provisioning, just pointed at an existing container instead
    of a freshly-created one.

    x_instance_password doubles as both the shared secret for this API
    (verified via verify_credentials, same as the other /instances routes)
    AND the real Odoo admin password (see provisioner.py: both are set from
    the same admin_password value at provisioning time) - so it can be
    passed straight through to the XML-RPC call below without needing to
    store or look up the plaintext password anywhere on this side.
    """
    _check_auth(customer_slug, x_instance_password)

    meta_path = CUSTOMERS_DIR / customer_slug / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="No such instance")
    meta = json.loads(meta_path.read_text())

    status = get_instance_status(customer_slug)
    if status.get("odoo_status") != "running":
        raise HTTPException(status_code=409, detail="Instance is not running - cannot install modules right now")

    result = install_modules(
        "localhost", meta["host_port"], customer_slug,
        meta["admin_login"], x_instance_password,
        req.modules,
    )
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result
