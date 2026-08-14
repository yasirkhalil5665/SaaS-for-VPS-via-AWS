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


def _resolve_country_id(models, uid, country_code):
    if not country_code:
        return None
    country_ids = models.execute_kw(
        MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
        "res.country", "search",
        [[["code", "=", country_code]]],
    )
    return country_ids[0] if country_ids else None


def sync_new_customer(
    person_name: str,
    company_name: str,
    customer_email: str,
    portal_password: str,
    customer_slug: str,
    package: str,
    instance_admin_password: str,
    customer_phone: str | None = None,
    country_code: str | None = None,
    domain: str = None,
) -> dict:
    """Creates (or finds) a Company contact and a Person contact under it on
    the main site, a portal user tied to the Person, and links a new
    saas.instance record for this purchase.

    person_name / company_name are deliberately separate - the signup form's
    "Full Name" and "Company Name" fields are different people/things, and
    conflating them (as an earlier version of this function did, using one
    value for both the partner and the login's display name) meant the
    actual person's name was never captured anywhere.

    One partner can have multiple saas.instance records - a person is
    expected to be able to buy more than one package. This function does
    not deduplicate or replace previous instances; it only deduplicates the
    Company/Person/user records themselves so repeat purchases by the same
    person don't create duplicate contacts.

    Called after FastAPI provisioning succeeds. Safe to call multiple times."""
    try:
        uid, models = _connect()
    except Exception as e:
        _logger.warning("Main site sync: connection failed: %s", e)
        return {"success": False, "error": str(e)}

    try:
        country_id = _resolve_country_id(models, uid, country_code)

        # 1. Find or create the Company contact (is_company=True). Matched by
        # name - if you have two unrelated customers who happen to use the
        # exact same company name, they will be merged onto one Company
        # record. Consider matching on a stronger key (e.g. a normalized
        # domain from the email) if that becomes a real collision risk.
        company_id = None
        if company_name:
            company_ids = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "res.partner", "search",
                [[["name", "=", company_name], ["is_company", "=", True]]],
            )
            if company_ids:
                company_id = company_ids[0]
            else:
                company_vals = {"name": company_name, "is_company": True}
                if customer_email:
                    company_vals["email"] = customer_email
                if customer_phone:
                    company_vals["phone"] = customer_phone
                if country_id:
                    company_vals["country_id"] = country_id
                company_id = models.execute_kw(
                    MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                    "res.partner", "create",
                    [company_vals],
                )

        # 2. Find or create the Person contact (is_company=False), linked
        # under the Company via parent_id. Matched by email, since that's
        # the actual login identifier.
        person_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "res.partner", "search",
            [[["email", "=", customer_email], ["is_company", "=", False]]],
        )
        if person_ids:
            person_id = person_ids[0]
        else:
            person_vals = {
                "name": person_name or customer_email.split("@")[0],
                "email": customer_email,
                "is_company": False,
            }
            if customer_phone:
                person_vals["phone"] = customer_phone
            if company_id:
                person_vals["parent_id"] = company_id
            if country_id:
                person_vals["country_id"] = country_id
            person_id = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "res.partner", "create",
                [person_vals],
            )

        # 3. Find or create the portal user for the Person
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
                    "name": person_name or customer_email.split("@")[0],
                    "login": customer_email,
                    "email": customer_email,
                    "password": portal_password,
                    "partner_id": person_id,
                    # Odoo 19 renamed res.users.groups_id -> group_ids.
                    "group_ids": [(6, 0, portal_group_ids)],
                }],
            )

        # 4. Always create a NEW saas.instance record for this purchase -
        # one person can legitimately buy multiple packages, so this is
        # intentionally not deduplicated by partner. It IS deduplicated by
        # customer_slug, since each provisioning run for the same slug
        # should not create a second record if this function gets called
        # again for the same purchase (e.g. a retried request).
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
                    "partner_id": person_id,
                    "customer_slug": customer_slug,
                    "package": package,
                    "admin_password": instance_admin_password,
                    "domain": domain,
                }],
            )

        return {"success": True, "company_id": company_id, "partner_id": person_id, "user_id": user_id}

    except Exception as e:
        _logger.warning("Main site sync failed: %s", e)
        return {"success": False, "error": str(e)}
