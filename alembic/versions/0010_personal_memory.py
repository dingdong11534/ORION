"""Add user_profile, personal_memories, and local_reminders tables.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-27 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profile",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("preferred_name", sa.String(length=120), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("response_preferences", sa.Text(), nullable=True),
        sa.Column("work_study_preferences", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "personal_memories",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="fact"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="user_explicit"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_personal_memories_cat_status", "personal_memories", ["category", "status"])

    op.create_table(
        "local_reminders",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False, server_default="checkin"),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("remind_time", sa.String(length=5), nullable=False, server_default="09:00"),
        sa.Column("quiet_start", sa.String(length=5), nullable=False, server_default="22:00"),
        sa.Column("quiet_end", sa.String(length=5), nullable=False, server_default="07:00"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_triggered_date", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("local_reminders")
    op.drop_index("idx_personal_memories_cat_status", table_name="personal_memories")
    op.drop_table("personal_memories")
    op.drop_table("user_profile")
