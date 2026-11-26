"""Milestone 4.10.1 tests: Full-knowledge access, spoiler restriction removal, literary analysis, and multi-stage retrieval."""

import zipfile
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import ChatRequest, ConversationSummary
from orion.application.knowledge.contracts import (
    KnowledgeLimits,
    KnowledgeScope,
    ReadingPolicy,
)
from orion.application.knowledge.retrieval import (
    DocumentRetrievalService,
    KnowledgeService,
    build_rag_prompt,
    validate_citations,
)
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.knowledge.extractor import LocalDocumentExtractor
from orion.infrastructure.knowledge.repository import SqliteKnowledgeStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.tools.workspace_tools import build_workspace_tool_system


def _make_epub(path: Path, chapters: list[tuple[str, str]]) -> None:
    container_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles>"
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    manifest_items = [
        f'<item id="ch{i}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(1, len(chapters) + 1)
    ]
    spine_items = [f'<itemref idref="ch{i}"/>' for i in range(1, len(chapters) + 1)]
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Full Access Novel</dc:title><dc:creator>Author</dc:creator>"
        "</metadata>"
        f"<manifest>{''.join(manifest_items)}</manifest>"
        f"<spine>{''.join(spine_items)}</spine>"
        "</package>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf)
        for i, (title, body) in enumerate(chapters, start=1):
            xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                f"<h1>{title}</h1><p>{body}</p></body></html>"
            )
            zf.writestr(f"OEBPS/ch{i}.xhtml", xhtml)


