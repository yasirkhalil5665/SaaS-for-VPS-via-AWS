from fastapi import APIRouter, HTTPException, Header

from app.services.instance_manager import get_instance_status, control_instance
from app.services.auth_store import verify_credentials

router = APIRouter()


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
