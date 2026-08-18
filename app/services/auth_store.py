import hashlib
import json
from pathlib import Path
from threading import Lock

# Previously this was a pure in-memory dict, which meant every uvicorn
# restart (which happens constantly during development, e.g. via --reload
# on any watched file change) silently wiped every customer's instance
# password. That produced 401s ("Session expired") for any instance
# provisioned before the most recent restart, with no way to recover
# short of re-provisioning. Persisting to disk fixes that permanently.

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"

_lock = Lock()
_cache: dict[str, str] = {}  # in-memory cache to avoid a disk read on every request


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _credentials_file(customer_slug: str) -> Path:
    return CUSTOMERS_DIR / customer_slug / ".auth_credentials.json"


def set_credentials(customer_slug: str, admin_password: str) -> None:
    hashed = _hash(admin_password)
    with _lock:
        _cache[customer_slug] = hashed
        creds_file = _credentials_file(customer_slug)
        creds_file.parent.mkdir(parents=True, exist_ok=True)
        creds_file.write_text(json.dumps({"password_hash": hashed}))


def verify_credentials(customer_slug: str, admin_password: str) -> bool:
    with _lock:
        stored_hash = _cache.get(customer_slug)
        if stored_hash is None:
            # Not in the in-memory cache (e.g. process just restarted) -
            # fall back to disk before concluding the customer doesn't exist.
            creds_file = _credentials_file(customer_slug)
            if creds_file.exists():
                try:
                    stored_hash = json.loads(creds_file.read_text())["password_hash"]
                    _cache[customer_slug] = stored_hash
                except (json.JSONDecodeError, KeyError):
                    stored_hash = None
    if stored_hash is None:
        return False
    return stored_hash == _hash(admin_password)


def delete_credentials(customer_slug: str) -> None:
    with _lock:
        _cache.pop(customer_slug, None)
        creds_file = _credentials_file(customer_slug)
        creds_file.unlink(missing_ok=True)
