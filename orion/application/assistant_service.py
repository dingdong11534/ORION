"""Assistant service orchestrating conversation workflows, model inference, and persistence."""

import logging
from collections.abc import Sequence

from orion.application.contracts import (
    AssistantResponse,
    ChatMessage,
    ChatRequest,
    MessageStatus,
    generate_request_id,
)
from orion.application.ports import (
    ConversationStore,
    ModelProvider,
    SettingsStore,
)

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
        self._init_active_conversation()

    def _init_active_conversation(self) -> None:
        """Load stored active_conversation_id from settings and validate existence."""
        if self._settings_store is None:
            return

        stored_id = self._settings_store.get_setting("active_conversation_id")
        if stored_id and isinstance(stored_id, str):
            try:
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

    def set_active_conversation(self, conversation_id: str | None) -> None:
        """Explicitly switch or clear the active conversation."""
        if conversation_id is not None:
            convs = self._conversation_store.list_conversations(limit=500)
            if not any(c.id == conversation_id for c in convs):
                raise ValueError(f"Conversation {conversation_id!r} not found.")
        self._active_conversation_id = conversation_id
        if self._settings_store is not None:
            if conversation_id is None:
                self._settings_store.delete_setting("active_conversation_id")
            else:
                self._settings_store.set_setting("active_conversation_id", conversation_id)

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
                raise AssistantServiceError(
                    f"Request {req.client_request_id!r} was interrupted. A retry must use a new client_request_id."
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
