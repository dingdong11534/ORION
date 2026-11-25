"""Milestone 4.5.1 — Streaming, Lifecycle, Output Budget, UTF-8, and QML Audit Tests."""

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

import orion.infrastructure.tools.execution_manager as em_mod
from orion.application.tools.approvals import ApprovalBroker
from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolProposal,
)
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
from orion.application.tools.registry import ToolRegistry
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.infrastructure.tools.execution_manager import (
    DEFAULT_ALLOWLISTED_COMMANDS,
    MAX_OUTPUT_BYTES,
    ExecutionError,
    ExecutionManager,
    OutputBatcher,
    _drain_stream,
)
from orion.infrastructure.tools.subprocess_executor import SubprocessExecutor
from orion.ui.qt_bridge import OrionBridge

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _CmdInput(BaseModel):
    command: str = Field(default="printf")
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=120.0)


class _CmdOutput(BaseModel):
    command: str
    stdout: str
    stderr: str
    exit_code: int | None


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# 1. ExecutionManager Instance Identity & ToolRouter -> Bridge Cancellation
# ---------------------------------------------------------------------------


def test_single_execution_manager_router_dispatch_and_bridge_cancel(
    ws: Path, monkeypatch: pytest.MonkeyPatch
):
    """ToolRouter -> SubprocessExecutor -> ExecutionManager cancelled via OrionBridge."""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    pid_file = ws / "child.pid"
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=ws
    )
    executor = SubprocessExecutor(execution_manager=mgr)

    mock_service = MagicMock()
    mock_service.active_conversation_id = "conv-audit"
    bridge = OrionBridge(assistant_service=mock_service, execution_manager=mgr)

    # Verify exact same ExecutionManager instance across executor and bridge
    assert executor.execution_manager is mgr
    assert bridge._execution_manager is mgr

    registry = ToolRegistry()
    tool = ToolDefinition(
        tool_id="system_printf",
        name="System Printf",
        description="Controlled command",
        permission_level=PermissionLevel.SYSTEM_EXECUTE,
        execution_type=ExecutionType.SUBPROCESS,
        is_mutating=False,
        input_model=_CmdInput,
        output_model=_CmdOutput,
    )
    registry.register(tool)
    registry.seal()

    pe = PermissionEngine(PermissionPolicy(workspace_root=ws, allow_system_execute=True))
    broker = ApprovalBroker()
    router = ToolRouter(
        registry=registry, permission_engine=pe, executor=executor, approval_broker=broker
    )

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={
            "command": "printf",
            "args": [
                "-c",
                (
                    f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                    "print('started-via-router', flush=True); time.sleep(15)"
                ),
            ],
        },
    )
    outcome = router.route(
        proposal,
        conversation_id="conv-audit",
        client_request_id="req-audit-1",
    )
    assert outcome.disposition == RouterDisposition.AWAITING_APPROVAL
    assert outcome.approval_request is not None

    req = outcome.approval_request

    ready = threading.Event()
    bridge.executionOutputChunk.connect(
        lambda eid, stream, text: ready.set() if "started-via-router" in text else None
    )

    result_holder: list = []

    def _dispatch():
        result_holder.append(router.dispatch_approved(req.approval_id, req.nonce))

    t = threading.Thread(target=_dispatch)
    t.start()

    deadline = time.monotonic() + 3.0
    while not ready.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert ready.is_set(), "Subprocess output did not arrive via bridge"

    child_pid = int(pid_file.read_text().strip())
    exec_id = req.execution_id

    # Cancel through OrionBridge
    bridge.cancelExecution(exec_id)
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert len(result_holder) == 1
    assert result_holder[0].result is not None
    assert result_holder[0].result.status == ExecutionStatus.CANCELLED

    # Confirm actual OS process is terminated
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)

    # Confirm shutdown through OrionBridge shuts down the exact same ExecutionManager
    bridge.shutdown(timeout_ms=2000)
    with pytest.raises(ExecutionError, match="shut down"):
        mgr.run_command(command="printf", args=["ok"])


# ---------------------------------------------------------------------------
# 2. >256 KB Combined Output Budget & Displayed Output Bounding
# ---------------------------------------------------------------------------


