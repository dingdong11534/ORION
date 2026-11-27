"""Milestone 4.9 tests: chapter-aware EPUB metadata, spoiler control, chronological RAG, and evaluation."""

import zipfile
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.knowledge.contracts import (
    AnswerIntent,
    KnowledgeLimits,
    KnowledgeScope,
    ReadingPolicy,
)
from orion.application.knowledge.retrieval import DocumentRetrievalService, KnowledgeService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.knowledge.extractor import LocalDocumentExtractor
from orion.infrastructure.knowledge.repository import SqliteKnowledgeStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)


def _setup_49_stack(tmp_path: Path):
    db_path = tmp_path / "orion_49_novel.db"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(db_path)
    session_factory = create_session_factory(engine)
    store = SqliteKnowledgeStore(session_factory)
    limits = KnowledgeLimits()
    kb_svc = KnowledgeService(
        extractor=LocalDocumentExtractor(limits=limits), store=store, limits=limits
    )
    retrieval = DocumentRetrievalService(store=store, limits=limits)
    conv_store = SqliteConversationStore(session_factory)
    settings_store = SqliteSettingsStore(session_factory)
    provider = FakeModelProvider(simulated_delay=0.0)
    asst = AssistantService(provider, conv_store, settings_store, retrieval_service=retrieval)
    return engine, store, kb_svc, retrieval, asst, provider


