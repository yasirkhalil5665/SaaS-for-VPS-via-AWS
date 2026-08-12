import os
import hmac

# Platform owner's password for the super admin panel.
# Set via env var in production: export ADMIN_PANEL_PASSWORD="something-strong"
ADMIN_PANEL_PASSWORD = os.environ.get("ADMIN_PANEL_PASSWORD", "changeme123")


def verify_admin_password(password: str) -> bool:
    if not password:
        return False
    return hmac.compare_digest(password, ADMIN_PANEL_PASSWORD)
