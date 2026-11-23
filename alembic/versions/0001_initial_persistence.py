"""Initial persistence schema for ORION.

Revision ID: 0001
Revises:
Create Date: 2026-11-23 16:40:00.000000

Schema summary
--------------
conversations  — dialogue threads with optimistic sequence counter
messages       — individual turns with strict role/status/content constraints
activity_events — append-only audit log; no FK to other tables
user_settings  — key-value configuration store
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. conversations table
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=37), nullable=False),
        sa.Column(
            "title",
            sa.String(),
            server_default="New Conversation",
            nullable=False,
        ),
        sa.Column("next_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_conversations_title_not_blank"),
        sa.CheckConstraint(
            "next_sequence >= 0", name="ck_conversations_next_sequence_non_negative"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_conversations_updated_at",
        "conversations",
        ["updated_at"],
        unique=False,
    )

    # 2. messages table
    #
    # Composite role/status/content/error_message constraint:
    #   User:       status=completed, non-empty content, error_message IS NULL
    #   Asst pending:  content IS NULL, error_message IS NULL
    #   Asst completed: non-empty content, error_message IS NULL
    #   Asst failed/interrupted: at least one of non-empty content OR non-empty error_message
    #
    # Rejected combinations:
    #   pending with content, pending with error, completed with error,
    #   failed/interrupted with neither content nor error, all invalid role/status pairs.
    _role_status_content_check = (
        "("
        "(role = 'user' AND status = 'completed'"
        " AND content IS NOT NULL AND length(trim(content)) > 0"
        " AND error_message IS NULL)"
        " OR "
        "(role = 'assistant' AND status = 'pending'"
        " AND content IS NULL AND error_message IS NULL)"
        " OR "
        "(role = 'assistant' AND status = 'completed'"
        " AND content IS NOT NULL AND length(trim(content)) > 0"
        " AND error_message IS NULL)"
        " OR "
        "(role = 'assistant' AND status IN ('failed', 'interrupted')"
        " AND ("
        "(content IS NOT NULL AND length(trim(content)) > 0)"
        " OR "
        "(error_message IS NOT NULL AND length(trim(error_message)) > 0)"
        "))"
        ")"
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=37), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.CheckConstraint("sequence_number >= 0", name="ck_messages_sequence_non_negative"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_allowed_roles"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'interrupted')",
            name="ck_messages_allowed_statuses",
        ),
        sa.CheckConstraint(
            "length(trim(client_request_id)) > 0",
            name="ck_messages_client_request_id_not_blank",
        ),
        sa.CheckConstraint(_role_status_content_check, name="ck_messages_role_status_content"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_messages_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "client_request_id",
            "role",
            name="uq_messages_conversation_request_role",
        ),
    )
    # NOTE: No separate index on messages is created.
    # uq_messages_conversation_sequence covers (conversation_id, sequence_number)
    # for conversation-scoped ordered retrieval.
    # uq_messages_conversation_request_role covers (conversation_id, client_request_id, role)
    # for request lookup within a conversation.

    # 3. activity_events table
    #
    # Intentionally no foreign key to conversations or messages.
    # aggregate_id stores the referenced entity ID as a plain string so that
    # historical references survive conversation or message deletion.
    op.create_table(
        "activity_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0", name="ck_activity_events_type_not_blank"
        ),
        sa.CheckConstraint(
            "length(trim(aggregate_type)) > 0",
            name="ck_activity_events_aggregate_type_not_blank",
        ),
        sa.CheckConstraint(
            "json_valid(payload_json) = 1 AND json_type(payload_json) = 'object'",
            name="ck_activity_events_payload_json_object",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_activity_events_created_at",
        "activity_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "idx_activity_events_aggregate",
        "activity_events",
        ["aggregate_type", "aggregate_id"],
        unique=False,
    )
    op.create_index(
        "idx_activity_events_event_type",
        "activity_events",
        ["event_type"],
        unique=False,
    )

    # 4. user_settings table
    op.create_table(
        "user_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.CheckConstraint("length(trim(key)) > 0", name="ck_user_settings_key_not_blank"),
        sa.CheckConstraint("json_valid(value_json) = 1", name="ck_user_settings_value_json_valid"),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_index("idx_activity_events_event_type", table_name="activity_events")
    op.drop_index("idx_activity_events_aggregate", table_name="activity_events")
    op.drop_index("idx_activity_events_created_at", table_name="activity_events")
    op.drop_table("activity_events")
    op.drop_table("messages")
    op.drop_index("idx_conversations_updated_at", table_name="conversations")
    op.drop_table("conversations")
