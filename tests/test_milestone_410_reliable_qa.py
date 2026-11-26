"""Milestone 4.10 tests: persistent conversation knowledge context, follow-ups, [S1] citations, and 52-query eval."""

import time
import zipfile
from pathlib import Path

from alembic.config import Config
from pypdf import PdfWriter
from sqlalchemy import text

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.knowledge.contracts import KnowledgeLimits, KnowledgeScope, ReadingPolicy
from orion.application.knowledge.query_resolver import KnowledgeQueryContextResolver
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


def _make_pdf(path: Path, pages: list[str]) -> None:
    writer = PdfWriter()
    for page_text in pages:
        page = writer.add_blank_page(width=612, height=792)
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        font_dict = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font_dict)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        escaped = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as fh:
        writer.write(fh)


def _make_epub(path: Path, chapters: list[tuple[str, str]]) -> None:
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
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine = []
        nav = []
        for idx, (title, body) in enumerate(chapters, start=1):
            fname = f"ch_{idx:03d}.xhtml"
            manifest.append(
                f'<item id="c{idx}" href="{fname}" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="c{idx}"/>')
            nav.append(f'<li><a href="{fname}">{title}</a></li>')
            zf.writestr(
                f"OEBPS/{fname}", f"<html><body><h1>{title}</h1><p>{body}</p></body></html>"
            )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Test Book</dc:title><dc:creator>Author</dc:creator>"
            "</metadata>"
            f"<manifest>{''.join(manifest)}</manifest>"
            f"<spine>{''.join(spine)}</spine></package>",
        )
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<body><nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>',
        )


def _setup_410(tmp_path: Path):
    db_path = tmp_path / "orion_410.db"
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
    return db_path, engine, store, kb_svc, retrieval, conv_store, asst, provider


def test_migration_0005_to_0006_and_conversation_context_persistence(tmp_path: Path):
    """Verify 0005 -> 0006 preserves conversations and per-conversation scope survives restart."""
    db_path = tmp_path / "mig_0006.db"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(alembic_cfg, "0005")

    eng = create_db_engine(db_path)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO conversations (id, title, next_sequence, created_at, updated_at) "
                "VALUES ('conv_old', 'Legacy Chat', 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        )
    eng.dispose()
    command.upgrade(alembic_cfg, "0006")

    eng2 = create_db_engine(db_path)
    with eng2.connect() as conn:
        row = conn.execute(
            text(
                "SELECT title, knowledge_scope, selected_document_ids_json FROM conversations WHERE id='conv_old'"
            )
        ).one()
        assert row == ("Legacy Chat", "none", "[]")
    eng2.dispose()

    # Now test full conversation scope isolation + follow-up + restart persistence
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _db2, engine, _store, kb_svc, retrieval, _conv_store, asst, provider = _setup_410(app_dir)
    try:
        os_pdf = tmp_path / "os_notes.pdf"
        _make_pdf(
            os_pdf,
            [
                "Paging divides memory into fixed-size frames and avoids external fragmentation.",
                "Disadvantages of paging include internal fragmentation and page table overhead.",
                "Segmentation divides memory into variable-sized logical segments like code and stack.",
            ],
        )
        rec_os, _ = kb_svc.import_file(os_pdf)

        bio_txt = tmp_path / "biology.txt"
        bio_txt.write_text(
            "Mitochondria generate ATP via oxidative phosphorylation.", encoding="utf-8"
        )
        _rec_bio, _ = kb_svc.import_file(bio_txt)

        # Conv A: Selected Sources = os_notes.pdf
        conv_a = asst.create_new_conversation("OS Study")
        provider.fixed_response = "Paging uses fixed-size frames [S1]."
        "".join(
            asst.stream_message(
                "Explain paging.",
                selected_document_ids=[rec_os.id],
                knowledge_scope=KnowledgeScope.SELECTED_DOCUMENTS,
            )
        )

        # Follow-up in Conv A WITHOUT repeating selected_document_ids or scope!
        refs_fu: list[list[dict]] = []
        provider.fixed_response = "Disadvantages include internal fragmentation [S1] and [S99]."
        ans_fu = "".join(
            asst.stream_message(
                "What are its disadvantages?",
                on_references=refs_fu.append,
            )
        )
        assert refs_fu and refs_fu[0]
        assert all(r["document_id"] == rec_os.id for r in refs_fu[0])
        assert any("internal fragmentation" in r["excerpt"] for r in refs_fu[0])
        assert "[S1]" in ans_fu

        # Verify persisted message stripped invalid [S99] while keeping valid [S1]
        msgs_a = asst.get_conversation_messages(conv_a.id)
        last_asst = [m for m in msgs_a if m.role == "assistant"][-1]
        assert "[S1]" in (last_asst.content or "")
        assert "[S99]" not in (last_asst.content or "")

        # Conv B: starts with default 'none' (normal chat) and stays isolated
        conv_b = asst.create_new_conversation("Normal Chat")
        assert conv_b.knowledge_scope == "none"
        refs_b: list[list[dict]] = []
        provider.fixed_response = "Just general chat."
        "".join(asst.stream_message("Hello!", on_references=refs_b.append))
        assert refs_b == []

        # Restart simulation: fresh AssistantService restores Conv A's persisted scope
        session_factory2 = create_session_factory(engine)
        conv_store2 = SqliteConversationStore(session_factory2)
        settings2 = SqliteSettingsStore(session_factory2)
        asst2 = AssistantService(provider, conv_store2, settings2, retrieval_service=retrieval)
        restored_a = asst2.select_conversation(conv_a.id)
        assert restored_a.knowledge_scope == "selected_documents"
        assert restored_a.selected_document_ids == (rec_os.id,)

        # Source Inspector adjacent context
        adj = asst2.get_passage_context(rec_os.id, 0)
        assert "Paging" in adj
    finally:
        engine.dispose()


