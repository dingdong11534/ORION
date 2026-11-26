"""Streaming, boundary-aware document chunker for local RAG."""

import hashlib
from collections.abc import Iterable, Iterator

from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import (
    ChunkDraft,
    ExtractedDocument,
    ExtractedSection,
)
from orion.application.knowledge.ports import IngestionCancelledError


def _find_split_point(text: str, start: int, max_end: int) -> int:
    """Find a clean boundary <= max_end without splitting mid-sentence when practical."""
    if max_end >= len(text):
        return len(text)

    min_split = start + max(100, (max_end - start) // 2)
    window = text[min_split:max_end]

    for sep in ("\n\n", ".\n", ". ", "?\n", "? ", "!\n", "! ", "\n", " "):
        idx = window.rfind(sep)
        if idx != -1:
            candidate = min_split + idx + len(sep)
            if candidate > start:
                return candidate
    return max_end


def _format_section_label(section: ExtractedSection) -> str | None:
    if section.chapter_title and section.section_title:
        if section.chapter_title == section.section_title:
            return section.chapter_title
        return f"{section.chapter_title} — {section.section_title}"
    return section.chapter_title or section.section_title


def iter_chunks(
    sections: Iterable[ExtractedSection],
    chunk_size: int = 1000,
    overlap: int = 150,
    max_chunks: int | None = None,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ChunkDraft]:
    """Yield ChunkDrafts incrementally from an iterable of ExtractedSections."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size.")

    chunk_index = 0
    for section in sections:
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("Document chunking cancelled by user.")

        raw = section.text.strip()
        if not raw:
            continue

        section_label = _format_section_label(section)
        pos = 0
        text_len = len(raw)
        while pos < text_len:
            if cancel_token is not None and cancel_token.is_cancelled:
                raise IngestionCancelledError("Document chunking cancelled by user.")
            if max_chunks is not None and chunk_index >= max_chunks:
                return

            max_end = min(pos + chunk_size, text_len)
            end = _find_split_point(raw, pos, max_end)
            piece = raw[pos:end].strip()
            if piece:
                digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                yield ChunkDraft(
                    chunk_index=chunk_index,
                    page_number=section.page_number,
                    section_title=section_label,
                    content=piece,
                    content_hash=digest,
                )
                chunk_index += 1

            if end >= text_len:
                break
            pos = max(pos + 1, end - overlap)


def chunk_document(
    extracted: ExtractedDocument,
    chunk_size: int = 1000,
    overlap: int = 150,
    max_chunks: int | None = None,
) -> list[ChunkDraft]:
    """Deterministically split ExtractedDocument into bounded, overlapping chunks."""
    return list(
        iter_chunks(
            extracted.sections,
            chunk_size=chunk_size,
            overlap=overlap,
            max_chunks=max_chunks,
        )
    )
