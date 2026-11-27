"""Add conversation_productivity_context table for persistent pronoun/ordinal context.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-27 11:32:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_productivity_context",
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_task_id", sa.String(length=64), nullable=True),
        sa.Column("last_habit_id", sa.String(length=64), nullable=True),
        sa.Column("last_list_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("last_list_type", sa.String(length=16), nullable=True),
        sa.Column("last_entity_type", sa.String(length=16), nullable=True),
        sa.Column("user_turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_task_turn", sa.Integer(), nullable=True),
        sa.Column("last_habit_turn", sa.Integer(), nullable=True),
        sa.Column("last_list_turn", sa.Integer(), nullable=True),
        sa.Column("pending_confirmation_action_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_productivity_context")
