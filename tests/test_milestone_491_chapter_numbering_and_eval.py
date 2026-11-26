"""Milestone 4.9.1 tests: EPUB chapter numbering consistency, spoiler boundaries, and comparative eval."""

import zipfile
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from orion.application.knowledge.contracts import (
    KnowledgeLimits,
    KnowledgeScope,
    ReadingPolicy,
)
from orion.application.knowledge.retrieval import (
    DocumentRetrievalService,
    KnowledgeService,
    build_fts_query,
    build_rag_prompt,
)
from orion.infrastructure.knowledge.extractor import LocalDocumentExtractor
from orion.infrastructure.knowledge.repository import SqliteKnowledgeStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory


def _setup_491(tmp_path: Path):
    db_path = tmp_path / "orion_491.db"
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
    return engine, store, kb_svc, retrieval


def _build_epub_with_frontmatter(
    path: Path,
    *,
    frontmatter: list[tuple[str, str]],
    chapters: list[tuple[str, str]],
    reverse_filenames: bool = True,
) -> None:
    """Build an EPUB with arbitrary frontmatter + chapters in explicit OPF spine order."""
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
        all_docs = [(t, b, True) for t, b in frontmatter] + [(t, b, False) for t, b in chapters]
        total = len(all_docs)
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        spine = []
        nav = []
        for idx, (title, body, _is_front) in enumerate(all_docs, start=1):
            prefix = f"{total + 1 - idx:04d}" if reverse_filenames else f"{idx:04d}"
            fname = f"sec_{prefix}.xhtml"
            manifest.append(
                f'<item id="s{idx}" href="{fname}" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="s{idx}"/>')
            if title:
                nav.append(f'<li><a href="{fname}">{title}</a></li>')
                heading_html = f"<h1>{title}</h1>"
            else:
                heading_html = ""
            zf.writestr(f"OEBPS/{fname}", f"<html><body>{heading_html}<p>{body}</p></body></html>")

        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Numbering Test Book</dc:title><dc:creator>Author</dc:creator>"
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


def test_epub_numbering_variants_and_2334_chapter_offset(tmp_path: Path):
    """Verify display_chapter_number vs reading_position across frontmatter, missing/duplicate titles, and 2334+1 spine."""
    engine, _store, kb_svc, _ret = _setup_491(tmp_path)
    try:
        # 1) Cover + Introductory material + duplicate titles + missing heading
        epub_a = tmp_path / "variants.epub"
        _build_epub_with_frontmatter(
            epub_a,
            frontmatter=[
                ("Cover", "Cover art credits."),
                ("Information", "Author notes and synopsis."),
            ],
            chapters=[
                ("Chapter 1", "Beginning in Qing Mao Mountain."),
                ("", "Untitled interlude paragraph."),
                ("Duplicate Title", "First occurrence of duplicate title."),
                ("Duplicate Title", "Second occurrence of duplicate title."),
                ("Chapter 85: Hot-blooded and cold-blooded", "Fang Yuan refined Moonlight Gu."),
            ],
        )
        rec_a, _ = kb_svc.import_file(epub_a)
        chaps_a, total_a = kb_svc.list_chapters(rec_a.id, limit=50)
        assert total_a == 7
        assert chaps_a[0].display_chapter_number is None  # Cover
        assert chaps_a[1].display_chapter_number is None  # Information
        assert chaps_a[2].display_chapter_number == 1 and chaps_a[2].reading_position == 3
        assert chaps_a[3].display_chapter_number is None and chaps_a[3].reading_position == 4
        assert chaps_a[4].reading_position == 5 and chaps_a[5].reading_position == 6
        assert chaps_a[6].display_chapter_number == 85 and chaps_a[6].reading_position == 7

        # 2) 2,334 chapters + 1 frontmatter spine entry (mimicking Reverend Insanity)
        epub_big = tmp_path / "big_2335.epub"
        _build_epub_with_frontmatter(
            epub_big,
            frontmatter=[("Information", "Reverend Insanity synopsis.")],
            chapters=[(f"Chapter {i}", f"Content of chapter {i}.") for i in range(1, 2335)],
            reverse_filenames=True,
        )
        rec_big, _ = kb_svc.import_file(epub_big)
        assert rec_big.total_chapters == 2335
        ch85, _ = kb_svc.list_chapters(rec_big.id, query="85")
        assert any(c.display_chapter_number == 85 and c.reading_position == 86 for c in ch85)
        ch1212, _ = kb_svc.list_chapters(rec_big.id, query="1212")
        assert any(c.display_chapter_number == 1212 and c.reading_position == 1213 for c in ch1212)
    finally:
        engine.dispose()


