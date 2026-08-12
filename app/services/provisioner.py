import subprocess
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from app.models.packages import PACKAGES
from app.services.odoo_config import wait_for_odoo, create_database, install_modules, configure_company, reset_admin_credentials
from app.services.nginx_manager import generate_nginx_config
from app.services.email_service import send_welcome_email
from app.services.auth_store import set_credentials
from app.services.golden_template import golden_template_available, restore_from_golden, GOLDEN_ADMIN_LOGIN, GOLDEN_ADMIN_PASSWORD
from app.services.main_site_sync import sync_new_customer

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
CUSTOMERS_DIR = BASE_DIR / "customers"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

MASTER_PASSWORD = "admin"  # TODO: move to env var / secrets


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
) -> dict:
    if package not in PACKAGES:
        raise ValueError(f"Unknown package: {package}")

    timer = _Timer()

    specs = PACKAGES[package]
    customer_dir = CUSTOMERS_DIR / customer_slug

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
        ["docker", "compose", "-f", str(compose_path), "up", "-d"],
        cwd=str(customer_dir),
        capture_output=True,
        text=True,
    )
    timer.lap("docker_compose_up")

    if result.returncode != 0:
        return {
            "customer_slug": customer_slug,
            "status": "container_start_failed",
            "stderr": result.stderr,
            "timing": timer.marks,
        }

    # 4. Wait for Odoo to be ready inside the container
    ready = wait_for_odoo("localhost", host_port, timeout=120)
    timer.lap("wait_for_odoo")
    if not ready:
        return {
            "customer_slug": customer_slug,
            "status": "odoo_not_ready",
            "compose_stderr": result.stderr,
            "timing": timer.marks,
        }

    # 5. Create the database — fast path (clone golden template) or slow path (fresh install)
    admin_login = f"admin@{customer_slug}.local"

    if golden_template_available():
        restore_result = restore_from_golden(customer_slug)
        timer.lap("golden_clone")
        db_result = {
            "db_name": customer_slug,
            "admin_login": admin_login,
            "status": "cloned_from_golden" if restore_result["success"] else "clone_failed",
            "restore_detail": restore_result,
        }
        install_result = {"installed": "included in golden template", "skipped": True}

        if restore_result["success"]:
            reset_result = reset_admin_credentials(
                "localhost", host_port,
                customer_slug,
                GOLDEN_ADMIN_LOGIN, GOLDEN_ADMIN_PASSWORD,
                admin_login, admin_password,
            )
            db_result["credential_reset"] = reset_result
        timer.lap("credential_reset")
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

    # 7. Configure company info
    company_result = None
    if company_info:
        company_result = configure_company(
            "localhost", host_port,
            customer_slug, admin_login, admin_password,
            company_info,
        )
    timer.lap("configure_company")

    # 8. Configure Nginx
    nginx_result = generate_nginx_config(customer_slug)
    timer.lap("nginx_config")

    # 9. Send welcome email
    email_result = None
    customer_email = (company_info or {}).get("email")
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

    # 10. Save persistent metadata
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

    # 11. Sync to main site: creates portal user + saas.instance record so the
    # customer can log in at the main site's /web/login and see /my/instance.
    main_site_result = None
    if customer_email:
        main_site_result = sync_new_customer(
            customer_name=(company_info or {}).get("name") or customer_slug,
            customer_email=customer_email,
            portal_password=admin_password,
            customer_slug=customer_slug,
            package=package,
            instance_admin_password=admin_password,
            domain=nginx_result["domain"],
        )
    timer.lap("main_site_sync")

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