def test_over_256kb_combined_stdout_stderr_budget_and_byte_counts(
    ws: Path, monkeypatch: pytest.MonkeyPatch
):
    """Produce >256 KB combined stdout/stderr and verify budget, no deadlock, truncation, and byte counts."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    emitted_chunks: list[tuple[str, str, str]] = []
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=ws,
        output_listener=lambda eid, s, t: emitted_chunks.append((eid, s, t)),
    )

    # Produce 160 KB stdout + 160 KB stderr = 320 KB total (> 256 KB)
    out = mgr.run_command(
        command="printf",
        args=[
            "-c",
            (
                "import sys; "
                "sys.stdout.write('A' * 163840); sys.stdout.flush(); "
                "sys.stderr.write('B' * 163840); sys.stderr.flush()"
            ),
        ],
        execution_id="budget-320kb",
        timeout_seconds=10.0,
    )

    assert out.status == ExecutionStatus.COMPLETED
    assert out.truncated is True

    # Retained output within 256 KB combined budget (128 KB per stream)
    assert len(out.stdout.encode("utf-8")) <= MAX_OUTPUT_BYTES // 2
    assert len(out.stderr.encode("utf-8")) <= MAX_OUTPUT_BYTES // 2
    assert len(out.stdout.encode("utf-8")) + len(out.stderr.encode("utf-8")) <= MAX_OUTPUT_BYTES

    # Displayed output delivered to listener is also bounded within 256 KB
    displayed_out = "".join(c[2] for c in emitted_chunks if c[1] == "stdout")
    displayed_err = "".join(c[2] for c in emitted_chunks if c[1] == "stderr")
    assert len(displayed_out.encode("utf-8")) <= MAX_OUTPUT_BYTES // 2
    assert len(displayed_err.encode("utf-8")) <= MAX_OUTPUT_BYTES // 2

    # Total drained byte counts remain accurate (163840 each)
    assert out.stdout_bytes == 163840
    assert out.stderr_bytes == 163840


# ---------------------------------------------------------------------------
# 3. Pipe-Reader Cancellation Safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["stdout_blocked", "stderr_blocked", "both_blocked"])
def test_cancel_while_pipe_readers_blocked(ws: Path, monkeypatch: pytest.MonkeyPatch, mode: str):
    """Cancellation while stdout, stderr, or both are blocked exits cleanly without pipe close races."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    started = threading.Event()
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=ws,
        lifecycle_listener=lambda eid, evt, p: started.set() if evt == "started" else None,
    )

    if mode == "stdout_blocked":
        script = "import sys, time; sys.stderr.write('err\\n'); sys.stderr.flush(); time.sleep(15)"
    elif mode == "stderr_blocked":
        script = "import sys, time; sys.stdout.write('out\\n'); sys.stdout.flush(); time.sleep(15)"
    else:
        script = "import time; time.sleep(15)"

    holder: list = []

    def _run():
        holder.append(
            mgr.run_command(
                command="printf",
                args=["-c", script],
                execution_id=f"pipe-{mode}",
                timeout_seconds=15.0,
            )
        )

    t = threading.Thread(target=_run)
    t.start()
    assert started.wait(timeout=3.0)
    time.sleep(0.1)

    assert mgr.cancel(f"pipe-{mode}") is True
    t.join(timeout=5.0)
    assert not t.is_alive(), f"Thread did not exit cleanly for {mode}"
    assert holder[0].status == ExecutionStatus.CANCELLED


def test_concurrent_cancel_and_shutdown_idempotent(ws: Path, monkeypatch: pytest.MonkeyPatch):
    """Concurrent cancel() and shutdown() must remain idempotent and never raise."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    started = threading.Event()
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=ws,
        lifecycle_listener=lambda eid, evt, p: started.set() if evt == "started" else None,
    )

    holder: list = []
    t = threading.Thread(
        target=lambda: holder.append(
            mgr.run_command(
                command="printf",
                args=["-c", "import time; time.sleep(15)"],
                execution_id="race-cancel-shutdown",
                timeout_seconds=15.0,
            )
        )
    )
    t.start()
    assert started.wait(timeout=3.0)
    time.sleep(0.1)

    t_c = threading.Thread(target=lambda: mgr.cancel("race-cancel-shutdown"))
    t_s = threading.Thread(target=lambda: mgr.shutdown(wait=True, timeout=3.0))
    t_c.start()
    t_s.start()
    t_c.join(timeout=5.0)
    t_s.join(timeout=5.0)
    t.join(timeout=5.0)

    assert not t.is_alive()
    assert holder[0].status == ExecutionStatus.CANCELLED


def test_cancel_racing_with_natural_completion(ws: Path, monkeypatch: pytest.MonkeyPatch):
    """Cancellation racing with fast natural completion never corrupts state."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=ws
    )
    for i in range(5):
        eid = f"race-nat-{i}"
        holder: list = []
        t = threading.Thread(
            target=lambda eid=eid, holder=holder: holder.append(
                mgr.run_command(
                    command="printf",
                    args=["-c", "print('fast')"],
                    execution_id=eid,
                )
            )
        )
        t.start()
        mgr.cancel(eid)
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert holder[0].status in (ExecutionStatus.COMPLETED, ExecutionStatus.CANCELLED)


# ---------------------------------------------------------------------------
# 4. UTF-8 Incremental Decoder Correctness
# ---------------------------------------------------------------------------


