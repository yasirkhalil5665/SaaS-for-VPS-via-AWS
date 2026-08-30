import os
import xmlrpc.client
import logging
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

# Your MAIN Odoo site (the marketing/pricing site with saas_dashboard installed) -
# NOT the customer's own provisioned instance. Set these to match your setup.
MAIN_SITE_HOST = os.environ.get("MAIN_SITE_HOST", "localhost")
MAIN_SITE_PORT = int(os.environ.get("MAIN_SITE_PORT", "8069"))
MAIN_SITE_DB = os.environ.get("MAIN_SITE_DB", "Test")
MAIN_SITE_ADMIN_LOGIN = os.environ.get("MAIN_SITE_ADMIN_LOGIN", "odoo")
MAIN_SITE_ADMIN_PASSWORD = os.environ.get("MAIN_SITE_ADMIN_PASSWORD", "")

# The public URL a person's BROWSER should hit (auto-login redirect target,
# verification links) - deliberately separate from MAIN_SITE_HOST/PORT
# above, which is the internal host:port this process uses for XML-RPC and
# is very often NOT reachable/correct from outside (docker-internal
# hostnames, non-standard ports, etc). Hardcoded to the real domain as the
# fallback (not derived from MAIN_SITE_HOST) because that derivation is
# exactly what silently produced http://localhost:8069 verification links
# in production before - MAIN_SITE_PUBLIC_URL was unset, so it fell back to
# building a URL from MAIN_SITE_HOST, which is correctly "localhost" for
# XML-RPC but was never meant to be customer-facing.
MAIN_SITE_PUBLIC_URL = os.environ.get(
    "MAIN_SITE_PUBLIC_URL", "https://coolbites.site"
).rstrip("/")

# Shared sender for transactional emails (welcome, invoice) sent via
# saas.instance.send_transactional_email(). Without this, Odoo has no
# email_from at all unless mail.catchall.domain / mail.default.from are
# configured system-wide - which produced exactly the "You must either
# provide a sender address explicitly or configure..." error. Same address
# already confirmed working for referral invites.
TRANSACTIONAL_EMAIL_FROM = os.environ.get("TRANSACTIONAL_EMAIL_FROM", "18G491@gmail.com")


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


def email_has_account(customer_email: str) -> bool:
    """Pre-check used by /provision before it does anything else - a
    duplicate signup with an already-registered email should be rejected
    outright and pointed at the login page, not silently reuse the existing
    account (that reuse behavior is intentional elsewhere, e.g. an admin
    manually provisioning a second package for an existing client, but the
    public self-serve /signup form is one-email-one-account only).
    """
    try:
        uid, models = _connect()
    except Exception as e:
        _logger.warning("email_has_account: connection failed: %s", e)
        # Fail open on our own infra trouble rather than blocking every
        # signup because the main site was briefly unreachable - a genuine
        # duplicate will still get caught downstream by the unique login
        # constraint on res.users itself.
        return False
    try:
        user_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "res.users", "search",
            [[["login", "=", customer_email]]],
        )
        return bool(user_ids)
    except Exception as e:
        _logger.warning("email_has_account: lookup failed: %s", e)
        return False


