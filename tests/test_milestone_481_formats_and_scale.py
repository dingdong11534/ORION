"""Milestone 4.8.1 tests for scalable ingestion, EPUB2/3, expanded formats, archive security, and RAG."""

import json
import tracemalloc
import zipfile
from pathlib import Path

import openpyxl
import pytest
from alembic.config import Config
from odf.opendocument import OpenDocumentSpreadsheet, OpenDocumentText
from odf.table import Table, TableCell, TableRow
from odf.text import H as OdfHeading
from odf.text import P as OdfParagraph
from pptx import Presentation

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import IngestionStatus, KnowledgeLimits, ReadingPolicy
from orion.application.knowledge.ports import (
    DocumentExtractionError,
    IngestionCancelledError,
    KnowledgeError,
)
from orion.application.knowledge.retrieval import DocumentRetrievalService, KnowledgeService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.knowledge.extractor import SUPPORTED_EXTENSIONS, LocalDocumentExtractor
from orion.infrastructure.knowledge.repository import SqliteKnowledgeStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)


def _setup_knowledge_stack(tmp_path: Path, limits: KnowledgeLimits | None = None):
    """Create isolated DB, KnowledgeService, RetrievalService, and AssistantService."""
    db_path = tmp_path / "orion_481.db"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(db_path)
    session_factory = create_session_factory(engine)
    store = SqliteKnowledgeStore(session_factory)
    eff_limits = limits or KnowledgeLimits()
    extractor = LocalDocumentExtractor(limits=eff_limits)
    knowledge_svc = KnowledgeService(extractor=extractor, store=store, limits=eff_limits)
    retrieval_svc = DocumentRetrievalService(store=store, limits=eff_limits)
    conv_store = SqliteConversationStore(session_factory)
    settings_store = SqliteSettingsStore(session_factory)
    provider = FakeModelProvider(simulated_delay=0.0)
    assistant = AssistantService(
        provider,
        conv_store,
        settings_store,
        retrieval_service=retrieval_svc,
    )
    return engine, store, extractor, knowledge_svc, retrieval_svc, assistant, provider


