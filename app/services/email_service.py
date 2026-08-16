import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pathlib import Path

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() == "true"
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@yourdomain.com")

# The main Odoo site's login page - not the customer's own instance domain.
# Customers log in here first (portal account), not directly into their
# raw <slug>.localhost instance.
MAIN_SITE_HOST = os.environ.get("MAIN_SITE_HOST", "localhost")
MAIN_SITE_PORT = os.environ.get("MAIN_SITE_PORT", "8069")


def send_welcome_email(
    to_email: str,
    customer_slug: str,
    domain: str,
    admin_login: str,
    admin_password: str,
    package: str,
) -> dict:
    login_url = f"http://{MAIN_SITE_HOST}:{MAIN_SITE_PORT}/web/login?login={to_email}"
    subject = f"Your Odoo workspace is ready — {customer_slug}"
    body = f"""Hi,

Your Odoo workspace has been created and is ready to use.

Log in here: {login_url}
Package: {package}

Your dedicated instance admin login (for direct access if you ever need it):
Login: {admin_login}
Password: {admin_password}

If you have any questions, reach out to our support team.

Thanks,
The Team
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return {"sent": True, "to": to_email}
    except Exception as e:
        return {"sent": False, "to": to_email, "error": str(e)}


def send_verification_email(to_email: str, verification_url: str) -> dict:
    subject = "Verify your email address"
    body = f"""Hi,

Please confirm your email address by clicking the link below:

{verification_url}

If you didn't sign up for this, you can ignore this email.

Thanks,
The Team
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return {"sent": True, "to": to_email}
    except Exception as e:
        return {"sent": False, "to": to_email, "error": str(e)}


def send_invoice_email(
    to_email: str,
    invoice: dict,
    pdf_path: Path,
) -> dict:
    subject = f"Invoice #{invoice['invoice_id']} — {invoice['company_name']}"
    body = f"""Hi,

Please find attached your invoice for the {invoice['package'].capitalize()} plan.

Invoice #: {invoice['invoice_id']}
Amount: ${invoice['amount']:.2f} {invoice['currency']}
Due date: {invoice['due_at'][:10]}

Payment can be made via the link in your customer dashboard, or Stripe checkout
if enabled on your account.

Thanks,
The Team
"""

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(body))

    if pdf_path.exists():
        with open(pdf_path, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype="pdf")
            attachment.add_header(
                "Content-Disposition", "attachment", filename=f"invoice-{invoice['invoice_id']}.pdf"
            )
            msg.attach(attachment)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return {"sent": True, "to": to_email}
    except Exception as e:
        return {"sent": False, "to": to_email, "error": str(e)}
