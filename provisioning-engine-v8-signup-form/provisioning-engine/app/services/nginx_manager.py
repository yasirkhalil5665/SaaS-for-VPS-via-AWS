import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).resolve().parent.parent.parent
NGINX_CONF_D = BASE_DIR / "nginx" / "conf.d"
TEMPLATES_DIR = BASE_DIR / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

BASE_DOMAIN = "localhost"  # change to your real domain in production, e.g. "erisp.com"


def generate_nginx_config(customer_slug: str) -> dict:
    template = env.get_template("nginx-server.conf.j2")
    rendered = template.render(customer_slug=customer_slug, base_domain=BASE_DOMAIN)

    NGINX_CONF_D.mkdir(parents=True, exist_ok=True)
    conf_path = NGINX_CONF_D / f"{customer_slug}.conf"
    conf_path.write_text(rendered)

    # Reload nginx so the new server block takes effect (no downtime)
    result = subprocess.run(
        ["docker", "exec", "nginx-proxy", "nginx", "-s", "reload"],
        capture_output=True,
        text=True,
    )

    return {
        "conf_file": str(conf_path),
        "domain": f"{customer_slug}.{BASE_DOMAIN}",
        "reload_stdout": result.stdout,
        "reload_stderr": result.stderr,
        "reload_returncode": result.returncode,
    }
