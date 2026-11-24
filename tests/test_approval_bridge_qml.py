"""Tests for Qt bridge approval integration, QML approval dialog, and audit event persistence."""

import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from pydantic import BaseModel, Field
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.tools.approvals import ApprovalBroker, ApprovalStatus
from orion.application.tools.contracts import (
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolInvocation,
)
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.ui.qt_bridge import OrionBridge

os.environ["QT_QPA_PLATFORM"] = "offscreen"


class SampleInput(BaseModel):
    filepath: str = Field(..., min_length=1)
    content: str = Field(default="")


class SampleOutput(BaseModel):
    status: str


def _apply_migrations(db_url: str) -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def _make_service_and_store(tmp_path: Path):
    db_file = tmp_path / f"approval_test_{uuid.uuid4().hex}.db"
    _apply_migrations(f"sqlite:///{db_file}")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    provider = FakeModelProvider(simulated_delay=0.01)
    service = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )
    return service, conv_store


@pytest.fixture(scope="session")
def qgui_app():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def _create_sample_tool(is_mutating: bool = True) -> ToolDefinition:
    return ToolDefinition(
        tool_id="workspace_write_sample",
        name="Workspace Write Sample",
        description="Writes files inside workspace",
        permission_level=PermissionLevel.WORKSPACE_WRITE
        if is_mutating
        else PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=is_mutating,
        input_model=SampleInput,
        output_model=SampleOutput,
    )


def test_bridge_approval_signal_and_decision(tmp_path):
    """Bridge emits approvalRequested, receives decision, and emits approvalResolved."""
    service, _ = _make_service_and_store(tmp_path)
    broker = ApprovalBroker()
    bridge = OrionBridge(assistant_service=service, approval_broker=broker)

    requested_events: list[dict] = []
    resolved_events: list[tuple[str, str]] = []

    bridge.approvalRequested.connect(lambda req: requested_events.append(req))
    bridge.approvalResolved.connect(lambda aid, status: resolved_events.append((aid, status)))

    tool = _create_sample_tool()
    inv = ToolInvocation(
        tool_id=tool.tool_id,
        arguments={"filepath": "out.txt", "content": "data"},
        client_request_id="req_bridge_1",
        conversation_id="conv_bridge_1",
    )

    # Creating request on broker triggers listener -> bridge.approvalRequested
    req = broker.create_request(tool, inv)

    assert len(requested_events) == 1
    assert requested_events[0]["approval_id"] == req.approval_id
    assert requested_events[0]["nonce"] == req.nonce
    assert requested_events[0]["tool_name"] == "Workspace Write Sample"

    # User approves via bridge slot
    bridge.submitApprovalDecision(req.approval_id, req.nonce, True)

    assert len(resolved_events) == 1
    assert resolved_events[0] == (req.approval_id, ApprovalStatus.APPROVED.value)
    assert broker.get_pending(req.approval_id).status == ApprovalStatus.APPROVED


def test_bridge_stop_generation_cancels_approvals(tmp_path):
    """Calling stopGeneration on bridge cancels active pending approvals."""
    service, _ = _make_service_and_store(tmp_path)
    broker = ApprovalBroker()
    bridge = OrionBridge(assistant_service=service, approval_broker=broker)

    cancelled_events: list[str] = []
    bridge.approvalCancelled.connect(lambda aid: cancelled_events.append(aid))

    tool = _create_sample_tool()
    inv = ToolInvocation(
        tool_id=tool.tool_id,
        arguments={"filepath": "out.txt", "content": "data"},
        client_request_id="req_to_stop",
        conversation_id="conv_bridge_1",
    )

    req = broker.create_request(tool, inv)
    bridge._current_request_id = "req_to_stop"

    bridge.stopGeneration()

    assert req.approval_id in cancelled_events
    assert broker.get_pending(req.approval_id).status == ApprovalStatus.CANCELLED


def test_bridge_shutdown_cancels_all_approvals(tmp_path):
    """Bridge shutdown cancels all pending approvals."""
    service, _ = _make_service_and_store(tmp_path)
    broker = ApprovalBroker()
    bridge = OrionBridge(assistant_service=service, approval_broker=broker)

    cancelled_events: list[str] = []
    bridge.approvalCancelled.connect(lambda aid: cancelled_events.append(aid))

    tool = _create_sample_tool()
    req1 = broker.create_request(
        tool,
        ToolInvocation(
            tool_id=tool.tool_id,
            arguments={"filepath": "f1.txt", "content": "1"},
            client_request_id="req_1",
            conversation_id="conv_1",
        ),
    )
    req2 = broker.create_request(
        tool,
        ToolInvocation(
            tool_id=tool.tool_id,
            arguments={"filepath": "f2.txt", "content": "2"},
            client_request_id="req_2",
            conversation_id="conv_1",
        ),
    )

    bridge.shutdown(timeout_ms=1000)

    assert set(cancelled_events) == {req1.approval_id, req2.approval_id}
    assert len(broker.list_pending()) == 0