def test_spoiler_boundaries_exact_chapter_85_86_87_and_invalid_inputs(tmp_path: Path):
    """Verify Chapter 85 allows Ch 85 (pos 86), excludes Ch 86 (pos 87); advancing to 86 allows Ch 86 and excludes 87."""
    engine, store, kb_svc, retrieval = _setup_491(tmp_path)
    try:
        chapters = []
        for i in range(1, 91):
            if i == 85:
                chapters.append(
                    ("Chapter 85", "Unique token ALPHA85 appears in chapter eighty-five.")
                )
            elif i == 86:
                chapters.append(
                    ("Chapter 86", "Unique token BETA86 appears in chapter eighty-six.")
                )
            elif i == 87:
                chapters.append(
                    ("Chapter 87", "Unique token GAMMA87 appears in chapter eighty-seven.")
                )
            else:
                chapters.append((f"Chapter {i}", f"Routine cultivation event {i}."))

        epub_p = tmp_path / "boundary.epub"
        _build_epub_with_frontmatter(
            epub_p,
            frontmatter=[("Information", "Book information.")],
            chapters=chapters,
        )
        rec, _ = kb_svc.import_file(epub_p)

        # Set reading progress to display_chapter=85 -> resolves to reading_position=86
        rec_85 = kb_svc.update_reading_progress(rec.id, display_chapter=85)
        assert rec_85.display_reading_chapter == 85
        assert rec_85.current_reading_position == 86

        # Ch 85 allowed, Ch 86 excluded in SQL
        res_85 = retrieval.retrieve("ALPHA85", [rec.id], KnowledgeScope.SELECTED_DOCUMENTS)
        assert res_85.has_evidence and res_85.passages[0].display_chapter_number == 85

        res_86_blocked = retrieval.retrieve("BETA86", [rec.id], KnowledgeScope.SELECTED_DOCUMENTS)
        assert not res_86_blocked.has_evidence
        assert store.search_chunks('"beta86"', [rec.id], respect_reading_policy=True) == []

        # Future chapter warning
        res_warn = retrieval.retrieve(
            "What happens in Chapter 86?", [rec.id], KnowledgeScope.SELECTED_DOCUMENTS
        )
        assert not res_warn.has_evidence and res_warn.spoiler_warning is not None
        assert "85" in res_warn.spoiler_warning

        # Advance to display_chapter=86 -> Ch 86 allowed, Ch 87 excluded
        rec_86 = kb_svc.update_reading_progress(rec.id, display_chapter=86)
        assert rec_86.display_reading_chapter == 86
        assert rec_86.current_reading_position == 87

        res_86_ok = retrieval.retrieve("BETA86", [rec.id], KnowledgeScope.SELECTED_DOCUMENTS)
        assert res_86_ok.has_evidence and res_86_ok.passages[0].display_chapter_number == 86

        res_87_blocked = retrieval.retrieve("GAMMA87", [rec.id], KnowledgeScope.SELECTED_DOCUMENTS)
        assert not res_87_blocked.has_evidence

        # Invalid progress & empty/inverted ranges rejected
        with pytest.raises(ValueError):
            kb_svc.update_reading_progress(rec.id, display_chapter=0)
        with pytest.raises(ValueError):
            kb_svc.update_reading_progress(
                rec.id, reading_policy=ReadingPolicy.CHAPTER_RANGE, range_start=50, range_end=10
            )
    finally:
        engine.dispose()


