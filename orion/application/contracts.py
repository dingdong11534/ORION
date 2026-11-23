import uuid
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

    content: str

    @field_validator("content")
    @classmethod
    def validate_content_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Message content cannot be blank or contain only whitespace.")
        return trimmed


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
