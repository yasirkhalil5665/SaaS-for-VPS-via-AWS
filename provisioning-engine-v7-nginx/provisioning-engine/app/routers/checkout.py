from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.stripe_service import create_checkout_session
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


class CheckoutRequest(BaseModel):
    customer_slug: str
    package: str
    company_info: CompanyInfo | None = None


@router.post("/checkout")
def checkout(req: CheckoutRequest):
    try:
        host_port = get_next_port()
        url = create_checkout_session(
            req.customer_slug,
            req.package,
            host_port,
            req.company_info.model_dump(exclude_none=True) if req.company_info else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"checkout_url": url}


@router.get("/checkout/success")
def checkout_success(session_id: str | None = None):
    return {"message": "Payment successful. Your instance is being provisioned.", "session_id": session_id}


@router.get("/checkout/cancel")
def checkout_cancel():
    return {"message": "Checkout cancelled."}