def test_query_resolver_followups_topic_changes_and_ambiguity():
    """Verify KnowledgeQueryContextResolver handles pronouns, ordinals, topic shifts, and ambiguity."""
    resolver = KnowledgeQueryContextResolver()

    # Pronoun follow-up
    r1 = resolver.resolve("What are its disadvantages?", recent_user_messages=["Explain paging."])
    assert r1.is_follow_up and not r1.needs_clarification
    assert "paging" in r1.resolved_query.lower()

    # Example follow-up
    r2 = resolver.resolve("Give me an example.", recent_user_messages=["Explain paging."])
    assert r2.is_follow_up and "paging" in r2.resolved_query.lower()

    # Comparison follow-up
    r3 = resolver.resolve(
        "How is it different from segmentation?", recent_user_messages=["Explain paging."]
    )
    assert r3.is_follow_up and "paging" in r3.resolved_query.lower()

    # Topic change (standalone question without ellipsis)
    r4 = resolver.resolve(
        "What is the boiling point of liquid oxygen?",
        recent_user_messages=["Explain paging."],
    )
    assert not r4.is_follow_up
    assert "paging" not in r4.resolved_query.lower()

    # Genuinely ambiguous bare reference with no prior topic -> clarification
    r5 = resolver.resolve("Explain the second point.", recent_user_messages=[])
    assert r5.needs_clarification and r5.clarification_message is not None


def test_citation_validation_unit():
    """Verify [S1]..[Sk] citation validation removes nonexistent IDs."""
    from orion.application.knowledge.contracts import RetrievedPassage

    passages = [
        RetrievedPassage(
            chunk_id="c1",
            document_id="d1",
            display_name="doc.pdf",
            source_path="/tmp/doc.pdf",
            chunk_index=0,
            content="Alpha fact.",
            excerpt="Alpha fact.",
            rank=-1.0,
        )
    ]
    cleaned, valid, invalid = validate_citations("Fact [S1] and bogus [S4].", passages)
    assert valid == ["S1"]
    assert invalid == ["S4"]
    assert "[S1]" in cleaned and "[S4]" not in cleaned


