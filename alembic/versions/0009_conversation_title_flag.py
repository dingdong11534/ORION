"""Add title_is_custom flag to conversations and backfill deterministic titles.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-27 11:37:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from orion.application.contracts import derive_conversation_title

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "title_is_custom",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, title FROM conversations WHERE title = 'New Conversation'")
    ).fetchall()
    for cid, _ in rows:
        first_msg = conn.execute(
            sa.text(
                "SELECT content FROM messages "
                "WHERE conversation_id = :cid AND role = 'user' AND content IS NOT NULL "
                "ORDER BY sequence_number ASC LIMIT 1"
            ),
            {"cid": cid},
        ).fetchone()
        if first_msg and first_msg[0]:
            new_title = derive_conversation_title(str(first_msg[0]))
            if new_title and new_title != "New Conversation":
                conn.execute(
                    sa.text("UPDATE conversations SET title = :title WHERE id = :cid"),
                    {"title": new_title, "cid": cid},
                )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("title_is_custom")
