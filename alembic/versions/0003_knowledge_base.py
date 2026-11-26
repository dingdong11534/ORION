"""Add local document knowledge base tables and FTS5 index.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("modified_at", sa.String(), nullable=False),
        sa.Column("indexed_at", sa.String(), nullable=False),
        sa.Column("ingestion_status", sa.String(length=32), nullable=False),
        sa.Column("extraction_version", sa.String(length=16), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_path", name="uq_document_sources_source_path"),
    )
    op.create_index("idx_document_sources_indexed_at", "document_sources", ["indexed_at"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_doc_idx"),
    )
    op.create_index("idx_document_chunks_document_id", "document_chunks", ["document_id"])

    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5("
        "chunk_id UNINDEXED, document_id UNINDEXED, content)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_chunks_fts")
    op.drop_index("idx_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("idx_document_sources_indexed_at", table_name="document_sources")
    op.drop_table("document_sources")
