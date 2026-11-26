"""Milestone 4.9 tests: KnowledgeScope, FTS5 all-indexed retrieval, Workspace-to-Knowledge, and redesigned QML."""

import gc
import os
import subprocess
import sys
import time
from pathlib import Path

from alembic.config import Config
from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.knowledge.contracts import IngestionStatus, KnowledgeLimits, KnowledgeScope
from orion.application.knowledge.retrieval import DocumentRetrievalService, KnowledgeService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.knowledge.extractor import SUPPORTED_EXTENSIONS, LocalDocumentExtractor
from orion.infrastructure.knowledge.repository import SqliteKnowledgeStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.tools.workspace_tools import build_workspace_tool_system
from orion.ui.qt_bridge import OrionBridge

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _setup_stack(tmp_path: Path):
    db_path = tmp_path / "orion_49.db"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(db_path)
    session_factory = create_session_factory(engine)
    store = SqliteKnowledgeStore(session_factory)
    limits = KnowledgeLimits()
    extractor = LocalDocumentExtractor(limits=limits)
    kb_svc = KnowledgeService(extractor=extractor, store=store, limits=limits)
    retrieval = DocumentRetrievalService(store=store, limits=limits)
    conv_store = SqliteConversationStore(session_factory)
    settings_store = SqliteSettingsStore(session_factory)
    provider = FakeModelProvider(simulated_delay=0.0)
    asst = AssistantService(
        provider,
        conv_store,
        settings_store,
        retrieval_service=retrieval,
    )
    return engine, store, kb_svc, retrieval, asst, provider


