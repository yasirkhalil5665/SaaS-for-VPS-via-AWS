from fastapi import APIRouter, HTTPException

from app.services.instance_manager import get_instance_status, control_instance

router = APIRouter()


@router.get("/instances/{customer_slug}")
def instance_status(customer_slug: str):
    status = get_instance_status(customer_slug)
    if not status.get("exists"):
        raise HTTPException(status_code=404, detail="No such instance")
    return status


@router.post("/instances/{customer_slug}/{action}")
def instance_control(customer_slug: str, action: str):
    try:
        result = control_instance(customer_slug, action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
