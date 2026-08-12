import os
import xmlrpc.client
import logging

_logger = logging.getLogger(__name__)

# Your MAIN Odoo site (the marketing/pricing site with saas_portal installed) -
# NOT the customer's own provisioned instance. Set these to match your setup.
MAIN_SITE_HOST = os.environ.get("MAIN_SITE_HOST", "localhost")
MAIN_SITE_PORT = int(os.environ.get("MAIN_SITE_PORT", "8069"))
MAIN_SITE_DB = os.environ.get("MAIN_SITE_DB", "Test")
MAIN_SITE_ADMIN_LOGIN = os.environ.get("MAIN_SITE_ADMIN_LOGIN", "odoo")
MAIN_SITE_ADMIN_PASSWORD = os.environ.get("MAIN_SITE_ADMIN_PASSWORD", "")


def _connect():
    common_url = f"http://{MAIN_SITE_HOST}:{MAIN_SITE_PORT}/xmlrpc/2/common"
    object_url = f"http://{MAIN_SITE_HOST}:{MAIN_SITE_PORT}/xmlrpc/2/object"

    common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
    uid = common.authenticate(MAIN_SITE_DB, MAIN_SITE_ADMIN_LOGIN, MAIN_SITE_ADMIN_PASSWORD, {})
    if not uid:
        raise RuntimeError("Could not authenticate with main site - check MAIN_SITE_ADMIN_* env vars")

    models = xmlrpc.client.ServerProxy(object_url, allow_none=True)
    return uid, models


def sync_new_customer(
    customer_name: str,
    customer_email: str,
    portal_password: str,
    customer_slug: str,
    package: str,
    instance_admin_password: str,
    domain: str = None,
) -> dict:
    """Creates (or finds) a portal user on the main site and links a
    saas.instance record to it, so the customer can log in at /web/login
    and see their instance under /my/instance.

    Called after FastAPI provisioning succeeds. Safe to call multiple times -
    won't create duplicate partners/users for the same email."""
    try:
        uid, models = _connect()
    except Exception as e:
        _logger.warning("Main site sync: connection failed: %s", e)
        return {"success": False, "error": str(e)}

    try:
        # 1. Find or create the partner
        partner_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "res.partner", "search",
            [[["email", "=", customer_email]]],
        )
        if partner_ids:
            partner_id = partner_ids[0]
        else:
            partner_id = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "res.partner", "create",
                [{"name": customer_name, "email": customer_email}],
            )

        # 2. Find or create the portal user for that partner
        user_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "res.users", "search",
            [[["login", "=", customer_email]]],
        )
        if user_ids:
            user_id = user_ids[0]
        else:
            # Resolve base.group_portal by xmlid rather than translated name -
            # searching res.groups by name="Portal" can silently match 0 or
            # >1 groups depending on locale/installed modules.
            try:
                portal_group_ref = models.execute_kw(
                    MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                    "ir.model.data", "check_object_reference",
                    ["base", "group_portal"],
                )
                portal_group_ids = [portal_group_ref[1]] if portal_group_ref else []
            except Exception:
                portal_group_ids = []

            if not portal_group_ids:
                # Do NOT silently create a user with no group - that produces
                # an account that can log in but has no portal access at all
                # (broken/blank /my page). Fail loudly instead so it's caught
                # during testing rather than discovered by a confused customer.
                return {
                    "success": False,
                    "error": "Could not resolve base.group_portal on main site - "
                             "refusing to create a user with no portal access.",
                }

            user_id = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "res.users", "create",
                [{
                    "name": customer_name,
                    "login": customer_email,
                    "email": customer_email,
                    "password": portal_password,
                    "partner_id": partner_id,
                    # Odoo 19 renamed res.users.groups_id -> group_ids.
                    "group_ids": [(6, 0, portal_group_ids)],
                }],
            )

        # 3. Create the saas.instance record linking partner -> their FastAPI instance
        existing_instance_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "search",
            [[["customer_slug", "=", customer_slug]]],
        )
        if not existing_instance_ids:
            models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "saas.instance", "create",
                [{
                    "partner_id": partner_id,
                    "customer_slug": customer_slug,
                    "package": package,
                    "admin_password": instance_admin_password,
                    "domain": domain,
                }],
            )

        return {"success": True, "partner_id": partner_id, "user_id": user_id}

    except Exception as e:
        _logger.warning("Main site sync failed: %s", e)
        return {"success": False, "error": str(e)}
