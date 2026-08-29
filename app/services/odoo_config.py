import time
import xmlrpc.client


def wait_for_odoo(host: str, port: int, timeout: int = 120) -> bool:
    """Poll Odoo's common endpoint until it responds or timeout hits."""
    url = f"http://{host}:{port}/xmlrpc/2/common"
    common = xmlrpc.client.ServerProxy(url, allow_none=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            common.version()
            return True
        except Exception:
            time.sleep(0.75)
    return False


def create_database(
    host: str,
    port: int,
    master_password: str,
    db_name: str,
    admin_login: str,
    admin_password: str,
    lang: str = "en_US",
    country_code: str | None = None,
) -> dict:
    """Create a new Odoo database via XML-RPC db service."""
    url = f"http://{host}:{port}/xmlrpc/2/db"
    db_service = xmlrpc.client.ServerProxy(url, allow_none=True)

    db_service.create_database(
        master_password,
        db_name,
        False,  # demo data
        lang,
        admin_password,
        admin_login,
        country_code,
    )

    return {"db_name": db_name, "admin_login": admin_login, "status": "created"}


def configure_company(
    host: str,
    port: int,
    db_name: str,
    admin_login: str,
    admin_password: str,
    company_info: dict,
) -> dict:
    """Authenticate and update the main company's info (res.company id=1)."""
    common_url = f"http://{host}:{port}/xmlrpc/2/common"
    object_url = f"http://{host}:{port}/xmlrpc/2/object"

    common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
    uid = common.authenticate(db_name, admin_login, admin_password, {})

    if not uid:
        return {"company_id": None, "updated_fields": [], "error": "Authentication failed"}

    models = xmlrpc.client.ServerProxy(object_url, allow_none=True)
    company_id = 1

    update_vals = {}
    if "name" in company_info:
        update_vals["name"] = company_info["name"]
    if "street" in company_info:
        update_vals["street"] = company_info["street"]
    if "city" in company_info:
        update_vals["city"] = company_info["city"]
    if "country_code" in company_info:
        country_ids = models.execute_kw(
            db_name, uid, admin_password,
            "res.country", "search",
            [[["code", "=", company_info["country_code"]]]],
        )
        if country_ids:
            update_vals["country_id"] = country_ids[0]
    if "phone" in company_info:
        update_vals["phone"] = company_info["phone"]
    if "email" in company_info:
        update_vals["email"] = company_info["email"]
    if "currency_code" in company_info:
        currency_ids = models.execute_kw(
            db_name, uid, admin_password,
            "res.currency", "search",
            [[["name", "=", company_info["currency_code"]]]],
        )
        if currency_ids:
            update_vals["currency_id"] = currency_ids[0]

    if update_vals:
        models.execute_kw(
            db_name, uid, admin_password,
            "res.company", "write",
            [[company_id], update_vals],
        )

    # Set timezone on the admin user (tz lives on res.users, not res.company)
    if "timezone" in company_info:
        user_ids = models.execute_kw(
            db_name, uid, admin_password,
            "res.users", "search",
            [[["login", "=", admin_login]]],
        )
        if user_ids:
            models.execute_kw(
                db_name, uid, admin_password,
                "res.users", "write",
                [user_ids, {"tz": company_info["timezone"]}],
            )

    return {"company_id": company_id, "updated_fields": list(update_vals.keys())}


def install_modules(
    host: str,
    port: int,
    db_name: str,
    admin_login: str,
    admin_password: str,
    modules: list[str],
) -> dict:
    """Authenticate and install a list of modules by technical name."""
    common_url = f"http://{host}:{port}/xmlrpc/2/common"
    object_url = f"http://{host}:{port}/xmlrpc/2/object"

    common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
    uid = common.authenticate(db_name, admin_login, admin_password, {})

    if not uid:
        return {"installed": [], "error": "Authentication failed"}

    models = xmlrpc.client.ServerProxy(object_url, allow_none=True)

    module_ids = models.execute_kw(
        db_name, uid, admin_password,
        "ir.module.module", "search",
        [[["name", "in", modules]]],
    )

    if module_ids:
        models.execute_kw(
            db_name, uid, admin_password,
            "ir.module.module", "button_immediate_install",
            [module_ids],
        )

    return {"installed": modules}


def reset_admin_credentials(
    host: str,
    port: int,
    db_name: str,
    old_login: str,
    old_password: str,
    new_login: str,
    new_password: str,
) -> dict:
    """Used after cloning from the golden template: the cloned database still
    has the golden template's original admin login/password. This authenticates
    with those known golden credentials, then changes the admin user's login
    and password to the new customer's actual chosen ones.

    Split into two separate write() calls (password, then login) rather than
    one combined {"login": ..., "password": ...} dict - and, critically,
    re-authenticates with the NEW credentials afterward before reporting
    success. A write() call not raising an exception was previously treated
    as proof it worked; it isn't - it only proves Odoo didn't error, not that
    the password Odoo ends up storing is actually the one that was sent.
    Re-authenticating closes that exact gap.
    """
    common_url = f"http://{host}:{port}/xmlrpc/2/common"
    object_url = f"http://{host}:{port}/xmlrpc/2/object"

    common = xmlrpc.client.ServerProxy(common_url, allow_none=True)

    # wait_for_odoo (called earlier in provisioner.py) only proves the Odoo
    # process itself is up - it runs BEFORE this database even exists yet
    # (golden restore happens after). Odoo still needs to build/register a
    # Registry for this brand new database on first access, which can take
    # a few seconds right after a pg_restore. A single retry with a fixed
    # 2s pause (the previous approach) wasn't a generous enough window -
    # this polls for up to 30s specifically for THIS database to become
    # authenticatable before giving up for real.
    uid = None
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            uid = common.authenticate(db_name, old_login, old_password, {})
        except Exception:
            uid = None
        if uid:
            break
        time.sleep(1.5)

    if not uid:
        return {"success": False, "error": "Could not authenticate with golden template credentials"}

    models = xmlrpc.client.ServerProxy(object_url, allow_none=True)

    user_ids = models.execute_kw(
        db_name, uid, old_password,
        "res.users", "search",
        [[["login", "=", old_login]]],
    )
    if not user_ids:
        return {"success": False, "error": f"Could not find user with login {old_login}"}

    try:
        # Password first, while old_login/old_password are still valid for
        # authenticating this same call.
        models.execute_kw(
            db_name, uid, old_password,
            "res.users", "write",
            [user_ids, {"password": new_password}],
        )
        # Login second, as its own call - once this lands, old_login/
        # old_password stop being usable to authenticate at all.
        models.execute_kw(
            db_name, uid, old_password,
            "res.users", "write",
            [user_ids, {"login": new_login}],
        )
    except Exception as e:
        return {"success": False, "error": f"Credential write failed: {e}"}

    # The actual proof: can the NEW login/password log in? Neither write()
    # succeeding nor raising no exception is trustworthy on its own - this
    # is the only check that confirms the customer's real password works.
    verify_uid = common.authenticate(db_name, new_login, new_password, {})
    if not verify_uid:
        return {
            "success": False,
            "error": "Credentials were written but did not verify - new login/password could not authenticate after the change.",
        }

    return {"success": True, "new_login": new_login}

