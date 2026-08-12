from fastapi import APIRouter, HTTPException, Header

from app.services.admin_auth import verify_admin_password
from app.services.admin_manager import list_all_customers, get_customer_detail
from app.services.instance_manager import control_instance
from app.services.deprovisioner import deprovision_customer
from app.services.status_store import delete_status

router = APIRouter()


def _check_admin(x_admin_password: str | None):
    if not verify_admin_password(x_admin_password or ""):
        raise HTTPException(status_code=401, detail="Invalid or missing admin password")


@router.get("/admin/customers")
def admin_list_customers(x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    return {"customers": list_all_customers()}


@router.get("/admin/customers/{customer_slug}")
def admin_customer_detail(customer_slug: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    detail = get_customer_detail(customer_slug)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such customer")
    return detail


@router.post("/admin/customers/{customer_slug}/suspend")
def admin_suspend(customer_slug: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    return control_instance(customer_slug, "stop")


@router.post("/admin/customers/{customer_slug}/resume")
def admin_resume(customer_slug: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    return control_instance(customer_slug, "start")


@router.delete("/admin/customers/{customer_slug}")
def admin_delete_customer(
    customer_slug: str,
    remove_data: bool = True,
    x_admin_password: str | None = Header(default=None),
):
    _check_admin(x_admin_password)
    result = deprovision_customer(customer_slug, remove_data=remove_data)
    delete_status(customer_slug)
    return result
