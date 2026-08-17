import os
import xmlrpc.client
import logging

from app.services.email_service import send_verification_email

_logger = logging.getLogger(__name__)

# Your MAIN Odoo site (the marketing/pricing site with saas_dashboard installed) -
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


def create_pending_customer(
    person_name: str,
    company_name: str,
    customer_email: str,
    portal_password: str,
    customer_slug: str,
    package: str,
    instance_admin_password: str,
    customer_phone: str | None = None,
    country_code: str | None = None,
) -> dict:
    """Creates the Company, Person, portal login, and a saas.instance record
    (state='provisioning' by default on the model) - deliberately BEFORE any
    Docker work happens. This is the fast part (a handful of XML-RPC calls,
    typically well under a second) so the person can be redirected to login
    almost immediately after submitting the signup form, instead of waiting
    for the full container provisioning chain to finish first.

    mark_instance_ready() / mark_instance_failed() are called later, once
    the actual Docker provisioning either succeeds or fails, to flip the
    same record's state so /my/instance stops showing "setting up".

    One partner can have multiple saas.instance records - a person is
    expected to be able to buy more than one package. Company/Person/user
    records ARE deduplicated (repeat purchases by the same person reuse the
    same contacts); saas.instance records are not.
    """
    try:
        uid, models = _connect()
    except Exception as e:
        _logger.warning("Main site sync: connection failed: %s", e)
        return {"success": False, "error": str(e)}

    try:
        country_id = _resolve_country_id(models, uid, country_code)

        # Person (matched by email) is looked up FIRST, before touching
        # Company at all. One email = one Person = one Company, permanently.
        # If this person already exists, their existing parent_id company is
        # reused regardless of whatever company_name was typed this time -
        # otherwise, signing up again under a different company name would
        # create an orphan Company record with no real link to anything,
        # instead of just adding another package under their real account.
        person_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "res.partner", "search",
            [[["email", "=", customer_email], ["is_company", "=", False]]],
        )

        is_new_person = not person_ids
        verification_token = None

        if person_ids:
            person_id = person_ids[0]
            existing = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "res.partner", "read",
                [[person_id]], {"fields": ["parent_id"]},
            )
            company_id = existing[0]["parent_id"][0] if existing and existing[0].get("parent_id") else None
        else:
            # Only for a genuinely new person: find-or-create the Company by
            # name. Matched by name - two unrelated customers with the exact
            # same company name will be merged onto one Company record.
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

            import secrets
            verification_token = secrets.token_urlsafe(32)

            person_vals = {
                "name": person_name or customer_email.split("@")[0],
                "email": customer_email,
                "is_company": False,
                "email_verified": False,
                "email_verification_token": verification_token,
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
                    "group_ids": [(6, 0, portal_group_ids)],  # Odoo 19 renamed groups_id -> group_ids
                }],
            )

        # 4. Create the saas.instance record for THIS purchase. Deliberately
        # not deduplicated by partner (multiple packages allowed) - only by
        # customer_slug, so a retried request doesn't create a duplicate row.
        existing_instance_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "search",
            [[["customer_slug", "=", customer_slug]]],
        )
        if existing_instance_ids:
            instance_id = existing_instance_ids[0]
        else:
            # state defaults to 'provisioning' on the model itself - not set
            # explicitly here so this function doesn't need to know the
            # model's field list beyond what it actually creates.
            instance_id = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "saas.instance", "create",
                [{
                    "partner_id": person_id,
                    "customer_slug": customer_slug,
                    "package": package,
                    "admin_password": instance_admin_password,
                }],
            )

        # Send the verification email only for a genuinely new person - not
        # on every repeat purchase, and not blocking the rest of this
        # function if sending fails (a bounced/misconfigured SMTP setup
        # shouldn't prevent the account itself from being created).
        email_result = None
        if is_new_person and verification_token:
            verify_url = f"http://{MAIN_SITE_HOST}:{MAIN_SITE_PORT}/verify-email?token={verification_token}"
            email_result = send_verification_email(customer_email, verify_url)

        return {
            "success": True,
            "company_id": company_id,
            "partner_id": person_id,
            "user_id": user_id,
            "instance_id": instance_id,
            "verification_email": email_result,
        }

    except Exception as e:
        _logger.warning("Main site sync (create_pending_customer) failed: %s", e)
        return {"success": False, "error": str(e)}


def mark_instance_ready(customer_slug: str) -> dict:
    """Called once Docker provisioning actually finishes successfully."""
    try:
        uid, models = _connect()
        instance_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "search",
            [[["customer_slug", "=", customer_slug]]],
        )
        if not instance_ids:
            return {"success": False, "error": "No saas.instance record found for this slug"}
        models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "write",
            [instance_ids, {"state": "ready", "error_message": False}],
        )
        return {"success": True}
    except Exception as e:
        _logger.warning("Main site sync (mark_instance_ready) failed: %s", e)
        return {"success": False, "error": str(e)}


def mark_instance_failed(customer_slug: str, error_message: str) -> dict:
    """Called if Docker provisioning fails after the account was already
    created - so /my/instance shows a clear failure instead of spinning on
    "setting up" forever (the meta-refresh only stops once state changes)."""
    try:
        uid, models = _connect()
        instance_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "search",
            [[["customer_slug", "=", customer_slug]]],
        )
        if not instance_ids:
            return {"success": False, "error": "No saas.instance record found for this slug"}
        models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "write",
            [instance_ids, {"state": "failed", "error_message": (error_message or "")[:250]}],
        )
        return {"success": True}
    except Exception as e:
        _logger.warning("Main site sync (mark_instance_failed) failed: %s", e)
        return {"success": False, "error": str(e)}
