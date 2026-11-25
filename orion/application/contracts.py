import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


def generate_request_id() -> str:
    """Generate prefixed UUIDv4 for a client request submission.

    Result is 'req_' + 32 hexadecimal characters = 36 characters total.
    """
    return f"req_{uuid.uuid4().hex}"


def _require_timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("Timestamp must be timezone-aware.")
    return value


class ChatMessage(BaseModel):
    """Immutable validated chat message entity."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=utc_now)

    @field_validator("content")
    @classmethod
    def validate_content_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Message content cannot be blank or contain only whitespace.")
        return trimmed

    @field_validator("timestamp")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class ChatRequest(BaseModel):
    """Validated incoming chat request from UI or client."""

    model_config = ConfigDict(frozen=True)

    content: str
    client_request_id: str = Field(default_factory=generate_request_id)
    conversation_id: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Message content cannot be blank or contain only whitespace.")
        return trimmed

    @field_validator("client_request_id")
    @classmethod
    def validate_client_request_id(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("client_request_id cannot be blank or contain only whitespace.")
        return trimmed


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001, S110
                pass

    def register(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._event.is_set():
                try:
                    callback()
                except Exception:  # noqa: BLE001, S110
                    pass
                return
            self._callbacks.append(callback)


class AssistantServiceInterruptedError(Exception):
    """Raised when an active assistant response is cancelled by the user."""

    def __init__(self, message: str, partial_content: str | None = None) -> None:
        super().__init__(message)
        self.partial_content = partial_content


# ---------------------------------------------------------------------------
# Persistence contracts — independent of Qt and SQLAlchemy.
# ---------------------------------------------------------------------------


class MessageStatus(StrEnum):
    """Valid statuses for a persisted message."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ConversationSummary(BaseModel):
    """Immutable summary of a conversation thread."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class PersistedMessage(BaseModel):
    """Immutable representation of a persisted message row."""

    model_config = ConfigDict(frozen=True)

    id: str
    conversation_id: str
    client_request_id: str
    sequence_number: int
    role: Literal["user", "assistant"]
    status: MessageStatus
    content: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | None) -> str | None:
        return value  # None is valid for pending/failed/interrupted messages

    def is_pending(self) -> bool:
        return self.status == MessageStatus.PENDING

    def is_terminal(self) -> bool:
        return self.status in (
            MessageStatus.COMPLETED,
            MessageStatus.FAILED,
            MessageStatus.INTERRUPTED,
        )


class RequestRecord(BaseModel):
    """Immutable pair of user message and assistant message for one request."""

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    client_request_id: str
    user_message: PersistedMessage
    assistant_message: PersistedMessage


class RequestStartResult(BaseModel):
    """Result of a begin_request() call."""

    model_config = ConfigDict(frozen=True)

    created: bool
    """True if new messages were inserted; False if an existing request was returned."""
    request: RequestRecord


class AssistantResponse(BaseModel):
    """Immutable application response contract returned by AssistantService."""

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    client_request_id: str
    user_message: PersistedMessage
    assistant_message: PersistedMessage
    status: MessageStatus
    is_new: bool
    content: str
    role: Literal["assistant"] = "assistant"


class ActivityEvent(BaseModel):
    """Immutable representation of a persisted activity event."""

    model_config = ConfigDict(frozen=True)

    id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str | None
    payload: dict[str, Any]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class ThinkingDelta(BaseModel):
    """Incremental reasoning/thinking chunk emitted separately from assistant answer text."""

    model_config = ConfigDict(frozen=True)

    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


class TextDelta(BaseModel):
    """Incremental assistant final-answer text chunk."""

    model_config = ConfigDict(frozen=True)

    type: Literal["text_delta"] = "text_delta"
    text: str


class GenerationCompleted(BaseModel):
    """Terminal event emitted when model generation completes successfully."""

    model_config = ConfigDict(frozen=True)

    type: Literal["generation_completed"] = "generation_completed"
    stop_reason: str = "stop"
    final_text: str = ""


class GenerationFailed(BaseModel):
    """Terminal event emitted when model generation fails."""

    model_config = ConfigDict(frozen=True)

    type: Literal["generation_failed"] = "generation_failed"
    error: str
    retryable: bool = False


GenerationEvent = ThinkingDelta | TextDelta | GenerationCompleted | GenerationFailed
