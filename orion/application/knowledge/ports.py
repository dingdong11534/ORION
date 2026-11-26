"""Protocol ports and domain exceptions for ORION's local Knowledge Base subsystem."""

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import (
    ChunkDraft,
    DocumentSourceRecord,
    ExtractedDocument,
    ExtractedHeader,
    ExtractedSection,
    RetrievedPassage,
)


class KnowledgeError(Exception):
    """Base exception for knowledge base extraction, indexing, and retrieval errors."""


class DocumentExtractionError(KnowledgeError):
    """Raised when a document fails security, format, or resource validation during extraction."""


class IngestionCancelledError(KnowledgeError):
    """Raised when an active document or folder ingestion is cancelled by the user."""


class DocumentExtractorPort(Protocol):
    """Protocol for modular, streaming local document extractors."""

    def iter_sections(
        self,
        file_path: Path,
        cancel_token: CancellationToken | None = None,
    ) -> tuple[ExtractedHeader, Iterator[ExtractedSection]]:
        """Safely open file_path and yield (header, section_iterator) incrementally."""
        ...

    def extract(self, file_path: Path) -> ExtractedDocument:
        """Safely open and extract structured text and metadata from file_path."""
        ...


class KnowledgeStorePort(Protocol):
    """Protocol for persistent SQLite + FTS5 document source and chunk storage."""

    def list_sources(self) -> Sequence[DocumentSourceRecord]:
        """Return all persisted document sources ordered by indexed_at descending."""
        ...

    def get_source(self, document_id: str) -> DocumentSourceRecord | None:
        """Return a document source by ID, or None if not found."""
        ...

    def get_source_by_path(self, source_path: str) -> DocumentSourceRecord | None:
        """Return a document source by canonical source_path, or None."""
        ...

    def begin_document_index(
        self,
        header: ExtractedHeader,
        extraction_version: str = "1.1",
    ) -> tuple[DocumentSourceRecord, bool]:
        """Prepare or check a document for streaming indexing.

        Returns (record, needs_indexing). If content_hash and extraction_version
        already match an INDEXED source, returns (record, False).
        """
        ...

    def insert_chunks_batch(
        self,
        document_id: str,
        chunks: Sequence[ChunkDraft],
    ) -> int:
        """Persist a batch of chunks into document_chunks and document_chunks_fts."""
        ...

    def finalize_document_index(
        self,
        document_id: str,
        chunk_count: int,
    ) -> DocumentSourceRecord:
        """Mark a document source as INDEXED with final chunk_count."""
        ...

    def mark_document_failed(
        self,
        document_id: str,
        error_message: str,
    ) -> None:
        """Mark an interrupted or failed document index as FAILED."""
        ...

    def upsert_document(
        self,
        extracted: ExtractedDocument,
        chunks: Sequence[ChunkDraft],
        extraction_version: str = "1.1",
    ) -> tuple[DocumentSourceRecord, bool]:
        """Persist or re-index a document and its chunks transactionally."""
        ...

    def remove_source(self, document_id: str) -> bool:
        """Delete a document source and all its chunks + FTS entries transactionally."""
        ...

    def total_indexed_bytes(self) -> int:
        """Return sum of file_size across all indexed document sources."""
        ...

    def free_disk_bytes(self) -> int | None:
        """Return available free bytes on the database filesystem, or None if unknown."""
        ...

    def search_chunks(
        self,
        query: str,
        document_ids: Sequence[str],
        top_k: int = 4,
    ) -> Sequence[RetrievedPassage]:
        """Search FTS5 chunks strictly within the explicitly selected document_ids."""
        ...
