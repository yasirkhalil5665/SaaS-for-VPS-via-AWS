import subprocess
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from jinja2 import Environment, FileSystemLoader

from app.models.packages import PACKAGES
from app.services.odoo_config import wait_for_odoo, create_database, install_modules, configure_company, reset_admin_credentials
from app.services.nginx_manager import generate_nginx_config
from app.services.email_service import send_welcome_email
from app.services.auth_store import set_credentials
from app.services.golden_template import golden_template_available, restore_from_golden, GOLDEN_ADMIN_LOGIN, GOLDEN_ADMIN_PASSWORD
from app.services.main_site_sync import create_pending_customer, mark_instance_ready, mark_instance_failed

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
CUSTOMERS_DIR = BASE_DIR / "customers"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

MASTER_PASSWORD = "admin"  # TODO: move to env var / secrets


def is_slug_taken(customer_slug: str) -> bool:
    """Ground-truth check against the filesystem, not just the in-memory
    status store (which is wiped on every restart - the old duplicate check
    based purely on status_store could silently let a real duplicate through
    after any restart)."""
    return (CUSTOMERS_DIR / customer_slug).exists()


class _Timer:
    """Tracks how long each provisioning step takes, for diagnosing slowness."""
    def __init__(self):
        self.marks = {}
        self._last = time.monotonic()

    def lap(self, step_name: str):
        now = time.monotonic()
        self.marks[step_name] = round(now - self._last, 2)
        self._last = now

    def total(self) -> float:
        return round(sum(self.marks.values()), 2)


