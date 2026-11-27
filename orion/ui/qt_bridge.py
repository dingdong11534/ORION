"""Qt bridge exposing a thread-safe interface between QML and AssistantService."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QThreadPool, QUrl, Signal, Slot

from orion.application.assistant_service import AssistantService
from orion.application.contracts import (
    AssistantResponse,
    AssistantServiceInterruptedError,
    CancellationToken,
    ChatMessage,
    ConversationSummary,
    MessageStatus,
    PersistedMessage,
    generate_request_id,
)
from orion.application.knowledge.contracts import KnowledgeScope, ReadingPolicy
from orion.application.knowledge.retrieval import KnowledgeService
from orion.application.productivity.action_policy import ProductivityActionCoordinator
from orion.application.productivity.contracts import (
    HabitFrequency,
    HabitRecord,
    HabitStatistics,
    TaskFilter,
    TaskPriority,
    TaskRecord,
    TaskStatus,
)
from orion.application.productivity.date_parser import parse_natural_date
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.task_service import TaskService
from orion.application.tools.contracts import ExecutionStatus
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.application.tools.workspace_browser import WorkspaceBrowserService
from orion.infrastructure.workers import Worker, WorkerSignals

if TYPE_CHECKING:
    from orion.application.tools.approvals import ApprovalBroker
    from orion.infrastructure.tools.execution_manager import ExecutionManager

logger = logging.getLogger(__name__)


def _scope_to_mode(scope_str: str) -> str:
    low = (scope_str or "none").strip().lower()
    if low == "all_indexed":
        return "ask_knowledge"
    if low == "selected_documents":
        return "selected_sources"
    if low == "web_search":
        return "web_search"
    if low == "web_and_selected":
        return "web_and_selected"
    return "chat"


def _format_relative_time(dt: Any) -> str:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    target = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    secs = max(0, int((now - target).total_seconds()))
    if secs < 60:
        return "Just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days}d ago"
    return target.strftime("%b %-d")


def _conv_to_dict(c: ConversationSummary) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "title_is_custom": bool(getattr(c, "title_is_custom", False)),
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "relative_time": _format_relative_time(c.updated_at),
        "knowledge_scope": getattr(c, "knowledge_scope", "none"),
        "selected_document_ids": list(getattr(c, "selected_document_ids", ())),
        "active_document_id": getattr(c, "active_document_id", None) or "",
        "last_knowledge_question": getattr(c, "last_knowledge_question", None) or "",
        "last_references": list(getattr(c, "last_references", ())),
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
    thinkingChunk = Signal(str, str)
    responseInterrupted = Signal(str, str)
    errorOccurred = Signal(str)
    busyChanged = Signal(bool)
    showThinkingChanged = Signal(bool)
    thinkingSupportedChanged = Signal(bool)

    conversationsLoaded = Signal(list, str)
    activeConversationChanged = Signal(str)
    historyLoaded = Signal(list)
    conversationCreated = Signal(dict)
    conversationDeleted = Signal(str)
    conversationRenamed = Signal(dict)
    focusComposerRequested = Signal()

    approvalRequested = Signal(dict)
    approvalResolved = Signal(str, str)
    approvalCancelled = Signal(str)

    # Execution lifecycle signals
    executionStarted = Signal(str, str, list, str)  # (execution_id, command, args, cwd)
    executionOutputChunk = Signal(str, str, str)  # (execution_id, stream, text)
    executionFinished = Signal(
        str, str, int, float, bool, str
    )  # (execution_id, status, exit_code, duration_ms, truncated, error_message)

    # User-initiated workspace browser signals
    workspaceDirectoryLoaded = Signal(str, list, int, bool, str)
    workspaceFileLoaded = Signal(str, str, int, bool, str)
    workspaceToolError = Signal(str, str, str)
    workspaceToolBusyChanged = Signal(bool)
    workspaceToolsAvailableChanged = Signal(bool)

    # Local Knowledge Base & RAG signals
    knowledgeSourcesLoaded = Signal(list)
    knowledgeImportCompleted = Signal(int, list)
    knowledgeSourceRemoved = Signal(str)
    knowledgeError = Signal(str)
    knowledgeBusyChanged = Signal(bool)
    knowledgeProgress = Signal(dict)
    knowledgeModeChanged = Signal(str)
    conversationKnowledgeContextChanged = Signal(str, list)
    passageContextLoaded = Signal(str, int, str)
    knowledgeSearchResults = Signal(str, list)
    epubChaptersLoaded = Signal(str, list, int, int)
    workspaceAddedToKnowledge = Signal(str, str, bool)
    responseReferences = Signal(str, list)

    # Personal productivity signals (Milestone 4.11 & 4.13)
    productivityAvailableChanged = Signal(bool)
    productivityDataLoaded = Signal(dict)
    productivityActionResult = Signal(str, dict)
    productivityError = Signal(str)
    habitStatsLoaded = Signal(dict)

    # Theme & Offline Voice signals (Milestone 4.12 Phase F & 4.13)
    themeModeChanged = Signal(str)
    voiceStateChanged = Signal()
    voiceSettingsChanged = Signal()
    voiceAssetStatusChanged = Signal()
    voiceTranscriptReady = Signal(str)
    voiceError = Signal(str, str)
    memoryDataLoaded = Signal(dict)
    homeDashboardLoaded = Signal(dict)

    # Session, Background Jobs & Orb signals (Milestone 4.14 Phase E)
    sessionStateChanged = Signal(str)
    backgroundModeChanged = Signal(bool)
    backgroundJobsLoaded = Signal(list)

    def __init__(
        self,
        assistant_service: AssistantService,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
        approval_broker: "ApprovalBroker | None" = None,
        execution_manager: "ExecutionManager | None" = None,
        tool_router: ToolRouter | None = None,
        workspace_root: Path | None = None,
        knowledge_service: KnowledgeService | None = None,
        task_service: TaskService | None = None,
        habit_service: HabitService | None = None,
        productivity_coordinator: ProductivityActionCoordinator | None = None,
        voice_service: Any = None,
        memory_service: Any = None,
        session_factory: Any = None,
        interaction_session: Any = None,
        job_store: Any = None,
        job_supervisor: Any = None,
    ) -> None:
        super().__init__(parent)
        self._service = assistant_service
        self._approval_broker = approval_broker
        if self._approval_broker is not None:
            self._approval_broker.set_request_listener(self._handle_approval_requested)

        self._execution_manager: ExecutionManager | None = execution_manager
        if self._execution_manager is not None:
            self._execution_manager.set_output_listener(self._on_execution_output_chunk)
            self._execution_manager.set_lifecycle_listener(self._on_execution_lifecycle)

        self._workspace_root: Path | None = workspace_root
        self._workspace_browser: WorkspaceBrowserService | None = None
        self._workspace_busy: bool = False
        self._active_workspace_workers: dict[str, Worker] = {}
        self._cancelled_workspace_ids: set[str] = set()
        self._current_workspace_request_id: str | None = None
        if tool_router is not None:
            self.set_workspace_tools(tool_router, workspace_root)

        self._knowledge_service: KnowledgeService | None = knowledge_service
        self._task_service: TaskService | None = task_service
        self._voice_service: Any = voice_service
        self._memory_service: Any = memory_service
        self._session_factory: Any = session_factory
        self._interaction_session: Any = interaction_session
        self._job_store: Any = job_store
        self._job_supervisor: Any = job_supervisor
        self._background_mode: bool = False
        if self._interaction_session is not None:
            self._interaction_session.add_listener(
                lambda state: self.sessionStateChanged.emit(str(state))
            )
        self._voice_state: str = "idle"
        self._theme_mode: str = "light"
        if getattr(self._service, "_settings_store", None) is not None:
            try:
                saved_theme = self._service._settings_store.get_setting("ui_theme_mode")
                if saved_theme in ("light", "dark", "system"):
                    self._theme_mode = saved_theme
                if self._voice_service is not None:
                    v_en = self._service._settings_store.get_setting("voice_enabled")
                    v_as = self._service._settings_store.get_setting("voice_auto_speak")
                    from orion.application.voice.contracts import VoiceSettings

                    cur = self._voice_service.settings
                    self._voice_service.update_settings(
                        VoiceSettings(
                            enabled=(v_en != "false") if v_en is not None else cur.enabled,
                            input_device=cur.input_device,
                            tts_voice=cur.tts_voice,
                            auto_speak_responses=(v_as == "true")
                            if v_as is not None
                            else cur.auto_speak_responses,
                            idle_unload_seconds=cur.idle_unload_seconds,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to load UI/voice settings: %s", exc)
        self._habit_service: HabitService | None = habit_service
        self._productivity_coordinator: ProductivityActionCoordinator | None = (
            productivity_coordinator
        )
        self._knowledge_busy: bool = False
        self._active_knowledge_workers: dict[str, Worker] = {}
        self._knowledge_cancel_token: CancellationToken | None = None
        self._knowledge_pool = QThreadPool(self)
        self._knowledge_pool.setMaxThreadCount(1)

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

    @Property(bool, notify=showThinkingChanged)
    def showThinking(self) -> bool:
        """Expose persistent Show thinking trace preference to QML."""
        if hasattr(self._service, "get_show_thinking"):
            return bool(self._service.get_show_thinking())
        return False

    @Property(bool, notify=thinkingSupportedChanged)
    def thinkingSupported(self) -> bool:
        """Expose whether the active model supports thinking traces."""
        if hasattr(self._service, "supports_thinking"):
            return bool(self._service.supports_thinking())
        return False

    @Property(str, constant=True)
    def providerDisplay(self) -> str:
        """Expose active runtime provider and model name to QML."""
        if hasattr(self._service, "get_provider_display"):
            return str(self._service.get_provider_display())
        return "Ollama"

    @Property(bool, constant=True)
    def isSimulatedProvider(self) -> bool:
        """Expose whether the active provider is simulated (FakeModelProvider)."""
        if hasattr(self._service, "is_simulated_provider"):
            return bool(self._service.is_simulated_provider())
        return False

    @Property(str, notify=workspaceToolsAvailableChanged)
    def workspaceRoot(self) -> str:
        """Expose the configured workspace root path to QML."""
        return str(self._workspace_root) if self._workspace_root is not None else ""

    @Property(bool, notify=workspaceToolsAvailableChanged)
    def workspaceToolsAvailable(self) -> bool:
        """Return True when user-initiated workspace tools are configured."""
        return self._workspace_browser is not None

    @Property(bool, notify=workspaceToolBusyChanged)
    def workspaceToolBusy(self) -> bool:
        """Return True while a user-initiated workspace tool request is running."""
        return self._workspace_busy

    @Property(bool, constant=True)
    def knowledgeAvailable(self) -> bool:
        """Return True when local Knowledge Base service is configured."""
        return self._knowledge_service is not None

    @Property(bool, notify=knowledgeBusyChanged)
    def knowledgeBusy(self) -> bool:
        """Return True while a knowledge import/reindex/remove operation is running."""
        return self._knowledge_busy

    @Property(str, notify=knowledgeModeChanged)
    def knowledgeMode(self) -> str:
        """Return persisted knowledge mode ('chat', 'ask_knowledge', or 'selected_sources')."""
        if hasattr(self._service, "get_knowledge_mode"):
            return str(self._service.get_knowledge_mode())
        return "chat"

    @Slot(str)
    def setKnowledgeMode(self, mode: str) -> None:
        """Persist and broadcast the active knowledge mode (and update active conversation)."""
        if self._is_shutting_down:
            return
        if hasattr(self._service, "set_knowledge_mode"):
            new_mode = self._service.set_knowledge_mode(mode)
            if hasattr(self._service, "update_conversation_knowledge_context"):
                scope_val = (
                    "all_indexed"
                    if new_mode == "ask_knowledge"
                    else (
                        "selected_documents"
                        if new_mode == "selected_sources"
                        else (
                            "web_search"
                            if new_mode == "web_search"
                            else ("web_and_selected" if new_mode == "web_and_selected" else "none")
                        )
                    )
                )
                self._service.update_conversation_knowledge_context(knowledge_scope=scope_val)
            self.knowledgeModeChanged.emit(new_mode)

    @Slot(list)
    def setConversationSelectedSources(self, selected_document_ids: list) -> None:
        """Persist selected source IDs for the active conversation."""
        if self._is_shutting_down:
            return
        clean = [str(d) for d in (selected_document_ids or []) if str(d).strip()]
        if hasattr(self._service, "update_conversation_knowledge_context"):
            self._service.update_conversation_knowledge_context(
                selected_document_ids=clean,
                active_document_id=clean[0] if len(clean) == 1 else "",
            )

    @Slot(str, int)
    def loadPassageContext(self, document_id: str, chunk_index: int) -> None:
        """Load nearby adjacent chunk text for the Source Reference Inspector."""
        if self._is_shutting_down or not document_id:
            return

        def _task(_token: CancellationToken) -> tuple[str, Any]:
            ctx = (
                self._service.get_passage_context(document_id, chunk_index)
                if hasattr(self._service, "get_passage_context")
                else ""
            )
            return ("passage_ctx", (document_id, chunk_index, ctx))

        self._run_knowledge_worker(_task)

    @Property(str, constant=True)
    def supportedKnowledgeFormats(self) -> str:
        """Return human-readable summary of supported Knowledge Base formats."""
        return (
            "Text/Code (.txt .md .rst .log .py .js .ts .go .rs .c .cpp .java .sql .sh .tex), "
            "Docs (.pdf .docx .epub .html .pptx .xlsx .xlsm .csv .tsv .rtf .odt .ods .xml .json .ipynb)"
        )

    @Slot()
    def cancelKnowledgeIngestion(self) -> None:
        """Cancel the active knowledge ingestion task, if any."""
        if self._knowledge_cancel_token is not None:
            self._knowledge_cancel_token.cancel()

    def _set_knowledge_busy(self, busy: bool) -> None:
        if self._knowledge_busy != busy:
            self._knowledge_busy = busy
            self.knowledgeBusyChanged.emit(busy)

    @staticmethod
    def _source_to_dict(s: Any) -> dict[str, Any]:
        return {
            "id": s.id,
            "display_name": s.display_name,
            "source_path": s.source_path,
            "file_type": s.file_type,
            "file_size": s.file_size,
            "chunk_count": s.chunk_count,
            "ingestion_status": s.ingestion_status.value
            if hasattr(s.ingestion_status, "value")
            else str(s.ingestion_status),
            "indexed_at": s.indexed_at.isoformat(),
            "error_message": s.error_message or "",
            "title": getattr(s, "title", None) or "",
            "author": getattr(s, "author", None) or "",
            "total_chapters": int(getattr(s, "total_chapters", 0) or 0),
            "current_reading_position": (
                int(s.current_reading_position)
                if getattr(s, "current_reading_position", None) is not None
                else -1
            ),
            "display_reading_chapter": (
                int(s.display_reading_chapter)
                if getattr(s, "display_reading_chapter", None) is not None
                else -1
            ),
            "reading_policy": (
                s.reading_policy.value
                if hasattr(getattr(s, "reading_policy", None), "value")
                else str(getattr(s, "reading_policy", "unconfigured"))
            ),
            "range_start": (
                int(s.range_start) if getattr(s, "range_start", None) is not None else -1
            ),
            "range_end": int(s.range_end) if getattr(s, "range_end", None) is not None else -1,
            "progress_updated_at": (
                s.progress_updated_at.isoformat()
                if getattr(s, "progress_updated_at", None) is not None
                else ""
            ),
        }

    @staticmethod
    def _to_local_path(url_or_path: Any) -> Path:
        if isinstance(url_or_path, QUrl):
            return Path(url_or_path.toLocalFile() or url_or_path.toString())
        raw = str(url_or_path).strip()
        if raw.startswith("file://"):
            return Path(QUrl(raw).toLocalFile())
        return Path(raw)

    def _run_knowledge_worker(
        self,
        fn: Callable[[CancellationToken], tuple[str, Any]],
    ) -> None:
        if self._is_shutting_down:
            self.knowledgeError.emit("Application is shutting down.")
            return
        if self._knowledge_service is None:
            self.knowledgeError.emit("Knowledge Base service is not configured.")
            return

        req_id = generate_request_id()
        token = CancellationToken()
        self._knowledge_cancel_token = token
        signals = WorkerSignals(parent=self)
        worker = Worker(lambda: fn(token), signals=signals)
        self._active_knowledge_workers[req_id] = worker
        self._set_knowledge_busy(True)

        def _on_res(res: object) -> None:
            if self._is_shutting_down or not isinstance(res, tuple) or len(res) != 2:
                return
            op_name, data = res
            if op_name == "list":
                self.knowledgeSourcesLoaded.emit(data)
            elif op_name == "import":
                count, errs, sources = data
                self.knowledgeImportCompleted.emit(count, errs)
                self.knowledgeSourcesLoaded.emit(sources)
            elif op_name == "remove":
                doc_id, sources = data
                self.knowledgeSourceRemoved.emit(doc_id)
                self.knowledgeSourcesLoaded.emit(sources)
            elif op_name == "search":
                q_str, hits = data
                self.knowledgeSearchResults.emit(q_str, hits)
            elif op_name == "chapters":
                doc_id, chapters, total, offset = data
                self.epubChaptersLoaded.emit(doc_id, chapters, total, offset)
            elif op_name == "passage_ctx":
                doc_id, cidx, ctx_txt = data
                self.passageContextLoaded.emit(doc_id, cidx, ctx_txt)
            elif op_name == "workspace_import":
                rel_p, doc_id, already_indexed, sources = data
                self.knowledgeSourcesLoaded.emit(sources)
                self.workspaceAddedToKnowledge.emit(rel_p, doc_id, already_indexed)

        def _on_err(err: str) -> None:
            if not self._is_shutting_down:
                self.knowledgeError.emit(err)

        def _on_fin() -> None:
            if self._knowledge_cancel_token is token:
                self._knowledge_cancel_token = None
            self._active_knowledge_workers.pop(req_id, None)
            if not self._active_knowledge_workers:
                self._set_knowledge_busy(False)

        worker.signals.result.connect(_on_res)
        worker.signals.error.connect(_on_err)
        worker.signals.finished.connect(_on_fin)
        self._knowledge_pool.start(worker)

    @Slot()
    def loadKnowledgeSources(self) -> None:
        """Load all persisted knowledge sources asynchronously."""
        svc = self._knowledge_service

        def _task(_token: CancellationToken) -> tuple[str, Any]:
            assert svc is not None
            return ("list", [self._source_to_dict(s) for s in svc.list_sources()])

        self._run_knowledge_worker(_task)

    @Slot(list)
    def importKnowledgeFiles(self, file_urls_or_paths: list) -> None:
        """Import one or more user-selected files into the local Knowledge Base."""
        svc = self._knowledge_service
        paths = [self._to_local_path(p) for p in (file_urls_or_paths or [])]

        def _task(token: CancellationToken) -> tuple[str, Any]:
            assert svc is not None
            count = 0
            errors: list[str] = []
            total = len(paths)
            for idx, p in enumerate(paths, start=1):
                if token.is_cancelled:
                    errors.append("Import cancelled by user.")
                    break
                try:
                    svc.import_file(
                        p,
                        cancel_token=token,
                        on_progress=lambda info: self.knowledgeProgress.emit(info),
                    )
                    count += 1
                    self.knowledgeProgress.emit(
                        {
                            "stage": "files",
                            "display_name": p.name,
                            "files_total": total,
                            "files_completed": idx,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{p.name}: {exc}")
            sources = [self._source_to_dict(s) for s in svc.list_sources()]
            return ("import", (count, errors, sources))

        self._run_knowledge_worker(_task)

    @Slot(str)
    def importKnowledgeFolder(self, folder_url_or_path: str) -> None:
        """Import supported files from an explicitly user-selected folder."""
        svc = self._knowledge_service
        folder = self._to_local_path(folder_url_or_path)

        def _task(token: CancellationToken) -> tuple[str, Any]:
            assert svc is not None
            from orion.infrastructure.knowledge.extractor import SUPPORTED_EXTENSIONS

            recs, errors, stats = svc.import_folder(
                folder,
                supported_extensions=SUPPORTED_EXTENSIONS,
                cancel_token=token,
                on_progress=lambda info: self.knowledgeProgress.emit(info),
            )
            self.knowledgeProgress.emit(
                {
                    "stage": "folder_done",
                    "display_name": folder.name,
                    "files_total": stats["total"],
                    "files_completed": stats["completed"],
                    "files_skipped": stats["skipped"],
                    "files_failed": stats["failed"],
                }
            )
            sources = [self._source_to_dict(s) for s in svc.list_sources()]
            return ("import", (len(recs), errors, sources))

        self._run_knowledge_worker(_task)

    @Slot(str)
    def reindexKnowledgeSource(self, document_id: str) -> None:
        """Re-index an existing knowledge source from its source_path."""
        svc = self._knowledge_service

        def _task(token: CancellationToken) -> tuple[str, Any]:
            assert svc is not None
            errors: list[str] = []
            count = 0
            try:
                svc.reindex_source(document_id, cancel_token=token)
                count = 1
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            sources = [self._source_to_dict(s) for s in svc.list_sources()]
            return ("import", (count, errors, sources))

        self._run_knowledge_worker(_task)

    @Slot(str)
    def removeKnowledgeSource(self, document_id: str) -> None:
        """Remove a document source and its chunks from the Knowledge Base."""
        svc = self._knowledge_service

        def _task(_token: CancellationToken) -> tuple[str, Any]:
            assert svc is not None
            svc.remove_source(document_id)
            sources = [self._source_to_dict(s) for s in svc.list_sources()]
            return ("remove", (document_id, sources))

        self._run_knowledge_worker(_task)

    @Slot(str, str, int)
    def searchKnowledgeDirect(self, query: str, document_id: str = "", top_k: int = 12) -> None:
        """Run direct FTS5 search without invoking any LLM and emit knowledgeSearchResults."""
        if self._is_shutting_down:
            return
        q = query.strip()
        if not q:
            self.knowledgeSearchResults.emit("", [])
            return

        def _task(_token: CancellationToken) -> tuple[str, Any]:
            if hasattr(self._service, "search_knowledge_direct"):
                hits = self._service.search_knowledge_direct(
                    q, document_id=document_id or None, top_k=max(1, min(top_k, 50))
                )
            else:
                hits = []
            return ("search", (q, hits))

        self._run_knowledge_worker(_task)

    @Slot(str, int, str, int, int)
    def setEpubReadingProgress(
        self,
        document_id: str,
        reading_position: int,
        reading_policy: str,
        range_start: int = -1,
        range_end: int = -1,
    ) -> None:
        """Update EPUB reading progress and spoiler policy, then refresh sources."""
        if self._is_shutting_down or self._knowledge_service is None:
            return
        svc = self._knowledge_service
        try:
            policy = ReadingPolicy(reading_policy.strip().lower())
        except ValueError:
            policy = ReadingPolicy.WHOLE_DOCUMENT

        def _task(_token: CancellationToken) -> tuple[str, Any]:
            svc.update_reading_progress(
                document_id=document_id,
                display_chapter=reading_position if reading_position > 0 else None,
                reading_policy=policy,
                range_start=range_start if range_start > 0 else None,
                range_end=range_end if range_end > 0 else None,
            )
            return ("list", [self._source_to_dict(s) for s in svc.list_sources()])

        self._run_knowledge_worker(_task)

    @Slot(str, str, int, int)
    def loadEpubChapters(
        self,
        document_id: str,
        query: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> None:
        """Load paginated chapter entries for an EPUB source without loading all into QML."""
        if self._is_shutting_down or self._knowledge_service is None:
            return
        svc = self._knowledge_service

        def _task(_token: CancellationToken) -> tuple[str, Any]:
            chapters, total = svc.list_chapters(
                document_id=document_id,
                query=query,
                offset=offset,
                limit=limit,
            )
            payload = [
                {
                    "reading_position": c.reading_position,
                    "display_chapter_number": (
                        c.display_chapter_number if c.display_chapter_number is not None else -1
                    ),
                    "spine_index": c.spine_index if c.spine_index is not None else -1,
                    "chapter_index": c.chapter_index if c.chapter_index is not None else -1,
                    "chapter_title": c.chapter_title,
                    "source_locator": c.source_locator or "",
                    "chunk_count": c.chunk_count,
                }
                for c in chapters
            ]
            return ("chapters", (document_id, payload, total, offset))

        self._run_knowledge_worker(_task)

    @Slot(str)
    def addWorkspaceFileToKnowledge(self, relative_path: str) -> None:
        """Explicitly import a validated workspace file into the Knowledge Base."""
        if self._is_shutting_down:
            return
        if self._knowledge_service is None:
            self.knowledgeError.emit("Knowledge Base service is not configured.")
            return
        if self._workspace_root is None:
            self.knowledgeError.emit("Workspace root is not configured.")
            return

        from orion.application.tools.sensitive_paths import (
            is_sensitive_filename,
            is_sensitive_relative_path,
        )
        from orion.infrastructure.tools.path_resolver import WorkspacePathResolver

        rel = relative_path.strip()
        try:
            if is_sensitive_relative_path(rel):
                self.knowledgeError.emit(f"Sensitive workspace file cannot be indexed: {rel}")
                return
            resolver = WorkspacePathResolver(self._workspace_root)
            resolved = resolver.resolve_path(rel)
            if is_sensitive_filename(resolved.name):
                self.knowledgeError.emit(f"Sensitive workspace file cannot be indexed: {rel}")
                return
            if not resolved.is_file():
                self.knowledgeError.emit(f"Workspace path is not a readable file: {rel}")
                return
        except Exception as exc:  # noqa: BLE001
            self.knowledgeError.emit(str(exc))
            return

        svc = self._knowledge_service

        def _task(token: CancellationToken) -> tuple[str, Any]:
            rec, needs_reindex = svc.import_file(
                resolved,
                cancel_token=token,
                on_progress=lambda info: self.knowledgeProgress.emit(info),
            )
            sources = [self._source_to_dict(s) for s in svc.list_sources()]
            return ("workspace_import", (rel, rec.id, not needs_reindex, sources))

        self._run_knowledge_worker(_task)

    def set_workspace_tools(
        self,
        tool_router: ToolRouter,
        workspace_root: Path | None = None,
    ) -> None:
        """Attach the sealed workspace ToolRouter for user-initiated inspection."""
        self._workspace_root = workspace_root
        conv_store = getattr(self._service, "_conversation_store", None)
        recorder = getattr(conv_store, "record_activity_event", None)
        self._workspace_browser = WorkspaceBrowserService(
            router=tool_router,
            event_recorder=recorder if callable(recorder) else None,
        )
        self.workspaceToolsAvailableChanged.emit(True)

    def _set_workspace_busy(self, busy: bool) -> None:
        if self._workspace_busy != busy:
            self._workspace_busy = busy
            self.workspaceToolBusyChanged.emit(busy)

    @Slot(str)
    def listWorkspaceDirectory(self, relative_path: str) -> None:
        """User-initiated directory listing routed deterministically through ToolRouter."""
        self._dispatch_workspace_tool("workspace_list_directory", relative_path)

    @Slot(str)
    def readWorkspaceFile(self, relative_path: str) -> None:
        """User-initiated file read routed deterministically through ToolRouter."""
        self._dispatch_workspace_tool("workspace_read_file", relative_path)

    @Slot()
    def cancelWorkspaceTool(self) -> None:
        """Cancel the active in-flight workspace browser request, if any."""
        if self._current_workspace_request_id is not None:
            self._cancelled_workspace_ids.add(self._current_workspace_request_id)
            self._current_workspace_request_id = None
            self._set_workspace_busy(False)

    def _dispatch_workspace_tool(self, tool_id: str, relative_path: str) -> None:
        if self._is_shutting_down:
            self.workspaceToolError.emit(tool_id, relative_path, "Application is shutting down.")
            return
        if self._workspace_browser is None:
            self.workspaceToolError.emit(
                tool_id, relative_path, "Workspace browser tools are not configured."
            )
            return

        browser = self._workspace_browser
        request_id = generate_request_id()
        self._current_workspace_request_id = request_id
        conv_id = self._service.active_conversation_id or "workspace_browser"

        def _task() -> tuple[str, str, Any]:
            if tool_id == "workspace_list_directory":
                decision = browser.list_directory(
                    relative_path=relative_path,
                    client_request_id=request_id,
                    conversation_id=conv_id,
                )
            else:
                decision = browser.read_file(
                    relative_path=relative_path,
                    client_request_id=request_id,
                    conversation_id=conv_id,
                )
            return (tool_id, relative_path, decision)

        signals = WorkerSignals(parent=self)
        worker = Worker(_task, signals=signals)
        self._active_workspace_workers[request_id] = worker
        self._set_workspace_busy(True)
        worker.signals.result.connect(
            lambda res, tid=request_id: self._handle_workspace_result(tid, res)
        )
        worker.signals.error.connect(
            lambda err, tid=request_id, t=tool_id, p=relative_path: self._handle_workspace_error(
                tid, t, p, err
            )
        )
        worker.signals.finished.connect(lambda tid=request_id: self._handle_workspace_finished(tid))
        self._thread_pool.start(worker)

    @Slot(str, object)
    def _handle_workspace_result(self, task_id: str, payload: object) -> None:
        if self._is_shutting_down or task_id in self._cancelled_workspace_ids:
            return
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        tool_id, relative_path, decision = payload

        if decision.disposition != RouterDisposition.DISPATCHED:
            msg = decision.error_message or f"Request rejected ({decision.disposition.value})."
            self.workspaceToolError.emit(tool_id, relative_path, msg)
            return

        result = decision.result
        if result is None or result.status != ExecutionStatus.COMPLETED or result.output is None:
            msg = (
                (result.error_message if result is not None else None)
                or decision.error_message
                or "Workspace tool execution failed."
            )
            self.workspaceToolError.emit(tool_id, relative_path, msg)
            return

        output = result.output if isinstance(result.output, dict) else result.output.model_dump()
        if tool_id == "workspace_list_directory":
            entries = [
                {
                    "name": e["name"],
                    "entry_type": e["entry_type"],
                    "size_bytes": e.get("size_bytes") if e.get("size_bytes") is not None else -1,
                    "is_excluded": bool(e.get("is_excluded", False)),
                }
                for e in output.get("entries", [])
            ]
            self.workspaceDirectoryLoaded.emit(
                str(output.get("relative_path", "")),
                entries,
                int(output.get("total_entries", len(entries))),
                bool(output.get("truncated", False)),
                task_id,
            )
        elif tool_id == "workspace_read_file":
            self.workspaceFileLoaded.emit(
                str(output.get("relative_path", "")),
                str(output.get("content", "")),
                int(output.get("size_bytes", 0)),
                bool(output.get("truncated", False)),
                task_id,
            )

    @Slot(str, str, str, str)
    def _handle_workspace_error(
        self, task_id: str, tool_id: str, relative_path: str, error_message: str
    ) -> None:
        if self._is_shutting_down or task_id in self._cancelled_workspace_ids:
            return
        self.workspaceToolError.emit(tool_id, relative_path, error_message)

    @Slot(str)
    def _handle_workspace_finished(self, task_id: str) -> None:
        if self._current_workspace_request_id == task_id:
            self._current_workspace_request_id = None
        self._cancelled_workspace_ids.discard(task_id)
        self._active_workspace_workers.pop(task_id, None)
        if not self._active_workspace_workers:
            self._set_workspace_busy(False)

    @Slot(bool)
    def setShowThinking(self, enabled: bool) -> None:
        """Persist the Show thinking trace preference."""
        if self._is_shutting_down:
            return
        if hasattr(self._service, "set_show_thinking"):
            new_val = bool(self._service.set_show_thinking(enabled))
            self.showThinkingChanged.emit(new_val)

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
        worker.signals.thinking.connect(self._handle_worker_thinking)
        worker.signals.references.connect(self._handle_worker_references)
        worker.signals.productivity_action.connect(self._handle_worker_productivity_action)
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
        if hasattr(self._service, "supports_thinking"):
            self.thinkingSupportedChanged.emit(bool(self._service.supports_thinking()))
        if hasattr(self._service, "get_knowledge_mode"):
            self.knowledgeModeChanged.emit(str(self._service.get_knowledge_mode()))

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
        """Create a new conversation record and make it active asynchronously.

        If the current active conversation has zero messages, refocus the composer
        instead of creating a duplicate empty record.
        """
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return

        if self._is_busy:
            self.errorOccurred.emit("An operation is already in progress. Please wait.")
            return

        active_id = self._service.active_conversation_id
        if active_id:
            try:
                msgs = self._service.get_conversation_messages(active_id)
                if len(msgs) == 0:
                    self.focusComposerRequested.emit()
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to check active conversation messages: %s", exc)

        def _task() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            conv = self._service.create_new_conversation("New Conversation")
            convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
            return ("conversation_created", _conv_to_dict(conv), convs)

        task_id = generate_request_id()
        self._start_worker(task_id, _task)

    @Slot(str, str)
    def renameConversation(self, conversation_id: str, new_title: str) -> None:
        """Rename a conversation asynchronously and lock against auto-titling."""
        if self._is_shutting_down:
            self.errorOccurred.emit("Application is shutting down. Request rejected.")
            return
        cid = conversation_id.strip()
        title = " ".join((new_title or "").split()).strip()
        if not cid or not title:
            return

        def _task() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            conv = self._service.rename_conversation(cid, title)
            convs = [_conv_to_dict(c) for c in self._service.list_conversations()]
            return ("conversation_renamed", _conv_to_dict(conv), convs)

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
        if self._approval_broker is not None:
            cancelled_ids = self._approval_broker.cancel_for_request(req_id)
            for aid in cancelled_ids:
                self.approvalCancelled.emit(aid)

    @Slot(str, str, bool)
    def submitApprovalDecision(self, approval_id: str, nonce: str, approved: bool) -> None:
        """Submit user approval or rejection decision to the approval broker."""
        if self._approval_broker is None:
            logger.warning("No approval broker configured on OrionBridge.")
            return
        resolution = self._approval_broker.resolve(
            approval_id=approval_id,
            nonce=nonce,
            approved=approved,
        )
        self.approvalResolved.emit(approval_id, resolution.status.value)

    @Slot(str)
    def cancelApproval(self, approval_id: str) -> None:
        """Cancel a pending approval request."""
        if self._approval_broker is None:
            return
        req = self._approval_broker.get_pending(approval_id)
        if req is not None:
            cancelled_ids = self._approval_broker.cancel_for_request(req.client_request_id)
            for aid in cancelled_ids:
                self.approvalCancelled.emit(aid)

    def _handle_approval_requested(self, request_data: Any) -> None:
        """Bridge listener forwarding new approval requests to QML."""
        if hasattr(request_data, "to_ui_dict"):
            self.approvalRequested.emit(request_data.to_ui_dict())
        elif isinstance(request_data, dict):
            self.approvalRequested.emit(request_data)

    # ------------------------------------------------------------------
    # Execution lifecycle and output listeners (called from worker threads)
    # ------------------------------------------------------------------

    def _on_execution_output_chunk(self, execution_id: str, stream: str, text: str) -> None:
        """Forward a live output chunk from the subprocess worker thread to QML.

        Called from the subprocess drainer thread — must use a queued signal
        invocation to hop safely onto the Qt main thread.
        """
        if self._is_shutting_down:
            return
        try:
            self.executionOutputChunk.emit(execution_id, stream, text)
        except RuntimeError:
            # QObject already deleted (e.g. during rapid shutdown)
            pass

    def _on_execution_lifecycle(
        self, execution_id: str, event: str, payload: dict[str, Any]
    ) -> None:
        """Handle execution lifecycle events from the worker thread.

        ``event`` is one of: 'started', 'completed', 'failed', 'cancelled', 'timed_out'.
        """
        if self._is_shutting_down:
            return
        try:
            if event == "started":
                self.executionStarted.emit(
                    execution_id,
                    payload.get("command", ""),
                    payload.get("args", []),
                    payload.get("cwd", ""),
                )
            else:
                self.executionFinished.emit(
                    execution_id,
                    event,
                    int(payload.get("exit_code") or -1),
                    float(payload.get("duration_ms") or 0.0),
                    bool(payload.get("truncated", False)),
                    str(payload.get("error_message") or ""),
                )
        except RuntimeError:
            pass

    @Slot(str)
    def cancelExecution(self, execution_id: str) -> None:
        """Cancel the active subprocess execution identified by execution_id."""
        if self._is_shutting_down:
            return
        if self._execution_manager is None:
            logger.warning("cancelExecution called but no ExecutionManager is configured.")
            return
        cancelled = self._execution_manager.cancel(execution_id)
        if not cancelled:
            logger.debug("cancelExecution: no active execution found for id %s", execution_id)

    def set_execution_manager(self, manager: "ExecutionManager") -> None:
        """Attach an ExecutionManager and wire output/lifecycle listeners."""
        self._execution_manager = manager
        manager.set_output_listener(self._on_execution_output_chunk)
        manager.set_lifecycle_listener(self._on_execution_lifecycle)

    @Slot(str)
    def sendMessage(self, message: str) -> None:
        """Validate input and initiate background message processing using current mode."""
        mode = self.knowledgeMode
        if mode == "ask_knowledge":
            self.sendMessageWithScope(message, "ask_knowledge", [])
        else:
            self.sendMessageWithScope(message, "chat", [])

    @Slot(str, list)
    def sendMessageWithSources(self, message: str, selected_document_ids: list) -> None:
        """Validate input and initiate background message processing with optional RAG sources."""
        doc_ids = [str(d) for d in (selected_document_ids or []) if str(d).strip()]
        mode = "selected_sources" if doc_ids else "chat"
        self.sendMessageWithScope(message, mode, doc_ids)

    @Slot(str, str, list)
    def sendMessageWithScope(self, message: str, mode: str, selected_document_ids: list) -> None:
        """Initiate background message processing with explicit KnowledgeScope."""
        self.sendMessageWithScopeAndRequestId(
            message, mode, selected_document_ids, generate_request_id()
        )

    @Slot(str, str, list, str)
    def sendMessageWithScopeAndRequestId(
        self, message: str, mode: str, selected_document_ids: list, client_request_id: str
    ) -> None:
        """Initiate background message processing with explicit KnowledgeScope and client request ID."""
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

        norm_mode = (mode or "chat").strip().lower()
        if norm_mode == "ask_knowledge":
            scope = KnowledgeScope.ALL_INDEXED
        elif norm_mode == "selected_sources":
            scope = KnowledgeScope.SELECTED_DOCUMENTS
        elif norm_mode == "web_search":
            scope = KnowledgeScope.WEB_SEARCH
        elif norm_mode == "web_and_selected":
            scope = KnowledgeScope.WEB_AND_SELECTED
        else:
            scope = KnowledgeScope.NONE

        target_cid = self._service.active_conversation_id
        request_id = (client_request_id or "").strip() or generate_request_id()
        self._current_request_id = request_id
        doc_ids = [str(d) for d in (selected_document_ids or []) if str(d).strip()]

        def _task(signals: WorkerSignals) -> tuple[str, ...]:
            accumulated: list[str] = []
            try:
                for chunk in self._service.stream_message(
                    trimmed,
                    client_request_id=request_id,
                    conversation_id=target_cid,
                    on_thinking=lambda t: signals.thinking.emit(request_id, t),
                    selected_document_ids=doc_ids,
                    on_references=lambda refs: signals.references.emit(request_id, refs),
                    knowledge_scope=scope,
                    on_productivity_action=lambda act: signals.productivity_action.emit(
                        request_id, act
                    ),
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

    @Slot(str, str)
    def _handle_worker_thinking(self, task_id: str, chunk: str) -> None:
        if self._is_shutting_down:
            return
        if task_id not in self._active_workers:
            return
        if task_id in self._cancelled_request_ids:
            return
        self.thinkingChunk.emit(chunk, task_id)

    @Slot(str, object)
    def _handle_worker_references(self, task_id: str, refs: object) -> None:
        if self._is_shutting_down:
            return
        if task_id not in self._active_workers:
            return
        if task_id in self._cancelled_request_ids:
            return
        self.responseReferences.emit(task_id, list(refs) if isinstance(refs, list) else [])

    @Slot(str, object)
    def _handle_worker_productivity_action(self, task_id: str, action: object) -> None:
        if self._is_shutting_down:
            return
        if task_id not in self._active_workers:
            return
        if task_id in self._cancelled_request_ids:
            return
        if isinstance(action, dict):
            self.productivityActionResult.emit(task_id, action)
            self._emit_productivity_snapshot()

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
                active_conv = next((c for c in convs if c["id"] == active_id), None)
                if active_conv is not None:
                    restored_mode = _scope_to_mode(active_conv.get("knowledge_scope", "none"))
                    restored_ids = list(active_conv.get("selected_document_ids", []))
                    self.conversationKnowledgeContextChanged.emit(restored_mode, restored_ids)
                    self.knowledgeModeChanged.emit(restored_mode)
                return
            if tag == "conversation_selected":
                _, conv_dict, history = result
                self.activeConversationChanged.emit(conv_dict["id"])
                self.historyLoaded.emit(history)
                restored_mode = _scope_to_mode(conv_dict.get("knowledge_scope", "none"))
                restored_ids = list(conv_dict.get("selected_document_ids", []))
                self.conversationKnowledgeContextChanged.emit(restored_mode, restored_ids)
                self.knowledgeModeChanged.emit(restored_mode)
                return
            if tag == "conversation_created":
                _, conv_dict, convs = result
                self.conversationCreated.emit(conv_dict)
                self.conversationsLoaded.emit(convs, conv_dict["id"])
                self.activeConversationChanged.emit(conv_dict["id"])
                self.historyLoaded.emit([])
                self.conversationKnowledgeContextChanged.emit("chat", [])
                self.knowledgeModeChanged.emit("chat")
                self.focusComposerRequested.emit()
                return
            if tag == "conversation_renamed":
                _, conv_dict, convs = result
                self.conversationRenamed.emit(conv_dict)
                active_id = self._service.active_conversation_id or conv_dict["id"]
                self.conversationsLoaded.emit(convs, active_id)
                return
            if tag == "voice_transcribed":
                _, text = result
                self._set_voice_state("idle")
                if text:
                    self.voiceTranscriptReady.emit(text)
                return
            if tag == "voice_spoken":
                self._set_voice_state("idle")
                return
            if tag == "voice_error":
                _, code, msg = result
                self._set_voice_state("idle")
                self.voiceError.emit(code, msg)
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
    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Coordinate safe application shutdown.

        Stops accepting new requests, signals cancellation to active streaming workers,
        drains the dedicated thread pool, and ensures all workers and signals remain alive
        until execution has completed.

        Resumable: if the first call times out, subsequent calls skip cancellation
        signaling and proceed directly to draining.

        Returns True when all workers have finished; False if the timeout elapsed
        with workers still active.
        """
        if not self._is_shutting_down:
            self._is_shutting_down = True

            # Signal cancellation for any active generation or workspace requests (once only)
            if self._current_request_id is not None:
                self._cancelled_request_ids.add(self._current_request_id)
            if self._current_workspace_request_id is not None:
                self._cancelled_workspace_ids.add(self._current_workspace_request_id)
            if self._knowledge_cancel_token is not None:
                self._knowledge_cancel_token.cancel()
            if hasattr(self._service, "cancel_all_requests"):
                self._service.cancel_all_requests()
            if self._approval_broker is not None:
                cancelled_ids = self._approval_broker.cancel_all()
                for aid in cancelled_ids:
                    self.approvalCancelled.emit(aid)

            # Shut down the execution manager (cancels active subprocess)
            if self._execution_manager is not None:
                try:
                    self._execution_manager.shutdown(wait=False)
                except Exception:  # noqa: BLE001
                    logger.debug("ExecutionManager shutdown error during bridge shutdown")

        drained = self._thread_pool.waitForDone(timeout_ms)
        drained_kb = self._knowledge_pool.waitForDone(timeout_ms)
        if not drained or not drained_kb:
            logger.warning(
                "OrionBridge shutdown timed out with %d active worker(s) still executing.",
                len(self._active_workers),
            )
            return False

        self._active_workers.clear()
        self._active_workspace_workers.clear()
        self._active_knowledge_workers.clear()
        self._set_busy(False)
        self._set_workspace_busy(False)
        self._set_knowledge_busy(False)
        return True

    # ------------------------------------------------------------------
    # Personal Productivity Slots & Properties (Milestone 4.11)
    # ------------------------------------------------------------------

    @Property(bool, notify=productivityAvailableChanged)
    def productivityAvailable(self) -> bool:
        return self._task_service is not None and self._habit_service is not None

    @staticmethod
    def _task_to_dict(t: TaskRecord) -> dict[str, Any]:
        due_disp = ""
        if t.due_at is not None:
            local_dt = t.due_at.astimezone()
            due_disp = (
                local_dt.strftime("%a, %b %-d")
                if t.due_is_date_only
                else local_dt.strftime("%a, %b %-d %-I:%M %p")
            )
        return {
            "id": t.id,
            "title": t.title,
            "description": t.description or "",
            "status": t.status.value,
            "priority": t.priority.value,
            "due_at": t.due_at.isoformat() if t.due_at else "",
            "due_display": due_disp,
            "due_is_date_only": t.due_is_date_only,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
            "completed_at": t.completed_at.isoformat() if t.completed_at else "",
        }

    @staticmethod
    def _habit_to_dict(h: HabitRecord, st: HabitStatistics) -> dict[str, Any]:
        return {
            "id": h.id,
            "name": h.name,
            "description": h.description or "",
            "frequency": h.frequency.value,
            "target_count": h.target_count,
            "completed_today": st.completed_today,
            "current_streak": st.current_streak,
            "longest_streak": st.longest_streak,
            "completion_rate": round(st.completion_rate * 100),
            "weekly_completed": st.weekly_completed,
            "weekly_target": st.weekly_target,
            "monthly_completed": st.monthly_completed,
            "monthly_target": st.monthly_target,
            "total_completions": st.total_completions,
            "recent_dates": [c.completed_date.isoformat() for c in st.recent_completions[:7]],
        }

    def _emit_productivity_snapshot(
        self,
        *,
        status_filter: str = "",
        priority_filter: str = "",
        query: str = "",
        sort_by: str = "priority_due",
    ) -> None:
        if self._task_service is None or self._habit_service is None:
            return
        st_enum = TaskStatus(status_filter) if status_filter else None
        pr_enum = TaskPriority(priority_filter) if priority_filter else None
        all_tasks = [
            self._task_to_dict(t)
            for t in self._task_service.list_tasks(
                TaskFilter(
                    status=st_enum,
                    priority=pr_enum,
                    query=query or None,
                    sort_by=sort_by or "priority_due",
                )
            )
        ]
        from orion.application.productivity.today_query import build_today_overview

        overview = build_today_overview(
            self._task_service, self._habit_service, self._task_service._clock
        )
        today_tasks = [self._task_to_dict(t) for t in overview.due_today]
        overdue_tasks = [self._task_to_dict(t) for t in overview.overdue]
        upcoming_tasks = [self._task_to_dict(t) for t in overview.upcoming]
        unscheduled_tasks = [self._task_to_dict(t) for t in overview.unscheduled]
        completed_today_tasks = [self._task_to_dict(t) for t in overview.completed_today]
        habits = [self._habit_to_dict(h, st) for h, st in overview.habits]
        self.productivityDataLoaded.emit(
            {
                "today_tasks": today_tasks,
                "overdue_tasks": overdue_tasks,
                "upcoming_tasks": upcoming_tasks,
                "unscheduled_tasks": unscheduled_tasks,
                "completed_today_tasks": completed_today_tasks,
                "all_tasks": all_tasks,
                "habits": habits,
            }
        )

    @Slot(str, str, str, str)
    def loadProductivityData(
        self,
        status_filter: str = "",
        priority_filter: str = "",
        query: str = "",
        sort_by: str = "priority_due",
    ) -> None:
        self._emit_productivity_snapshot(
            status_filter=status_filter,
            priority_filter=priority_filter,
            query=query,
            sort_by=sort_by,
        )

    @Slot(str, str, str, str)
    def createTask(
        self, title: str, description: str = "", priority: str = "medium", due_expression: str = ""
    ) -> None:
        if self._task_service is None:
            return
        try:
            due_at = None
            is_date_only = True
            if due_expression.strip():
                parsed = parse_natural_date(due_expression, self._task_service._clock)
                if parsed.ambiguous or parsed.resolved_at is None:
                    self.productivityError.emit(parsed.error or "Invalid due date.")
                    return
                due_at = parsed.resolved_at
                is_date_only = parsed.is_date_only
            self._task_service.create_task(
                title,
                description=description or None,
                priority=TaskPriority(priority.strip().lower() or "medium"),
                due_at=due_at,
                due_is_date_only=is_date_only,
            )
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str, str, str, str, str, str)
    def updateTask(
        self,
        task_id: str,
        title: str,
        description: str,
        priority: str,
        status: str,
        due_expression: str = "",
    ) -> None:
        if self._task_service is None:
            return
        try:
            kwargs: dict[str, Any] = {
                "title": title,
                "description": description,
                "priority": TaskPriority(priority.strip().lower()),
                "status": TaskStatus(status.strip().lower()),
            }
            if due_expression.strip():
                parsed = parse_natural_date(due_expression, self._task_service._clock)
                if parsed.ambiguous or parsed.resolved_at is None:
                    self.productivityError.emit(parsed.error or "Invalid due date.")
                    return
                kwargs["due_at"] = parsed.resolved_at
                kwargs["due_is_date_only"] = parsed.is_date_only
            self._task_service.update_task(task_id, **kwargs)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def completeTask(self, task_id: str) -> None:
        if self._task_service is None:
            return
        try:
            self._task_service.complete_task(task_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def reopenTask(self, task_id: str) -> None:
        if self._task_service is None:
            return
        try:
            self._task_service.reopen_task(task_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def cancelTask(self, task_id: str) -> None:
        if self._task_service is None:
            return
        try:
            self._task_service.cancel_task(task_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def archiveTask(self, task_id: str) -> None:
        if self._task_service is None:
            return
        try:
            self._task_service.archive_task(task_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str, str, str, int)
    def createHabit(
        self, name: str, description: str = "", frequency: str = "daily", target_count: int = 1
    ) -> None:
        if self._habit_service is None:
            return
        try:
            self._habit_service.create_habit(
                name,
                description=description or None,
                frequency=HabitFrequency(frequency.strip().lower() or "daily"),
                target_count=max(1, int(target_count)),
            )
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str, str, str, str, int)
    def updateHabit(
        self, habit_id: str, name: str, description: str, frequency: str, target_count: int
    ) -> None:
        if self._habit_service is None:
            return
        try:
            self._habit_service.update_habit(
                habit_id,
                name=name,
                description=description,
                frequency=HabitFrequency(frequency.strip().lower()),
                target_count=max(1, int(target_count)),
            )
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def completeHabit(self, habit_id: str) -> None:
        if self._habit_service is None:
            return
        try:
            self._habit_service.complete_habit(habit_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def undoHabitCompletion(self, habit_id: str) -> None:
        if self._habit_service is None:
            return
        try:
            self._habit_service.undo_completion(habit_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def archiveHabit(self, habit_id: str) -> None:
        if self._habit_service is None:
            return
        try:
            self._habit_service.archive_habit(habit_id)
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Slot(str)
    def undoProductivityAction(self, action_id: str) -> None:
        if self._productivity_coordinator is None:
            return
        try:
            res = self._productivity_coordinator.undo_action(action_id)
            self.productivityActionResult.emit("", res.model_dump(mode="json"))
            self._emit_productivity_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    # ------------------------------------------------------------------
    # Theme & Habit Stats & Offline Voice (Milestone 4.12 Phase F & 4.13)
    # ------------------------------------------------------------------

    @Property(str, notify=themeModeChanged)
    def themeMode(self) -> str:
        return self._theme_mode

    @Slot(str)
    def setThemeMode(self, mode: str) -> None:
        clean = (mode or "light").strip().lower()
        if clean not in ("light", "dark", "system"):
            clean = "light"
        if clean == self._theme_mode:
            return
        self._theme_mode = clean
        if getattr(self._service, "_settings_store", None) is not None:
            try:
                self._service._settings_store.set_setting("ui_theme_mode", clean)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist ui_theme_mode: %s", exc)
        self.themeModeChanged.emit(clean)

    @Slot(str, int, int)
    def loadHabitStats(self, habit_id: str, year: int, month: int) -> None:
        if self._habit_service is None:
            return
        try:
            from orion.application.productivity.habit_stats_query import HabitStatsQuery

            q = HabitStatsQuery(self._habit_service, self._habit_service._clock)
            ms = q.get_habit_month_stats(habit_id, int(year), int(month))
            year_dates = q.get_habit_year_completed_dates(habit_id, int(year)) if habit_id else ()
            daily = q.get_monthly_daily_series(int(year), int(month))
            self.habitStatsLoaded.emit(
                {
                    "habit_id": habit_id,
                    "year": int(year),
                    "month": int(month),
                    "month_stats": {
                        "days_in_month": ms.days_in_month,
                        "elapsed_days": ms.elapsed_days,
                        "completed_dates": list(ms.completed_dates),
                        "scheduled_days": ms.scheduled_days,
                        "current_streak": ms.current_streak,
                        "longest_streak": ms.longest_streak,
                        "completion_percent": ms.completion_percent,
                    }
                    if ms is not None
                    else None,
                    "year_completed_dates": list(year_dates),
                    "daily_series": [
                        {
                            "day": p.day,
                            "date_iso": p.date_iso,
                            "scheduled_count": p.scheduled_count,
                            "completed_count": p.completed_count,
                            "ratio": p.ratio,
                        }
                        for p in daily
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.productivityError.emit(str(exc))

    @Property(bool, constant=True)
    def voiceAvailable(self) -> bool:
        return self._voice_service is not None

    @Property(bool, notify=voiceSettingsChanged)
    def voiceEnabled(self) -> bool:
        return bool(self._voice_service and self._voice_service.settings.enabled)

    @Property(bool, notify=voiceSettingsChanged)
    def voiceAutoSpeak(self) -> bool:
        return bool(self._voice_service and self._voice_service.settings.auto_speak_responses)

    @Property(str, notify=voiceStateChanged)
    def voiceState(self) -> str:
        return self._voice_state

    @Property("QVariantMap", notify=voiceAssetStatusChanged)
    def voiceAssetStatus(self) -> dict[str, Any]:
        if self._voice_service is None:
            return {"stt_ready": False, "tts_ready": False, "mic_available": False, "fix_hint": ""}
        st = self._voice_service.get_asset_status()
        return {
            "stt_ready": st.stt_ready,
            "stt_model_path": str(st.stt_model_path),
            "tts_ready": st.tts_ready,
            "tts_model_path": str(st.tts_model_path),
            "mic_available": st.mic_available,
            "audio_backend": st.audio_backend,
            "fix_hint": st.fix_hint,
        }

    @Slot()
    def refreshVoiceStatus(self) -> None:
        self.voiceAssetStatusChanged.emit()

    @Slot(bool, bool)
    def setVoiceSettings(self, enabled: bool, auto_speak: bool) -> None:
        if self._voice_service is None:
            return
        from orion.application.voice.contracts import VoiceSettings

        cur = self._voice_service.settings
        self._voice_service.update_settings(
            VoiceSettings(
                enabled=bool(enabled),
                input_device=cur.input_device,
                tts_voice=cur.tts_voice,
                auto_speak_responses=bool(auto_speak),
                idle_unload_seconds=cur.idle_unload_seconds,
            )
        )
        if getattr(self._service, "_settings_store", None) is not None:
            try:
                self._service._settings_store.set_setting(
                    "voice_enabled", "true" if enabled else "false"
                )
                self._service._settings_store.set_setting(
                    "voice_auto_speak", "true" if auto_speak else "false"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist voice settings: %s", exc)
        self.voiceSettingsChanged.emit()

    def _set_voice_state(self, state: str) -> None:
        if self._voice_state != state:
            self._voice_state = state
            self.voiceStateChanged.emit()

    @Slot()
    def startVoiceRecording(self) -> None:
        if self._voice_service is None:
            self.voiceError.emit("voice_disabled", "Voice service is not configured.")
            return
        try:
            self._voice_service.start_recording()
            self._set_voice_state("recording")
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", "device_error")
            self.voiceError.emit(str(code), str(exc))
            self._set_voice_state("idle")

    @Slot()
    def stopVoiceRecording(self) -> None:
        if self._voice_service is None:
            return
        self._set_voice_state("transcribing")

        def _task() -> tuple[str, ...]:
            try:
                res = self._voice_service.stop_recording_and_transcribe()
                return ("voice_transcribed", res.text)
            except Exception as exc:  # noqa: BLE001
                code = str(getattr(exc, "code", "transcription_failed"))
                return ("voice_error", code, str(exc))

        self._start_worker(generate_request_id(), _task)

    @Slot()
    def cancelVoiceRecording(self) -> None:
        if self._voice_service is None:
            return
        self._voice_service.cancel_recording()
        self._set_voice_state("idle")

    @Slot(str)
    def speakMessage(self, text: str) -> None:
        if self._voice_service is None:
            return
        self._set_voice_state("speaking")

        def _task() -> tuple[str, ...]:
            try:
                self._voice_service.speak_text(text)
                return ("voice_spoken", "")
            except Exception as exc:  # noqa: BLE001
                code = str(getattr(exc, "code", "speech_generation_failed"))
                return ("voice_error", code, str(exc))

        self._start_worker(generate_request_id(), _task)

    @Slot()
    def stopSpeaking(self) -> None:
        if self._voice_service is None:
            return
        self._voice_service.cancel_playback()
        self._set_voice_state("idle")

    # ------------------------------------------------------------------
    # Personal Memory & Profile (Milestone 4.12 Phase D)
    # ------------------------------------------------------------------

    @Slot()
    def loadPersonalMemory(self) -> None:
        if self._memory_service is None:
            return
        prof = self._memory_service.get_profile()
        mems = self._memory_service.list_memories()
        self.memoryDataLoaded.emit(
            {
                "profile": {
                    "preferred_name": prof.preferred_name or "",
                    "timezone": prof.timezone,
                    "response_preferences": prof.response_preferences or "",
                    "work_study_preferences": prof.work_study_preferences or "",
                },
                "memories": [
                    {
                        "id": m.id,
                        "category": m.category.value,
                        "title": m.title,
                        "content": m.content,
                        "status": m.status.value,
                        "source": m.source,
                    }
                    for m in mems
                ],
            }
        )

    @Slot(str, str, str, str)
    def saveUserProfile(
        self,
        preferred_name: str,
        timezone: str,
        response_preferences: str,
        work_study_preferences: str,
    ) -> None:
        if self._memory_service is None:
            return
        self._memory_service.save_profile(
            preferred_name=preferred_name,
            timezone=timezone,
            response_preferences=response_preferences,
            work_study_preferences=work_study_preferences,
        )
        self.loadPersonalMemory()

    @Slot(str, str, str)
    def addPersonalMemory(self, category: str, title: str, content: str) -> None:
        if self._memory_service is None or not content.strip():
            return
        from orion.application.memory.contracts import MemoryCategory

        self._memory_service.add_memory(
            category=MemoryCategory(category.strip().lower() or "fact"),
            title=title.strip() or content.strip()[:50],
            content=content.strip(),
            source="settings_ui",
        )
        self.loadPersonalMemory()

    @Slot(str)
    def markMemoryStale(self, memory_id: str) -> None:
        if self._memory_service is None:
            return
        from orion.application.memory.contracts import MemoryStatus

        self._memory_service.update_memory(memory_id, status=MemoryStatus.STALE)
        self.loadPersonalMemory()

    @Slot(str)
    def deletePersonalMemory(self, memory_id: str) -> None:
        if self._memory_service is None:
            return
        self._memory_service.delete_memory(memory_id)
        self.loadPersonalMemory()

    # ------------------------------------------------------------------
    # Home Dashboard (Milestone 4.12 Phase E)
    # ------------------------------------------------------------------

    @Slot()
    def loadHomeDashboard(self) -> None:
        if (
            self._task_service is None
            or self._habit_service is None
            or self._session_factory is None
        ):
            return
        from orion.application.productivity.home_dashboard_query import build_home_dashboard

        data = build_home_dashboard(
            task_service=self._task_service,
            habit_service=self._habit_service,
            clock=self._task_service._clock,
            session_factory=self._session_factory,
            conversation_store=self._service._conversation_store,
            knowledge_service=self._knowledge_service,
            memory_service=self._memory_service,
        )
        self.homeDashboardLoaded.emit(data)

    # ------------------------------------------------------------------
    # InteractionSession & Background Jobs (Milestone 4.14 Phase E)
    # ------------------------------------------------------------------

    @Property(str, notify=sessionStateChanged)
    def sessionState(self) -> str:
        if self._interaction_session is None:
            return "locked"
        return str(self._interaction_session.state)

    @Property(str, notify=sessionStateChanged)
    def wakeWordStatus(self) -> str:
        if self._interaction_session is None:
            return "Wake-word listener not configured"
        return self._interaction_session.wake_word_status()

    @Property(str, notify=sessionStateChanged)
    def speakerVerificationStatus(self) -> str:
        if self._interaction_session is None:
            return "Voice unlock disabled"
        return self._interaction_session.speaker_verification_status()

    @Property(bool, notify=backgroundModeChanged)
    def backgroundMode(self) -> bool:
        return self._background_mode

    @Slot()
    def lockSession(self) -> None:
        if self._interaction_session is not None:
            logger.info("UI requested manual session lock")
            self._interaction_session.lock()

    @Slot()
    def unlockSessionManual(self) -> None:
        if self._interaction_session is not None:
            logger.info("UI requested manual session unlock")
            self._interaction_session.unlock_manual()

    @Slot()
    def toggleSessionLock(self) -> None:
        if self._interaction_session is None:
            logger.warning("toggleSessionLock called without InteractionSession")
            return
        from orion.application.session.interaction_session import SessionState

        cur = self._interaction_session.state
        logger.info("UI toggleSessionLock invoked (current=%s)", cur)
        if cur == SessionState.UNLOCKED:
            self._interaction_session.lock()
        else:
            self._interaction_session.unlock_manual()

    @Slot(bool)
    def setBackgroundMode(self, enabled: bool) -> None:
        val = bool(enabled)
        if self._background_mode != val:
            logger.info("UI setBackgroundMode -> %s", val)
            self._background_mode = val
            self.backgroundModeChanged.emit(val)

    @Slot()
    def loadBackgroundJobs(self) -> None:
        if self._job_store is None:
            self.backgroundJobsLoaded.emit([])
            return
        from orion.application.jobs.contracts import StepStatus

        jobs = self._job_store.list_jobs(limit=25)
        payload = []
        for j in jobs:
            steps = self._job_store.list_steps(j.id)
            completed = sum(1 for s in steps if s.status == StepStatus.SUCCEEDED)
            payload.append(
                {
                    "id": j.id,
                    "summary": j.user_request_summary,
                    "workflow_type": j.workflow_type,
                    "status": str(j.status),
                    "current_step_index": completed,
                    "total_steps": len(steps),
                    "approval_reason": j.error_summary or "",
                    "error_summary": j.error_summary or "",
                }
            )
        self.backgroundJobsLoaded.emit(payload)

    @Slot(str)
    def approveBackgroundJob(self, job_id: str) -> None:
        if self._interaction_session is not None:
            from orion.application.session.interaction_session import SessionState

            if self._interaction_session.state != SessionState.UNLOCKED:
                logger.warning(
                    "Refused background job approval for %s while session is %s",
                    job_id,
                    self._interaction_session.state,
                )
                return
        if self._job_supervisor is not None:
            self._job_supervisor.approve_job(job_id)
            self.loadBackgroundJobs()
        elif self._job_store is not None:
            self._job_store.approve_job(job_id)
            self.loadBackgroundJobs()

    @Slot(str)
    def cancelBackgroundJob(self, job_id: str) -> None:
        if self._job_supervisor is not None:
            self._job_supervisor.cancel_job(job_id)
            self.loadBackgroundJobs()
        elif self._job_store is not None:
            self._job_store.request_cancellation(job_id)
            self.loadBackgroundJobs()
