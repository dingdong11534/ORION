"""SQLite + FTS5 repository for local Knowledge Base document sources and batched chunks."""

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from orion.application.knowledge.contracts import (
    ChapterInfo,
    ChunkDraft,
    DocumentSourceRecord,
    ExtractedDocument,
    ExtractedHeader,
    IngestionStatus,
    ReadingPolicy,
    RetrievedPassage,
)
from orion.application.knowledge.ports import KnowledgeStorePort
from orion.infrastructure.persistence.models import (
    DocumentChunkModel,
    DocumentSourceModel,
    generate_chunk_id,
    generate_document_id,
)
from orion.infrastructure.persistence.types import utc_now


def _source_to_record(m: DocumentSourceModel) -> DocumentSourceRecord:
    try:
        policy = ReadingPolicy(m.reading_policy)
    except ValueError:
        policy = ReadingPolicy.UNCONFIGURED
    return DocumentSourceRecord(
        id=m.id,
        display_name=m.display_name,
        source_path=m.source_path,
        file_type=m.file_type,
        file_size=m.file_size,
        content_hash=m.content_hash,
        modified_at=m.modified_at,
        indexed_at=m.indexed_at,
        ingestion_status=IngestionStatus(m.ingestion_status),
        extraction_version=m.extraction_version,
        chunk_count=m.chunk_count,
        error_message=m.error_message,
        title=m.title,
        author=m.author,
        total_chapters=m.total_chapters or 0,
        current_reading_position=m.current_reading_position,
        display_reading_chapter=m.display_reading_chapter,
        reading_policy=policy,
        range_start=m.range_start,
        range_end=m.range_end,
        progress_updated_at=m.progress_updated_at,
    )


