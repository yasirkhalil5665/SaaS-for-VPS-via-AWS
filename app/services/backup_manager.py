import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def _backups_dir(customer_slug: str) -> Path:
    d = CUSTOMERS_DIR / customer_slug / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup(customer_slug: str) -> dict:
    db_container = f"postgres-{customer_slug}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{customer_slug}-{timestamp}.dump"
    backups_dir = _backups_dir(customer_slug)
    local_path = backups_dir / filename

    # Dump inside the container, then copy the file out to the host
    code, out, err = _run([
        "docker", "exec", db_container,
        "pg_dump", "-U", "odoo", "-Fc", customer_slug, "-f", f"/{filename}",
    ])
    if code != 0:
        return {"success": False, "step": "pg_dump", "error": err}

    code, out, err = _run([
        "docker", "cp", f"{db_container}:/{filename}", str(local_path),
    ])
    if code != 0:
        return {"success": False, "step": "docker cp", "error": err}

    # Clean up the dump file left inside the container
    _run(["docker", "exec", db_container, "rm", f"/{filename}"])

    size_bytes = local_path.stat().st_size if local_path.exists() else 0

    return {
        "success": True,
        "filename": filename,
        "created_at": timestamp,
        "size_bytes": size_bytes,
    }


def list_backups(customer_slug: str) -> list[dict]:
    backups_dir = _backups_dir(customer_slug)
    entries = []
    for f in sorted(backups_dir.glob("*.dump"), reverse=True):
        stat = f.stat()
        entries.append({
            "filename": f.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return entries


def restore_backup(customer_slug: str, filename: str) -> dict:
    db_container = f"postgres-{customer_slug}"
    backups_dir = _backups_dir(customer_slug)
    local_path = backups_dir / filename

    if not local_path.exists() or ".." in filename:
        return {"success": False, "error": "Backup file not found"}

    code, out, err = _run([
        "docker", "cp", str(local_path), f"{db_container}:/restore.dump",
    ])
    if code != 0:
        return {"success": False, "step": "docker cp", "error": err}

    # --clean drops existing objects first so restore reflects the backup exactly
    code, out, err = _run([
        "docker", "exec", db_container,
        "pg_restore", "-U", "odoo", "-d", customer_slug,
        "--clean", "--if-exists", "--no-owner", "--no-privileges", "/restore.dump",
    ])
    success = code == 0 or "error" not in err.lower()

    _run(["docker", "exec", db_container, "rm", "/restore.dump"])

    return {"success": success, "returncode": code, "stderr": err}


def delete_backup(customer_slug: str, filename: str) -> dict:
    backups_dir = _backups_dir(customer_slug)
    local_path = backups_dir / filename

    if ".." in filename:
        return {"success": False, "error": "Invalid filename"}

    if not local_path.exists():
        return {"success": False, "error": "Backup file not found"}

    local_path.unlink()
    return {"success": True, "filename": filename}


def get_backup_path(customer_slug: str, filename: str) -> Path | None:
    if ".." in filename:
        return None
    path = _backups_dir(customer_slug) / filename
    return path if path.exists() else None
