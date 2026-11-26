"""SQLAlchemy models and declarative base for ORION persistent storage.

Identifier format and lengths:
  conv_ + 32 hex chars = 37 characters  (conversation IDs)
  msg_  + 32 hex chars = 36 characters  (message IDs)
  evt_  + 32 hex chars = 36 characters  (activity event IDs)
  req_  + 32 hex chars = 36 characters  (client request IDs)
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from orion.infrastructure.persistence.types import JsonText, UtcDateTime, utc_now


def generate_conversation_id() -> str:
    """Generate prefixed UUIDv4 for a conversation entity.

    Result is 'conv_' + 32 hexadecimal characters = 37 characters total.
    """
    return f"conv_{uuid.uuid4().hex}"


def generate_message_id() -> str:
    """Generate prefixed UUIDv4 for a message entity.

    Result is 'msg_' + 32 hexadecimal characters = 36 characters total.
    """
    return f"msg_{uuid.uuid4().hex}"


def generate_event_id() -> str:
    """Generate prefixed UUIDv4 for an activity event entity.

    Result is 'evt_' + 32 hexadecimal characters = 36 characters total.
    """
    return f"evt_{uuid.uuid4().hex}"


def generate_request_id() -> str:
    """Generate prefixed UUIDv4 for a client request submission.

    Result is 'req_' + 32 hexadecimal characters = 36 characters total.
    """
    return f"req_{uuid.uuid4().hex}"


def generate_document_id() -> str:
    """Generate prefixed UUIDv4 for an indexed document source ('doc_' + 32 hex chars)."""
    return f"doc_{uuid.uuid4().hex}"


def generate_chunk_id() -> str:
    """Generate prefixed UUIDv4 for an indexed document chunk ('chk_' + 32 hex chars)."""
    return f"chk_{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""


class ConversationModel(Base):
    """Represents a conversation dialogue thread."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(37), primary_key=True, default=generate_conversation_id)
    title: Mapped[str] = mapped_column(String, nullable=False, default="New Conversation")
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    messages: Mapped[list["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_conversations_title_not_blank"),
        CheckConstraint("next_sequence >= 0", name="ck_conversations_next_sequence_non_negative"),
        Index("idx_conversations_updated_at", "updated_at"),
    )


class MessageModel(Base):
    """Represents an individual message turn in a conversation.

    Role/status/content rules:
    - User messages: always status='completed', non-empty content, NULL error_message.
    - Assistant pending: content NULL, error_message NULL.
    - Assistant completed: non-empty content, NULL error_message.
    - Assistant failed/interrupted: at least one of non-empty content OR non-empty
      error_message must be present.
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_message_id)
    conversation_id: Mapped[str] = mapped_column(
        String(37),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationship
    conversation: Mapped["ConversationModel"] = relationship(
        "ConversationModel", back_populates="messages"
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_messages_conversation_sequence"
        ),
        UniqueConstraint(
            "client_request_id",
            "role",
            name="uq_messages_client_request_role",
        ),
        CheckConstraint("sequence_number >= 0", name="ck_messages_sequence_non_negative"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_allowed_roles"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'interrupted')",
            name="ck_messages_allowed_statuses",
        ),
        CheckConstraint(
            "length(trim(client_request_id)) > 0",
            name="ck_messages_client_request_id_not_blank",
        ),
        # Composite role/status/content/error_message validity constraint.
        # Rejects: pending with content, pending with error_message,
        #          completed with error_message, failed/interrupted with neither,
        #          all invalid role/status combinations.
        CheckConstraint(
            "("
            # User messages: completed, non-empty content, no error
            "(role = 'user' AND status = 'completed'"
            " AND content IS NOT NULL AND length(trim(content)) > 0"
            " AND error_message IS NULL)"
            " OR "
            # Assistant pending: no content, no error
            "(role = 'assistant' AND status = 'pending'"
            " AND content IS NULL AND error_message IS NULL)"
            " OR "
            # Assistant completed: non-empty content, no error
            "(role = 'assistant' AND status = 'completed'"
            " AND content IS NOT NULL AND length(trim(content)) > 0"
            " AND error_message IS NULL)"
            " OR "
            # Assistant failed/interrupted: at least one of content or error_message
            "(role = 'assistant' AND status IN ('failed', 'interrupted')"
            " AND ("
            "(content IS NOT NULL AND length(trim(content)) > 0)"
            " OR "
            "(error_message IS NOT NULL AND length(trim(error_message)) > 0)"
            "))"
            ")",
            name="ck_messages_role_status_content",
        ),
    )


class ActivityEventModel(Base):
    """Append-only audit and activity event record.

    Events are never deleted and survive conversation or message deletion.
    Foreign keys to other aggregates are intentionally omitted so that
    historical aggregate references are preserved after deletion.
    aggregate_id stores the referenced entity's ID as a plain string
    with no referential constraint.
    """

    __tablename__ = "activity_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_event_id)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String, nullable=False)
    aggregate_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[Any] = mapped_column(JsonText, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("length(trim(event_type)) > 0", name="ck_activity_events_type_not_blank"),
        CheckConstraint(
            "length(trim(aggregate_type)) > 0",
            name="ck_activity_events_aggregate_type_not_blank",
        ),
        CheckConstraint(
            "json_valid(payload_json) = 1 AND json_type(payload_json) = 'object'",
            name="ck_activity_events_payload_json_object",
        ),
        Index("idx_activity_events_created_at", "created_at"),
        Index("idx_activity_events_aggregate", "aggregate_type", "aggregate_id"),
        Index("idx_activity_events_event_type", "event_type"),
    )


class UserSettingModel(Base):
    """Key-value application settings and state store (non-sensitive only)."""

    __tablename__ = "user_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[Any] = mapped_column(JsonText, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("length(trim(key)) > 0", name="ck_user_settings_key_not_blank"),
        CheckConstraint("json_valid(value_json) = 1", name="ck_user_settings_value_json_valid"),
    )


class DocumentSourceModel(Base):
    """Persisted metadata for a user-selected local document in the Knowledge Base."""

    __tablename__ = "document_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_document_id)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    source_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    ingestion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(16), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunks: Mapped[list["DocumentChunkModel"]] = relationship(
        "DocumentChunkModel",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("idx_document_sources_indexed_at", "indexed_at"),)


class DocumentChunkModel(Base):
    """Persisted bounded text chunk belonging to a DocumentSourceModel."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_chunk_id)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    document: Mapped["DocumentSourceModel"] = relationship(
        "DocumentSourceModel", back_populates="chunks"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_doc_idx"),
        Index("idx_document_chunks_document_id", "document_id"),
    )
