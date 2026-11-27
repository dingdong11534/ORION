"""Add per-conversation knowledge scope, selected sources, and last references.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26 20:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "knowledge_scope", sa.String(length=32), nullable=False, server_default="none"
            )
        )
        batch_op.add_column(
            sa.Column(
                "selected_document_ids_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
        batch_op.add_column(sa.Column("active_document_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("last_knowledge_question", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("last_references_json", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("last_references_json")
        batch_op.drop_column("last_knowledge_question")
        batch_op.drop_column("active_document_id")
        batch_op.drop_column("selected_document_ids_json")
        batch_op.drop_column("knowledge_scope")
