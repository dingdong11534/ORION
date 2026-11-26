"""Typed Pydantic contracts for ORION's local Knowledge Base and RAG subsystem."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IngestionStatus(StrEnum):
    """Lifecycle status of an indexed document source."""

    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class KnowledgeLimits(BaseModel):
    """Resource-aware bounds for local document ingestion and bounded RAG retrieval."""

    model_config = ConfigDict(frozen=True)

    # Format-specific security & resource limits (None = no arbitrary cap)
    max_streaming_file_bytes: int | None = None
    max_binary_file_bytes: int = Field(default=128 * 1024 * 1024, gt=0)
    max_extracted_chars: int | None = None
    max_chunks_per_document: int | None = None
    max_total_indexed_bytes: int | None = None
    max_folder_files: int | None = None

    # Archive ZIP-bomb / XML-safety bounds
    max_archive_uncompressed_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    max_archive_entries: int = Field(default=20_000, gt=0)
    max_archive_compression_ratio: float = Field(default=200.0, gt=1.0)

    # Disk-space & streaming batching controls
    min_free_disk_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    warn_free_disk_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    large_file_warn_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    batch_size: int = Field(default=100, ge=1, le=2000)
    stream_read_block_bytes: int = Field(default=262_144, ge=4096)

    # Chunking & LLM context window bounds (Qwen3 num_ctx=2048)
    chunk_size_chars: int = Field(default=1000, ge=200, le=4000)
    chunk_overlap_chars: int = Field(default=150, ge=0, le=1000)
    default_top_k: int = Field(default=4, ge=1, le=12)
    max_retrieved_context_chars: int = Field(default=2600, ge=400, le=12000)


class ExtractedSection(BaseModel):
    """Single structural section, page, chapter, slide, sheet, or row group."""

    model_config = ConfigDict(frozen=True)

    page_number: int | None = None
    section_title: str | None = None
    chapter_title: str | None = None
    text: str


class ExtractedHeader(BaseModel):
    """Streaming document header metadata emitted before sections are iterated."""

    model_config = ConfigDict(frozen=True)

    display_name: str
    source_path: str
    file_type: str
    file_size: int
    content_hash: str
    modified_at: datetime
    title: str | None = None
    author: str | None = None
    language: str | None = None


class ExtractedDocument(BaseModel):
    """Normalized, untrusted document extraction result with source metadata."""

    model_config = ConfigDict(frozen=True)

    display_name: str
    source_path: str
    file_type: str
    file_size: int
    content_hash: str
    modified_at: datetime
    title: str | None = None
    author: str | None = None
    language: str | None = None
    sections: tuple[ExtractedSection, ...]


class ChunkDraft(BaseModel):
    """Chunk produced by the streaming chunker prior to batched SQLite persistence."""

    model_config = ConfigDict(frozen=True)

    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    content: str
    content_hash: str


class DocumentSourceRecord(BaseModel):
    """Persisted metadata record for an indexed local document source."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    source_path: str
    file_type: str
    file_size: int
    content_hash: str
    modified_at: datetime
    indexed_at: datetime
    ingestion_status: IngestionStatus
    extraction_version: str
    chunk_count: int = 0
    error_message: str | None = None


class DocumentChunkRecord(BaseModel):
    """Persisted document chunk record."""

    model_config = ConfigDict(frozen=True)

    id: str
    document_id: str
    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    content: str
    content_hash: str


class RetrievedPassage(BaseModel):
    """A single ranked passage retrieved from the SQLite FTS5 index."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    display_name: str
    source_path: str
    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    content: str
    excerpt: str
    rank: float
    is_selected_source: bool = True


class RetrievalResult(BaseModel):
    """Bounded retrieval output returned by DocumentRetrievalService."""

    model_config = ConfigDict(frozen=True)

    query: str
    selected_document_ids: tuple[str, ...]
    passages: tuple[RetrievedPassage, ...]
    total_chars: int = 0
    has_evidence: bool = False
    message: str | None = None
