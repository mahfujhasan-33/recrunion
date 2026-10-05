import logging
import tempfile
import time
from pathlib import Path

from app.config import get_settings

HEARTBEAT_PATH = Path(tempfile.gettempdir()) / "recrunion-worker-heartbeat"
logger = logging.getLogger(__name__)


def run_worker() -> None:
    """Run the M0 worker heartbeat loop until the container stops."""

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("Worker started", extra={"operation": "worker_start", "status": "running"})

    while True:
        HEARTBEAT_PATH.touch()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_worker()