def provision_customer(
    customer_slug: str,
    package: str,
    host_port: int,
    admin_password: str = "admin123",
    modules: list[str] | None = None,
    company_info: dict | None = None,
    full_name: str | None = None,
    referral_token: str | None = None,
    on_account_ready: Callable[[dict], None] | None = None,
) -> dict:
    """on_account_ready, if given, is called as soon as the portal login
    exists - BEFORE any Docker work starts - so the caller (the /provision
    route) can flag the job as "you can log in now" independently of the
    much slower container provisioning that follows. This is what lets the
    signup page redirect to login in under a second instead of waiting
    15-30+ seconds for the whole chain to finish."""
    if package not in PACKAGES:
        raise ValueError(f"Unknown package: {package}")
    if is_slug_taken(customer_slug):
        raise ValueError(
            "This name is already taken. Please choose a different company name."
        )

    timer = _Timer()
    specs = PACKAGES[package]
    customer_dir = CUSTOMERS_DIR / customer_slug
    admin_login = f"admin@{customer_slug}.local"
    customer_email = (company_info or {}).get("email")

    # 0. Create the account FIRST - Company, Person, portal login, and a
    # saas.instance record (state='provisioning' by default) - before any
    # Docker work. This is what the customer needs to be able to log in;
    # the rest of this function is about making that login's dashboard
    # actually show something real.
    main_site_result = None
    if customer_email:
        main_site_result = create_pending_customer(
            person_name=full_name or customer_email.split("@")[0],
            company_name=(company_info or {}).get("name") or customer_slug,
            customer_email=customer_email,
            customer_phone=(company_info or {}).get("phone"),
            country_code=(company_info or {}).get("country_code"),
            portal_password=admin_password,
            customer_slug=customer_slug,
            package=package,
            instance_admin_password=admin_password,
            referral_token=referral_token,
        )
    timer.lap("main_site_sync_create")

    if on_account_ready:
        on_account_ready({
            "customer_slug": customer_slug,
            "login_email": customer_email,
            "main_site_sync": main_site_result,
        })

    try:
        # 1. Create customer directory structure
        (customer_dir / "addons").mkdir(parents=True, exist_ok=True)
        (customer_dir / "postgresql").mkdir(parents=True, exist_ok=True)

        # 2. Render docker-compose.yml from template
        template = env.get_template("docker-compose.yml.j2")
        rendered = template.render(customer_slug=customer_slug, host_port=host_port, **specs)
        compose_path = customer_dir / "docker-compose.yml"
        compose_path.write_text(rendered)
        timer.lap("setup_files")

        # 3. Run docker compose up -d
        result = subprocess.run(
            # Explicit -p pins the compose project name to customer_slug
            # instead of letting compose derive it from the cwd basename.
            ["docker", "compose", "-p", customer_slug, "-f", str(compose_path), "up", "-d"],
            cwd=str(customer_dir),
            capture_output=True,
            text=True,
        )
        timer.lap("docker_compose_up")

        if result.returncode != 0:
            # docker compose's normal first-run output is a wall of
            # "Network X Creating/Created", "Volume Y Creating/Created" -
            # completely expected, not an error. The actual failure reason
            # is always further down. Truncating from the *front*
            # (stderr[:200]) chopped off the real message and left only
            # this noise. Log the full output server-side, and surface the
            # tail (where docker puts the real error) to the customer.
            print(f"[provision:{customer_slug}] docker compose up failed (rc={result.returncode}):\n{result.stderr}")
            if customer_email:
                tail = result.stderr.strip()[-500:]
                mark_instance_failed(customer_slug, f"Container start failed: {tail}")
            return {
                "customer_slug": customer_slug,
                "status": "container_start_failed",
                "stderr": result.stderr,
                "main_site_sync": main_site_result,
                "timing": timer.marks,
            }

        # 4. Wait for Odoo to be ready inside the container
        ready = wait_for_odoo("localhost", host_port, timeout=120)
        timer.lap("wait_for_odoo")
        if not ready:
            if customer_email:
                mark_instance_failed(customer_slug, "The instance did not become ready in time.")
            return {
                "customer_slug": customer_slug,
                "status": "odoo_not_ready",
                "compose_stderr": result.stderr,
                "main_site_sync": main_site_result,
                "timing": timer.marks,
            }

        # 5. Create the database - fast path (clone golden template) or slow path (fresh install)
        if golden_template_available():
            restore_result = restore_from_golden(customer_slug)
            timer.lap("golden_clone")
            db_result = {
                "db_name": customer_slug,
                "admin_login": admin_login,
                "status": "cloned_from_golden" if restore_result["success"] else "clone_failed",
                "restore_detail": restore_result,
            }

            if restore_result["success"]:
                reset_result = reset_admin_credentials(
                    "localhost", host_port,
                    customer_slug,
                    GOLDEN_ADMIN_LOGIN, GOLDEN_ADMIN_PASSWORD,
                    admin_login, admin_password,
                )
                db_result["credential_reset"] = reset_result
            timer.lap("credential_reset")

            # The golden template only has sale_management + crm baked in.
            # Install only the delta on top of the clone, so the common
            # case (no extra modules) stays fast.
            GOLDEN_BASELINE_MODULES = {"sale_management", "crm"}
            extra_modules = [m for m in (modules or []) if m not in GOLDEN_BASELINE_MODULES]
            if extra_modules and restore_result["success"]:
                extra_install_result = install_modules(
                    "localhost", host_port,
                    customer_slug, admin_login, admin_password,
                    extra_modules,
                )
                install_result = {
                    "installed": sorted(GOLDEN_BASELINE_MODULES),
                    "extra_installed": extra_install_result,
                }
                timer.lap("install_extra_modules")
            else:
                install_result = {"installed": "included in golden template", "skipped": True}
        else:
            db_result = create_database(
                "localhost", host_port,
                MASTER_PASSWORD, customer_slug,
                admin_login, admin_password,
            )
            timer.lap("create_database")
            modules = modules or ["sale_management", "crm"]
            install_result = install_modules(
                "localhost", host_port,
                customer_slug, admin_login, admin_password,
                modules,
            )
            timer.lap("install_modules")

        set_credentials(customer_slug, admin_password)

        # 6. Configure company info
        company_result = None
        if company_info:
            company_result = configure_company(
                "localhost", host_port,
                customer_slug, admin_login, admin_password,
                company_info,
            )
        timer.lap("configure_company")

        # 7. Configure Nginx
        nginx_result = generate_nginx_config(customer_slug)
        timer.lap("nginx_config")

        # 8. Send welcome email
        email_result = None
        if customer_email:
            email_result = send_welcome_email(
                to_email=customer_email,
                customer_slug=customer_slug,
                domain=nginx_result["domain"],
                admin_login=admin_login,
                admin_password=admin_password,
                package=package,
            )
        timer.lap("send_email")

        # 9. Save persistent metadata
        meta = {
            "customer_slug": customer_slug,
            "package": package,
            "host_port": host_port,
            "domain": nginx_result["domain"],
            "admin_login": admin_login,
            "company_name": (company_info or {}).get("name"),
            "customer_email": customer_email,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (customer_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        timer.lap("save_metadata")

        # 10. Flip the account created in step 0 over to "ready" now that
        # Docker provisioning actually succeeded.
        if customer_email:
            mark_instance_ready(customer_slug)
        timer.lap("mark_ready")

        return {
            "customer_slug": customer_slug,
            "package": package,
            "host_port": host_port,
            "status": "provisioned",
            "db": db_result,
            "modules": install_result,
            "company": company_result,
            "nginx": nginx_result,
            "email": email_result,
            "main_site_sync": main_site_result,
            "compose_file": str(compose_path),
            "timing": {**timer.marks, "total_seconds": timer.total()},
        }

    except Exception as e:
        # The account already exists at this point (step 0 ran before this
        # try block) - without this, a mid-chain failure would leave the
        # customer logged in and staring at "Setting up..." forever, since
        # nothing would ever flip state away from 'provisioning'.
        if customer_email:
            mark_instance_failed(customer_slug, str(e))
        raise
