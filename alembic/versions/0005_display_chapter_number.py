"""Add display_chapter_number and display_reading_chapter for accurate EPUB numbering.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26 20:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_sources") as batch_op:
        batch_op.add_column(sa.Column("display_reading_chapter", sa.Integer(), nullable=True))

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.add_column(sa.Column("display_chapter_number", sa.Integer(), nullable=True))

    op.create_index(
        "idx_document_chunks_doc_display_ch",
        "document_chunks",
        ["document_id", "display_chapter_number"],
    )


def downgrade() -> None:
    op.drop_index("idx_document_chunks_doc_display_ch", table_name="document_chunks")

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.drop_column("display_chapter_number")

    with op.batch_alter_table("document_sources") as batch_op:
        batch_op.drop_column("display_reading_chapter")