class _ChunkedBinaryStream:
    """Deterministic binary stream yielding exact byte slices per read1() call."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def read1(self, _n: int = 4096) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


def test_utf8_split_multibyte_character_reconstructed():
    """Multi-byte UTF-8 sequence split across read1() boundaries reconstructs without replacement."""
    rocket = "🚀".encode()  # 4 bytes: f0 9f 9a 80
    stream = _ChunkedBinaryStream([b"hi ", rocket[:2], rocket[2:], b"!"])

    emitted: list[str] = []
    batcher = OutputBatcher("utf8-split", lambda eid, s, text: emitted.append(text))
    buf = bytearray()
    overflow = [False]
    total_bytes = [0]

    _drain_stream(
        stream,  # type: ignore[arg-type]
        buf,
        max_bytes=1024,
        overflow_flag=overflow,
        lock=threading.Lock(),
        stream_name="stdout",
        batcher=batcher,
        total_bytes_counter=total_bytes,
    )
    batcher.close()

    assert "".join(emitted) == "hi 🚀!"
    assert buf.decode("utf-8") == "hi 🚀!"
    assert total_bytes[0] == len(b"hi ") + 4 + 1


def test_utf8_incomplete_trailing_sequence_flushed_with_replacement():
    """Incomplete trailing UTF-8 bytes at EOF flush with U+FFFD and count raw bytes accurately."""
    raw = b"abc\xf0\x9f"  # 5 raw bytes, incomplete 4-byte char at EOF
    stream = _ChunkedBinaryStream([raw])

    emitted: list[str] = []
    batcher = OutputBatcher("utf8-eof", lambda eid, s, text: emitted.append(text))
    buf = bytearray()
    overflow = [False]
    total_bytes = [0]

    _drain_stream(
        stream,  # type: ignore[arg-type]
        buf,
        max_bytes=1024,
        overflow_flag=overflow,
        lock=threading.Lock(),
        stream_name="stdout",
        batcher=batcher,
        total_bytes_counter=total_bytes,
    )
    batcher.close()

    decoded = "".join(emitted)
    assert decoded == "abc\ufffd"
    assert total_bytes[0] == 5  # Raw bytes, not len(decoded) == 4


# ---------------------------------------------------------------------------
# 5. QML Execution Drawer Runtime Verification
# ---------------------------------------------------------------------------


def test_qml_execution_drawer_controls_bounding_and_dismiss(
    ws: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify QML executionDrawer plain text, read-only, Stop/Dismiss, ID isolation, and display cap."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    app = QGuiApplication.instance() or QGuiApplication([])
    mock_service = MagicMock()
    mock_service.active_conversation_id = ""
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=ws
    )
    bridge = OrionBridge(assistant_service=mock_service, execution_manager=mgr)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("orion", bridge)
    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load"
    root = roots[0]

    stdout_disp = root.findChild(QObject, "stdoutDisplay")
    stderr_disp = root.findChild(QObject, "stderrDisplay")
    stop_btn = root.findChild(QObject, "stopExecutionButton")
    dismiss_btn = root.findChild(QObject, "dismissExecutionButton")
    assert stdout_disp is not None
    assert stderr_disp is not None
    assert stop_btn is not None
    assert dismiss_btn is not None

    # Read-only & PlainText enforcement (Main.qml sets textFormat: TextEdit.PlainText)
    assert stdout_disp.property("readOnly") is True
    assert stderr_disp.property("readOnly") is True
    qml_src = qml_file.read_text()
    assert 'objectName: "stdoutDisplay"' in qml_src
    assert 'objectName: "stderrDisplay"' in qml_src
    assert qml_src.count("textFormat: TextEdit.PlainText") >= 2

    # Start execution 1
    bridge.executionStarted.emit("exec-1", "printf", ["hi"], str(ws))
    app.processEvents()
    assert root.property("executionPanelVisible") is True
    assert root.property("executionStatus") == "running"
    assert stop_btn.property("visible") is True
    assert stop_btn.property("enabled") is True

    # Matching chunk updates stdout; mismatched executionId is ignored
    bridge.executionOutputChunk.emit("exec-1", "stdout", "<b>plain</b>")
    bridge.executionOutputChunk.emit("exec-other", "stdout", "SHOULD_NOT_APPEAR")
    app.processEvents()
    assert root.property("executionStdout") == "<b>plain</b>"

    # Bounding: pushing > 131072 chars truncates display and sets executionTruncated
    bridge.executionOutputChunk.emit("exec-1", "stdout", "X" * 140000)
    app.processEvents()
    assert len(root.property("executionStdout")) == 131072
    assert root.property("executionTruncated") is True

    # Finish execution 1 -> Stop becomes unavailable, Dismiss becomes visible
    bridge.executionFinished.emit("exec-1", "completed", 0, 125.0, True, "")
    app.processEvents()
    assert root.property("executionStatus") == "completed"
    assert stop_btn.property("visible") is False
    assert stop_btn.property("enabled") is False
    assert dismiss_btn.property("visible") is True

    # Simulate Dismiss: clears executionId so late chunks cannot mutate dismissed execution
    root.setProperty("executionPanelVisible", False)
    root.setProperty("executionId", "")
    bridge.executionOutputChunk.emit("exec-1", "stderr", "LATE_CHUNK")
    app.processEvents()
    assert root.property("executionStderr") == ""

    bridge.shutdown(timeout_ms=1000)
    engine.deleteLater()
    app.processEvents()
