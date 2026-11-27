"""Add chapter-aware EPUB metadata and reading-progress spoiler control columns.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_sources") as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("author", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("total_chapters", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("current_reading_position", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "reading_policy",
                sa.String(length=32),
                nullable=False,
                server_default="unconfigured",
            )
        )
        batch_op.add_column(sa.Column("range_start", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("range_end", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("progress_updated_at", sa.String(), nullable=True))

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.add_column(sa.Column("spine_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("chapter_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("reading_position", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("chapter_title", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("source_locator", sa.String(), nullable=True))

    op.create_index(
        "idx_document_chunks_doc_reading_pos",
        "document_chunks",
        ["document_id", "reading_position"],
    )


def downgrade() -> None:
    op.drop_index("idx_document_chunks_doc_reading_pos", table_name="document_chunks")

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.drop_column("source_locator")
        batch_op.drop_column("chapter_title")
        batch_op.drop_column("reading_position")
        batch_op.drop_column("chapter_index")
        batch_op.drop_column("spine_index")

    with op.batch_alter_table("document_sources") as batch_op:
        batch_op.drop_column("progress_updated_at")
        batch_op.drop_column("range_end")
        batch_op.drop_column("range_start")
        batch_op.drop_column("reading_policy")
        batch_op.drop_column("current_reading_position")
        batch_op.drop_column("total_chapters")
        batch_op.drop_column("author")
        batch_op.drop_column("title")