def test_comparative_retrieval_evaluation_and_grounding_prompt(tmp_path: Path):
    """Measure Precision@4, Recall@4, MRR, Hit Rate, Future Leakage, and Grounding across 8 query types."""
    engine, store, kb_svc, retrieval = _setup_491(tmp_path)
    try:
        chapters = []
        for i in range(1, 131):
            if i == 10:
                chapters.append(
                    ("Chapter 10", "Fang Yuan awakened his C-grade aptitude in Gu Yue village.")
                )
            elif i == 40:
                chapters.append(
                    (
                        "Chapter 40",
                        "The Spring Autumn Cicada enabled Fang Yuan to rebirth from 500 years.",
                    )
                )
            elif i == 85:
                chapters.append(
                    (
                        "Chapter 85",
                        "Fang Yuan demonstrated cold-blooded ruthlessness refining Moonlight Gu.",
                    )
                )
            elif i == 110:
                chapters.append(
                    (
                        "Chapter 110",
                        "Gu Yue Fang Yuan escaped Qing Mao Mountain during the ice calamity.",
                    )
                )
            elif i == 125:
                chapters.append(
                    ("Chapter 125", "Southern Border caravan journey toward Shang Clan City.")
                )
            else:
                chapters.append((f"Chapter {i}", f"General cultivation narrative for section {i}."))

        epub_p = tmp_path / "eval_book.epub"
        _build_epub_with_frontmatter(
            epub_p, frontmatter=[("Information", "Preface")], chapters=chapters
        )
        rec, _ = kb_svc.import_file(epub_p)
        kb_svc.update_reading_progress(
            rec.id, display_chapter=100, reading_policy=ReadingPolicy.SPOILER_SAFE
        )

        # (query, expected_display_chapters, is_future_or_absent)
        dataset = [
            ("Who is Fang Yuan in Gu Yue village?", {10}, False),  # character
            ("What happened with Moonlight Gu?", {85}, False),  # specific event
            ("Describe Fang Yuan's character development", {10, 40, 85}, False),  # development
            ("Give a timeline of Fang Yuan's early stages", {10, 40, 85}, False),  # timeline
            ("What happened in Chapter 40?", {40}, False),  # chapter-specific
            ("What does Spring Autumn Cicada do?", {40}, False),  # proper-name
            ("How did Fang Yuan travel back 500 years?", {40}, False),  # paraphrased
            ("What happens in Chapter 125 Shang Clan City?", set(), True),  # future spoiler (>100)
            ("What is the orbital velocity of Mars?", set(), True),  # no evidence
        ]

        # BEFORE baseline: FTS5 query without policy enforcement
        before_leaks = 0
        for q, _exp, _fut in dataset:
            raw = store.search_chunks(
                build_fts_query(q),
                [rec.id],
                top_k=4,
                respect_reading_policy=False,
            )
            before_leaks += sum(1 for p in raw if (p.display_chapter_number or 0) > 100)

        # AFTER (Milestone 4.9.1)
        after_leaks = 0
        hits = 0
        answerable = 0
        precisions: list[float] = []
        recalls: list[float] = []
        mrrs: list[float] = []
        insufficient_ok = 0
        insufficient_total = 0

        for q, expected_chs, is_blocked_or_absent in dataset:
            res = retrieval.retrieve(q, [rec.id], KnowledgeScope.SELECTED_DOCUMENTS)
            for p in res.passages:
                if (p.display_chapter_number or 0) > 100:
                    after_leaks += 1
            if is_blocked_or_absent:
                insufficient_total += 1
                if not res.has_evidence:
                    insufficient_ok += 1
            else:
                answerable += 1
                found = [p.display_chapter_number for p in res.passages if p.display_chapter_number]
                rel_count = sum(1 for ch in found if ch in expected_chs)
                if rel_count > 0:
                    hits += 1
                precisions.append(rel_count / len(res.passages) if res.passages else 0.0)
                recalls.append(len(set(found) & expected_chs) / len(expected_chs))
                rr = 0.0
                for rank_i, ch in enumerate(found, start=1):
                    if ch in expected_chs:
                        rr = 1.0 / rank_i
                        break
                mrrs.append(rr)

        assert before_leaks > 0
        assert after_leaks == 0
        assert hits == answerable
        assert insufficient_ok == insufficient_total
        assert sum(mrrs) / len(mrrs) >= 0.7

        # Grounding prompt check for character development
        res_dev = retrieval.retrieve(
            "Describe Fang Yuan's character development",
            [rec.id],
            KnowledgeScope.SELECTED_DOCUMENTS,
        )
        prompt = build_rag_prompt("Describe Fang Yuan's character development", res_dev)
        assert "Spoiler-safe through Chapter 100" in prompt
        assert "Chapter 10" in prompt
        assert "not an exhaustive scan" in prompt
    finally:
        engine.dispose()
