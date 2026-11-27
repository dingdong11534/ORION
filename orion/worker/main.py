"""Independent ORION background worker process (`python -m orion.worker.main`)."""

import logging
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from orion.application.jobs.supervisor import BackgroundJobSupervisor
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)

logger = logging.getLogger(__name__)


class DesktopNotifier:
    """Best-effort Linux desktop notification via notify-send."""

    def notify(self, title: str, body: str) -> None:
        exe = shutil.which("notify-send")
        if not exe:
            return
        try:
            subprocess.run([exe, title, body], check=False, timeout=3)
        except Exception:
            logger.debug("notify-send failed for %r", title, exc_info=True)


def build_worker_supervisor(
    db_path: Path | str | None = None,
    *,
    browser_service: object | None = None,
) -> tuple[BackgroundJobSupervisor, object]:
    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    store = SqliteBackgroundJobStore(sf)
    supervisor = BackgroundJobSupervisor(
        store,
        browser_service=browser_service,
        notifier=DesktopNotifier(),
    )
    # Register built-in safe diagnostic workflow
    supervisor.register_workflow(
        "noop_ping",
        [("ping", True, lambda job, ctx: "pong")],
    )
    return supervisor, engine


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s %(message)s")
    supervisor, engine = build_worker_supervisor()
    supervisor.recover_on_startup(stale_seconds=60.0)

    stopping = False

    def _handle_sig(signum: int, _frame: object) -> None:
        nonlocal stopping
        logger.info("Worker received signal %s; shutting down gracefully...", signum)
        stopping = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    logger.info("ORION background worker started.")
    try:
        while not stopping:
            ran = supervisor.run_next_job()
            if ran is None:
                time.sleep(1.0)
    finally:
        engine.dispose()
        logger.info("ORION background worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
