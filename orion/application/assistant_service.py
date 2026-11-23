"""Assistant service orchestrating conversation workflows, model inference, and persistence."""

import logging
import threading
from collections.abc import Callable, Iterator, Sequence

from orion.application.contracts import (
    AssistantResponse,
    AssistantServiceInterruptedError,
    CancellationToken,
    ChatMessage,
    ChatRequest,
    ConversationSummary,
    MessageStatus,
    PersistedMessage,
    generate_request_id,
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
    ) -> None:
        self._model_provider = model_provider
        self._conversation_store = conversation_store
        self._settings_store = settings_store
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

        # C. Load model history
        # Only include completed turns where both user and assistant completed,
        # excluding the current request turn.
        try:
            persisted_history = self._conversation_store.load_history(cid, limit=100)
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
            and m.client_request_id != req.client_request_id
        ]

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

    def stream_message(
        self,
        content_or_request: str | ChatRequest,
        client_request_id: str | None = None,
        conversation_id: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
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

        # C. Load model history
        try:
            persisted_history = self._conversation_store.load_history(cid, limit=100)
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
            and m.client_request_id != req.client_request_id
        ]

        # Register cancellation token for this active request
        cancellation_token = CancellationToken()
        with self._cancellations_lock:
            self._active_cancellations[req.client_request_id] = cancellation_token

        accumulated_chunks: list[str] = []
        try:
            # D. Stream response chunks without holding open database transactions
            if cancellation_token.is_cancelled:
                raise OllamaCancelledError("Generation cancelled by user.")

            if hasattr(self._model_provider, "stream_response"):
                chunk_iter = self._model_provider.stream_response(
                    prompt=req.content,
                    history=chat_history,
                    cancellation_token=cancellation_token,
                )
            else:
                full_text = self._model_provider.generate_response(
                    prompt=req.content,
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

            # E. Successful completion: persist completed assistant response exactly once
            full_response = "".join(accumulated_chunks)
            self._conversation_store.complete_request(
                conversation_id=cid,
                client_request_id=req.client_request_id,
                response_content=full_response,
            )
        except Exception as model_exc:
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
