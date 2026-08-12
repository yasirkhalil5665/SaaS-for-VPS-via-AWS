import json
from pathlib import Path

from app.services.instance_manager import get_instance_status

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"


def list_all_customers() -> list[dict]:
    if not CUSTOMERS_DIR.exists():
        return []

    results = []
    for customer_dir in sorted(CUSTOMERS_DIR.iterdir()):
        if not customer_dir.is_dir():
            continue
        meta_path = customer_dir / "meta.json"
        if not meta_path.exists():
            continue

        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue

        slug = meta.get("customer_slug", customer_dir.name)
        live_status = get_instance_status(slug)

        results.append({
            **meta,
            "odoo_status": live_status.get("odoo_status", "unknown"),
            "db_status": live_status.get("db_status", "unknown"),
        })

    return results


def get_customer_detail(customer_slug: str) -> dict | None:
    meta_path = CUSTOMERS_DIR / customer_slug / "meta.json"
    if not meta_path.exists():
        return None

    meta = json.loads(meta_path.read_text())
    live_status = get_instance_status(customer_slug)

    return {**meta, **live_status}
