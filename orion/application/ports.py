from collections.abc import Iterator, Sequence
from typing import Any, Protocol

from orion.application.contracts import (
    ActivityEvent,
    CancellationToken,
    ChatMessage,
    ConversationSummary,
    PersistedMessage,
    RequestRecord,
    RequestStartResult,
)

# Default upper bound for history queries.
_DEFAULT_HISTORY_LIMIT = 100


class ModelProvider(Protocol):
    """Protocol defining the interface for generating assistant responses."""

    def generate_response(
        self,
        prompt: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Generate a text response for the given prompt and conversation history."""
        ...


class StreamingModelProvider(ModelProvider, Protocol):
    """Protocol extending ModelProvider to support incremental chunk streaming."""

    def stream_response(
        self,
        prompt: str,
        history: Sequence[ChatMessage] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> Iterator[str]:
        """Stream response chunks incrementally for the given prompt and conversation history."""
        ...


class ConversationStore(Protocol):
    """Protocol for persistent conversation and message storage.

    All methods are synchronous.  One session is opened and closed per call.
    No session object is ever stored on the implementation or exposed to callers.
    """

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, title: str = "New Conversation") -> ConversationSummary:
        """Create a new conversation with the given title.

        Raises:
            ValueError: If the title is blank.
            PersistenceError: On database failure.
        """
        ...

    def get_conversation(self, conversation_id: str) -> ConversationSummary | None:
        """Return the ConversationSummary for conversation_id, or None if absent.

        Raises:
            PersistenceError: On database failure.
        """
        ...

    def list_conversations(
        self, limit: int = _DEFAULT_HISTORY_LIMIT
    ) -> Sequence[ConversationSummary]:
        """Return conversations ordered by updated_at descending.

        Raises:
            PersistenceError: On database failure.
        """
        ...

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete a conversation and cascade-delete its messages.

        Records a conversation.deleted activity event in the same transaction.
        Clears user_settings.active_conversation_id if it points here.
        Activity events referencing this conversation are preserved.

        Raises:
            ConversationNotFoundError: If the conversation does not exist.
            PersistenceError: On database failure.
        """
        ...

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def begin_request(
        self,
        client_request_id: str,
        user_content: str,
        conversation_id: str | None = None,
    ) -> RequestStartResult:
        """Start a new request turn within a conversation.

        If conversation_id is None, a new conversation is created.
        If conversation_id is provided, the conversation must already exist.

        On first call for a client_request_id:
        - Allocates two consecutive sequence numbers atomically.
        - Persists the user message (status=completed).
        - Persists a pending assistant message.
        - Persists message.created events for both.
        - All writes happen in one transaction.
        - Returns RequestStartResult(created=True, request=...).

        On repeat call with the same client_request_id (idempotency):
        - Does not insert duplicate messages.
        - Returns RequestStartResult(created=False, request=...).
        - Preserves the existing assistant message state.
        - If the existing request is structurally incomplete, raises
          PersistenceIntegrityError instead of silently starting another.
        - If the existing request has different user_content, raises
          PersistenceIntegrityError.

        Raises:
            ValueError: If user_content is blank.
            ConversationNotFoundError: If conversation_id is supplied but absent.
            PersistenceIntegrityError: If the existing request is broken.
            PersistenceError: On database failure.
        """
        ...

    def get_request(
        self,
        conversation_id: str,
        client_request_id: str,
    ) -> RequestRecord | None:
        """Return the RequestRecord for client_request_id in conversation_id, or None if absent.

        Raises:
            PersistenceError: On database failure.
        """
        ...

    def load_history(
        self,
        conversation_id: str,
        limit: int = _DEFAULT_HISTORY_LIMIT,
    ) -> Sequence[PersistedMessage]:
        """Return the most recent messages in ascending sequence_number order.

        Raises:
            PersistenceError: On database failure.
        """
        ...

    def complete_request(
        self,
        conversation_id: str,
        client_request_id: str,
        response_content: str,
    ) -> PersistedMessage:
        """Mark the assistant message as completed with the given content.

        Transitions only from pending.
        If already completed with identical content, returns the existing record (idempotent).
        If already completed with different content, raises InvalidStateTransitionError.
        Does not overwrite failed or interrupted messages.

        Raises:
            ValueError: If response_content is blank.
            RequestNotFoundError: If the assistant message is not found.
            InvalidStateTransitionError: If the current state does not allow completion.
            PersistenceError: On database failure.
        """
        ...

    def fail_request(
        self,
        conversation_id: str,
        client_request_id: str,
        error_message: str,
        partial_content: str | None = None,
    ) -> PersistedMessage:
        """Mark the assistant message as failed.

        Updates only a pending assistant message.
        Records an assistant.response.failed activity event atomically.

        Raises:
            ValueError: If error_message is blank.
            RequestNotFoundError: If the assistant message is not found.
            InvalidStateTransitionError: If not currently pending.
            PersistenceError: On database failure.
        """
        ...

    def interrupt_request(
        self,
        conversation_id: str,
        client_request_id: str,
        partial_content: str | None = None,
        reason: str | None = None,
    ) -> PersistedMessage:
        """Mark the assistant message as interrupted.

        Transitions only from pending. Preserves generated partial content and sets
        the interruption reason.
        If already interrupted with identical content and reason, returns the existing record (idempotent).
        If already completed or failed, raises InvalidStateTransitionError.

        Raises:
            RequestNotFoundError: If the assistant message is not found.
            InvalidStateTransitionError: If the message is not in pending state.
            PersistenceError: On database failure.
        """
        ...

    def load_activity(
        self,
        limit: int = _DEFAULT_HISTORY_LIMIT,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
    ) -> Sequence[ActivityEvent]:
        """Return activity events in ascending created_at order.

        Optionally filter by aggregate_type and aggregate_id.

        Raises:
            PersistenceError: On database failure.
        """
        ...


class SettingsStore(Protocol):
    """Protocol for persistent JSON key-value application settings.

    Non-sensitive configuration only.  Do not store credentials.
    """

    def get_setting(self, key: str) -> Any:
        """Return the Python value for key, or None if absent.

        Raises:
            PersistenceError: On database failure.
        """
        ...

    def set_setting(self, key: str, value: Any) -> None:
        """Persist key=value.  Value must be JSON-serializable.

        Raises:
            ValueError: If key is blank or value is not JSON-serializable.
            PersistenceError: On database failure.
        """
        ...

    def delete_setting(self, key: str) -> None:
        """Remove the setting for key if it exists (no-op if absent).

        Raises:
            PersistenceError: On database failure.
        """
        ...


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class PersistenceError(Exception):
    """Base class for unrecoverable persistence failures."""


class PersistenceIntegrityError(PersistenceError):
    """Raised when the database contains structurally inconsistent data."""


class ConversationNotFoundError(PersistenceError):
    """Raised when a referenced conversation does not exist."""


class RequestNotFoundError(PersistenceError):
    """Raised when a referenced request (user+assistant pair) does not exist."""


class InvalidStateTransitionError(PersistenceError):
    """Raised when a message state transition is not permitted."""
