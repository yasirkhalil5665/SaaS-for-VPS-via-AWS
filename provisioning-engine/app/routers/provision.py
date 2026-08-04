from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.provisioner import provision_customer

router = APIRouter()


class ProvisionRequest(BaseModel):
    customer_slug: str
    package: str  # "starter" | "business" | "enterprise"


@router.post("/provision")
def provision(req: ProvisionRequest):
    try:
        result = provision_customer(req.customer_slug, req.package)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