def test_knowledge_scope_modes_all_indexed_and_exclusions(tmp_path: Path):
    """Verify NONE, ALL_INDEXED, SELECTED_DOCUMENTS, failed exclusion, empty-ID guard, and >100 files."""
    engine, store, kb_svc, retrieval, asst, provider = _setup_stack(tmp_path)
    try:
        doc_a = tmp_path / "propulsion.txt"
        doc_a.write_text(
            "The CAN-FD bus operates at 5 Mbps with dual redundancy.", encoding="utf-8"
        )
        rec_a, _ = kb_svc.import_file(doc_a)

        doc_b = tmp_path / "thermal.txt"
        doc_b.write_text(
            "Radiator coolant loop nominal temperature is 295 Kelvin.", encoding="utf-8"
        )
        rec_b, _ = kb_svc.import_file(doc_b)

        # Create a FAILED document that must be excluded from ALL_INDEXED
        failed_doc = tmp_path / "broken_note.txt"
        failed_doc.write_text("Secret failed content ZZZ999.", encoding="utf-8")
        rec_fail, _ = kb_svc.import_file(failed_doc)
        store.mark_document_failed(rec_fail.id, "Simulated failure")

        # 1) Default knowledge mode is 'chat' and persists changes
        assert asst.get_knowledge_mode() == "chat"
        assert asst.set_knowledge_mode("ask_knowledge") == "ask_knowledge"
        assert asst.get_knowledge_mode() == "ask_knowledge"

        # 2) CHAT mode (NONE) performs NO retrieval
        refs_chat: list[list[dict]] = []
        provider.fixed_response = "General answer without docs."
        "".join(
            asst.stream_message(
                "What is the radiator coolant temperature?",
                knowledge_scope=KnowledgeScope.NONE,
                on_references=refs_chat.append,
            )
        )
        assert refs_chat == []

        # 3) Empty selected_document_ids with SELECTED_DOCUMENTS is NOT ALL_INDEXED
        res_empty = retrieval.retrieve(
            "What is the CAN-FD speed?",
            selected_document_ids=[],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_empty.has_evidence is False
        assert res_empty.passages == ()

        # 4) ALL_INDEXED searches all indexed docs and excludes FAILED doc
        res_all = retrieval.retrieve(
            "What speed does CAN-FD run at?",
            selected_document_ids=[],
            scope=KnowledgeScope.ALL_INDEXED,
        )
        assert res_all.has_evidence is True
        assert any(p.document_id == rec_a.id for p in res_all.passages)

        res_fail_query = retrieval.retrieve(
            "ZZZ999",
            selected_document_ids=[],
            scope=KnowledgeScope.ALL_INDEXED,
        )
        assert res_fail_query.has_evidence is False

        # 5) SELECTED_DOCUMENTS strictly filters
        res_sel = retrieval.retrieve(
            "radiator coolant temperature",
            selected_document_ids=[rec_a.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_sel.has_evidence is False
        res_sel_b = retrieval.retrieve(
            "radiator coolant temperature",
            selected_document_ids=[rec_b.id],
            scope=KnowledgeScope.SELECTED_DOCUMENTS,
        )
        assert res_sel_b.has_evidence is True
        assert all(p.document_id == rec_b.id for p in res_sel_b.passages)

        # 6) ALL_INDEXED insufficient evidence returns explicit message without calling LLM
        refs_none: list[list[dict]] = []
        ans_none = "".join(
            asst.stream_message(
                "What is the quantum flux capacitor rating?",
                knowledge_scope=KnowledgeScope.ALL_INDEXED,
                on_references=refs_none.append,
            )
        )
        assert "Insufficient evidence" in ans_none
        assert refs_none == [[]]

        # 7) >100 indexed files work in ALL_INDEXED retrieval
        many_dir = tmp_path / "bulk_105"
        many_dir.mkdir()
        for i in range(105):
            (many_dir / f"note_{i:03d}.txt").write_text(
                f"Subsystem {i} calibration token ALPHA_{i}_KEY.", encoding="utf-8"
            )
        imported, errs, _stats = kb_svc.import_folder(
            many_dir, supported_extensions=SUPPORTED_EXTENSIONS
        )
        assert not errs and len(imported) == 105
        res_bulk = retrieval.retrieve(
            "ALPHA_102_KEY",
            selected_document_ids=[],
            scope=KnowledgeScope.ALL_INDEXED,
        )
        assert res_bulk.has_evidence is True
        assert any("note_102.txt" == p.display_name for p in res_bulk.passages)
    finally:
        engine.dispose()


def test_bridge_direct_search_and_workspace_to_knowledge_security(tmp_path: Path):
    """Verify direct FTS5 search without LLM and Workspace-to-Knowledge security checks."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "readme.md").write_text("# ORION\nTelemetry rate is 42 Hz.", encoding="utf-8")
    (ws / ".env").write_text("API_KEY=secret", encoding="utf-8")

    outside = tmp_path / "outside.txt"
    outside.write_text("Outside secret", encoding="utf-8")
    symlink_file = ws / "escape.txt"
    symlink_file.symlink_to(outside)

    engine, store, kb_svc, _retrieval, asst, _provider = _setup_stack(tmp_path)
    try:
        _reg, router, _svc = build_workspace_tool_system(ws)
        app = QCoreApplication.instance() or QCoreApplication([])
        bridge = OrionBridge(
            asst,
            knowledge_service=kb_svc,
            tool_router=router,
            workspace_root=ws,
        )

        added_events: list[tuple[str, str, bool]] = []
        err_events: list[str] = []
        search_events: list[tuple[str, list]] = []
        bridge.workspaceAddedToKnowledge.connect(lambda p, d, a: added_events.append((p, d, a)))
        bridge.knowledgeError.connect(lambda e: err_events.append(e))
        bridge.knowledgeSearchResults.connect(lambda q, h: search_events.append((q, h)))

        # 1) Valid workspace file added to Knowledge
        bridge.addWorkspaceFileToKnowledge("readme.md")
        dl = time.monotonic() + 3.0
        while (bridge.knowledgeBusy or not added_events) and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        assert len(added_events) == 1
        assert added_events[0][0] == "readme.md"
        assert added_events[0][2] is False  # first time indexed

        # Re-adding reports already_indexed=True
        bridge.addWorkspaceFileToKnowledge("readme.md")
        dl = time.monotonic() + 3.0
        while (bridge.knowledgeBusy or len(added_events) < 2) and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        assert added_events[1][2] is True

        # 2) Direct FTS5 search returns excerpt without LLM call
        bridge.searchKnowledgeDirect("42 Hz", "", 10)
        dl = time.monotonic() + 3.0
        while (bridge.knowledgeBusy or not search_events) and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        assert search_events and search_events[0][1]
        assert "42 Hz" in search_events[0][1][0]["excerpt"]

        # 3) Security rejections: traversal, symlink escape, sensitive .env
        for bad in ("../outside.txt", "escape.txt", ".env"):
            before = len(err_events)
            bridge.addWorkspaceFileToKnowledge(bad)
            app.processEvents()
            assert len(err_events) == before + 1

        # 4) Verify failed/indexed counts invariant
        assert all(s.ingestion_status == IngestionStatus.INDEXED for s in store.list_sources())
        bridge.shutdown(timeout_ms=1000)
    finally:
        engine.dispose()


def _run_qml_49_redesign_check(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    sub = ws / "docs"
    sub.mkdir()
    (sub / "guide.md").write_text("# Guide\nBeacon frequency is 144 MHz.", encoding="utf-8")

    engine, _store, kb_svc, _ret, asst, provider = _setup_stack(tmp_path)
    _reg, router, _svc = build_workspace_tool_system(ws)
    provider.fixed_response = "The beacon frequency is 144 MHz."

    bridge = OrionBridge(
        asst,
        knowledge_service=kb_svc,
        tool_router=router,
        workspace_root=ws,
    )
    app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])
    qml_engine = QQmlApplicationEngine()
    qml_engine.rootContext().setContextProperty("orion", bridge)
    qml_file = Path(__file__).resolve().parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    qml_engine.load(QUrl.fromLocalFile(str(qml_file)))
    roots = qml_engine.rootObjects()
    assert roots, "Main.qml failed to load"
    root = roots[0]

    dl = time.monotonic() + 3.0
    while root.property("isInitializing") and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)

    # Verify 3-experience navigation controls exist
    assert root.findChild(QObject, "navChatButton") is not None
    assert root.findChild(QObject, "knowledgeToggleButton") is not None
    assert root.findChild(QObject, "workspaceBrowserToggleButton") is not None
    assert root.findChild(QObject, "modeChatButton") is not None
    assert root.findChild(QObject, "modeAskKnowledgeButton") is not None
    assert root.findChild(QObject, "modeSelectedButton") is not None

    # Switch to Workspace, navigate to 'docs', add guide.md to Knowledge
    ws_btn = root.findChild(QObject, "workspaceBrowserToggleButton")
    QMetaObject.invokeMethod(ws_btn, "click", Qt.DirectConnection)
    app.processEvents()
    assert root.property("activePage") == "workspace"

    bridge.listWorkspaceDirectory("docs")
    dl = time.monotonic() + 3.0
    while bridge.workspaceToolBusy and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)
    assert root.property("workspaceCurrentPath") == "docs"

    bridge.addWorkspaceFileToKnowledge("docs/guide.md")
    dl = time.monotonic() + 3.0
    while bridge.knowledgeBusy and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)
    src_model = root.findChild(QObject, "knowledgeSourcesModel")
    assert src_model is not None and src_model.property("count") == 1

    # Switch to Ask Knowledge mode and ask without manually selecting any doc
    ask_btn = root.findChild(QObject, "modeAskKnowledgeButton")
    QMetaObject.invokeMethod(ask_btn, "click", Qt.DirectConnection)
    app.processEvents()
    assert bridge.knowledgeMode == "ask_knowledge"

    composer = root.findChild(QObject, "composer")
    composer.setProperty("text", "What is the beacon frequency?")
    QMetaObject.invokeMethod(root, "sendMessage", Qt.DirectConnection)
    dl = time.monotonic() + 4.0
    while bridge.isBusy and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    chat_model = root.findChild(QObject, "chatModel")
    assert chat_model.property("count") >= 2

    # Verify responsive resizing works cleanly
    root.setProperty("width", 840)
    root.setProperty("height", 580)
    app.processEvents()
    root.setProperty("width", 1280)
    root.setProperty("height", 800)
    app.processEvents()

    bridge.shutdown(timeout_ms=1000)
    qml_engine.deleteLater()
    app.processEvents()
    del qml_engine
    gc.collect()
    engine.dispose()


def test_qml_49_redesign_integration(tmp_path: Path):
    """Run redesigned 3-page QML verification in an isolated Qt process."""
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from tests.test_milestone_49_redesign import _run_qml_49_redesign_check; "
                f"_run_qml_49_redesign_check(Path({str(tmp_path)!r}))"
            ),
        ],
        cwd=str(Path(__file__).parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
