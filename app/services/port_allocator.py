from pathlib import Path
from threading import Lock

_lock = Lock()
# Store outside customers/ since Docker sometimes leaves root-owned files in there,
# which previously caused permission errors writing this counter.
_STATE_DIR = Path(__file__).resolve().parent.parent.parent / ".state"
_COUNTER_FILE = _STATE_DIR / "port_counter"
_BASE_PORT = 8100


def get_next_port() -> int:
    with _lock:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        if _COUNTER_FILE.exists():
            current = int(_COUNTER_FILE.read_text().strip() or _BASE_PORT)
        else:
            current = _BASE_PORT
        next_port = current + 1
        _COUNTER_FILE.write_text(str(next_port))
        return next_port