def test_milestone_410_52_query_evaluation_benchmark(tmp_path: Path):
    """Run a 52-query evaluation dataset across PDF, EPUB, TXT, MD, and CSV measuring all 9 metrics."""
    _db, engine, _store, kb_svc, retrieval, _cs, _asst, _prov = _setup_410(tmp_path)
    try:
        pdf_os = tmp_path / "os.pdf"
        _make_pdf(
            pdf_os,
            [
                "Virtual memory uses paging with fixed-size 4 KB frames and page tables.",
                "Paging disadvantages include internal fragmentation and TLB miss latency.",
                "Segmentation uses variable-size logical segments and can suffer external fragmentation.",
            ],
        )
        rec_os, _ = kb_svc.import_file(pdf_os)

        md_eng = tmp_path / "propulsion.md"
        md_eng.write_text(
            "# Engine Spec\nThe Raptor engine burns methalox with 350 seconds vacuum Isp.\n"
            "Turbopump spinup requires 1.4 seconds before main chamber ignition.\n",
            encoding="utf-8",
        )
        rec_eng, _ = kb_svc.import_file(md_eng)

        txt_bio = tmp_path / "cell_bio.txt"
        txt_bio.write_text(
            "Mitochondria produce ATP through oxidative phosphorylation in eukaryotic cells.\n"
            "Chloroplasts perform photosynthesis using chlorophyll in plant cells.\n",
            encoding="utf-8",
        )
        rec_bio, _ = kb_svc.import_file(txt_bio)

        # Contradictory / distinct sensor logs
        csv_log = tmp_path / "sensors.csv"
        csv_log.write_text("sensor,reading\nalpha,98.6\nbeta,102.4\n", encoding="utf-8")
        rec_csv, _ = kb_svc.import_file(csv_log)

        epub_novel = tmp_path / "novel.epub"
        _make_epub(
            epub_novel,
            [
                ("Chapter 1", "Fang Yuan awakened in Gu Yue village with C-grade aptitude."),
                ("Chapter 2", "Spring Autumn Cicada allows rebirth through the River of Time."),
                ("Chapter 3", "Moonlight Gu emits a crescent blade of blue light."),
                ("Chapter 4", "Secret future event in Chapter 4: Sovereign Immortal Aperture."),
            ],
        )
        rec_epub, _ = kb_svc.import_file(epub_novel)
        kb_svc.update_reading_progress(
            rec_epub.id, display_chapter=3, reading_policy=ReadingPolicy.SPOILER_SAFE
        )

        resolver = KnowledgeQueryContextResolver()

        # 52 curated queries: (query, prior_user_q_or_None, expected_doc_id_or_None)
        queries: list[tuple[str, str | None, str | None]] = [
            # Factual (12)
            ("What frame size does paging use?", None, rec_os.id),
            ("What fuel does the Raptor engine burn?", None, rec_eng.id),
            ("What is Raptor vacuum Isp?", None, rec_eng.id),
            ("How long does turbopump spinup take?", None, rec_eng.id),
            ("What organelle produces ATP?", None, rec_bio.id),
            ("What pigment do chloroplasts use?", None, rec_bio.id),
            ("What is sensor alpha reading?", None, rec_csv.id),
            ("What is sensor beta reading?", None, rec_csv.id),
            ("What aptitude did Fang Yuan awaken with?", None, rec_epub.id),
            ("What does Spring Autumn Cicada allow?", None, rec_epub.id),
            ("What does Moonlight Gu emit?", None, rec_epub.id),
            ("What does segmentation use?", None, rec_os.id),
            # Explanation (8)
            ("Explain paging in virtual memory.", None, rec_os.id),
            ("Explain oxidative phosphorylation in mitochondria.", None, rec_bio.id),
            ("Explain photosynthesis in chloroplasts.", None, rec_bio.id),
            ("Explain external fragmentation in segmentation.", None, rec_os.id),
            ("Explain TLB miss latency in paging.", None, rec_os.id),
            ("Explain Raptor turbopump spinup.", None, rec_eng.id),
            ("Explain Gu Yue village awakening.", None, rec_epub.id),
            ("Explain River of Time rebirth.", None, rec_epub.id),
            # Comparison / Synthesis (6)
            ("Compare paging and segmentation fragmentation.", None, rec_os.id),
            ("Compare mitochondria and chloroplasts.", None, rec_bio.id),
            ("Compare sensor alpha and sensor beta.", None, rec_csv.id),
            ("Compare internal fragmentation and external fragmentation.", None, rec_os.id),
            ("Summarize Raptor engine ignition and Isp.", None, rec_eng.id),
            ("Summarize virtual memory paging and segmentation.", None, rec_os.id),
            # Follow-ups (8)
            ("What are its disadvantages?", "Explain paging.", rec_os.id),
            ("How is it different from segmentation?", "Explain paging.", rec_os.id),
            ("What is its spinup time?", "Tell me about Raptor engine.", rec_eng.id),
            ("What does it produce?", "Explain mitochondria.", rec_bio.id),
            ("What light does it emit?", "What is Moonlight Gu?", rec_epub.id),
            ("What is its reading?", "Check sensor alpha.", rec_csv.id),
            ("Give me an example of fragmentation.", "Explain paging.", rec_os.id),
            ("Why is it used in plant cells?", "Explain chloroplasts.", rec_bio.id),
            # Paraphrased & Proper names (8)
            ("How do plant cells capture light energy?", None, rec_bio.id),
            ("What causes wasted space inside fixed frames?", None, rec_os.id),
            ("Which propulsion unit uses methane and oxygen?", None, rec_eng.id),
            ("Who had C-grade talent in Gu Yue?", None, rec_epub.id),
            ("What Gu creates a blue crescent blade?", None, rec_epub.id),
            ("What time-travel Gu does Fang Yuan possess?", None, rec_epub.id),
            ("Which memory scheme avoids external fragmentation?", None, rec_os.id),
            ("How much power/ATP comes from mitochondria?", None, rec_bio.id),
            # EPUB chapter-specific (3)
            ("What happens in Chapter 1?", None, rec_epub.id),
            ("What happens in Chapter 2?", None, rec_epub.id),
            ("What happens in Chapter 3?", None, rec_epub.id),
            # Spoiler boundary + Ambiguous + Missing evidence (7)
            ("What happens in Chapter 4?", None, None),
            ("What is Sovereign Immortal Aperture?", None, None),
            ("Explain the second point.", None, None),
            ("What is the orbital velocity of Neptune?", None, None),
            ("What is the boiling point of tungsten?", None, None),
            ("Who won the 1998 chess championship?", None, None),
            ("What is the melting point of palladium?", None, None),
        ]
        assert len(queries) == 52

        t0 = time.monotonic()
        hits = 0
        answerable = 0
        followup_ok = 0
        followup_tot = 0
        insuff_ok = 0
        insuff_tot = 0
        future_leaks = 0
        cite_ok = 0
        cite_tot = 0
        p4_scores: list[float] = []
        mrr_scores: list[float] = []

        for q, prior_q, exp_doc_id in queries:
            resolved = resolver.resolve(q, recent_user_messages=[prior_q] if prior_q else [])
            if resolved.needs_clarification:
                insuff_tot += 1
                insuff_ok += 1
                continue
            res = retrieval.retrieve(resolved.resolved_query, (), scope=KnowledgeScope.ALL_INDEXED)
            for p in res.passages:
                cite_tot += 1
                if p.document_id and p.display_name:
                    cite_ok += 1
                if p.document_id == rec_epub.id and (p.display_chapter_number or 0) > 3:
                    future_leaks += 1
            if exp_doc_id is None:
                insuff_tot += 1
                if not res.has_evidence:
                    insuff_ok += 1
            else:
                answerable += 1
                found_docs = [p.document_id for p in res.passages]
                rel = sum(1 for d in found_docs if d == exp_doc_id)
                if rel > 0:
                    hits += 1
                p4_scores.append(rel / len(found_docs) if found_docs else 0.0)
                rr = 0.0
                for rank_i, d in enumerate(found_docs, start=1):
                    if d == exp_doc_id:
                        rr = 1.0 / rank_i
                        break
                mrr_scores.append(rr)
                if prior_q is not None:
                    followup_tot += 1
                    if rel > 0:
                        followup_ok += 1

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        print(
            f"\n52-QUERY EVAL: Hits={hits}/{answerable} P@4={sum(p4_scores) / len(p4_scores):.3f} "
            f"MRR={sum(mrr_scores) / len(mrr_scores):.3f} FollowUps={followup_ok}/{followup_tot} "
            f"Citations={cite_ok}/{cite_tot} Insuff={insuff_ok}/{insuff_tot} "
            f"FutureLeaks={future_leaks} Time={elapsed_ms:.1f}ms"
        )
        assert future_leaks == 0
        assert insuff_ok == insuff_tot
        assert hits >= 40
        assert followup_ok == followup_tot
        assert cite_ok == cite_tot
        assert sum(mrr_scores) / len(mrr_scores) >= 0.80
        assert elapsed_ms < 5000.0
        # Verify prompt generation stays within bounded context
        sample_res = retrieval.retrieve("Explain paging.", (), scope=KnowledgeScope.ALL_INDEXED)
        assert sample_res.total_chars <= KnowledgeLimits().max_retrieved_context_chars
        assert "[S1]" in build_rag_prompt("Explain paging.", sample_res)
    finally:
        engine.dispose()
