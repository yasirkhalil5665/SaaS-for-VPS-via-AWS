import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from app.models.packages import PACKAGES

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
CUSTOMERS_DIR = BASE_DIR / "customers"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def provision_customer(customer_slug: str, package: str) -> dict:
    if package not in PACKAGES:
        raise ValueError(f"Unknown package: {package}")

    specs = PACKAGES[package]
    customer_dir = CUSTOMERS_DIR / customer_slug

    # 1. Create customer directory structure
    (customer_dir / "addons").mkdir(parents=True, exist_ok=True)
    (customer_dir / "postgresql").mkdir(parents=True, exist_ok=True)

    # 2. Render docker-compose.yml from template
    template = env.get_template("docker-compose.yml.j2")
    rendered = template.render(customer_slug=customer_slug, **specs)
    compose_path = customer_dir / "docker-compose.yml"
    compose_path.write_text(rendered)

    # 3. Run docker compose up -d
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d"],
        cwd=str(customer_dir),
        capture_output=True,
        text=True,
    )

    return {
        "customer_slug": customer_slug,
        "package": package,
        "compose_file": str(compose_path),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
