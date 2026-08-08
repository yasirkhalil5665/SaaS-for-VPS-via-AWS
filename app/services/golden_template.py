import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GOLDEN_DUMP_PATH = BASE_DIR / "templates" / "golden.dump"

# Credentials the golden template database was created with.
# Must match whatever admin_login/admin_password were used when the
# "golden" customer was originally provisioned (before dumping it).
GOLDEN_ADMIN_LOGIN = "admin@golden.local"
GOLDEN_ADMIN_PASSWORD = "admin123"


def golden_template_available() -> bool:
    return GOLDEN_DUMP_PATH.exists()


def restore_from_golden(customer_slug: str) -> dict:
    """Restores the golden.dump into the customer's postgres container.
    Much faster than create_database + install_modules from scratch,
    since it skips schema creation and asset compilation entirely."""
    db_container = f"postgres-{customer_slug}"

    code, out, err = _run([
        "docker", "exec", db_container,
        "createdb", "-U", "odoo", customer_slug,
    ])
    if code != 0 and "already exists" not in err:
        return {"success": False, "step": "createdb", "error": err}

    code, out, err = _run([
        "docker", "cp", str(GOLDEN_DUMP_PATH), f"{db_container}:/golden.dump",
    ])
    if code != 0:
        return {"success": False, "step": "docker cp", "error": err}

    code, out, err = _run([
        "docker", "exec", db_container,
        "pg_restore", "-U", "odoo", "-d", customer_slug,
        "--no-owner", "--no-privileges", "/golden.dump",
    ])
    success = "error" not in err.lower() or code == 0

    return {"success": success, "step": "pg_restore", "returncode": code, "stderr": err}


def _run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr
