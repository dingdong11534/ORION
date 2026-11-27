"""Add automation_jobs, automation_job_steps, approved_applications, automation_audit_events.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-27 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("user_request_summary", sa.Text(), nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("browser_lease_id", sa.String(length=64), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("idx_automation_jobs_status", "automation_jobs", ["status"])

    op.create_table(
        "automation_job_steps",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("automation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=120), nullable=False),
        sa.Column("idempotent", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "step_index", name="uq_automation_job_step_idx"),
    )

    op.create_table(
        "approved_applications",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("executable_path", sa.String(length=500), nullable=False),
        sa.Column("allowed_args_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("approved_by_user", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "automation_audit_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("capability_name", sa.String(length=120), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("request_origin", sa.String(length=64), nullable=False),
        sa.Column("approval_state", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("idx_automation_audit_job", "automation_audit_events", ["job_id"])


def downgrade() -> None:
    op.drop_index("idx_automation_audit_job", table_name="automation_audit_events")
    op.drop_table("automation_audit_events")
    op.drop_table("approved_applications")
    op.drop_table("automation_job_steps")
    op.drop_index("idx_automation_jobs_status", table_name="automation_jobs")
    op.drop_table("automation_jobs")