def _make_excerpt(content: str, max_len: int = 220) -> str:
    clean = " ".join(content.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip() + "..."


class SqliteKnowledgeStore(KnowledgeStorePort):
    """Transactional, batched SQLite + FTS5 implementation of KnowledgeStorePort."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_sources(self) -> Sequence[DocumentSourceRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(DocumentSourceModel).order_by(DocumentSourceModel.indexed_at.desc())
                )
                .scalars()
                .all()
            )
            return [_source_to_record(r) for r in rows]

    def get_source(self, document_id: str) -> DocumentSourceRecord | None:
        with self._session_factory() as session:
            row = session.get(DocumentSourceModel, document_id)
            return _source_to_record(row) if row is not None else None

    def get_source_by_path(self, source_path: str) -> DocumentSourceRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(DocumentSourceModel).where(DocumentSourceModel.source_path == source_path)
            ).scalar_one_or_none()
            return _source_to_record(row) if row is not None else None

    def total_indexed_bytes(self) -> int:
        with self._session_factory() as session:
            val = session.execute(
                select(func.coalesce(func.sum(DocumentSourceModel.file_size), 0))
            ).scalar()
            return int(val or 0)

    def free_disk_bytes(self) -> int | None:
        try:
            bind = self._session_factory.kw.get("bind")
            db_path_str = getattr(getattr(bind, "url", None), "database", None)
            if db_path_str and db_path_str != ":memory:":
                target = Path(db_path_str).resolve().parent
                if target.exists():
                    return shutil.disk_usage(target).free
        except Exception:  # noqa: BLE001
            return None
        return None

    def begin_document_index(
        self,
        header: ExtractedHeader,
        extraction_version: str = "1.1",
    ) -> tuple[DocumentSourceRecord, bool]:
        with self._session_factory() as session, session.begin():
            existing = session.execute(
                select(DocumentSourceModel).where(
                    DocumentSourceModel.source_path == header.source_path
                )
            ).scalar_one_or_none()

            if (
                existing is not None
                and existing.content_hash == header.content_hash
                and existing.extraction_version == extraction_version
                and existing.ingestion_status == IngestionStatus.INDEXED.value
            ):
                return _source_to_record(existing), False

            now = utc_now()
            if existing is not None:
                doc_id = existing.id
                session.execute(
                    text("DELETE FROM document_chunks_fts WHERE document_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                session.execute(
                    text("DELETE FROM document_chunks WHERE document_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                existing.display_name = header.display_name
                existing.file_type = header.file_type
                existing.file_size = header.file_size
                existing.content_hash = header.content_hash
                existing.modified_at = header.modified_at
                existing.indexed_at = now
                existing.ingestion_status = IngestionStatus.INDEXING.value
                existing.extraction_version = extraction_version
                existing.chunk_count = 0
                existing.error_message = None
                existing.title = header.title
                existing.author = header.author
                existing.total_chapters = 0
                source_model = existing
            else:
                doc_id = generate_document_id()
                source_model = DocumentSourceModel(
                    id=doc_id,
                    display_name=header.display_name,
                    source_path=header.source_path,
                    file_type=header.file_type,
                    file_size=header.file_size,
                    content_hash=header.content_hash,
                    modified_at=header.modified_at,
                    indexed_at=now,
                    ingestion_status=IngestionStatus.INDEXING.value,
                    extraction_version=extraction_version,
                    chunk_count=0,
                    error_message=None,
                    title=header.title,
                    author=header.author,
                    total_chapters=0,
                )
                session.add(source_model)
            session.flush()
            return _source_to_record(source_model), True

    def insert_chunks_batch(
        self,
        document_id: str,
        chunks: Sequence[ChunkDraft],
    ) -> int:
        if not chunks:
            return 0
        with self._session_factory() as session, session.begin():
            fts_rows: list[dict[str, str]] = []
            for ch in chunks:
                cid = generate_chunk_id()
                session.add(
                    DocumentChunkModel(
                        id=cid,
                        document_id=document_id,
                        chunk_index=ch.chunk_index,
                        page_number=ch.page_number,
                        section_title=ch.section_title,
                        spine_index=ch.spine_index,
                        chapter_index=ch.chapter_index,
                        reading_position=ch.reading_position,
                        display_chapter_number=ch.display_chapter_number,
                        chapter_title=ch.chapter_title,
                        source_locator=ch.source_locator,
                        content=ch.content,
                        content_hash=ch.content_hash,
                    )
                )
                fts_rows.append(
                    {"chunk_id": cid, "document_id": document_id, "content": ch.content}
                )
            session.execute(
                text(
                    "INSERT INTO document_chunks_fts (chunk_id, document_id, content) "
                    "VALUES (:chunk_id, :document_id, :content)"
                ),
                fts_rows,
            )
        return len(chunks)

    def finalize_document_index(
        self,
        document_id: str,
        chunk_count: int,
        total_chapters: int = 0,
    ) -> DocumentSourceRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(DocumentSourceModel, document_id)
            if row is None:
                raise ValueError(f"DocumentSourceModel {document_id!r} not found.")
            row.ingestion_status = IngestionStatus.INDEXED.value
            row.chunk_count = chunk_count
            if total_chapters > 0:
                row.total_chapters = total_chapters
            else:
                max_pos = session.execute(
                    select(func.max(DocumentChunkModel.reading_position)).where(
                        DocumentChunkModel.document_id == document_id
                    )
                ).scalar_one_or_none()
                row.total_chapters = int(max_pos or 0)
            row.indexed_at = utc_now()
            row.error_message = None
            session.flush()
            return _source_to_record(row)

    def update_reading_progress(
        self,
        document_id: str,
        reading_position: int | None = None,
        reading_policy: ReadingPolicy | None = None,
        range_start: int | None = None,
        range_end: int | None = None,
        display_chapter: int | None = None,
    ) -> DocumentSourceRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(DocumentSourceModel, document_id)
            if row is None:
                raise ValueError(f"DocumentSourceModel {document_id!r} not found.")

            def _resolve_display(ch_num: int) -> int | None:
                return session.execute(
                    select(func.min(DocumentChunkModel.reading_position)).where(
                        DocumentChunkModel.document_id == document_id,
                        DocumentChunkModel.display_chapter_number == ch_num,
                    )
                ).scalar_one_or_none()

            if display_chapter is not None:
                if display_chapter < 1:
                    raise ValueError("Reading chapter must be >= 1.")
                resolved_pos = _resolve_display(int(display_chapter))
                if resolved_pos is not None:
                    row.current_reading_position = int(resolved_pos)
                    row.display_reading_chapter = int(display_chapter)
                else:
                    max_ch = int(row.total_chapters or 1)
                    clamped = (
                        min(max(1, int(display_chapter)), max_ch)
                        if max_ch > 0
                        else max(1, int(display_chapter))
                    )
                    row.current_reading_position = clamped
                    row.display_reading_chapter = clamped
                if (
                    reading_policy is None
                    and row.reading_policy == ReadingPolicy.UNCONFIGURED.value
                ):
                    row.reading_policy = ReadingPolicy.SPOILER_SAFE.value
            elif reading_position is not None:
                if reading_position < 1:
                    raise ValueError("Reading position must be >= 1.")
                max_ch = int(row.total_chapters or 0)
                eff_pos = (
                    min(int(reading_position), max_ch) if max_ch > 0 else int(reading_position)
                )
                row.current_reading_position = eff_pos
                disp = session.execute(
                    select(func.max(DocumentChunkModel.display_chapter_number)).where(
                        DocumentChunkModel.document_id == document_id,
                        DocumentChunkModel.reading_position == eff_pos,
                    )
                ).scalar_one_or_none()
                row.display_reading_chapter = int(disp) if disp is not None else eff_pos
                if (
                    reading_policy is None
                    and row.reading_policy == ReadingPolicy.UNCONFIGURED.value
                ):
                    row.reading_policy = ReadingPolicy.SPOILER_SAFE.value

            if reading_policy is not None:
                row.reading_policy = reading_policy.value

            if range_start is not None or range_end is not None:
                rs = int(range_start) if range_start is not None else (row.range_start or 1)
                re_val = int(range_end) if range_end is not None else (row.range_end or rs)
                if rs < 1 or re_val < 1 or re_val < rs:
                    raise ValueError(f"Invalid chapter range: {rs}..{re_val}.")
                res_rs = _resolve_display(rs)
                res_re = _resolve_display(re_val)
                row.range_start = int(res_rs) if res_rs is not None else rs
                row.range_end = int(res_re) if res_re is not None else re_val

            row.progress_updated_at = utc_now()
            session.flush()
            return _source_to_record(row)

    def list_chapters(
        self,
        document_id: str,
        query: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[ChapterInfo], int]:
        q_clean = query.strip().lower()
        where_extra = (
            " AND (lower(COALESCE(chapter_title, '')) LIKE :q "
            "OR CAST(display_chapter_number AS TEXT) = :q_exact)"
            if q_clean
            else ""
        )
        params: dict[str, Any] = {
            "doc_id": document_id,
            "offset": max(0, offset),
            "limit": max(1, min(limit, 500)),
        }
        if q_clean:
            params["q"] = f"%{q_clean}%"
            params["q_exact"] = q_clean

        count_sql = text(
            "SELECT COUNT(DISTINCT reading_position) FROM document_chunks "
            "WHERE document_id = :doc_id AND reading_position IS NOT NULL" + where_extra
        )
        list_sql = text(
            "SELECT reading_position, MAX(display_chapter_number) AS display_chapter_number, "
            "MIN(spine_index) AS spine_index, MIN(chapter_index) AS chapter_index, "
            "COALESCE(MAX(chapter_title), 'Chapter ' || reading_position) AS chapter_title, "
            "MIN(source_locator) AS source_locator, COUNT(*) AS chunk_count "
            "FROM document_chunks "
            "WHERE document_id = :doc_id AND reading_position IS NOT NULL"
            + where_extra
            + " GROUP BY reading_position ORDER BY reading_position ASC LIMIT :limit OFFSET :offset"
        )
        with self._session_factory() as session:
            total = int(session.execute(count_sql, params).scalar_one() or 0)
            rows = session.execute(list_sql, params).mappings().all()

        chapters = [
            ChapterInfo(
                reading_position=int(r["reading_position"]),
                display_chapter_number=(
                    int(r["display_chapter_number"])
                    if r["display_chapter_number"] is not None
                    else None
                ),
                spine_index=int(r["spine_index"]) if r["spine_index"] is not None else None,
                chapter_index=int(r["chapter_index"]) if r["chapter_index"] is not None else None,
                chapter_title=str(r["chapter_title"]),
                source_locator=r["source_locator"],
                chunk_count=int(r["chunk_count"]),
            )
            for r in rows
        ]
        return chapters, total

    def mark_document_failed(
        self,
        document_id: str,
        error_message: str,
    ) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(DocumentSourceModel, document_id)
            if row is None:
                return
            session.execute(
                text("DELETE FROM document_chunks_fts WHERE document_id = :doc_id"),
                {"doc_id": document_id},
            )
            session.execute(
                text("DELETE FROM document_chunks WHERE document_id = :doc_id"),
                {"doc_id": document_id},
            )
            row.ingestion_status = IngestionStatus.FAILED.value
            row.chunk_count = 0
            row.error_message = error_message[:500]

    def upsert_document(
        self,
        extracted: ExtractedDocument,
        chunks: Sequence[ChunkDraft],
        extraction_version: str = "1.1",
    ) -> tuple[DocumentSourceRecord, bool]:
        header = ExtractedHeader(
            display_name=extracted.display_name,
            source_path=extracted.source_path,
            file_type=extracted.file_type,
            file_size=extracted.file_size,
            content_hash=extracted.content_hash,
            modified_at=extracted.modified_at,
            title=extracted.title,
            author=extracted.author,
            language=extracted.language,
        )
        record, needs_index = self.begin_document_index(
            header, extraction_version=extraction_version
        )
        if not needs_index:
            return record, False
        self.insert_chunks_batch(record.id, chunks)
        return self.finalize_document_index(record.id, len(chunks)), True

    def remove_source(self, document_id: str) -> bool:
        with self._session_factory() as session, session.begin():
            existing = session.get(DocumentSourceModel, document_id)
            if existing is None:
                return False
            session.execute(
                text("DELETE FROM document_chunks_fts WHERE document_id = :doc_id"),
                {"doc_id": document_id},
            )
            session.delete(existing)
            return True

    def search_chunks(
        self,
        query: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 4,
        chapter_min: int | None = None,
        chapter_max: int | None = None,
        respect_reading_policy: bool = True,
    ) -> Sequence[RetrievedPassage]:
        if not query.strip() or top_k <= 0:
            return []

        chapter_clause = ""
        params: dict[str, Any] = {"query": query, "top_k": top_k}
        if chapter_min is not None and chapter_max is not None:
            chapter_clause = (
                " AND (s.file_type != 'epub' OR ("
                "(c.display_chapter_number IS NOT NULL AND c.display_chapter_number BETWEEN :cmin AND :cmax) "
                "OR (c.display_chapter_number IS NULL AND c.reading_position IS NOT NULL AND c.reading_position BETWEEN :cmin AND :cmax)"
                "))"
            )
            if respect_reading_policy:
                chapter_clause += (
                    " AND ("
                    "s.file_type != 'epub' "
                    "OR s.reading_policy = 'whole_document' "
                    "OR (s.reading_policy = 'spoiler_safe' AND s.current_reading_position IS NOT NULL "
                    "AND c.reading_position IS NOT NULL AND c.reading_position <= s.current_reading_position) "
                    "OR (s.reading_policy = 'chapter_range' AND s.range_start IS NOT NULL AND s.range_end IS NOT NULL "
                    "AND c.reading_position IS NOT NULL AND c.reading_position BETWEEN s.range_start AND s.range_end)"
                    ")"
                )
            params["cmin"] = int(chapter_min)
            params["cmax"] = int(chapter_max)
        elif respect_reading_policy:
            chapter_clause = (
                " AND ("
                "s.file_type != 'epub' "
                "OR s.reading_policy = 'whole_document' "
                "OR (s.reading_policy = 'spoiler_safe' AND s.current_reading_position IS NOT NULL "
                "AND c.reading_position IS NOT NULL AND c.reading_position <= s.current_reading_position) "
                "OR (s.reading_policy = 'chapter_range' AND s.range_start IS NOT NULL AND s.range_end IS NOT NULL "
                "AND c.reading_position IS NOT NULL AND c.reading_position BETWEEN s.range_start AND s.range_end)"
                ")"
            )

        base_select = (
            "SELECT f.chunk_id, f.document_id, s.display_name, s.file_type, s.source_path, "
            "c.chunk_index, c.page_number, c.section_title, "
            "c.spine_index, c.chapter_index, c.reading_position, c.display_chapter_number, "
            "c.chapter_title, c.source_locator, "
            "c.content, bm25(document_chunks_fts) AS rank_score "
            "FROM document_chunks_fts AS f "
            "JOIN document_chunks AS c ON c.id = f.chunk_id "
            "JOIN document_sources AS s ON s.id = f.document_id "
            "WHERE document_chunks_fts MATCH :query "
            "AND s.ingestion_status = 'indexed'" + chapter_clause
        )

        if document_ids is None:
            stmt = text(base_select + " ORDER BY rank_score ASC LIMIT :top_k")
            is_selected = False
        else:
            clean_ids = [did for did in document_ids if did]
            if not clean_ids:
                return []
            stmt = text(
                base_select + " AND f.document_id IN :doc_ids ORDER BY rank_score ASC LIMIT :top_k"
            ).bindparams(bindparam("doc_ids", expanding=True))
            params["doc_ids"] = clean_ids
            is_selected = True

        with self._session_factory() as session:
            rows = session.execute(stmt, params).mappings().all()

        return [
            RetrievedPassage(
                chunk_id=str(r["chunk_id"]),
                document_id=str(r["document_id"]),
                display_name=str(r["display_name"]),
                file_type=str(r["file_type"] or ""),
                source_path=str(r["source_path"]),
                chunk_index=int(r["chunk_index"]),
                page_number=r["page_number"],
                section_title=r["section_title"],
                spine_index=r["spine_index"],
                chapter_index=r["chapter_index"],
                reading_position=r["reading_position"],
                display_chapter_number=r["display_chapter_number"],
                chapter_title=r["chapter_title"],
                source_locator=r["source_locator"],
                content=str(r["content"]),
                excerpt=_make_excerpt(str(r["content"])),
                rank=float(r["rank_score"]),
                is_selected_source=is_selected,
            )
            for r in rows
        ]

    def get_adjacent_context(self, document_id: str, chunk_index: int, window: int = 1) -> str:
        """Return bounded surrounding chunk text for the Source Inspector."""
        lo = max(0, int(chunk_index) - max(0, window))
        hi = int(chunk_index) + max(0, window)
        with self._session_factory() as session:
            rows = session.execute(
                select(DocumentChunkModel.chunk_index, DocumentChunkModel.content)
                .where(
                    DocumentChunkModel.document_id == document_id,
                    DocumentChunkModel.chunk_index >= lo,
                    DocumentChunkModel.chunk_index <= hi,
                )
                .order_by(DocumentChunkModel.chunk_index.asc())
            ).all()
        return "\n\n---\n\n".join(str(r[1]) for r in rows if r[1])
