import subprocess
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def get_instance_status(customer_slug: str) -> dict:
    odoo_container = f"odoo-{customer_slug}"
    db_container = f"postgres-{customer_slug}"

    customer_dir = CUSTOMERS_DIR / customer_slug
    if not customer_dir.exists():
        return {"exists": False}

    # Container state (running/exited/etc.)
    code, out, _ = _run([
        "docker", "inspect", "--format",
        '{"status":"{{.State.Status}}","started_at":"{{.State.StartedAt}}"}',
        odoo_container,
    ])
    if code != 0:
        return {"exists": True, "container_found": False}

    try:
        odoo_state = json.loads(out.strip())
    except Exception:
        odoo_state = {"status": "unknown"}

    code_db, out_db, _ = _run([
        "docker", "inspect", "--format", "{{.State.Status}}", db_container,
    ])
    db_status = out_db.strip() if code_db == 0 else "not_found"

    # Live resource usage (CPU %, memory usage/limit) — single snapshot, no streaming
    code_stats, out_stats, _ = _run([
        "docker", "stats", "--no-stream", "--format",
        '{"cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}"}',
        odoo_container,
    ])
    stats = None
    if code_stats == 0 and out_stats.strip():
        try:
            stats = json.loads(out_stats.strip())
        except Exception:
            stats = None

    return {
        "exists": True,
        "container_found": True,
        "odoo_status": odoo_state.get("status"),
        "odoo_started_at": odoo_state.get("started_at"),
        "db_status": db_status,
        "stats": stats,
    }


def control_instance(customer_slug: str, action: str) -> dict:
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"Unknown action: {action}")

    odoo_container = f"odoo-{customer_slug}"
    db_container = f"postgres-{customer_slug}"

    results = {}

    if action == "start":
        # DB must be up before Odoo
        code, out, err = _run(["docker", "start", db_container])
        results["db"] = {"returncode": code, "stdout": out, "stderr": err}
        code, out, err = _run(["docker", "start", odoo_container])
        results["odoo"] = {"returncode": code, "stdout": out, "stderr": err}
    elif action == "stop":
        code, out, err = _run(["docker", "stop", odoo_container])
        results["odoo"] = {"returncode": code, "stdout": out, "stderr": err}
        code, out, err = _run(["docker", "stop", db_container])
        results["db"] = {"returncode": code, "stdout": out, "stderr": err}
    elif action == "restart":
        code, out, err = _run(["docker", "restart", db_container])
        results["db"] = {"returncode": code, "stdout": out, "stderr": err}
        code, out, err = _run(["docker", "restart", odoo_container])
        results["odoo"] = {"returncode": code, "stdout": out, "stderr": err}

    return {"customer_slug": customer_slug, "action": action, "result": results}
