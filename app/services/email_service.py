import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() == "true"
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@yourdomain.com")


def send_welcome_email(
    to_email: str,
    customer_slug: str,
    domain: str,
    admin_login: str,
    admin_password: str,
    package: str,
) -> dict:
    subject = f"Your Odoo instance is ready — {customer_slug}"
    body = f"""Hi,

Your Odoo instance has been provisioned and is ready to use.

Instance URL: http://{domain}
Package: {package}
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