def _otp_email_html(code: str) -> str:
    return """
    <div style="font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px; color: #2b1f28;">
        <p style="font-size: 15px; margin: 0 0 20px;">Enter this code to verify your email address:</p>
        <div style="background: #faf6f9; border: 1px solid #e5d5df; border-radius: 10px; padding: 24px; text-align: center; margin-bottom: 20px;">
            <span style="font-size: 34px; font-weight: 700; letter-spacing: 8px; color: #714B67;">%s</span>
        </div>
        <p style="font-size: 13px; color: #6b6b6b; margin: 0;">This code expires in 15 minutes. If you didn't request this, you can safely ignore this email.</p>
    </div>
    """ % code


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
    referral_token: str | None = None,
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

        # 1. Find or create the Company contact (is_company=True). Matched by
        # name - two unrelated customers with the exact same company name
        # will be merged onto one Company record. Consider matching on a
        # stronger key if that becomes a real collision risk.
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
        # under the Company via parent_id. Matched by email.
        person_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "res.partner", "search",
            [[["email", "=", customer_email], ["is_company", "=", False]]],
        )
        if person_ids:
            person_id = person_ids[0]
            # BUG FIX: this branch used to just reuse person_id and stop -
            # any new phone number typed on a repeat signup was silently
            # discarded, so the contact kept whatever phone (if any) was on
            # file from the very first signup under this email, forever.
            # Only write fields that were actually submitted this time, and
            # only phone for now (name/country have the same reuse gap but
            # weren't part of what was reported - see PROJECT_SUMMARY_v31.md
            # Section 7 pattern before touching those too).
            if customer_phone:
                models.execute_kw(
                    MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                    "res.partner", "write",
                    [[person_id], {"phone": customer_phone}],
                )
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

            # Referral resolution - only on a genuinely NEW person record.
            # Deliberately not done for the existing-person reuse branch
            # above: attributing a repeat customer to whoever's link they
            # happened to click on a later, unrelated purchase would be
            # wrong - a referral should reflect who brought them onto the
            # platform in the first place, set once, never overwritten.
            #
            # Resolved against saas.referral (saas_dashboard module), which
            # tracks one record per invited email - NOT a field on
            # res.partner - because the portal's "Reseller Invite" page
            # needs to show each invite separately (who was invited, by
            # email, whether they've signed up yet), which a single
            # referral_token-on-res.partner design can't represent.
            #
            # A missing/invalid/already-resolved token is not an error - it
            # just means this signup isn't attributed to any invite.
            if referral_token:
                try:
                    resolved = models.execute_kw(
                        MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                        "saas.referral", "resolve_token",
                        [[], referral_token, person_id],
                    )
                    if not resolved:
                        _logger.warning(
                            "Referral token did not resolve (no matching invited "
                            "record found): token=%s person_id=%s", referral_token, person_id,
                        )
                except Exception as e:
                    _logger.warning(
                        "Referral resolution raised an exception: token=%s person_id=%s error=%s",
                        referral_token, person_id, e,
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
            trial_ends_at = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            instance_id = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "saas.instance", "create",
                [{
                    "partner_id": person_id,
                    "customer_slug": customer_slug,
                    "package": package,
                    "admin_password": instance_admin_password,
                    "trial_ends_at": trial_ends_at,
                }],
            )

        # Verification session token: identifies this pending signup to the
        # public /auth/verify page - it does NOT log anyone in by itself.
        # Login only happens there, after the OTP below is confirmed
        # correct. See saas.instance.generate_auto_login_token/
        # consume_verification_token for the enforcement.
        verify_token = None
        try:
            verify_token = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "saas.instance", "generate_auto_login_token",
                [[instance_id]],
            )
        except Exception as e:
            _logger.warning("Could not generate verification token for %s: %s", customer_slug, e)

        # Email verification (OTP) - this IS the gate. The person cannot
        # reach /my until they enter this code correctly on /auth/verify.
        try:
            otp_code = models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "saas.instance", "generate_email_otp",
                [[instance_id]],
            )
            models.execute_kw(
                MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
                "saas.instance", "send_transactional_email",
                [[], "Your verification code", _otp_email_html(otp_code), customer_email],
                {"email_from": TRANSACTIONAL_EMAIL_FROM},
            )
        except Exception as e:
            _logger.warning("Could not send verification email for %s: %s", customer_slug, e)

        return {
            "success": True,
            "company_id": company_id,
            "partner_id": person_id,
            "user_id": user_id,
            "instance_id": instance_id,
            "verify_url": f"{MAIN_SITE_PUBLIC_URL}/auth/verify?token={verify_token}" if verify_token else None,
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


def send_welcome_email_via_odoo(customer_slug: str, customer_email: str, domain: str,
                                 admin_login: str, admin_password: str, package: str) -> dict:
    """Replaces the old email_service.send_welcome_email(), which used its
    own raw smtplib connection with separate SMTP_* env vars, completely
    bypassing Odoo - meaning it never showed up in Settings > Technical >
    Email > Emails, and used different credentials than whatever's actually
    configured as the Outgoing Mail Server there. Routes through
    saas.instance.send_transactional_email() instead, so it's a real Odoo
    mail.mail record sent via Odoo's own configured mail server."""
    subject = f"Your Odoo instance is ready - {customer_slug}"
    body_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 480px; margin: 0 auto;">
            <p style="font-size: 15px; color: #212529;">
                Your Odoo instance has been provisioned and is ready to use.
            </p>
            <table style="font-size: 14px; color: #212529; margin: 20px 0;">
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Instance URL</td><td>http://{domain}</td></tr>
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Package</td><td>{package}</td></tr>
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Login</td><td>{admin_login}</td></tr>
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Password</td><td>{admin_password}</td></tr>
            </table>
            <p style="font-size: 13px; color: #6c757d;">
                If you have any questions, reach out to our support team.
            </p>
        </div>
    """
    try:
        uid, models = _connect()
        instance_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "search",
            [[["customer_slug", "=", customer_slug]]],
        )
        if not instance_ids:
            return {"success": False, "error": "No saas.instance record found for this slug"}
        result = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "send_transactional_email",
            [instance_ids[:1], subject, body_html, customer_email, TRANSACTIONAL_EMAIL_FROM],
        )
        return result if isinstance(result, dict) else {"success": bool(result)}
    except Exception as e:
        _logger.warning("Welcome email via Odoo failed for %s: %s", customer_slug, e)
        return {"success": False, "error": str(e)}


def send_invoice_email_via_odoo(customer_slug: str, customer_email: str, invoice: dict, pdf_bytes: bytes | None) -> dict:
    """Replaces the old email_service.send_invoice_email() - same reasoning
    as send_welcome_email_via_odoo above: routes through Odoo's own mail
    server instead of a separate raw SMTP connection, and the PDF gets
    attached as a real Odoo attachment on the mail.mail record instead of
    just being an email attachment nobody in Odoo can see afterward."""
    subject = f"Invoice #{invoice['invoice_id']} - {invoice['company_name']}"
    body_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 480px; margin: 0 auto;">
            <p style="font-size: 15px; color: #212529;">
                Please find attached your invoice for the {invoice['package'].capitalize()} plan.
            </p>
            <table style="font-size: 14px; color: #212529; margin: 20px 0;">
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Invoice #</td><td>{invoice['invoice_id']}</td></tr>
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Amount</td><td>${invoice['amount']:.2f} {invoice['currency']}</td></tr>
                <tr><td style="color:#6c757d; padding: 4px 12px 4px 0;">Due date</td><td>{invoice['due_at'][:10]}</td></tr>
            </table>
            <p style="font-size: 13px; color: #6c757d;">
                Payment can be made via the link in your customer dashboard, or Stripe checkout
                if enabled on your account.
            </p>
        </div>
    """
    try:
        uid, models = _connect()
        instance_ids = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "search",
            [[["customer_slug", "=", customer_slug]]],
        )
        if not instance_ids:
            return {"success": False, "error": "No saas.instance record found for this slug"}

        args = [instance_ids[:1], subject, body_html, customer_email, TRANSACTIONAL_EMAIL_FROM]
        kwargs = {}
        if pdf_bytes:
            import base64
            kwargs = {
                "attachment_name": f"invoice-{invoice['invoice_id']}.pdf",
                "attachment_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            }
        result = models.execute_kw(
            MAIN_SITE_DB, uid, MAIN_SITE_ADMIN_PASSWORD,
            "saas.instance", "send_transactional_email",
            args, kwargs,
        )
        return result if isinstance(result, dict) else {"success": bool(result)}
    except Exception as e:
        _logger.warning("Invoice email via Odoo failed for %s: %s", customer_slug, e)
        return {"success": False, "error": str(e)}
