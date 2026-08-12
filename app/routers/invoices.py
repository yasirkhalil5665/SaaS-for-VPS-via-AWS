from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse

from app.services.invoice_manager import (
    create_invoice, list_invoices, get_invoice, update_invoice_status,
    get_invoice_pdf_path, load_customer_meta,
)
from app.services.email_service import send_invoice_email
from app.services.admin_auth import verify_admin_password
from app.services.auth_store import verify_credentials

router = APIRouter()


def _check_admin(x_admin_password: str | None):
    if not verify_admin_password(x_admin_password or ""):
        raise HTTPException(status_code=401, detail="Invalid or missing admin password")


def _check_customer(customer_slug: str, x_instance_password: str | None):
    if not x_instance_password or not verify_credentials(customer_slug, x_instance_password):
        raise HTTPException(status_code=401, detail="Invalid or missing instance password")


# --- Admin endpoints: generate, review, send, mark paid ---

@router.post("/admin/customers/{customer_slug}/invoices")
def admin_create_invoice(customer_slug: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    result = create_invoice(customer_slug)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result["invoice"]


@router.get("/admin/customers/{customer_slug}/invoices")
def admin_list_invoices(customer_slug: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    return {"invoices": list_invoices(customer_slug)}


@router.post("/admin/customers/{customer_slug}/invoices/{invoice_id}/send")
def admin_send_invoice(customer_slug: str, invoice_id: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    invoice = get_invoice(customer_slug, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")

    pdf_path = get_invoice_pdf_path(customer_slug, invoice_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="Invoice PDF not found")

    meta = load_customer_meta(customer_slug)
    to_email = meta.get("customer_email")
    if not to_email:
        raise HTTPException(status_code=400, detail="No customer email on file")

    result = send_invoice_email(to_email, invoice, pdf_path)
    return result


@router.post("/admin/customers/{customer_slug}/invoices/{invoice_id}/mark-paid")
def admin_mark_paid(customer_slug: str, invoice_id: str, x_admin_password: str | None = Header(default=None)):
    _check_admin(x_admin_password)
    result = update_invoice_status(customer_slug, invoice_id, "paid")
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result["invoice"]


# --- Customer-facing endpoints: view and download own invoices ---

@router.get("/instances/{customer_slug}/invoices")
def customer_list_invoices(customer_slug: str, x_instance_password: str | None = Header(default=None)):
    _check_customer(customer_slug, x_instance_password)
    return {"invoices": list_invoices(customer_slug)}


@router.get("/instances/{customer_slug}/invoices/{invoice_id}/download")
def customer_download_invoice(
    customer_slug: str, invoice_id: str, x_instance_password: str | None = Header(default=None)
):
    _check_customer(customer_slug, x_instance_password)
    path = get_invoice_pdf_path(customer_slug, invoice_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Invoice PDF not found")
    return FileResponse(path, filename=f"invoice-{invoice_id}.pdf", media_type="application/pdf")
