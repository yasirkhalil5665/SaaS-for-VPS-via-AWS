import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).resolve().parent.parent.parent
NGINX_CONF_D = BASE_DIR / "nginx" / "conf.d"
TEMPLATES_DIR = BASE_DIR / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

BASE_DOMAIN = "localhost"  # change to your real domain in production, e.g. "erisp.com"


def _validate_config() -> tuple[bool, str]:
    """Run nginx -t inside the container. Returns (is_valid, output)."""
    result = subprocess.run(
        ["docker", "exec", "nginx-proxy", "nginx", "-t"],
        capture_output=True,
        text=True,
    )
    is_valid = result.returncode == 0
    return is_valid, (result.stdout + result.stderr)


def _safe_reload() -> dict:
    """Validate config before reloading. Never applies a broken config."""
    is_valid, test_output = _validate_config()

    if not is_valid:
        return {
            "reloaded": False,
            "reason": "Config validation failed, reload skipped to avoid breaking other customers",
            "nginx_test_output": test_output,
        }

    result = subprocess.run(
        ["docker", "exec", "nginx-proxy", "nginx", "-s", "reload"],
        capture_output=True,
        text=True,
    )
    return {
        "reloaded": result.returncode == 0,
        "reload_stdout": result.stdout,
        "reload_stderr": result.stderr,
    }


def generate_nginx_config(customer_slug: str) -> dict:
    template = env.get_template("nginx-server.conf.j2")
    rendered = template.render(customer_slug=customer_slug, base_domain=BASE_DOMAIN)

    NGINX_CONF_D.mkdir(parents=True, exist_ok=True)
    conf_path = NGINX_CONF_D / f"{customer_slug}.conf"
    backup_content = conf_path.read_text() if conf_path.exists() else None

    conf_path.write_text(rendered)

    reload_result = _safe_reload()

    # If reload failed validation, roll back to avoid leaving a broken/half-applied file
    if not reload_result.get("reloaded"):
        if backup_content is not None:
            conf_path.write_text(backup_content)
        else:
            conf_path.unlink(missing_ok=True)
        _safe_reload()  # reload again with the bad file removed/rolled back

    return {
        "conf_file": str(conf_path),
        "domain": f"{customer_slug}.{BASE_DOMAIN}",
        **reload_result,
    }


def remove_nginx_config(customer_slug: str) -> dict:
    """Delete a customer's nginx config and safely reload."""
    conf_path = NGINX_CONF_D / f"{customer_slug}.conf"
    existed = conf_path.exists()

    if existed:
        conf_path.unlink()

    reload_result = _safe_reload()

    return {
        "conf_file": str(conf_path),
        "existed": existed,
        **reload_result,
    }
