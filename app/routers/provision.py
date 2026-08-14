from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.services.provisioner import provision_customer
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
    set_status(req.customer_slug, {"state": "in_progress"})
    try:
        result = provision_customer(
            req.customer_slug, req.package, host_port,
            req.admin_password, req.modules,
            req.company_info.model_dump(exclude_none=True) if req.company_info else None,
            req.full_name,
        )
        result["state"] = "done"
        set_status(req.customer_slug, result)
    except Exception as e:
        set_status(req.customer_slug, {"state": "failed", "error": str(e)})


@router.post("/provision")
def provision(req: ProvisionRequest, background_tasks: BackgroundTasks):
    if get_status(req.customer_slug) is not None:
        raise HTTPException(status_code=400, detail="Customer slug already being/has been provisioned")

    # Port is always allocated server-side now — client can no longer pick/spoof it
    host_port = get_next_port()

    set_status(req.customer_slug, {"state": "queued"})
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
