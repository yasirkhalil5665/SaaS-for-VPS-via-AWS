import hashlib
from threading import Lock

_lock = Lock()
_credentials: dict[str, str] = {}  # customer_slug -> sha256 hash of admin_password


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def set_credentials(customer_slug: str, admin_password: str) -> None:
    with _lock:
        _credentials[customer_slug] = _hash(admin_password)


def verify_credentials(customer_slug: str, admin_password: str) -> bool:
    with _lock:
        stored_hash = _credentials.get(customer_slug)
    if stored_hash is None:
        return False
    return stored_hash == _hash(admin_password)


def delete_credentials(customer_slug: str) -> None:
    with _lock:
        _credentials.pop(customer_slug, None)
