"""Qt bridge exposing a thread-safe interface between QML and AssistantService."""

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Property, QObject, QThreadPool, Signal, Slot

from orion.application.assistant_service import AssistantService
from orion.application.contracts import (
    AssistantResponse,
    AssistantServiceInterruptedError,
    ChatMessage,
    ConversationSummary,
    MessageStatus,
    PersistedMessage,
    generate_request_id,
)
from orion.infrastructure.workers import Worker, WorkerSignals

logger = logging.getLogger(__name__)


def _conv_to_dict(c: ConversationSummary) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _msg_to_dict(m: PersistedMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "client_request_id": m.client_request_id,
        "sequence_number": m.sequence_number,
        "role": m.role,
        "status": m.status.value,
        "content": m.content or "",
        "error_message": m.error_message or "",
        "created_at": m.created_at.isoformat(),
    }


class OrionBridge(QObject):
    """QObject bridge exposing a narrow, thread-safe interface to the QML view.

    Coordinates worker ownership, dedicated thread pool execution, and clean
    shutdown handling to prevent accessing deleted QObjects.
    """

    responseReady = Signal(str)
    responseChunk = Signal(str, str)
    responseInterrupted = Signal(str, str)
    errorOccurred = Signal(str)
    busyChanged = Signal(bool)

    conversationsLoaded = Signal(list, str)
    activeConversationChanged = Signal(str)
    historyLoaded = Signal(list)
    conversationCreated = Signal(dict)
    conversationDeleted = Signal(str)

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
        self._current_request_id: str | None = None
        self._cancelled_request_ids: set[str] = set()

    @Property(bool, notify=busyChanged)
    def isBusy(self) -> bool:
        """Expose current processing state to QML property bindings."""
        return self._is_busy

    @Property(str, notify=activeConversationChanged)
    def activeConversationId(self) -> str:
        """Expose current active conversation ID to QML property bindings."""
        return self._service.active_conversation_id or ""

    def _set_busy(self, busy: bool) -> None:
        if self._is_busy != busy:
            self._is_busy = busy
            self.busyChanged.emit(busy)

    def _start_worker(
        self,
        task_id: str,
        fn: Callable[..., Any],
        *args: Any,
        pass_signals: bool = False,
        **kwargs: Any,
    ) -> bool:
        """Create and enqueue a background worker with deterministic tracking."""
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return False

        signals = WorkerSignals(parent=self)
        worker_args = (signals, *args) if pass_signals else args
        worker = Worker(fn, *worker_args, signals=signals, **kwargs)

        self._active_workers[task_id] = worker
        self._set_busy(True)

        worker.signals.chunk.connect(self._handle_worker_chunk)
        worker.signals.result.connect(lambda res, tid=task_id: self._handle_worker_result(tid, res))
        worker.signals.error.connect(lambda err, tid=task_id: self._handle_worker_error(tid, err))
        worker.signals.finished.connect(lambda tid=task_id: self._handle_worker_finished(tid))

        self._thread_pool.start(worker)
        return True

    @Slot()
    def loadInitialState(self) -> None:
        """Asynchronously load conversations and active history on startup."""
        if self._is_shutting_down:
            return

        def _task() -> tuple[str, list[dict[str, Any]], str, list[dict[str, Any]]]:
            convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
            active_id = self._service.active_conversation_id or ""
            history: list[dict[str, Any]] = []
            if active_id:
                history = [_msg_to_dict(m) for m in self._service.get_active_conversation_history()]
            return ("initial_state", convs, active_id, history)

        task_id = generate_request_id()
        self._start_worker(task_id, _task)

    @Slot(str)
    def selectConversation(self, conversation_id: str) -> None:
        """Switch to an existing conversation and load its history asynchronously."""
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return

        if self._is_busy:
            self.errorOccurred.emit("An operation is already in progress. Please wait.")
            return

        cid = conversation_id.strip()
        if not cid:
            return

        def _task() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            conv = self._service.select_conversation(cid)
            history = [_msg_to_dict(m) for m in self._service.get_conversation_messages(cid)]
            return ("conversation_selected", _conv_to_dict(conv), history)

        task_id = generate_request_id()
        self._start_worker(task_id, _task)

    @Slot()
    def createNewConversation(self) -> None:
        """Create a new conversation record and make it active asynchronously."""
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return

        if self._is_busy:
            self.errorOccurred.emit("An operation is already in progress. Please wait.")
            return

        def _task() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            conv = self._service.create_new_conversation("New Conversation")
            convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
            return ("conversation_created", _conv_to_dict(conv), convs)

        task_id = generate_request_id()
        self._start_worker(task_id, _task)

    @Slot(str)
    def deleteConversation(self, conversation_id: str) -> None:
        """Delete a conversation asynchronously with confirmation."""
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return

        if self._is_busy:
            self.errorOccurred.emit("An operation is already in progress. Please wait.")
            return

        cid = conversation_id.strip()
        if not cid:
            return

        def _task() -> tuple[str, str, bool, list[dict[str, Any]], str]:
            was_active = self._service.active_conversation_id == cid
            self._service.delete_conversation(cid)
            convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
            active_id = self._service.active_conversation_id or ""
            return ("conversation_deleted", cid, was_active, convs, active_id)

        task_id = generate_request_id()
        self._start_worker(task_id, _task)

    @Slot(str)
    def send(self, message: str) -> None:
        """Compatibility alias for sendMessage."""
        self.sendMessage(message)

    @Slot()
    def stopGeneration(self) -> None:
        """Request cooperative cancellation of the active response generation."""
        if self._is_shutting_down:
            return
        if not self._current_request_id:
            return
        req_id = self._current_request_id
        self._cancelled_request_ids.add(req_id)
        self._service.cancel_request(req_id)

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

        target_cid = self._service.active_conversation_id
        request_id = generate_request_id()
        self._current_request_id = request_id

        def _task(signals: WorkerSignals) -> tuple[str, ...]:
            accumulated: list[str] = []
            try:
                for chunk in self._service.stream_message(
                    trimmed,
                    client_request_id=request_id,
                    conversation_id=target_cid,
                ):
                    accumulated.append(chunk)
                    signals.chunk.emit(request_id, chunk)
            except AssistantServiceInterruptedError as exc:
                partial_text = exc.partial_content or "".join(accumulated)
                convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
                active_id = self._service.active_conversation_id or ""
                return ("send_message_interrupted", partial_text, convs, active_id)

            full_text = "".join(accumulated)
            convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
            active_id = self._service.active_conversation_id or ""
            return ("send_message_stream_result", full_text, convs, active_id)

        self._start_worker(request_id, _task, pass_signals=True)

    @Slot(str, str)
    def _handle_worker_chunk(self, task_id: str, chunk: str) -> None:
        if self._is_shutting_down:
            return
        if task_id not in self._active_workers:
            return
        if task_id in self._cancelled_request_ids:
            return
        self.responseChunk.emit(chunk, task_id)

    @Slot(str, object)
    def _handle_worker_result(self, task_id: str, result: object) -> None:
        if self._is_shutting_down:
            return

        if isinstance(result, tuple) and result:
            tag = result[0]
            if tag == "initial_state":
                _, convs, active_id, history = result
                self.conversationsLoaded.emit(convs, active_id)
                self.activeConversationChanged.emit(active_id)
                self.historyLoaded.emit(history)
                return
            if tag == "conversation_selected":
                _, conv_dict, history = result
                self.activeConversationChanged.emit(conv_dict["id"])
                self.historyLoaded.emit(history)
                return
            if tag == "conversation_created":
                _, conv_dict, convs = result
                self.conversationCreated.emit(conv_dict)
                self.conversationsLoaded.emit(convs, conv_dict["id"])
                self.activeConversationChanged.emit(conv_dict["id"])
                self.historyLoaded.emit([])
                return
            if tag == "conversation_deleted":
                _, deleted_id, was_active, convs, active_id = result
                self.conversationDeleted.emit(deleted_id)
                self.conversationsLoaded.emit(convs, active_id)
                if was_active:
                    self.activeConversationChanged.emit("")
                    self.historyLoaded.emit([])
                return
            if tag == "send_message_interrupted":
                _, partial_text, convs, active_id = result
                self.responseInterrupted.emit(partial_text, task_id)
                self.conversationsLoaded.emit(convs, active_id)
                self.activeConversationChanged.emit(active_id)
                return
            if tag == "send_message_stream_result":
                _, full_text, convs, active_id = result
                self.responseReady.emit(full_text)
                self.conversationsLoaded.emit(convs, active_id)
                self.activeConversationChanged.emit(active_id)
                return
            if tag == "send_message_result":
                _, asst_res, convs, active_id = result
                self._dispatch_assistant_response(asst_res)
                self.conversationsLoaded.emit(convs, active_id)
                self.activeConversationChanged.emit(active_id)
                return

        self._dispatch_assistant_response(result)

    def _dispatch_assistant_response(self, result: object) -> None:
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
                self.errorOccurred.emit(
                    f"Request was interrupted: {result.assistant_message.error_message or result.assistant_message.content or 'Cancelled'}"
                )

            cid = result.conversation_id
            if self._service.active_conversation_id != cid:
                self._service.select_conversation(cid)
            self.activeConversationChanged.emit(cid)
        elif isinstance(result, ChatMessage):
            self.responseReady.emit(result.content)
        else:
            self.responseReady.emit(str(result))

    @Slot(str, str)
    def _handle_worker_error(self, task_id: str, error_message: str) -> None:
        if self._is_shutting_down:
            return
        if task_id in self._cancelled_request_ids:
            return
        self.errorOccurred.emit(error_message)

    @Slot(str)
    def _handle_worker_finished(self, task_id: str) -> None:
        if self._current_request_id == task_id:
            self._current_request_id = None
        self._cancelled_request_ids.discard(task_id)
        self._active_workers.pop(task_id, None)
        if not self._active_workers:
            self._set_busy(False)

    @Slot()
    def shutdown(self, timeout_ms: int = -1) -> bool:
        """Coordinate safe application shutdown.

        Stops accepting new requests, drains the dedicated thread pool, and ensures
        all workers and signals remain alive until execution has completed.
        """
        self._is_shutting_down = True

        drained = self._thread_pool.waitForDone(timeout_ms)
        if not drained:
            logger.warning(
                "OrionBridge shutdown timed out with %d active worker(s) still executing.",
                len(self._active_workers),
            )
            return False

        self._active_workers.clear()
        self._set_busy(False)
        return True