def _setup(tmp_path: Path, provider: FakeModelProvider | None = None):
    db_path = tmp_path / "orion_4101.db"
    cfg = Config("/home/ding/Projects/ORION/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)
    store = SqliteKnowledgeStore(sf)
    limits = KnowledgeLimits()
    kb_svc = KnowledgeService(LocalDocumentExtractor(limits=limits), store, limits=limits)
    retrieval = DocumentRetrievalService(store, limits=limits)
    conv_store = SqliteConversationStore(sf)
    settings_store = SqliteSettingsStore(sf)
    prov = provider or FakeModelProvider(fixed_response="Grounded answer [S1].")
    asst = AssistantService(prov, conv_store, settings_store, retrieval_service=retrieval)
    return engine, sf, store, kb_svc, retrieval, conv_store, asst, prov


def test_complete_epub_retrieval_regardless_of_reading_progress_and_legacy_policy(tmp_path: Path):
    """Tests 1, 2, 7, 8, 11: full EPUB retrieval, future chapters, legacy policy, and persistence."""
    engine, sf, _store, kb_svc, retrieval, conv_store, asst, _prov = _setup(tmp_path)
    try:
        chapters = [(f"Chapter {i}", f"Standard cultivation event {i}.") for i in range(1, 130)]
        chapters[9] = ("Chapter 10", "Fang Yuan refined Moonlight Gu in Gu Yue Village.")
        chapters[84] = ("Chapter 85", "Fang Yuan defeated Fang Zheng in the clan arena.")
        chapters[119] = (
            "Chapter 120",
            "Ying Wu Xie used Burning Soul Bursting Luck against Heavenly Court.",
        )
        chapters[128] = (
            "Chapter 129",
            "Ending revelation: Fang Yuan refined Heaven Refining Demon Venerable Gu.",
        )
        epub_path = tmp_path / "novel.epub"
        _make_epub(epub_path, chapters)
        rec, _ = kb_svc.import_file(epub_path)
        assert rec.reading_policy == ReadingPolicy.WHOLE_DOCUMENT

        # Simulate legacy spoiler_safe row in SQLite without re-indexing
        with sf() as s, s.begin():
            s.execute(
                text(
                    "UPDATE document_sources SET reading_policy='spoiler_safe', "
                    "current_reading_position=10, display_reading_chapter=10 WHERE id=:id"
                ),
                {"id": rec.id},
            )
        reloaded = next(x for x in kb_svc.list_sources() if x.id == rec.id)
        assert reloaded.reading_policy == ReadingPolicy.WHOLE_DOCUMENT
        assert reloaded.display_reading_chapter == 10  # informational progress preserved!

        # Future chapter 120 and ending chapter 129 are both retrievable
        res_120 = retrieval.retrieve(
            "What happened in Chapter 120 Burning Soul Bursting Luck?",
            [rec.id],
            KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_120.has_evidence and res_120.spoiler_warning is None
        assert any(p.display_chapter_number == 120 for p in res_120.passages)

        res_end = retrieval.retrieve(
            "What is the ending revelation about Heaven Refining Demon Venerable?",
            [rec.id],
            KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_end.has_evidence and res_end.spoiler_warning is None
        assert any(p.display_chapter_number == 129 for p in res_end.passages)

        # Changing reading progress does not restrict retrieval
        kb_svc.update_reading_progress(rec.id, display_chapter=5)
        res_still = retrieval.retrieve(
            "What happened in Chapter 120?",
            [rec.id],
            KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_still.has_evidence and res_still.passages[0].display_chapter_number == 120

        # Conversation persistence intact
        conv = conv_store.create_conversation(title="Novel QA")
        list(
            asst.stream_message(
                ChatRequest(
                    conversation_id=conv.id,
                    client_request_id="req-4101-1",
                    content="What happened in Chapter 120?",
                ),
                knowledge_scope=KnowledgeScope.SELECTED_DOCUMENTS,
                selected_document_ids=(rec.id,),
            )
        )
        saved: ConversationSummary = next(
            c for c in conv_store.list_conversations() if c.id == conv.id
        )
        assert saved.knowledge_scope == "selected_documents"
        assert list(saved.selected_document_ids) == [rec.id]
    finally:
        engine.dispose()


def test_multistage_character_comparison_timeline_and_relationships(tmp_path: Path):
    """Tests 3, 4, 5, 6: chronological early-vs-late stages, timeline, and relationship queries."""
    engine, _sf, _store, kb_svc, retrieval, _cs, _asst, _prov = _setup(tmp_path)
    try:
        chapters = [(f"Chapter {i}", f"Routine filler passage {i}.") for i in range(1, 101)]
        chapters[2] = (
            "Chapter 3",
            "Early stage: Fang Yuan and Fang Zheng argued over their brotherly relationship in Gu Yue clan.",
        )
        chapters[39] = (
            "Chapter 40",
            "Middle stage: Fang Yuan's ruthless philosophy evolved after leaving Qing Mao Mountain, and his relationship with Fang Zheng turned hostile.",
        )
        chapters[74] = (
            "Chapter 75",
            "Late-mid stage: Fang Zheng joined Immortal Crane Sect while Fang Yuan pursued eternal life.",
        )
        chapters[99] = (
            "Chapter 100",
            "Late stage: Fang Yuan's final philosophy embraced complete self-interest over Fang Zheng's righteousness.",
        )
        epub_path = tmp_path / "arc.epub"
        _make_epub(epub_path, chapters)
        rec, _ = kb_svc.import_file(epub_path)

        for q in (
            "Explain Fang Yuan's complete character development and philosophy.",
            "How did his relationship with Fang Zheng evolve?",
            "Compare his early and late motivations.",
            "Give a timeline of Fang Yuan and Fang Zheng.",
        ):
            res = retrieval.retrieve(q, [rec.id], KnowledgeScope.SELECTED_DOCUMENTS, top_k=4)
            assert res.has_evidence and res.spoiler_warning is None
            chs = [p.display_chapter_number for p in res.passages if p.display_chapter_number]
            assert chs == sorted(chs), f"Expected chronological order for {q}, got {chs}"
            assert min(chs) <= 40 and max(chs) >= 75, f"Expected multi-stage coverage, got {chs}"
    finally:
        engine.dispose()


def test_uncensored_literary_prompt_citations_insufficient_evidence_and_no_toolrouter(
    tmp_path: Path,
):
    """Tests 9, 10, 12, 13: prompt policy, citation validation, insufficient evidence, multi-doc, 0 ToolRouter calls."""
    reply = "Fang Yuan betrayed his clan [S1] and fabricated [S99] was not in excerpts."
    prov = FakeModelProvider(fixed_response=reply)
    engine, _sf, _store, kb_svc, retrieval, conv_store, asst, _ = _setup(tmp_path, prov)
    try:
        ws = tmp_path / "ws"
        ws.mkdir()
        _reg, router, _ws = build_workspace_tool_system(ws)
        dispatches = 0
        orig_route = router.route

        def _spy(*args, **kwargs):
            nonlocal dispatches
            dispatches += 1
            return orig_route(*args, **kwargs)

        router.route = _spy  # type: ignore[method-assign]

        doc1 = tmp_path / "notes1.txt"
        doc1.write_text("Betrayal and manipulation in the Northern Plains arc.", encoding="utf-8")
        doc2 = tmp_path / "notes2.txt"
        doc2.write_text("Violence and death during the Imperial Court contest.", encoding="utf-8")
        r1, _ = kb_svc.import_file(doc1)
        r2, _ = kb_svc.import_file(doc2)

        # Multi-doc retrieval
        res_multi = retrieval.retrieve(
            "What happened with betrayal and Imperial Court?",
            [],
            KnowledgeScope.ALL_INDEXED,
        )
        assert res_multi.has_evidence
        doc_ids = {p.document_id for p in res_multi.passages}
        assert {r1.id, r2.id}.issubset(doc_ids)

        # Uncensored literary prompt without spoiler warnings
        prompt = build_rag_prompt("Discuss the betrayal and violence.", res_multi)
        assert "Do NOT withhold spoilers" in prompt
        assert "straightforwardly and objectively" in prompt
        assert "Spoiler-safe" not in prompt

        # Nonexistent [S99] citation stripped, [S1] preserved
        cleaned, valid_ids, invalid_ids = validate_citations(reply, res_multi.passages)
        assert "[S1]" in cleaned and "[S99]" not in cleaned
        assert valid_ids == ["S1"] and invalid_ids == ["S99"]

        # Insufficient evidence
        conv = conv_store.create_conversation(title="Check")
        out_missing = "".join(
            asst.stream_message(
                ChatRequest(
                    conversation_id=conv.id,
                    client_request_id="req-miss-1",
                    content="What is the orbital eccentricity of Ganymede?",
                ),
                knowledge_scope=KnowledgeScope.ALL_INDEXED,
            )
        )
        assert "Insufficient evidence" in out_missing
        assert dispatches == 0
    finally:
        engine.dispose()
