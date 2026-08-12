import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fpdf import FPDF

from app.models.packages import PACKAGES

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"

VALID_STATUSES = ["pending", "paid", "overdue", "cancelled"]


def _invoices_dir(customer_slug: str) -> Path:
    d = CUSTOMERS_DIR / customer_slug / "invoices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_customer_meta(customer_slug: str) -> dict:
    meta_path = CUSTOMERS_DIR / customer_slug / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text())


def create_invoice(customer_slug: str, package: str | None = None) -> dict:
    meta = load_customer_meta(customer_slug)
    package = package or meta.get("package")

    if package not in PACKAGES:
        return {"success": False, "error": f"Unknown package: {package}"}

    amount = PACKAGES[package]["monthly_price"]
    invoice_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc)
    due_date = now + timedelta(days=14)

    invoice = {
        "invoice_id": invoice_id,
        "customer_slug": customer_slug,
        "company_name": meta.get("company_name") or customer_slug,
        "package": package,
        "amount": amount,
        "currency": "USD",
        "status": "pending",
        "issued_at": now.isoformat(),
        "due_at": due_date.isoformat(),
        "paid_at": None,
    }

    invoices_dir = _invoices_dir(customer_slug)
    (invoices_dir / f"{invoice_id}.json").write_text(json.dumps(invoice, indent=2))

    _generate_pdf(invoice, invoices_dir / f"{invoice_id}.pdf")

    return {"success": True, "invoice": invoice}


def list_invoices(customer_slug: str) -> list[dict]:
    invoices_dir = _invoices_dir(customer_slug)
    results = []
    for f in sorted(invoices_dir.glob("*.json"), reverse=True):
        try:
            results.append(json.loads(f.read_text()))
        except Exception:
            continue
    return results


def get_invoice(customer_slug: str, invoice_id: str) -> dict | None:
    path = _invoices_dir(customer_slug) / f"{invoice_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def update_invoice_status(customer_slug: str, invoice_id: str, status: str) -> dict:
    if status not in VALID_STATUSES:
        return {"success": False, "error": f"Invalid status: {status}"}

    invoice = get_invoice(customer_slug, invoice_id)
    if invoice is None:
        return {"success": False, "error": "Invoice not found"}

    invoice["status"] = status
    if status == "paid":
        invoice["paid_at"] = datetime.now(timezone.utc).isoformat()

    path = _invoices_dir(customer_slug) / f"{invoice_id}.json"
    path.write_text(json.dumps(invoice, indent=2))

    return {"success": True, "invoice": invoice}


def get_invoice_pdf_path(customer_slug: str, invoice_id: str) -> Path | None:
    path = _invoices_dir(customer_slug) / f"{invoice_id}.pdf"
    return path if path.exists() else None


def _generate_pdf(invoice: dict, output_path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(113, 75, 103)  # brand purple
    pdf.cell(0, 12, "INVOICE", ln=True)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Invoice #{invoice['invoice_id']}", ln=True)
    pdf.ln(6)

    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Billed To:", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, invoice["company_name"], ln=True)
    pdf.cell(0, 7, f"Instance: {invoice['customer_slug']}", ln=True)
    pdf.ln(6)

    issued = invoice["issued_at"][:10]
    due = invoice["due_at"][:10]
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Issued: {issued}", ln=True)
    pdf.cell(0, 6, f"Due: {due}", ln=True)
    pdf.cell(0, 6, f"Status: {invoice['status'].upper()}", ln=True)
    pdf.ln(8)

    # Line item table
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(244, 241, 243)
    pdf.cell(120, 8, "Description", border=1, fill=True)
    pdf.cell(60, 8, "Amount", border=1, fill=True, align="R", ln=True)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(120, 8, f"{invoice['package'].capitalize()} Plan - Monthly Subscription", border=1)
    pdf.cell(60, 8, f"${invoice['amount']:.2f}", border=1, align="R", ln=True)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(120, 8, "Total", border=1)
    pdf.cell(60, 8, f"${invoice['amount']:.2f} {invoice['currency']}", border=1, align="R", ln=True)

    pdf.ln(12)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, "Thank you for your business. Payment instructions and Stripe link will be included in your invoice email.")

    pdf.output(str(output_path))
