import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from app.models.packages import PACKAGES
from app.services.odoo_config import wait_for_odoo, create_database, install_modules, configure_company
from app.services.nginx_manager import generate_nginx_config
from app.services.email_service import send_welcome_email

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

    # 5. Create the database
    admin_login = f"admin@{customer_slug}.local"
    db_result = create_database(
        "localhost", host_port,
        MASTER_PASSWORD, customer_slug,
        admin_login, admin_password,
    )

    # 6. Install requested modules (default to base essentials)
    modules = modules or ["sale_management", "crm"]
    install_result = install_modules(
        "localhost", host_port,
        customer_slug, admin_login, admin_password,
        modules,
    )

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