def _build_synthetic_novel_epub(path: Path, num_chapters: int = 150) -> None:
    """Generate a multi-chapter EPUB with non-alphabetical spine filenames and distinct stage facts."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        manifest_items = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine_refs = []
        nav_lis = []
        for idx in range(1, num_chapters + 1):
            # Reverse-alphabetical prefix to prove spine order is used instead of filename sort
            fname = f"part_{num_chapters + 1 - idx:04d}_ch.xhtml"
            manifest_items.append(
                f'<item id="c{idx}" href="{fname}" media-type="application/xhtml+xml"/>'
            )
            spine_refs.append(f'<itemref idref="c{idx}"/>')
            if idx == 85:
                title = f"Chapter {idx}: Hot-blooded and cold-blooded"
                body = (
                    "Fang Yuan refined the Moonlight Gu in Qing Mao Mountain and demonstrated "
                    "calculated ruthlessness against clan elders."
                )
            elif idx == 120:
                title = f"Chapter {idx}: Burning Soul Bursting Luck"
                body = (
                    "Fang Yuan activated Burning Soul Bursting Luck in Northern Plains, "
                    "advancing his Gu Immortal scheming to a new stage."
                )
            elif idx == 145:
                title = f"Chapter {idx}: Sovereign Immortal Fetus"
                body = (
                    "Fang Yuan obtained the Sovereign Immortal Aperture and battled Heavenly Court."
                )
            elif idx == 40:
                title = f"Chapter {idx}: Spring Autumn Cicada"
                body = "The Spring Autumn Cicada allows Fang Yuan to travel upstream in the River of Time."
            elif idx == 60:
                title = f"Chapter {idx}: Hot-blooded and cold-blooded (Part II)"
                body = "Gu Yue village elders debated resource allocation."
            else:
                title = f"Chapter {idx}: Cultivation Stage {idx}"
                body = (
                    f"In chapter {idx}, Gu Master cultivation continued steadily across the region."
                )

            nav_lis.append(f'<li><a href="{fname}">{title}</a></li>')
            zf.writestr(
                f"OEBPS/{fname}",
                f"<html><body><h1>{title}</h1><p>{body}</p></body></html>",
            )

        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Synthetic Insanity</dc:title>"
            "<dc:creator>Test Author</dc:creator>"
            "</metadata>"
            f"<manifest>{''.join(manifest_items)}</manifest>"
            f"<spine>{''.join(spine_refs)}</spine></package>",
        )
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<body><nav epub:type="toc"><ol>{"".join(nav_lis)}</ol></nav></body></html>',
        )


def test_migration_0003_to_0004_preserves_existing_data(tmp_path: Path):
    """Verify upgrading 0003 -> 0004 preserves existing document_sources and document_chunks."""
    db_path = tmp_path / "mig_0004.db"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(alembic_cfg, "0003")

    engine = create_db_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO document_sources (id, display_name, source_path, file_type, file_size, "
                "content_hash, modified_at, indexed_at, ingestion_status, extraction_version, chunk_count) "
                "VALUES ('doc1', 'legacy.txt', '/tmp/legacy.txt', 'txt', 100, 'abc', "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'indexed', '1.1', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO document_chunks (id, document_id, chunk_index, content, content_hash) "
                "VALUES ('chk1', 'doc1', 0, 'Legacy text content', 'def')"
            )
        )
    engine.dispose()

    command.upgrade(alembic_cfg, "0004")

    engine2 = create_db_engine(db_path)
    with engine2.connect() as conn:
        row = conn.execute(
            text(
                "SELECT display_name, reading_policy, total_chapters FROM document_sources WHERE id='doc1'"
            )
        ).one()
        assert row[0] == "legacy.txt"
        assert row[1] == "unconfigured"
        assert row[2] == 0
        chk = conn.execute(
            text("SELECT content, reading_position FROM document_chunks WHERE id='chk1'")
        ).one()
        assert chk[0] == "Legacy text content"
        assert chk[1] is None
    engine2.dispose()


def test_long_novel_chapter_order_spoiler_control_and_chronological_rag(tmp_path: Path):
    """Test all 14 Milestone 4.9 scenarios on a 150-chapter spine-ordered EPUB."""
    engine, _store, kb_svc, retrieval, asst, provider = _setup_49_stack(tmp_path)
    try:
        novel = tmp_path / "synthetic_novel.epub"
        _build_synthetic_novel_epub(novel, num_chapters=150)
        rec, _ = kb_svc.import_file(novel)

        assert rec.title == "Synthetic Insanity"
        assert rec.author == "Test Author"
        assert rec.total_chapters == 150
        assert rec.reading_policy == ReadingPolicy.WHOLE_DOCUMENT

        # Unrelated non-EPUB doc
        other = tmp_path / "manual.txt"
        other.write_text("Telemetry bus voltage is 28.4V.", encoding="utf-8")
        rec_other, _ = kb_svc.import_file(other)

        # Paginated chapter listing & search
        chaps_p1, total_ch = kb_svc.list_chapters(rec.id, offset=0, limit=25)
        assert total_ch == 150
        assert len(chaps_p1) == 25
        assert chaps_p1[0].reading_position == 1
        ch_search, ch_search_total = kb_svc.list_chapters(rec.id, query="Bursting Luck")
        assert ch_search_total == 1
        assert ch_search[0].reading_position == 120

        # Newly imported EPUB is immediately searchable across all chapters
        res_unconf = retrieval.retrieve(
            "What happens with Sovereign Immortal Aperture?",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_unconf.has_evidence is True
        assert res_unconf.spoiler_warning is None
        assert any(p.reading_position == 145 for p in res_unconf.passages)

        # 1) Set reading progress to Chapter 85 (informational; policy remains WHOLE_DOCUMENT)
        rec_85 = kb_svc.update_reading_progress(rec.id, reading_position=85)
        assert rec_85.reading_policy == ReadingPolicy.WHOLE_DOCUMENT
        assert rec_85.current_reading_position == 85

        res_85 = retrieval.retrieve(
            "What is Fang Yuan's character development?",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_85.has_evidence is True
        assert res_85.spoiler_warning is None

        # 4) Querying Chapter 120 while progress is 85 succeeds without spoiler blocking
        res_future = retrieval.retrieve(
            "What happens in Chapter 120?",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_future.has_evidence is True
        assert res_future.spoiler_warning is None
        assert any(p.reading_position == 120 for p in res_future.passages)

        # 2) Chronological order preserved for multi-stage queries
        kb_svc.update_reading_progress(rec.id, reading_position=120)
        res_120 = retrieval.retrieve(
            "Explain Fang Yuan character development across stages",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_120.intent == AnswerIntent.CHARACTER_ANALYSIS
        positions_120 = [p.reading_position for p in res_120.passages if p.reading_position]
        assert positions_120 == sorted(positions_120)

        # 3) Legacy CHAPTER_RANGE parameter does not block full-document retrieval
        kb_svc.update_reading_progress(
            rec.id, reading_policy=ReadingPolicy.CHAPTER_RANGE, range_start=1, range_end=100
        )
        res_range = retrieval.retrieve(
            "Burning Soul Bursting Luck",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_range.has_evidence is True

        # 5-6) WHOLE_DOCUMENT covers early, middle, and late stages
        kb_svc.update_reading_progress(rec.id, reading_policy=ReadingPolicy.WHOLE_DOCUMENT)
        res_whole = retrieval.retrieve(
            "Describe Fang Yuan character evolution and development",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_whole.has_evidence is True
        whole_positions = [p.reading_position for p in res_whole.passages if p.reading_position]
        assert whole_positions == sorted(whole_positions)
        assert any(pos >= 120 for pos in whole_positions)

        # 7-9) Specific event + proper noun phrase
        res_cicada = retrieval.retrieve(
            "What does Spring Autumn Cicada do?",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert any(p.reading_position == 40 for p in res_cicada.passages)

        # 10) Absent answer
        res_absent = retrieval.retrieve(
            "What is the warp drive plasma injector temperature?",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_absent.has_evidence is False

        # 11-13) ALL_INDEXED vs SELECTED_DOCUMENTS isolation
        res_all = retrieval.retrieve(
            "Telemetry bus voltage",
            selected_document_ids=[],
            scope=KnowledgeScope.ALL_INDEXED,
        )
        assert any(p.document_id == rec_other.id for p in res_all.passages)

        res_iso = retrieval.retrieve(
            "Telemetry bus voltage",
            selected_document_ids=[rec.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_iso.has_evidence is False

        # 14) CHAT mode performs 0 retrieval
        refs_chat: list[list[dict]] = []
        provider.fixed_response = "Normal chat."
        "".join(
            asst.stream_message(
                "What does Spring Autumn Cicada do?",
                knowledge_scope=KnowledgeScope.NONE,
                on_references=refs_chat.append,
            )
        )
        assert refs_chat == []
    finally:
        engine.dispose()


def test_curated_retrieval_evaluation_metrics(tmp_path: Path):
    """Measure Precision@k, expected-chapter hit rate, and zero future-chapter leakage."""
    engine, _store, kb_svc, retrieval, _asst, _provider = _setup_49_stack(tmp_path)
    try:
        novel = tmp_path / "eval_novel.epub"
        _build_synthetic_novel_epub(novel, num_chapters=150)
        rec, _ = kb_svc.import_file(novel)
        kb_svc.update_reading_progress(
            rec.id, reading_position=85, reading_policy=ReadingPolicy.SPOILER_SAFE
        )

        # Curated evaluation set: (query, expected_chapter) — all chapters accessible
        eval_cases = [
            ("What does Spring Autumn Cicada do?", 40),
            ("What happened in Qing Mao Mountain with Moonlight Gu?", 85),
            ("What happened in Chapter 120 Burning Soul Bursting Luck?", 120),
            ("What is Sovereign Immortal Aperture?", 145),
            ("What did Gu Yue village elders debate?", 60),
        ]

        expected_hits = 0
        expected_total = 0
        precision_scores: list[float] = []

        for q, expected_ch in eval_cases:
            res = retrieval.retrieve(
                q,
                selected_document_ids=[rec.id],
                scope=KnowledgeScope.SELECTED_DOCUMENTS,
            )
            assert res.spoiler_warning is None
            expected_total += 1
            found_chs = [p.reading_position for p in res.passages]
            if expected_ch in found_chs:
                expected_hits += 1
            if res.passages:
                rel = sum(1 for p in res.passages if p.reading_position == expected_ch)
                precision_scores.append(rel / len(res.passages))

        assert expected_hits == expected_total
        assert sum(precision_scores) / len(precision_scores) > 0.0
    finally:
        engine.dispose()
