"""Milestone 4.7 tests for user-initiated workspace browsing, deterministic ToolRouter
dispatch, security boundaries, audit events, and QML integration."""

import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from alembic.config import Config
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.tools.contracts import ExecutionStatus
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.application.tools.workspace_browser import WorkspaceBrowserService
from orion.application.tools.workspace_contracts import MAX_READ_BYTES
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.tools.workspace_tools import build_workspace_tool_system
from orion.ui.qt_bridge import OrionBridge

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_stores(tmp_path: Path):
    db_file = tmp_path / "m47.db"
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory), SqliteSettingsStore(factory), engine


def _make_synthetic_workspace(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "README.md").write_text("# Synthetic Workspace\nHello ORION\n", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (ws / "empty_dir").mkdir()
    (ws / ".env").write_text("SECRET_TOKEN=synthetic_do_not_read\n", encoding="utf-8")
    (ws / "keys").mkdir()
    (ws / "keys" / "id_rsa").write_text("SYNTHETIC_KEY\n", encoding="utf-8")
    (ws / "big.txt").write_text("A" * (MAX_READ_BYTES + 2048), encoding="utf-8")
    (ws / "binary.bin").write_bytes(b"\x00\x01\x02\xff\xfe\xfd")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")
    (ws / "escape_link.txt").symlink_to(outside / "secret.txt")
    return ws, outside


def test_workspace_browser_root_subdir_parent_and_only_two_tools(tmp_path: Path):
    ws, _ = _make_synthetic_workspace(tmp_path)
    conv_store, _settings, engine = _make_stores(tmp_path)
    try:
        _reg, router, _svc = build_workspace_tool_system(ws)
        browser = WorkspaceBrowserService(router, event_recorder=conv_store.record_activity_event)

        # 1. List workspace root
        dec_root = browser.list_directory("", "req_root")
        assert dec_root.disposition == RouterDisposition.DISPATCHED
        assert dec_root.result is not None
        names = {e["name"] for e in dec_root.result.output["entries"]}
        assert "README.md" in names
        assert "src" in names
        assert ".env" not in names  # filtered from listing too

        # 2. Navigate into subdirectory
        dec_sub = browser.list_directory("src", "req_sub")
        assert dec_sub.disposition == RouterDisposition.DISPATCHED
        assert [e["name"] for e in dec_sub.result.output["entries"]] == ["app.py"]

        # 3. Navigate back to parent (root)
        dec_parent = browser.list_directory("", "req_parent")
        assert dec_parent.disposition == RouterDisposition.DISPATCHED
        assert dec_parent.result.output["relative_path"] == ""

        # 4. Read harmless file
        dec_read = browser.read_file("src/app.py", "req_read")
        assert dec_read.disposition == RouterDisposition.DISPATCHED
        assert dec_read.result.output["content"] == "print('hello')\n"

        # 5. Only two tools permitted on WorkspaceBrowserService
        with pytest.raises(ValueError, match="not permitted"):
            browser._dispatch("workspace_git_status", {}, "req_bad", "conv")

        # 6. Audit events recorded in SQLite
        events = conv_store.load_activity(limit=20)
        assert len(events) >= 4
        assert any(e.payload.get("tool_id") == "workspace_read_file" for e in events)
    finally:
        engine.dispose()


def test_workspace_browser_security_rejections_and_limits(tmp_path: Path):
    ws, outside = _make_synthetic_workspace(tmp_path)
    _reg, router, _svc = build_workspace_tool_system(ws)
    browser = WorkspaceBrowserService(router)

    # Traversal outside workspace
    for bad in ("../outside/secret.txt", "../../etc/passwd", "src/../../outside/secret.txt"):
        dec = browser.read_file(bad, "req_trav")
        assert dec.disposition == RouterDisposition.DENIED

    # Absolute paths outside workspace
    for abs_p in ("/etc/passwd", str(outside / "secret.txt")):
        dec = browser.read_file(abs_p, "req_abs")
        assert dec.disposition == RouterDisposition.DENIED

    # Symlink escape blocked deterministically by PermissionEngine
    dec_sym = browser.read_file("escape_link.txt", "req_sym")
    assert dec_sym.disposition == RouterDisposition.DENIED

    # Sensitive files blocked at PermissionEngine before execution
    for sens in (".env", "keys/id_rsa", ".aws/credentials", "certs/server.pem"):
        dec_sens = browser.read_file(sens, "req_sens")
        assert dec_sens.disposition == RouterDisposition.DENIED

    # Nonexistent file
    dec_missing = browser.read_file("does_not_exist.py", "req_missing")
    assert dec_missing.disposition == RouterDisposition.DISPATCHED
    assert dec_missing.result.status == ExecutionStatus.FAILED

    # Binary/non-UTF-8 file rejected cleanly
    dec_bin = browser.read_file("binary.bin", "req_bin")
    assert dec_bin.disposition == RouterDisposition.DISPATCHED
    assert dec_bin.result.status == ExecutionStatus.FAILED

    # Oversized file truncated at MAX_READ_BYTES
    dec_big = browser.read_file("big.txt", "req_big")
    assert dec_big.disposition == RouterDisposition.DISPATCHED
    assert dec_big.result.status == ExecutionStatus.COMPLETED
    assert dec_big.result.output["truncated"] is True
    assert len(dec_big.result.output["content"].encode("utf-8")) <= MAX_READ_BYTES


def test_permission_engine_cannot_be_bypassed_and_model_disconnected(tmp_path: Path):
    ws, _ = _make_synthetic_workspace(tmp_path)
    conv_store, settings_store, engine = _make_stores(tmp_path)
    try:
        reg, _real_router, _ = build_workspace_tool_system(ws)
        deny_all_engine = PermissionEngine(
            PermissionPolicy(workspace_root=ws, allow_read_only=False, allowed_tool_ids=frozenset())
        )
        locked_router = ToolRouter(registry=reg, permission_engine=deny_all_engine)
        browser = WorkspaceBrowserService(locked_router)

        dec = browser.read_file("README.md", "req_locked")
        assert dec.disposition == RouterDisposition.DENIED

        # Model-generated tool call JSON is NOT executed or routed by AssistantService
        model_proposal_json = json.dumps(
            {
                "action": "propose_tool",
                "tool": "workspace_read_file",
                "arguments": {"path": "README.md"},
                "response": None,
            }
        )
        svc = AssistantService(
            FakeModelProvider(fixed_response=model_proposal_json, simulated_delay=0.0),
            conv_store,
            settings_store,
        )
        res = svc.send_message("Read README.md")
        assert res.content == model_proposal_json
        assert not any(e.event_type.startswith("tool.") for e in conv_store.load_activity(limit=20))
    finally:
        engine.dispose()


def test_bridge_workspace_dispatch_cancellation_and_errors(tmp_path: Path):
    ws, _ = _make_synthetic_workspace(tmp_path)
    conv_store, settings_store, engine = _make_stores(tmp_path)
    try:
        _reg, router, _ = build_workspace_tool_system(ws)
        svc = AssistantService(FakeModelProvider(simulated_delay=0.0), conv_store, settings_store)
        bridge = OrionBridge(svc, tool_router=router, workspace_root=ws)
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])

        dir_events = []
        file_events = []
        err_events = []
        bridge.workspaceDirectoryLoaded.connect(lambda *a: dir_events.append(a))
        bridge.workspaceFileLoaded.connect(lambda *a: file_events.append(a))
        bridge.workspaceToolError.connect(lambda *a: err_events.append(a))

        # Bridge list root
        bridge.listWorkspaceDirectory("")
        dl = time.monotonic() + 3.0
        while bridge.workspaceToolBusy and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert len(dir_events) == 1
        assert dir_events[0][0] == ""

        # Bridge read file
        bridge.readWorkspaceFile("README.md")
        dl = time.monotonic() + 3.0
        while bridge.workspaceToolBusy and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert len(file_events) == 1
        assert "Synthetic Workspace" in file_events[0][1]

        # Bridge sensitive path -> error signal
        bridge.readWorkspaceFile(".env")
        dl = time.monotonic() + 3.0
        while bridge.workspaceToolBusy and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert len(err_events) == 1
        assert "sensitive" in err_events[0][2].lower()

        # Cancellation drops in-flight result
        before_count = len(file_events)
        bridge.readWorkspaceFile("src/app.py")
        bridge.cancelWorkspaceTool()
        dl = time.monotonic() + 3.0
        while bridge._active_workspace_workers and time.monotonic() < dl:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert len(file_events) == before_count

        bridge.shutdown(timeout_ms=2000)
    finally:
        engine.dispose()


