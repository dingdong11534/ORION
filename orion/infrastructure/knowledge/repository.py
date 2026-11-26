"""SQLite + FTS5 repository for local Knowledge Base document sources and batched chunks."""

import shutil
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from orion.application.knowledge.contracts import (
    ChunkDraft,
    DocumentSourceRecord,
    ExtractedDocument,
    ExtractedHeader,
    IngestionStatus,
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
    ) -> DocumentSourceRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(DocumentSourceModel, document_id)
            if row is None:
                raise ValueError(f"DocumentSourceModel {document_id!r} not found.")
            row.ingestion_status = IngestionStatus.INDEXED.value
            row.chunk_count = chunk_count
            row.indexed_at = utc_now()
            row.error_message = None
            session.flush()
            return _source_to_record(row)

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
        document_ids: Sequence[str],
        top_k: int = 4,
    ) -> Sequence[RetrievedPassage]:
        clean_ids = [did for did in document_ids if did]
        if not clean_ids or not query.strip() or top_k <= 0:
            return []

        stmt = text(
            "SELECT f.chunk_id, f.document_id, s.display_name, s.source_path, "
            "c.chunk_index, c.page_number, c.section_title, c.content, "
            "bm25(document_chunks_fts) AS rank_score "
            "FROM document_chunks_fts AS f "
            "JOIN document_chunks AS c ON c.id = f.chunk_id "
            "JOIN document_sources AS s ON s.id = f.document_id "
            "WHERE document_chunks_fts MATCH :query "
            "AND f.document_id IN :doc_ids "
            "AND s.ingestion_status = 'indexed' "
            "ORDER BY rank_score ASC "
            "LIMIT :top_k"
        ).bindparams(bindparam("doc_ids", expanding=True))

        with self._session_factory() as session:
            rows = (
                session.execute(
                    stmt,
                    {"query": query, "doc_ids": clean_ids, "top_k": top_k},
                )
                .mappings()
                .all()
            )

        return [
            RetrievedPassage(
                chunk_id=str(r["chunk_id"]),
                document_id=str(r["document_id"]),
                display_name=str(r["display_name"]),
                source_path=str(r["source_path"]),
                chunk_index=int(r["chunk_index"]),
                page_number=r["page_number"],
                section_title=r["section_title"],
                content=str(r["content"]),
                excerpt=_make_excerpt(str(r["content"])),
                rank=float(r["rank_score"]),
                is_selected_source=True,
            )
            for r in rows
        ]
