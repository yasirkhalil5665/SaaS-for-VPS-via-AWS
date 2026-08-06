from threading import Lock

_lock = Lock()
_status: dict[str, dict] = {}


def set_status(customer_slug: str, data: dict) -> None:
    with _lock:
        _status[customer_slug] = data


def get_status(customer_slug: str) -> dict | None:
    with _lock:
        return _status.get(customer_slug)


def delete_status(customer_slug: str) -> None:
    with _lock:
        _status.pop(customer_slug, None)
