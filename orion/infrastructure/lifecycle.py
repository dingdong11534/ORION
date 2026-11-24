"""Lifecycle and graceful shutdown coordination for the ORION application."""

import logging
from typing import TYPE_CHECKING

from sqlalchemy import Engine

if TYPE_CHECKING:
    from orion.infrastructure.single_instance import SingleInstanceManager
    from orion.ui.qt_bridge import OrionBridge

logger = logging.getLogger(__name__)


class ApplicationLifecycle:
    """Coordinates graceful application shutdown in deterministic, idempotent order.

    Uses dual-state flags to allow resumed draining after a timeout:

    - ``_shutdown_started``: Set on first ``shutdown()`` call. Prevents repeated
      IPC server teardown and cancellation signaling.
    - ``_shutdown_complete``: Set only after all workers have drained. Guards
      engine disposal and lock release, ensuring they never happen while workers
      are still running.
    """

    def __init__(
        self,
        single_instance: "SingleInstanceManager",
        bridge: "OrionBridge",
        engine: Engine,
    ) -> None:
        self._single_instance = single_instance
        self._bridge = bridge
        self._engine = engine
        self._shutdown_started = False
        self._shutdown_complete = False

    @property
    def is_shut_down(self) -> bool:
        """Return True only when shutdown has fully completed (all workers drained)."""
        return self._shutdown_complete

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Execute the graceful application shutdown sequence.

        Returns True if all workers drained and final cleanup completed.
        Returns False if workers are still active after *timeout_ms*.

        When False is returned, resources (database engine, single-instance lock)
        are intentionally preserved.  A subsequent ``shutdown()`` call will
        resume draining with the new *timeout_ms* value.

        Order of operations (first call only for steps 1-2):
        1. Stop accepting secondary-instance IPC connections.
        2. Signal cancellation of active generation requests.
        3. Drain the thread pool (every call).
        4. Dispose of the SQLAlchemy engine (only after full drain).
        5. Release the single-instance lock (only after full drain).
        """
        if self._shutdown_complete:
            return True

        if not self._shutdown_started:
            self._shutdown_started = True
            logger.info("Initiating ORION graceful shutdown sequence...")

            # 1. Stop accepting secondary-instance IPC connections
            self._single_instance.close_ipc_server()

        # 2-3. Reject new workers, cancel active requests, drain thread pool
        drained = self._bridge.shutdown(timeout_ms=timeout_ms)
        if not drained:
            logger.warning(
                "Active background workers did not drain within %d ms. "
                "Database engine and lock preserved for resumed shutdown.",
                timeout_ms,
            )
            return False

        # 4. Dispose of the database engine only after all workers have stopped
        try:
            self._engine.dispose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error disposing database engine: %s", exc)

        # 5. Release single-instance lock
        self._single_instance.release_lock()

        self._shutdown_complete = True
        logger.info("ORION shutdown completed cleanly.")
        return True
