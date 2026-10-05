import time

from app.config import get_settings
from worker.main import HEARTBEAT_PATH


def is_worker_healthy() -> bool:
    """Return whether the worker heartbeat was updated recently."""

    if not HEARTBEAT_PATH.exists():
        return False

    heartbeat_age = time.time() - HEARTBEAT_PATH.stat().st_mtime
    maximum_age = max(30.0, get_settings().worker_poll_seconds * 3)
    return heartbeat_age <= maximum_age


if __name__ == "__main__":
    raise SystemExit(0 if is_worker_healthy() else 1)