def _build_epub(
    path: Path,
    *,
    version: str = "3.0",
    drm_file: str | None = None,
    include_opf: bool = True,
    evil_entry: str | None = None,
) -> None:
    """Create a valid EPUB2 (NCX) or EPUB3 (nav) archive with non-alphabetical spine order."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        if drm_file:
            if drm_file == "META-INF/encryption.xml":
                zf.writestr(
                    drm_file,
                    '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
                    '<EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>'
                    "</EncryptedData></encryption>",
                )
            else:
                zf.writestr(drm_file, "<drm>locked</drm>")
        if evil_entry:
            zf.writestr(evil_entry, "malicious")
        if include_opf:
            zf.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                "<rootfiles>"
                '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                "</rootfiles></container>",
            )
            toc_manifest = (
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                if version == "3.0"
                else '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            )
            spine_attr = ' toc="ncx"' if version == "2.0" else ""
            zf.writestr(
                "OEBPS/content.opf",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" version="' + version + '">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                "<dc:title>Orbital Handbook</dc:title>"
                "</metadata>"
                "<manifest>"
                f"{toc_manifest}"
                '<item id="ch1" href="z_first.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="ch2" href="a_second.xhtml" media-type="application/xhtml+xml"/>'
                "</manifest>"
                f"<spine{spine_attr}>"
                '<itemref idref="ch1"/>'
                '<itemref idref="ch2"/>'
                "</spine></package>",
            )
            if version == "3.0":
                zf.writestr(
                    "OEBPS/nav.xhtml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                    '<body><nav epub:type="toc"><ol>'
                    '<li><a href="z_first.xhtml">Chapter One: Ignition</a></li>'
                    '<li><a href="a_second.xhtml">Chapter Two: Orbit</a></li>'
                    "</ol></nav></body></html>",
                )
            else:
                zf.writestr(
                    "OEBPS/toc.ncx",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    "<navMap>"
                    '<navPoint id="n1" playOrder="1"><navLabel><text>Chapter 1 Ignition</text></navLabel>'
                    '<content src="z_first.xhtml"/></navPoint>'
                    '<navPoint id="n2" playOrder="2"><navLabel><text>Chapter 2 Orbit</text></navLabel>'
                    '<content src="a_second.xhtml"/></navPoint>'
                    "</navMap></ncx>",
                )
            zf.writestr(
                "OEBPS/z_first.xhtml",
                "<html><body><h1>Ignition</h1><p>Turbopump spinup takes 1.4 seconds.</p></body></html>",
            )
            zf.writestr(
                "OEBPS/a_second.xhtml",
                "<html><body><h1>Orbit</h1><p>Target apogee is 415 km.</p></body></html>",
            )


def test_epub2_epub3_spine_order_and_security_guards(tmp_path: Path):
    """Verify EPUB2/3 TOCs, spine reading order, DRM rejection, traversal, and ZIP-bomb guards."""
    extractor = LocalDocumentExtractor()

    epub3 = tmp_path / "book3.epub"
    _build_epub(epub3, version="3.0")
    ex3 = extractor.extract(epub3)
    assert ex3.file_type == "epub"
    # Spine order puts z_first before a_second despite alphabetical filename
    assert len(ex3.sections) == 2
    assert ex3.sections[0].chapter_title == "Chapter One: Ignition"
    assert "1.4 seconds" in ex3.sections[0].text
    assert ex3.sections[1].chapter_title == "Chapter Two: Orbit"
    assert "415 km" in ex3.sections[1].text

    epub2 = tmp_path / "book2.epub"
    _build_epub(epub2, version="2.0")
    ex2 = extractor.extract(epub2)
    assert ex2.sections[0].chapter_title == "Chapter 1 Ignition"
    assert ex2.sections[1].chapter_title == "Chapter 2 Orbit"

    # Invalid archive
    bad_zip = tmp_path / "broken.epub"
    bad_zip.write_bytes(b"not-a-zip")
    with pytest.raises(DocumentExtractionError, match="Malformed"):
        extractor.extract(bad_zip)

    # Missing OPF / container.xml
    no_opf = tmp_path / "no_opf.epub"
    _build_epub(no_opf, include_opf=False)
    with pytest.raises(DocumentExtractionError, match="container.xml|Malformed"):
        extractor.extract(no_opf)

    # Path traversal entry
    traversal = tmp_path / "evil.epub"
    _build_epub(traversal, evil_entry="../escape.txt")
    with pytest.raises(DocumentExtractionError, match="path traversal|unsafe path"):
        extractor.extract(traversal)

    # DRM protection checks
    for drm_name in ("META-INF/rights.xml", "META-INF/encryption.xml"):
        drm_epub = tmp_path / f"drm_{Path(drm_name).stem}.epub"
        _build_epub(drm_epub, drm_file=drm_name)
        with pytest.raises(DocumentExtractionError, match="DRM"):
            extractor.extract(drm_epub)

    # ZIP-bomb compression ratio check
    bomb = tmp_path / "bomb.epub"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", "A" * 2_000_000)
    strict_extractor = LocalDocumentExtractor(
        limits=KnowledgeLimits(max_archive_compression_ratio=5.0)
    )
    with pytest.raises(DocumentExtractionError, match="compression ratio|ZIP-bomb"):
        strict_extractor.extract(bomb)


def test_expanded_formats_extraction_and_xxe_legacy_rejection(tmp_path: Path):
    """Verify HTML, PPTX, XLSX, CSV, TSV, RTF, ODT, ODS, XML (+XXE), JSON, IPYNB, code, and legacy rejection."""
    extractor = LocalDocumentExtractor()

    # HTML strips script/style
    html_p = tmp_path / "page.html"
    html_p.write_text(
        "<html><head><style>body{color:red}</style><script>alert(1)</script></head>"
        "<body><h1>Telemetry</h1><p>Sensor alpha reads 98.6 F.</p></body></html>",
        encoding="utf-8",
    )
    ex_html = extractor.extract(html_p)
    assert "98.6 F" in ex_html.sections[0].text
    assert "alert(1)" not in ex_html.sections[0].text
    assert "color:red" not in ex_html.sections[0].text

    # PPTX slides + tables + speaker notes
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Propulsion Overview"
    slide.placeholders[1].text = "Isp is 348 seconds."
    table_shape = slide.shapes.add_table(2, 2, 100, 100, 400, 200)
    table_shape.table.cell(0, 0).text = "Stage"
    table_shape.table.cell(0, 1).text = "Thrust"
    table_shape.table.cell(1, 0).text = "Core"
    table_shape.table.cell(1, 1).text = "7607 kN"
    slide.notes_slide.notes_text_frame.text = "Verify gimbal check before T-0."
    pptx_p = tmp_path / "deck.pptx"
    prs.save(str(pptx_p))
    ex_pptx = extractor.extract(pptx_p)
    assert ex_pptx.sections[0].page_number == 1
    assert "Propulsion Overview" in (ex_pptx.sections[0].section_title or "")
    assert "348 seconds" in ex_pptx.sections[0].text
    assert "7607 kN" in ex_pptx.sections[0].text
    assert "Verify gimbal check" in ex_pptx.sections[0].text

    # XLSX read-only sheets + cell coordinates
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budgets"
    ws["A1"] = "Subsystem"
    ws["B1"] = "MassKg"
    ws["A2"] = "Avionics"
    ws["B2"] = 14.5
    xlsx_p = tmp_path / "budget.xlsx"
    wb.save(str(xlsx_p))
    wb.close()
    ex_xlsx = extractor.extract(xlsx_p)
    assert "Sheet Budgets" in (ex_xlsx.sections[0].section_title or "")
    assert "Avionics" in ex_xlsx.sections[0].text
    assert "14.5" in ex_xlsx.sections[0].text

    # CSV & TSV
    csv_p = tmp_path / "metrics.csv"
    csv_p.write_text("name,value\nvoltage,28.4\n", encoding="utf-8")
    ex_csv = extractor.extract(csv_p)
    assert "name: voltage" in ex_csv.sections[0].text
    assert "value: 28.4" in ex_csv.sections[0].text

    tsv_p = tmp_path / "metrics.tsv"
    tsv_p.write_text("sensor\tunit\ngyro\trad/s\n", encoding="utf-8")
    ex_tsv = extractor.extract(tsv_p)
    assert "gyro" in ex_tsv.sections[0].text

    # RTF
    rtf_p = tmp_path / "memo.rtf"
    rtf_p.write_text(
        r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Courier;}} Flight readiness approved.}",
        encoding="utf-8",
    )
    ex_rtf = extractor.extract(rtf_p)
    assert "Flight readiness approved." in ex_rtf.sections[0].text

    # ODT
    odt_doc = OpenDocumentText()
    odt_doc.text.addElement(OdfHeading(outlinelevel=1, text="Thermal Spec"))
    odt_doc.text.addElement(OdfParagraph(text="Radiator area is 4.2 square meters."))
    odt_p = tmp_path / "spec.odt"
    odt_doc.save(str(odt_p))
    ex_odt = extractor.extract(odt_p)
    assert "4.2 square meters" in ex_odt.sections[0].text

    # ODS
    ods_doc = OpenDocumentSpreadsheet()
    ods_table = Table(name="Power")
    row = TableRow()
    c1 = TableCell()
    c1.addElement(OdfParagraph(text="SolarArray"))
    c2 = TableCell()
    c2.addElement(OdfParagraph(text="1200W"))
    row.addElement(c1)
    row.addElement(c2)
    ods_table.addElement(row)
    ods_doc.spreadsheet.addElement(ods_table)
    ods_p = tmp_path / "power.ods"
    ods_doc.save(str(ods_p))
    ex_ods = extractor.extract(ods_p)
    assert "Sheet Power" in (ex_ods.sections[0].section_title or "")
    assert "1200W" in ex_ods.sections[0].text

    # XML safe + XXE rejection
    xml_p = tmp_path / "config.xml"
    xml_p.write_text("<system><mode>nominal</mode></system>", encoding="utf-8")
    ex_xml = extractor.extract(xml_p)
    assert "nominal" in ex_xml.sections[0].text

    xxe_p = tmp_path / "xxe.xml"
    xxe_p.write_text(
        '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><r>&xxe;</r>',
        encoding="utf-8",
    )
    with pytest.raises(DocumentExtractionError, match="DOCTYPE|ENTITY|Unsafe"):
        extractor.extract(xxe_p)

    # JSON
    json_p = tmp_path / "manifest.json"
    json_p.write_text(json.dumps({"mission": "ORION-481", "orbiter": True}), encoding="utf-8")
    ex_json = extractor.extract(json_p)
    assert "ORION-481" in ex_json.sections[0].text

    # IPYNB excludes outputs by default
    nb_p = tmp_path / "analysis.ipynb"
    nb_p.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Trajectory Analysis"]},
                    {
                        "cell_type": "code",
                        "source": ["delta_v = 3120"],
                        "outputs": [{"output_type": "stream", "text": ["SECRET_OUTPUT_999"]}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    ex_nb = extractor.extract(nb_p)
    joined_nb = "\n".join(s.text for s in ex_nb.sections)
    assert "Trajectory Analysis" in joined_nb
    assert "delta_v = 3120" in joined_nb
    assert "SECRET_OUTPUT_999" not in joined_nb

    # Expanded code extensions
    for ext, content in [
        ("sql", "SELECT * FROM telemetry WHERE altitude > 100;"),
        ("go", "package main\nfunc Orbit() int { return 42 }"),
        ("sh", "#!/usr/bin/env bash\necho 'launch ready'"),
    ]:
        p = tmp_path / f"sample.{ext}"
        p.write_text(content, encoding="utf-8")
        ex_code = extractor.extract(p)
        assert ex_code.file_type == ext

    # Legacy binary Office formats cleanly rejected
    for legacy in ("old.doc", "old.xls", "old.ppt"):
        lp = tmp_path / legacy
        lp.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        with pytest.raises(DocumentExtractionError, match="Unsupported|Legacy"):
            extractor.extract(lp)


def test_scale_streaming_large_file_many_chunks_folder_cancellation_and_disk_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify >20MB streaming ingestion with bounded RAM, >500 chunks, >100 files, cancel, retry, disk check."""
    engine, store, _ext, svc, _ret, _asst, _prov = _setup_knowledge_stack(
        tmp_path,
        limits=KnowledgeLimits(batch_size=50, stream_read_block_bytes=65536),
    )
    try:
        # 1) >20 MB text file (~21 MB) producing >500 chunks, streamed without loading full file into RAM
        large_txt = tmp_path / "large_telemetry.txt"
        line = "Telemetry frame 0x4A nominal bus voltage 28.1V temperature 22.4C attitude stable.\n"
        repeats = (21 * 1024 * 1024) // len(line) + 100
        with large_txt.open("w", encoding="utf-8") as fh:
            for _ in range(repeats):
                fh.write(line)
        assert large_txt.stat().st_size > 20 * 1024 * 1024

        progress_events: list[dict] = []
        tracemalloc.start()
        record, _dedup = svc.import_file(
            large_txt,
            on_progress=lambda ev: progress_events.append(ev),
        )
        _current, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert record.ingestion_status == IngestionStatus.INDEXED
        assert record.chunk_count > 500
        assert any(ev.get("stage") == "indexing" for ev in progress_events)
        # Peak Python traced memory during streaming + batched FTS5 must stay well below 2x file size
        # (in fact typically < 15 MB while indexing a >21 MB file)
        assert peak_bytes < 25 * 1024 * 1024

        # 2) >100 files in one folder (110 files)
        big_folder = tmp_path / "many_docs"
        big_folder.mkdir()
        for i in range(110):
            (big_folder / f"doc_{i:03d}.txt").write_text(
                f"Document index {i} contains calibration constant K{i}={i * 3}.",
                encoding="utf-8",
            )
        imported, errs, stats = svc.import_folder(
            big_folder, supported_extensions=SUPPORTED_EXTENSIONS
        )
        assert not errs
        assert len(imported) == 110
        assert stats["completed"] == 110
        assert stats["total"] == 110

        # 3) Cancellation mid-ingestion + retry recovery
        cancel_doc = tmp_path / "cancel_me.txt"
        cancel_doc.write_text(("Cancelable paragraph content line.\n" * 6000), encoding="utf-8")
        token = CancellationToken()

        def _cancel_on_first_batch(ev: dict) -> None:
            if ev.get("chunks_indexed", 0) >= 1:
                token.cancel()

        with pytest.raises(IngestionCancelledError):
            svc.import_file(cancel_doc, cancel_token=token, on_progress=_cancel_on_first_batch)

        sources = {s.source_path: s for s in store.list_sources()}
        failed_src = sources[str(cancel_doc.resolve())]
        assert failed_src.ingestion_status == IngestionStatus.FAILED

        # Retry succeeds and transitions to INDEXED
        retried, _ = svc.import_file(cancel_doc)
        assert retried.ingestion_status == IngestionStatus.INDEXED
        assert retried.chunk_count > 10

        # 4) Low disk space check rejects new import cleanly
        monkeypatch.setattr(store, "free_disk_bytes", lambda: 10 * 1024 * 1024)
        blocked_doc = tmp_path / "blocked.txt"
        blocked_doc.write_text("Should not index when disk is low.", encoding="utf-8")
        with pytest.raises(KnowledgeError, match="Low disk space"):
            svc.import_file(blocked_doc)
    finally:
        engine.dispose()