def _run_qml_workspace_browser_check(tmp_path: Path) -> None:
    ws, _ = _make_synthetic_workspace(tmp_path)
    conv_store, settings_store, db_engine = _make_stores(tmp_path)
    _reg, router, _ = build_workspace_tool_system(ws)
    svc = AssistantService(FakeModelProvider(simulated_delay=0.0), conv_store, settings_store)
    bridge = OrionBridge(svc, tool_router=router, workspace_root=ws)

    app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])
    qml_engine = QQmlApplicationEngine()
    qml_engine.rootContext().setContextProperty("orion", bridge)
    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    qml_engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))
    root = qml_engine.rootObjects()[0]

    dl = time.monotonic() + 3.0
    while root.property("isInitializing") and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)

    toggle_btn = root.findChild(QObject, "workspaceBrowserToggleButton")
    panel = root.findChild(QObject, "workspaceBrowserPanel")
    assert toggle_btn is not None and toggle_btn.property("visible") is True
    assert panel is not None

    # Open workspace browser and load root
    root.setProperty("workspacePanelOpen", True)
    bridge.listWorkspaceDirectory("")
    dl = time.monotonic() + 3.0
    while bridge.workspaceToolBusy and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    entries_model = root.findChild(QObject, "workspaceEntriesModel")
    assert entries_model is not None and entries_model.property("count") >= 2

    # Read README.md -> verify untrusted badge & result content
    bridge.readWorkspaceFile("README.md")
    dl = time.monotonic() + 3.0
    while bridge.workspaceToolBusy and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    badge = root.findChild(QObject, "untrustedDataBadge")
    content_item = root.findChild(QObject, "workspaceFileContentText")
    assert badge is not None
    assert content_item is not None and "Synthetic Workspace" in content_item.property("text")

    # Attempt .env -> error banner visible
    bridge.readWorkspaceFile(".env")
    dl = time.monotonic() + 3.0
    while bridge.workspaceToolBusy and time.monotonic() < dl:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    err_banner = root.findChild(QObject, "workspaceErrorBanner")
    assert err_banner is not None and err_banner.property("visible") is True

    bridge.shutdown(timeout_ms=1000)
    qml_engine.deleteLater()
    app.processEvents()
    del qml_engine
    gc.collect()
    db_engine.dispose()


def test_qml_workspace_browser_integration(tmp_path: Path):
    """Run QML Workspace Browser integration in an isolated Qt process."""
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from tests.test_milestone_47_workspace_browser import _run_qml_workspace_browser_check; "
                f"_run_qml_workspace_browser_check(Path({str(tmp_path)!r}))"
            ),
        ],
        cwd=str(Path(__file__).parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
