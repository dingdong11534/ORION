"""Assistant service orchestrating conversation workflows, model inference, and persistence."""

import logging
import os
import threading
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from orion.application.contracts import (
    AssistantResponse,
    AssistantServiceInterruptedError,
    CancellationToken,
    ChatMessage,
    ChatRequest,
    ConversationSummary,
    GenerationCompleted,
    MessageStatus,
    PersistedMessage,
    TextDelta,
    ThinkingDelta,
    generate_request_id,
)
from orion.application.knowledge.contracts import KnowledgeScope
from orion.application.knowledge.query_resolver import KnowledgeQueryContextResolver
from orion.application.knowledge.retrieval import (
    DocumentRetrievalService,
    build_rag_prompt,
    validate_citations,
)
from orion.application.ports import (
    ConversationNotFoundError,
    ConversationStore,
    ModelProvider,
    SettingsStore,
)
from orion.infrastructure.ollama_model_provider import OllamaCancelledError

logger = logging.getLogger(__name__)


class AssistantServiceError(Exception):
    """Raised when an error occurs during assistant service processing."""


class AssistantService:
    """Orchestrates assistant conversations, workflows, and persistent memory.

    Independent of GUI and database implementation details.
    """

    def __init__(
        self,
        model_provider: ModelProvider,
        conversation_store: ConversationStore,
        settings_store: SettingsStore | None = None,
        max_history_messages: int | None = None,
        max_history_chars: int = 2400,
        retrieval_service: DocumentRetrievalService | None = None,
        productivity_coordinator: Any = None,
        identity_service: Any = None,
    ) -> None:
        self._model_provider = model_provider
        self._conversation_store = conversation_store
        self._settings_store = settings_store
        self._retrieval_service = retrieval_service
        self._productivity_coordinator = productivity_coordinator
        self._identity_service = identity_service
        self._query_resolver = KnowledgeQueryContextResolver()
        if max_history_messages is None:
            env_limit = os.environ.get("ORION_MAX_HISTORY_MESSAGES", "12").strip()
            try:
                max_history_messages = max(0, int(env_limit))
            except ValueError:
                max_history_messages = 12
        self._max_history_messages = max_history_messages
        self._max_history_chars = max(0, max_history_chars)
        self._active_conversation_id: str | None = None
        self._active_cancellations: dict[str, CancellationToken] = {}
        self._cancellations_lock = threading.Lock()
        self._init_active_conversation()

    def _init_active_conversation(self) -> None:
        """Load stored active_conversation_id from settings and validate existence."""
        if self._settings_store is None:
            return

        stored_id = self._settings_store.get_setting("active_conversation_id")
        if stored_id and isinstance(stored_id, str):
            try:
                if hasattr(self._conversation_store, "get_conversation"):
                    conv = self._conversation_store.get_conversation(stored_id)
                    if conv is not None:
                        self._active_conversation_id = stored_id
                        return
                else:
                    convs = self._conversation_store.list_conversations(limit=500)
                    if any(c.id == stored_id for c in convs):
                        self._active_conversation_id = stored_id
                        return
                # Referenced conversation no longer exists: clear stale setting
                self._settings_store.delete_setting("active_conversation_id")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to validate active conversation on startup: %s", exc)
        self._active_conversation_id = None

    @property
    def active_conversation_id(self) -> str | None:
        """Return the current active conversation ID, if any."""
        return self._active_conversation_id

    def supports_thinking(self) -> bool:
        """Return True if the active model provider reports thinking capability."""
        if hasattr(self._model_provider, "supports_thinking"):
            try:
                return bool(self._model_provider.supports_thinking())
            except Exception:  # noqa: BLE001
                return False
        return False

    def is_simulated_provider(self) -> bool:
        """Return True when running against a simulated/fake model provider."""
        cls_name = type(self._model_provider).__name__.lower()
        return "fake" in cls_name

    def get_provider_display(self) -> str:
        """Return human-readable runtime provider and model label."""
        if self.is_simulated_provider():
            return "Simulation Mode"
        model_name = getattr(self._model_provider, "model_name", None)
        if isinstance(model_name, str) and model_name.strip():
            return f"Ollama · {model_name.strip()}"
        return type(self._model_provider).__name__

    def get_show_thinking(self) -> bool:
        """Return the persisted Show thinking preference, falling back to ORION_OLLAMA_THINK (default False)."""
        if self._settings_store is not None:
            try:
                saved = self._settings_store.get_setting("show_thinking_trace")
                if isinstance(saved, str):
                    lower = saved.strip().lower()
                    if lower in ("true", "1", "yes"):
                        return True
                    if lower in ("false", "0", "no"):
                        return False
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to read show_thinking_trace setting: %s", exc)
        env_val = os.environ.get("ORION_OLLAMA_THINK", "").strip().lower()
        return env_val in ("1", "true", "yes")

    def set_show_thinking(self, enabled: bool) -> bool:
        """Persist the Show thinking preference via SettingsStore."""
        val = bool(enabled)
        if self._settings_store is not None:
            try:
                self._settings_store.set_setting("show_thinking_trace", "true" if val else "false")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist show_thinking_trace setting: %s", exc)
        return val

    def get_knowledge_mode(self) -> str:
        """Return persisted knowledge mode ('chat', 'ask_knowledge', or 'selected_sources').

        Defaults to 'chat' so existing normal-chat behaviour is preserved.
        """
        valid = {"chat", "ask_knowledge", "selected_sources"}
        if self._settings_store is not None:
            try:
                saved = self._settings_store.get_setting("knowledge_mode")
                if isinstance(saved, str) and saved.strip().lower() in valid:
                    return saved.strip().lower()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to read knowledge_mode setting: %s", exc)
        return "chat"

    def set_knowledge_mode(self, mode: str) -> str:
        """Persist knowledge mode ('chat', 'ask_knowledge', or 'selected_sources')."""
        normalized = mode.strip().lower()
        if normalized not in {"chat", "ask_knowledge", "selected_sources"}:
            normalized = "chat"
        if self._settings_store is not None:
            try:
                self._settings_store.set_setting("knowledge_mode", normalized)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist knowledge_mode setting: %s", exc)
        return normalized

    def search_knowledge_direct(
        self,
        query: str,
        document_id: str | None = None,
        top_k: int = 12,
    ) -> list[dict[str, Any]]:
        """Run a direct FTS5 search without invoking any LLM."""
        if self._retrieval_service is None:
            return []
        passages = self._retrieval_service.search_direct(
            query=query,
            document_id=document_id,
            top_k=top_k,
        )
        return [
            {
                "chunk_id": p.chunk_id,
                "document_id": p.document_id,
                "display_name": p.display_name,
                "file_type": p.file_type,
                "source_path": p.source_path,
                "chunk_index": p.chunk_index,
                "page_number": p.page_number if p.page_number is not None else -1,
                "display_chapter_number": (
                    p.display_chapter_number if p.display_chapter_number is not None else -1
                ),
                "reading_position": p.reading_position if p.reading_position is not None else -1,
                "chapter_title": p.chapter_title or "",
                "section_title": p.section_title or "",
                "source_locator": p.source_locator or "",
                "excerpt": p.excerpt,
                "content": p.content,
            }
            for p in passages
        ]

    def get_passage_context(self, document_id: str, chunk_index: int) -> str:
        """Return bounded adjacent chunk context for Source Inspector."""
        if self._retrieval_service is None:
            return ""
        store = getattr(self._retrieval_service, "_store", None)
        if store is not None and hasattr(store, "get_adjacent_context"):
            return str(store.get_adjacent_context(document_id, chunk_index, window=1))
        return ""

    def update_conversation_knowledge_context(
        self,
        conversation_id: str | None = None,
        *,
        knowledge_scope: str | None = None,
        selected_document_ids: Sequence[str] | None = None,
        active_document_id: str | None = None,
    ) -> ConversationSummary | None:
        """Persist knowledge context for the target/active conversation."""
        cid = conversation_id or self._active_conversation_id
        if not cid or not hasattr(
            self._conversation_store, "update_conversation_knowledge_context"
        ):
            return None
        return self._conversation_store.update_conversation_knowledge_context(
            cid,
            knowledge_scope=knowledge_scope,
            selected_document_ids=selected_document_ids,
            active_document_id=active_document_id,
        )

    def get_active_conversation(self) -> ConversationSummary | None:
        """Return the ConversationSummary for the active conversation, or None."""
        if self._active_conversation_id is None:
            return None
        if hasattr(self._conversation_store, "get_conversation"):
            conv = self._conversation_store.get_conversation(self._active_conversation_id)
            if conv is not None:
                return conv
        convs = self._conversation_store.list_conversations(limit=500)
        return next((c for c in convs if c.id == self._active_conversation_id), None)

    def set_active_conversation(self, conversation_id: str | None) -> None:
        """Explicitly switch or clear the active conversation."""
        if conversation_id is not None:
            self.select_conversation(conversation_id)
            return

        self._active_conversation_id = None
        if self._settings_store is not None:
            try:
                self._settings_store.delete_setting("active_conversation_id")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to delete active_conversation_id setting: %s", exc)

    def select_conversation(self, conversation_id: str) -> ConversationSummary:
        """Select an existing conversation as the active conversation."""
        target_conv: ConversationSummary | None = None
        if hasattr(self._conversation_store, "get_conversation"):
            target_conv = self._conversation_store.get_conversation(conversation_id)
        if target_conv is None:
            convs = self._conversation_store.list_conversations(limit=500)
            target_conv = next((c for c in convs if c.id == conversation_id), None)

        if target_conv is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id!r} not found.")

        self._active_conversation_id = conversation_id
        if self._settings_store is not None:
            try:
                self._settings_store.set_setting("active_conversation_id", conversation_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist active_conversation_id setting: %s", exc)

        return target_conv

    def create_new_conversation(self, title: str = "New Conversation") -> ConversationSummary:
        """Explicitly create a new conversation and set it as the active conversation."""
        try:
            conv = self._conversation_store.create_conversation(title=title)
        except Exception as exc:
            raise AssistantServiceError(f"Failed to create new conversation: {exc}") from exc

        self._active_conversation_id = conv.id
        if self._settings_store is not None:
            try:
                self._settings_store.set_setting("active_conversation_id", conv.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist active_conversation_id setting: %s", exc)

        return conv

    def rename_conversation(self, conversation_id: str, new_title: str) -> ConversationSummary:
        """Rename a conversation and mark its title as user-customized."""
        if not hasattr(self._conversation_store, "rename_conversation"):
            raise AssistantServiceError("Conversation renaming is not supported by this store.")
        try:
            return self._conversation_store.rename_conversation(conversation_id, new_title)
        except ConversationNotFoundError:
            raise
        except Exception as exc:
            raise AssistantServiceError(
                f"Failed to rename conversation {conversation_id!r}: {exc}"
            ) from exc

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation.

        If deleting the active conversation, resets active conversation state and clears setting.
        If deleting an inactive conversation, preserves the active conversation.
        """
        was_active = self._active_conversation_id == conversation_id
        try:
            self._conversation_store.delete_conversation(conversation_id)
        except Exception as exc:
            raise AssistantServiceError(
                f"Failed to delete conversation {conversation_id!r}: {exc}"
            ) from exc

        if was_active:
            self._active_conversation_id = None
            if self._settings_store is not None:
                try:
                    self._settings_store.delete_setting("active_conversation_id")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to delete active_conversation_id setting: %s", exc)

    def list_conversations(self, limit: int = 100) -> Sequence[ConversationSummary]:
        """Return conversations ordered by recent activity (updated_at desc)."""
        try:
            return self._conversation_store.list_conversations(limit=limit)
        except Exception as exc:
            raise AssistantServiceError(f"Failed to list conversations: {exc}") from exc

    def get_conversation_messages(
        self, conversation_id: str, limit: int = 100
    ) -> Sequence[PersistedMessage]:
        """Return all persisted messages for a conversation in ascending sequence order."""
        try:
            return self._conversation_store.load_history(conversation_id, limit=limit)
        except Exception as exc:
            raise AssistantServiceError(
                f"Failed to load messages for conversation {conversation_id!r}: {exc}"
            ) from exc

    def get_active_conversation_history(self, limit: int = 100) -> Sequence[PersistedMessage]:
        """Return all persisted messages for the active conversation in ascending sequence order."""
        if self._active_conversation_id is None:
            return []
        return self.get_conversation_messages(self._active_conversation_id, limit=limit)

    def get_history(
        self, conversation_id: str | None = None, limit: int = 100
    ) -> Sequence[ChatMessage]:
        """Return the completed message history for the active or specified conversation."""
        cid = conversation_id or self._active_conversation_id
        if cid is None:
            return []
        try:
            persisted = self._conversation_store.load_history(cid, limit=limit)
        except Exception as exc:
            raise AssistantServiceError(f"Failed to load history from persistence: {exc}") from exc

        return [
            ChatMessage(
                id=m.id,
                role=m.role,
                content=m.content or "",
                timestamp=m.created_at,
            )
            for m in persisted
            if m.status == MessageStatus.COMPLETED and m.content
        ]

    def _load_bounded_model_history(
        self, conversation_id: str, exclude_request_id: str
    ) -> list[ChatMessage]:
        """Load completed conversation turns and bound to max_history_messages for inference."""
        try:
            persisted_history = self._conversation_store.load_history(conversation_id, limit=500)
        except Exception as exc:
            raise AssistantServiceError(f"Failed to load history for model: {exc}") from exc

        completed_request_ids = {
            m.client_request_id
            for m in persisted_history
            if m.role == "assistant" and m.status == MessageStatus.COMPLETED
        }

        chat_history = [
            ChatMessage(
                id=m.id,
                role=m.role,
                content=m.content or "",
                timestamp=m.created_at,
            )
            for m in persisted_history
            if m.status == MessageStatus.COMPLETED
            and m.content
            and m.client_request_id in completed_request_ids
            and m.client_request_id != exclude_request_id
        ]
        if self._max_history_messages == 0:
            return []
        bounded = chat_history[-self._max_history_messages :]
        if self._max_history_chars <= 0:
            return bounded
        # Enforce character/token budget from most recent backward while preserving turn order
        kept: list[ChatMessage] = []
        total_chars = 0
        for msg in reversed(bounded):
            msg_len = len(msg.content)
            if kept and total_chars + msg_len > self._max_history_chars:
                break
            kept.append(msg)
            total_chars += msg_len
        kept.reverse()
        return kept

    def send_message(
        self,
        content_or_request: str | ChatRequest,
        client_request_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AssistantResponse:
        """Process an incoming user message and return the assistant response.

        Validates input, records request in persistence, loads historical context,
        invokes the model provider without holding open database sessions,
        and records the terminal response state.
        """
        # A. Receive request & validate
        if isinstance(content_or_request, ChatRequest):
            req = content_or_request
        else:
            req_id = client_request_id or generate_request_id()
            req = ChatRequest(
                content=content_or_request,
                client_request_id=req_id,
                conversation_id=conversation_id,
            )

        target_cid = req.conversation_id or self._active_conversation_id

        # B. Begin persistent request
        try:
            start_result = self._conversation_store.begin_request(
                client_request_id=req.client_request_id,
                user_content=req.content,
                conversation_id=target_cid,
            )
        except Exception as exc:
            raise AssistantServiceError(f"Failed to begin persistent request: {exc}") from exc

        cid = start_result.request.conversation_id

        # Update active conversation if newly created or switched
        if self._active_conversation_id != cid:
            self._active_conversation_id = cid
            if self._settings_store is not None:
                try:
                    self._settings_store.set_setting("active_conversation_id", cid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to persist active_conversation_id setting: %s", exc)

        # Handle duplicate / existing request (created=False)
        if not start_result.created:
            asst = start_result.request.assistant_message
            if asst.status == MessageStatus.COMPLETED:
                return AssistantResponse(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    user_message=start_result.request.user_message,
                    assistant_message=asst,
                    status=MessageStatus.COMPLETED,
                    is_new=False,
                    content=asst.content or "",
                )
            if asst.status == MessageStatus.PENDING:
                return AssistantResponse(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    user_message=start_result.request.user_message,
                    assistant_message=asst,
                    status=MessageStatus.PENDING,
                    is_new=False,
                    content="",
                )
            if asst.status == MessageStatus.FAILED:
                return AssistantResponse(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    user_message=start_result.request.user_message,
                    assistant_message=asst,
                    status=MessageStatus.FAILED,
                    is_new=False,
                    content=asst.error_message or "",
                )
            if asst.status == MessageStatus.INTERRUPTED:
                return AssistantResponse(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    user_message=start_result.request.user_message,
                    assistant_message=asst,
                    status=MessageStatus.INTERRUPTED,
                    is_new=False,
                    content=asst.content
                    or asst.error_message
                    or "Request was interrupted. A retry must use a new client_request_id.",
                )

        # C. Load model history (bounded for inference; full history remains in SQLite)
        chat_history = self._load_bounded_model_history(cid, req.client_request_id)

        # D. Invoke model without holding open database transactions
        try:
            response_text = self._model_provider.generate_response(
                prompt=req.content,
                history=chat_history,
            )
        except Exception as model_exc:
            # F. Model failure: record in persistence
            err_msg = str(model_exc)
            try:
                self._conversation_store.fail_request(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    error_message=err_msg,
                )
            except Exception as persist_exc:  # noqa: BLE001
                raise AssistantServiceError(
                    f"Model provider generation failed: {model_exc}; "
                    f"additionally, recording failure in database failed: {persist_exc}"
                ) from model_exc
            raise AssistantServiceError(
                f"Model provider generation failed: {model_exc}"
            ) from model_exc

        # E. Successful response: commit completion
        try:
            completed_asst = self._conversation_store.complete_request(
                conversation_id=cid,
                client_request_id=req.client_request_id,
                response_content=response_text,
            )
        except Exception as complete_exc:
            # G. Complete request persistence failure
            raise AssistantServiceError(
                f"Failed to persist completed response: {complete_exc}"
            ) from complete_exc

        return AssistantResponse(
            conversation_id=cid,
            client_request_id=req.client_request_id,
            user_message=start_result.request.user_message,
            assistant_message=completed_asst,
            status=MessageStatus.COMPLETED,
            is_new=True,
            content=response_text,
        )

    def cancel_request(self, client_request_id: str) -> bool:
        """Cancel an active in-flight request if present.

        Returns True if a matching active request was found and cancellation requested.
        """
        with self._cancellations_lock:
            token = self._active_cancellations.get(client_request_id)
        if token is not None:
            token.cancel()
            return True
        return False

    def cancel_all_requests(self) -> int:
        """Cancel all active in-flight generation requests concurrently.

        Returns the number of active cancellation tokens signaled.
        """
        with self._cancellations_lock:
            tokens = list(self._active_cancellations.values())
        for token in tokens:
            token.cancel()
        return len(tokens)

    def recover_pending_requests(self, reason: str | None = None) -> int:
        """Recover abandoned pending assistant requests from prior process run.

        Delegates to ConversationStore.recover_pending_requests.
        """
        if hasattr(self._conversation_store, "recover_pending_requests"):
            return self._conversation_store.recover_pending_requests(
                reason or "Previous ORION process ended before generation completed."
            )
        return 0

    def stream_message(
        self,
        content_or_request: str | ChatRequest,
        client_request_id: str | None = None,
        conversation_id: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        selected_document_ids: Sequence[str] | None = None,
        on_references: Callable[[list[dict[str, Any]]], None] | None = None,
        knowledge_scope: KnowledgeScope | None = None,
        on_productivity_action: Callable[[dict[str, Any]], None] | None = None,
    ) -> Iterator[str]:
        """Process incoming user message incrementally, yielding chunks and persisting once complete."""
        # A. Receive request & validate
        if isinstance(content_or_request, ChatRequest):
            req = content_or_request
        else:
            req_id = client_request_id or generate_request_id()
            req = ChatRequest(
                content=content_or_request,
                client_request_id=req_id,
                conversation_id=conversation_id,
            )

        target_cid = req.conversation_id or self._active_conversation_id

        # B. Begin persistent request
        try:
            start_result = self._conversation_store.begin_request(
                client_request_id=req.client_request_id,
                user_content=req.content,
                conversation_id=target_cid,
            )
        except Exception as exc:
            raise AssistantServiceError(f"Failed to begin persistent request: {exc}") from exc

        cid = start_result.request.conversation_id

        # Update active conversation if newly created or switched
        if self._active_conversation_id != cid:
            self._active_conversation_id = cid
            if self._settings_store is not None:
                try:
                    self._settings_store.set_setting("active_conversation_id", cid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to persist active_conversation_id setting: %s", exc)

        # Handle duplicate / existing request (created=False)
        if not start_result.created:
            asst = start_result.request.assistant_message
            if asst.status == MessageStatus.COMPLETED:
                text = asst.content or ""
                if on_chunk is not None:
                    on_chunk(text)
                yield text
                return
            if asst.status == MessageStatus.PENDING:
                raise AssistantServiceError("A request with this ID is currently in progress.")
            if asst.status == MessageStatus.FAILED:
                raise AssistantServiceError(
                    f"Request failed: {asst.error_message or 'Unknown error'}"
                )
            if asst.status == MessageStatus.INTERRUPTED:
                raise AssistantServiceError(
                    f"Request was interrupted: {asst.error_message or asst.content or 'Cancelled'}"
                )

        # C. Load model history (bounded for inference; full history remains in SQLite)
        chat_history = self._load_bounded_model_history(cid, req.client_request_id)

        # Register cancellation token for this active request
        cancellation_token = CancellationToken()
        with self._cancellations_lock:
            self._active_cancellations[req.client_request_id] = cancellation_token

        accumulated_chunks: list[str] = []
        completion_stop_reason = "stop"
        try:
            # Snapshot thinking preference at request start so mid-request toggles never alter it
            snapshot_think = self.get_show_thinking() and self.supports_thinking()

            # C1. Trusted ORION identity and live capability reporting (never via RAG)
            if self._identity_service is not None and (
                self._identity_service.is_identity_or_capability_query(req.content)
            ):
                id_reply = self._identity_service.answer(req.content)
                accumulated_chunks.append(id_reply)
                if on_chunk is not None:
                    on_chunk(id_reply)
                yield id_reply
                self._conversation_store.complete_request(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    response_content=id_reply,
                )
                return

            # C2. Constrained natural-language productivity action check (only on user message)
            if self._productivity_coordinator is not None:
                prod_res = self._productivity_coordinator.handle_message(
                    req.content,
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                )
                if prod_res is not None:
                    if on_productivity_action is not None:
                        on_productivity_action(prod_res.model_dump(mode="json"))
                    accumulated_chunks.append(prod_res.summary)
                    if on_chunk is not None:
                        on_chunk(prod_res.summary)
                    yield prod_res.summary
                    self._conversation_store.complete_request(
                        conversation_id=cid,
                        client_request_id=req.client_request_id,
                        response_content=prod_res.summary,
                    )
                    return

            # D. Optional local RAG retrieval governed by explicit KnowledgeScope + Conversation Context
            model_prompt = req.content
            clean_doc_ids = [d for d in (selected_document_ids or ()) if d and d.strip()]
            conv_summary = (
                self._conversation_store.get_conversation(cid)
                if hasattr(self._conversation_store, "get_conversation")
                else None
            )
            if knowledge_scope is not None:
                effective_scope = knowledge_scope
            elif clean_doc_ids:
                effective_scope = KnowledgeScope.SELECTED_DOCUMENTS
            elif (
                conv_summary is not None
                and conv_summary.knowledge_scope == KnowledgeScope.ALL_INDEXED.value
            ):
                effective_scope = KnowledgeScope.ALL_INDEXED
            elif (
                conv_summary is not None
                and conv_summary.knowledge_scope == KnowledgeScope.SELECTED_DOCUMENTS.value
                and conv_summary.selected_document_ids
            ):
                effective_scope = KnowledgeScope.SELECTED_DOCUMENTS
                clean_doc_ids = list(conv_summary.selected_document_ids)
            else:
                effective_scope = KnowledgeScope.NONE

            active_retrieval_passages = ()
            if (
                effective_scope in (KnowledgeScope.ALL_INDEXED, KnowledgeScope.SELECTED_DOCUMENTS)
                and self._retrieval_service is not None
            ):
                recent_user_msgs = [
                    m.content for m in chat_history if m.role == "user" and m.content
                ]
                last_kq = conv_summary.last_knowledge_question if conv_summary else None
                resolved_q = self._query_resolver.resolve(
                    req.content,
                    recent_user_messages=recent_user_msgs,
                    last_knowledge_question=last_kq,
                )
                if resolved_q.needs_clarification:
                    clar_msg = resolved_q.clarification_message or (
                        "Could you clarify which topic or document section you are referring to?"
                    )
                    accumulated_chunks.append(clar_msg)
                    if on_chunk is not None:
                        on_chunk(clar_msg)
                    yield clar_msg
                    self._conversation_store.complete_request(
                        conversation_id=cid,
                        client_request_id=req.client_request_id,
                        response_content=clar_msg,
                    )
                    return

                retrieval = self._retrieval_service.retrieve(
                    resolved_q.resolved_query,
                    clean_doc_ids,
                    scope=effective_scope,
                )
                active_retrieval_passages = retrieval.passages
                refs_payload = [
                    {
                        "citation_id": f"S{idx}",
                        "chunk_id": p.chunk_id,
                        "document_id": p.document_id,
                        "display_name": p.display_name,
                        "file_type": p.file_type,
                        "source_path": p.source_path,
                        "chunk_index": p.chunk_index,
                        "page_number": p.page_number if p.page_number is not None else -1,
                        "reading_position": (
                            p.reading_position if p.reading_position is not None else -1
                        ),
                        "display_chapter_number": (
                            p.display_chapter_number if p.display_chapter_number is not None else -1
                        ),
                        "chapter_title": p.chapter_title or "",
                        "section_title": p.section_title or "",
                        "source_locator": p.source_locator or "",
                        "scope_summary": retrieval.scope_summary or "",
                        "excerpt": p.excerpt,
                        "content": p.content,
                        "is_selected_source": p.is_selected_source,
                    }
                    for idx, p in enumerate(retrieval.passages, start=1)
                ]
                if on_references is not None:
                    on_references(refs_payload)
                if hasattr(self._conversation_store, "update_conversation_knowledge_context"):
                    self._conversation_store.update_conversation_knowledge_context(
                        cid,
                        knowledge_scope=effective_scope.value,
                        selected_document_ids=clean_doc_ids,
                        active_document_id=clean_doc_ids[0] if len(clean_doc_ids) == 1 else "",
                        last_knowledge_question=req.content,
                        last_references=refs_payload,
                    )
                if not retrieval.has_evidence:
                    if effective_scope == KnowledgeScope.SELECTED_DOCUMENTS and not clean_doc_ids:
                        no_ev_msg = (
                            "No knowledge sources selected. Select one or more documents or "
                            "switch to Ask Knowledge mode to search all indexed documents."
                        )
                    elif effective_scope == KnowledgeScope.ALL_INDEXED:
                        no_ev_msg = (
                            "Insufficient evidence: no relevant passages were found in the "
                            "indexed knowledge base for this question."
                        )
                    else:
                        no_ev_msg = (
                            "Insufficient evidence: no relevant passages were found in the "
                            "selected knowledge sources for this question."
                        )
                    accumulated_chunks.append(no_ev_msg)
                    if on_chunk is not None:
                        on_chunk(no_ev_msg)
                    yield no_ev_msg
                    self._conversation_store.complete_request(
                        conversation_id=cid,
                        client_request_id=req.client_request_id,
                        response_content=no_ev_msg,
                    )
                    return
                model_prompt = build_rag_prompt(req.content, retrieval)
            elif hasattr(self._conversation_store, "update_conversation_knowledge_context"):
                self._conversation_store.update_conversation_knowledge_context(
                    cid,
                    knowledge_scope=KnowledgeScope.NONE.value,
                    selected_document_ids=[],
                    active_document_id="",
                )

            # Stream response chunks without holding open database transactions
            if cancellation_token.is_cancelled:
                raise OllamaCancelledError("Generation cancelled by user.")

            if hasattr(self._model_provider, "stream_events"):
                for event in self._model_provider.stream_events(
                    prompt=model_prompt,
                    history=chat_history,
                    cancellation_token=cancellation_token,
                    think=snapshot_think,
                ):
                    if cancellation_token.is_cancelled:
                        raise OllamaCancelledError("Generation cancelled by user.")
                    if isinstance(event, ThinkingDelta):
                        if snapshot_think and on_thinking is not None:
                            on_thinking(event.text)
                    elif isinstance(event, TextDelta):
                        accumulated_chunks.append(event.text)
                        if on_chunk is not None:
                            on_chunk(event.text)
                        yield event.text
                    elif isinstance(event, GenerationCompleted):
                        completion_stop_reason = event.stop_reason or "stop"
            else:
                if hasattr(self._model_provider, "stream_response"):
                    chunk_iter = self._model_provider.stream_response(
                        prompt=model_prompt,
                        history=chat_history,
                        cancellation_token=cancellation_token,
                    )
                else:
                    full_text = self._model_provider.generate_response(
                        prompt=model_prompt,
                        history=chat_history,
                    )
                    chunk_iter = iter([full_text])

                for chunk in chunk_iter:
                    if cancellation_token.is_cancelled:
                        raise OllamaCancelledError("Generation cancelled by user.")
                    accumulated_chunks.append(chunk)
                    if on_chunk is not None:
                        on_chunk(chunk)
                    yield chunk

            if cancellation_token.is_cancelled:
                raise OllamaCancelledError("Generation cancelled by user.")

            # E. Successful completion (only if stop_reason == 'stop'; truncated stays incomplete)
            full_response = "".join(accumulated_chunks)
            if active_retrieval_passages:
                full_response, _valid_cites, _invalid_cites = validate_citations(
                    full_response, active_retrieval_passages
                )
            if completion_stop_reason != "stop":
                self._conversation_store.interrupt_request(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    partial_content=full_response,
                    reason=f"Generation incomplete (stop_reason={completion_stop_reason}).",
                )
                raise AssistantServiceInterruptedError(
                    f"Request {req.client_request_id} ended incomplete ({completion_stop_reason}).",
                    partial_content=full_response,
                )
            self._conversation_store.complete_request(
                conversation_id=cid,
                client_request_id=req.client_request_id,
                response_content=full_response,
            )
        except Exception as model_exc:
            if isinstance(model_exc, AssistantServiceInterruptedError):
                raise
            if cancellation_token.is_cancelled or isinstance(model_exc, OllamaCancelledError):
                partial_text = "".join(accumulated_chunks)
                try:
                    self._conversation_store.interrupt_request(
                        conversation_id=cid,
                        client_request_id=req.client_request_id,
                        partial_content=partial_text,
                        reason="Generation cancelled by user.",
                    )
                except Exception as persist_exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to persist interrupted request %s: %s",
                        req.client_request_id,
                        persist_exc,
                    )
                raise AssistantServiceInterruptedError(
                    f"Request {req.client_request_id} was cancelled by user.",
                    partial_content=partial_text,
                ) from model_exc

            # Model failure: record failure in persistence
            err_msg = str(model_exc)
            try:
                self._conversation_store.fail_request(
                    conversation_id=cid,
                    client_request_id=req.client_request_id,
                    error_message=err_msg,
                )
            except Exception as persist_exc:  # noqa: BLE001
                raise AssistantServiceError(
                    f"Model provider generation failed: {model_exc}; "
                    f"additionally, recording failure in database failed: {persist_exc}"
                ) from model_exc
            raise AssistantServiceError(
                f"Model provider generation failed: {model_exc}"
            ) from model_exc
        finally:
            with self._cancellations_lock:
                self._active_cancellations.pop(req.client_request_id, None)
