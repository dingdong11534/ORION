import logging

from PySide6.QtCore import Property, QObject, QThreadPool, Signal, Slot

from orion.application.assistant_service import AssistantService
from orion.application.contracts import (
    AssistantResponse,
    ChatMessage,
    MessageStatus,
    generate_request_id,
)
from orion.infrastructure.workers import Worker, WorkerSignals

logger = logging.getLogger(__name__)


class OrionBridge(QObject):
    """QObject bridge exposing a narrow, thread-safe interface to the QML view.

    Coordinates worker ownership, dedicated thread pool execution, and clean
    shutdown handling to prevent accessing deleted QObjects.
    """

    responseReady = Signal(str)
    errorOccurred = Signal(str)
    busyChanged = Signal(bool)

    def __init__(
        self,
        assistant_service: AssistantService,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = assistant_service

        if thread_pool is not None:
            self._thread_pool = thread_pool
            self._owns_thread_pool = False
        else:
            self._thread_pool = QThreadPool(self)
            self._thread_pool.setMaxThreadCount(1)
            self._owns_thread_pool = True

        self._is_busy = False
        self._is_shutting_down = False
        self._active_workers: dict[str, Worker] = {}

    @Property(bool, notify=busyChanged)
    def isBusy(self) -> bool:
        """Expose current processing state to QML property bindings."""
        return self._is_busy

    @Slot(str)
    def send(self, message: str) -> None:
        """Compatibility alias for sendMessage."""
        self.sendMessage(message)

    @Slot(str)
    def sendMessage(self, message: str) -> None:
        """Validate input and initiate background message processing."""
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return

        trimmed = message.strip()
        if not trimmed:
            self.errorOccurred.emit("Message content cannot be blank.")
            return

        if self._is_busy:
            self.errorOccurred.emit("A request is already in progress. Please wait.")
            return

        self._set_busy(True)

        request_id = generate_request_id()
        signals = WorkerSignals(parent=self)
        worker = Worker(
            self._service.send_message,
            trimmed,
            client_request_id=request_id,
            signals=signals,
        )

        # Retain worker reference for deterministic lifecycle management
        self._active_workers[request_id] = worker

        worker.signals.result.connect(
            lambda res, req_id=request_id: self._handle_worker_result(req_id, res)
        )
        worker.signals.error.connect(
            lambda err, req_id=request_id: self._handle_worker_error(req_id, err)
        )
        worker.signals.finished.connect(
            lambda req_id=request_id: self._handle_worker_finished(req_id)
        )

        self._thread_pool.start(worker)

    def _set_busy(self, busy: bool) -> None:
        if self._is_busy != busy:
            self._is_busy = busy
            self.busyChanged.emit(busy)

    @Slot(str, object)
    def _handle_worker_result(self, request_id: str, result: object) -> None:
        if self._is_shutting_down:
            return

        if isinstance(result, AssistantResponse):
            if result.status == MessageStatus.COMPLETED:
                self.responseReady.emit(result.content)
            elif result.status == MessageStatus.PENDING:
                self.errorOccurred.emit("A request with this ID is currently in progress.")
            elif result.status == MessageStatus.FAILED:
                self.errorOccurred.emit(
                    f"Request failed: {result.assistant_message.error_message or 'Unknown error'}"
                )
            elif result.status == MessageStatus.INTERRUPTED:
                self.errorOccurred.emit("Request was interrupted. Please retry.")
        elif isinstance(result, ChatMessage):
            self.responseReady.emit(result.content)
        else:
            self.responseReady.emit(str(result))

    @Slot(str, str)
    def _handle_worker_error(self, request_id: str, error_message: str) -> None:
        if self._is_shutting_down:
            return

        self.errorOccurred.emit(error_message)

    @Slot(str)
    def _handle_worker_finished(self, request_id: str) -> None:
        self._active_workers.pop(request_id, None)
        if not self._active_workers:
            self._set_busy(False)

    @Slot()
    def shutdown(self, timeout_ms: int = -1) -> bool:
        """Coordinate safe application shutdown.

        Stops accepting new requests, drains the dedicated thread pool, and ensures
        all workers and signals remain alive until execution has completed.

        If timeout_ms is exceeded and workers are still active, returns False without
        clearing references or destroying signals, allowing in-flight workers to
        complete safely without memory corruption.
        """
        self._is_shutting_down = True

        # Attempt to drain the dedicated thread pool
        drained = self._thread_pool.waitForDone(timeout_ms)
        if not drained:
            logger.warning(
                "OrionBridge shutdown timed out with %d active worker(s) still executing.",
                len(self._active_workers),
            )
            return False

        # Only after the thread pool confirms all work has completed, clear references
        self._active_workers.clear()
        self._set_busy(False)
        return True
