import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from app.models.packages import PACKAGES
from app.services.odoo_config import wait_for_odoo, create_database, install_modules, configure_company, reset_admin_credentials
from app.services.nginx_manager import generate_nginx_config
from app.services.email_service import send_welcome_email
from app.services.auth_store import set_credentials
from app.services.golden_template import golden_template_available, restore_from_golden, GOLDEN_ADMIN_LOGIN, GOLDEN_ADMIN_PASSWORD

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
CUSTOMERS_DIR = BASE_DIR / "customers"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

MASTER_PASSWORD = "admin"  # TODO: move to env var / secrets


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

    # 3. Run docker compose up -d
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d"],
        cwd=str(customer_dir),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return {
            "customer_slug": customer_slug,
            "status": "container_start_failed",
            "stderr": result.stderr,
        }

    # 4. Wait for Odoo to be ready inside the container
    ready = wait_for_odoo("localhost", host_port, timeout=120)
    if not ready:
        return {
            "customer_slug": customer_slug,
            "status": "odoo_not_ready",
            "compose_stderr": result.stderr,
        }

    # 5. Create the database — fast path (clone golden template) or slow path (fresh install)
    admin_login = f"admin@{customer_slug}.local"

    if golden_template_available():
        restore_result = restore_from_golden(customer_slug)
        db_result = {
            "db_name": customer_slug,
            "admin_login": admin_login,
            "status": "cloned_from_golden" if restore_result["success"] else "clone_failed",
            "restore_detail": restore_result,
        }
        install_result = {"installed": "included in golden template", "skipped": True}

        # The clone still has the golden template's original login/password.
        # Reset it to this customer's real chosen credentials before doing anything else.
        if restore_result["success"]:
            reset_result = reset_admin_credentials(
                "localhost", host_port,
                customer_slug,
                GOLDEN_ADMIN_LOGIN, GOLDEN_ADMIN_PASSWORD,
                admin_login, admin_password,
            )
            db_result["credential_reset"] = reset_result
    else:
        db_result = create_database(
            "localhost", host_port,
            MASTER_PASSWORD, customer_slug,
            admin_login, admin_password,
        )
        # 6. Install requested modules (default to base essentials) — slow path only
        modules = modules or ["sale_management", "crm"]
        install_result = install_modules(
            "localhost", host_port,
            customer_slug, admin_login, admin_password,
            modules,
        )

    # Store credentials so the dashboard can authenticate this customer
    set_credentials(customer_slug, admin_password)

    # 7. Configure company info (name, address, currency, timezone, etc.)
    company_result = None
    if company_info:
        company_result = configure_company(
            "localhost", host_port,
            customer_slug, admin_login, admin_password,
            company_info,
        )

    # 8. Configure Nginx reverse proxy for subdomain routing
    nginx_result = generate_nginx_config(customer_slug)

    # 9. Send welcome email with instance URL and login credentials
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
        "compose_file": str(compose_path),
    }
