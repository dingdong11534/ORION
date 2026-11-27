"""Add personal productivity tables: tasks, habits, habit_completions, productivity_action_records.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26 22:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="todo"),
        sa.Column("priority", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_is_date_only", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_tasks_title_not_blank"),
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'cancelled')",
            name="ck_tasks_status_valid",
        ),
        sa.CheckConstraint(
            "priority IN ('critical', 'high', 'medium', 'low')",
            name="ck_tasks_priority_valid",
        ),
    )
    op.create_index("idx_tasks_status_archived", "tasks", ["status", "archived_at"])
    op.create_index("idx_tasks_due_at", "tasks", ["due_at"])
    op.create_index("idx_tasks_priority", "tasks", ["priority"])

    op.create_table(
        "habits",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("frequency", sa.String(length=32), nullable=False, server_default="daily"),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_habits_name_not_blank"),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_habits_frequency_valid",
        ),
        sa.CheckConstraint("target_count >= 1", name="ck_habits_target_count_pos"),
    )
    op.create_index("idx_habits_archived", "habits", ["archived_at"])
    op.create_index("idx_habits_name", "habits", ["name"])

    op.create_table(
        "habit_completions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "habit_id",
            sa.String(length=64),
            sa.ForeignKey("habits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_date", sa.String(length=10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("habit_id", "completed_date", name="uq_habit_completions_habit_date"),
    )
    op.create_index(
        "idx_habit_completions_habit_date", "habit_completions", ["habit_id", "completed_date"]
    )

    op.create_table(
        "productivity_action_records",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("client_request_id", sa.String(length=128), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("undo_payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "client_request_id", "intent", name="uq_productivity_action_req_intent"
        ),
    )
    op.create_index(
        "idx_productivity_actions_conv", "productivity_action_records", ["conversation_id"]
    )
    op.create_index(
        "idx_productivity_actions_created", "productivity_action_records", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_productivity_actions_created", table_name="productivity_action_records")
    op.drop_index("idx_productivity_actions_conv", table_name="productivity_action_records")
    op.drop_table("productivity_action_records")
    op.drop_index("idx_habit_completions_habit_date", table_name="habit_completions")
    op.drop_table("habit_completions")
    op.drop_index("idx_habits_name", table_name="habits")
    op.drop_index("idx_habits_archived", table_name="habits")
    op.drop_table("habits")
    op.drop_index("idx_tasks_priority", table_name="tasks")
    op.drop_index("idx_tasks_due_at", table_name="tasks")
    op.drop_index("idx_tasks_status_archived", table_name="tasks")
    op.drop_table("tasks")