def test_qml_approval_dialog_lifecycle(qgui_app, tmp_path):
    """QML approval dialog displays metadata, responds to approve/reject, and updates UI."""
    service, _ = _make_service_and_store(tmp_path)
    broker = ApprovalBroker()
    bridge = OrionBridge(assistant_service=service, approval_broker=broker)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    root = engine.rootObjects()[0]
    bridge._thread_pool.waitForDone(2000)
    qgui_app.processEvents()

    pending_model = root.findChild(QObject, "pendingApprovalsModel")
    approval_dialog = root.findChild(QObject, "approvalDialog")

    assert pending_model.property("count") == 0
    assert approval_dialog.property("visible") is False

    # 1. Trigger an approval request
    tool = _create_sample_tool(is_mutating=True)
    inv = ToolInvocation(
        tool_id=tool.tool_id,
        arguments={"filepath": "nested/doc.txt", "content": "hello world"},
        client_request_id="req_qml_1",
        conversation_id="conv_qml_1",
    )
    req = broker.create_request(tool, inv)
    qgui_app.processEvents()

    # Verify dialog became visible and model has 1 item
    assert pending_model.property("count") == 1
    assert approval_dialog.property("visible") is True
    assert approval_dialog.property("currentApprovalId") == req.approval_id
    assert approval_dialog.property("currentNonce") == req.nonce
    assert approval_dialog.property("isMutating") is True

    # 2. Check UI elements
    tool_name_text = root.findChild(QObject, "toolNameText")
    assert tool_name_text.property("text") == "Workspace Write Sample"

    args_text = root.findChild(QObject, "argsText")
    assert "nested/doc.txt" in args_text.property("text")

    # 3. Simulate user clicking "Approve Once"
    approve_button = root.findChild(QObject, "approvalApproveButton")
    assert approve_button.property("enabled") is True
    approve_button.clicked.emit()
    qgui_app.processEvents()

    # Once resolved, pendingApprovalsModel is cleared and dialog hidden
    assert pending_model.property("count") == 0
    assert approval_dialog.property("visible") is False
    assert broker.get_pending(req.approval_id).status == ApprovalStatus.APPROVED

    bridge.shutdown()


def test_qml_multiple_approval_requests(qgui_app, tmp_path):
    """Multiple pending approvals remain correctly queued and isolated."""
    service, _ = _make_service_and_store(tmp_path)
    broker = ApprovalBroker()
    bridge = OrionBridge(assistant_service=service, approval_broker=broker)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    root = engine.rootObjects()[0]
    bridge._thread_pool.waitForDone(2000)
    qgui_app.processEvents()

    pending_model = root.findChild(QObject, "pendingApprovalsModel")
    approval_dialog = root.findChild(QObject, "approvalDialog")

    tool = _create_sample_tool()
    req1 = broker.create_request(
        tool,
        ToolInvocation(
            tool_id=tool.tool_id,
            arguments={"filepath": "file1.txt", "content": "1"},
            client_request_id="req_multi_1",
            conversation_id="conv_1",
        ),
    )
    req2 = broker.create_request(
        tool,
        ToolInvocation(
            tool_id=tool.tool_id,
            arguments={"filepath": "file2.txt", "content": "2"},
            client_request_id="req_multi_2",
            conversation_id="conv_1",
        ),
    )
    qgui_app.processEvents()

    assert pending_model.property("count") == 2
    assert approval_dialog.property("currentApprovalId") == req1.approval_id

    # Reject first request
    reject_button = root.findChild(QObject, "approvalRejectButton")
    reject_button.clicked.emit()
    qgui_app.processEvents()

    # Now second request is active
    assert pending_model.property("count") == 1
    assert approval_dialog.property("currentApprovalId") == req2.approval_id
    assert broker.get_pending(req1.approval_id).status == ApprovalStatus.REJECTED

    # Approve second request
    approve_button = root.findChild(QObject, "approvalApproveButton")
    approve_button.clicked.emit()
    qgui_app.processEvents()

    assert pending_model.property("count") == 0
    assert approval_dialog.property("visible") is False
    assert broker.get_pending(req2.approval_id).status == ApprovalStatus.APPROVED

    bridge.shutdown()


def test_activity_events_persistence(tmp_path):
    """Activity events are recorded and loaded from SQLite persistence."""
    _, conv_store = _make_service_and_store(tmp_path)

    def record_evt(event_type: str, aggregate_type: str, aggregate_id: str | None, payload: dict):
        conv_store.record_activity_event(event_type, aggregate_type, aggregate_id, payload)

    broker = ApprovalBroker(event_recorder=record_evt)
    tool = _create_sample_tool()
    inv = ToolInvocation(
        tool_id=tool.tool_id,
        arguments={"filepath": "audit.txt", "content": "abc"},
        client_request_id="req_audit",
        conversation_id="conv_audit",
    )

    req = broker.create_request(tool, inv)
    broker.resolve(req.approval_id, req.nonce, approved=True)

    # Load events from persistence
    events = conv_store.load_activity(aggregate_type="tool_approval")
    assert len(events) == 2

    requested_evt = next(e for e in events if e.event_type == "tool.approval_requested")
    assert requested_evt.aggregate_id == req.approval_id
    assert requested_evt.payload["tool_id"] == tool.tool_id
    assert "nonce" not in requested_evt.payload

    approved_evt = next(e for e in events if e.event_type == "tool.approved")
    assert approved_evt.aggregate_id == req.approval_id
    assert approved_evt.payload["tool_id"] == tool.tool_id
    assert "nonce" not in approved_evt.payload