def test_rag_references_for_epub_pptx_xlsx_and_deduplication(tmp_path: Path):
    """Verify RAG citations for EPUB chapter, PPTX slide, XLSX sheet, deduplication, and scope."""
    engine, _store, _ext, svc, _ret, assistant, provider = _setup_knowledge_stack(tmp_path)
    try:
        epub_p = tmp_path / "handbook.epub"
        _build_epub(epub_p, version="3.0")
        epub_rec, _ = svc.import_file(epub_p)
        svc.update_reading_progress(epub_rec.id, reading_policy=ReadingPolicy.WHOLE_DOCUMENT)

        prs = Presentation()
        s1 = prs.slides.add_slide(prs.slide_layouts[1])
        s1.shapes.title.text = "Cryo Valves"
        s1.placeholders[1].text = "LOX bleed valve cycles at 12 Hz during chilldown."
        pptx_p = tmp_path / "valves.pptx"
        prs.save(str(pptx_p))
        pptx_rec, _ = svc.import_file(pptx_p)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Limits"
        ws["A1"] = "Component"
        ws["B1"] = "MaxTempC"
        ws["A2"] = "NozzleSkirt"
        ws["B2"] = 910
        xlsx_p = tmp_path / "limits.xlsx"
        wb.save(str(xlsx_p))
        wb.close()
        xlsx_rec, _ = svc.import_file(xlsx_p)

        assistant.create_new_conversation("RAG 4.8.1 Check")

        # EPUB chapter reference
        provider.fixed_response = "Target apogee is 415 km."
        refs_epub: list[list[dict]] = []
        "".join(
            assistant.stream_message(
                "What is the target apogee in the handbook?",
                selected_document_ids=[epub_rec.id],
                on_references=refs_epub.append,
            )
        )
        assert refs_epub and refs_epub[0]
        assert any(
            ref.get("display_name") == "handbook.epub"
            and "Chapter Two: Orbit" in ref.get("section_title", "")
            for ref in refs_epub[0]
        )

        # PPTX slide reference
        provider.fixed_response = "LOX bleed valve cycles at 12 Hz."
        refs_pptx: list[list[dict]] = []
        "".join(
            assistant.stream_message(
                "How fast does the LOX bleed valve cycle?",
                selected_document_ids=[pptx_rec.id],
                on_references=refs_pptx.append,
            )
        )
        assert refs_pptx and refs_pptx[0]
        assert any(
            ref.get("display_name") == "valves.pptx" and ref.get("page_number") == 1
            for ref in refs_pptx[0]
        )

        # XLSX sheet reference
        provider.fixed_response = "NozzleSkirt max temperature is 910 C."
        refs_xlsx: list[list[dict]] = []
        "".join(
            assistant.stream_message(
                "What is NozzleSkirt MaxTempC?",
                selected_document_ids=[xlsx_rec.id],
                on_references=refs_xlsx.append,
            )
        )
        assert refs_xlsx and refs_xlsx[0]
        assert any(
            ref.get("display_name") == "limits.xlsx"
            and "Sheet Limits" in ref.get("section_title", "")
            for ref in refs_xlsx[0]
        )

        # Scope isolation: querying apogee scoped only to xlsx_rec yields no EPUB ref
        provider.fixed_response = "Not found in selected source."
        refs_scoped: list[list[dict]] = []
        "".join(
            assistant.stream_message(
                "What is the target apogee?",
                selected_document_ids=[xlsx_rec.id],
                on_references=refs_scoped.append,
            )
        )
        flat_scoped = [r for batch in refs_scoped for r in batch]
        assert not any(ref.get("display_name") == "handbook.epub" for ref in flat_scoped)
    finally:
        engine.dispose()
