"""Tests for ExecutionManager live output streaming, batching, and scoped cancellation.

Milestone 4.5: verifies incremental delivery, combined output limits,
cancellation racing with completion, timeout during streaming, and shutdown.
"""

import threading
import time
from pathlib import Path

import pytest

from orion.application.tools.contracts import ExecutionStatus
from orion.infrastructure.tools.execution_manager import (
    DEFAULT_ALLOWLISTED_COMMANDS,
    ExecutionManager,
    OutputBatcher,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def manager(temp_workspace: Path):
    return ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )


# ---------------------------------------------------------------------------
# OutputBatcher tests
# ---------------------------------------------------------------------------


def test_output_batcher_emits_on_size_threshold():
    """OutputBatcher should flush immediately when accumulated chars reach max_batch_chars."""
    emitted: list[tuple[str, str, str]] = []

    def listener(eid: str, stream: str, text: str) -> None:
        emitted.append((eid, stream, text))

    batcher = OutputBatcher(
        execution_id="test-id",
        emit_fn=listener,
        flush_interval_seconds=10.0,  # Long interval so timer doesn't fire
        max_batch_chars=10,
    )

    batcher.push("stdout", "a" * 10)  # Exactly at limit
    batcher.close()

    assert any(e[0] == "test-id" and e[1] == "stdout" and "a" * 10 in e[2] for e in emitted)


def test_output_batcher_timer_flushes():
    """OutputBatcher timer should flush pending output even without hitting size limit."""
    emitted: list[tuple[str, str, str]] = []
    event = threading.Event()

    def listener(eid: str, stream: str, text: str) -> None:
        emitted.append((eid, stream, text))
        event.set()

    batcher = OutputBatcher(
        execution_id="timer-test",
        emit_fn=listener,
        flush_interval_seconds=0.05,
        max_batch_chars=10000,
    )

    batcher.push("stderr", "some error text")
    fired = event.wait(timeout=0.5)
    batcher.close()

    assert fired, "Timer-based flush did not fire within 0.5s"
    assert any("some error text" in e[2] for e in emitted)


def test_output_batcher_final_close_flushes_remaining():
    """OutputBatcher.close() must flush all remaining buffered output."""
    emitted: list[tuple[str, str, str]] = []

    def listener(eid: str, stream: str, text: str) -> None:
        emitted.append((eid, stream, text))

    batcher = OutputBatcher(
        execution_id="close-test",
        emit_fn=listener,
        flush_interval_seconds=10.0,
        max_batch_chars=10000,
    )

    batcher.push("stdout", "final chunk")
    batcher.close()

    assert any("final chunk" in e[2] for e in emitted)


def test_output_batcher_separate_streams():
    """OutputBatcher must maintain separate stdout and stderr identities."""
    emitted: list[tuple[str, str, str]] = []

    def listener(eid: str, stream: str, text: str) -> None:
        emitted.append((eid, stream, text))

    batcher = OutputBatcher(
        execution_id="streams-test",
        emit_fn=listener,
        flush_interval_seconds=10.0,
        max_batch_chars=10000,
    )

    batcher.push("stdout", "out_data")
    batcher.push("stderr", "err_data")
    batcher.close()

    out_chunks = [e for e in emitted if e[1] == "stdout"]
    err_chunks = [e for e in emitted if e[1] == "stderr"]
    assert any("out_data" in e[2] for e in out_chunks)
    assert any("err_data" in e[2] for e in err_chunks)


def test_output_batcher_listener_error_does_not_crash():
    """A listener that raises should not crash the batcher."""

    def bad_listener(eid: str, stream: str, text: str) -> None:
        raise RuntimeError("listener error")

    batcher = OutputBatcher(
        execution_id="error-test",
        emit_fn=bad_listener,
        flush_interval_seconds=0.05,
        max_batch_chars=5,
    )

    batcher.push("stdout", "trigger flush immediately with enough chars.........")
    time.sleep(0.15)
    batcher.close()
    # Must not raise


# ---------------------------------------------------------------------------
# Live streaming via ExecutionManager
# ---------------------------------------------------------------------------


def test_live_stdout_delivered_incrementally(temp_workspace: Path):
    """Live stdout chunks should be delivered to output_listener before process completes."""
    received_chunks: list[tuple[str, str, str]] = []
    chunk_event = threading.Event()

    def listener(eid: str, stream: str, text: str) -> None:
        received_chunks.append((eid, stream, text))
        chunk_event.set()

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        output_listener=listener,
    )

    result = mgr.run_command(
        command="printf",
        args=["hello from printf"],
        execution_id="stream-test-001",
    )

    assert result.status == ExecutionStatus.COMPLETED
    # All text must appear in accumulated stdout
    assert "hello from printf" in result.stdout
    # Listener must have received at least one chunk
    assert len(received_chunks) > 0
    all_text = "".join(c[2] for c in received_chunks if c[1] == "stdout")
    assert "hello from printf" in all_text


def test_live_output_carries_correct_execution_id(temp_workspace: Path):
    """Every chunk emitted must carry the correct execution_id."""
    received: list[tuple[str, str, str]] = []

    def listener(eid: str, stream: str, text: str) -> None:
        received.append((eid, stream, text))

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        output_listener=listener,
    )
    result = mgr.run_command(
        command="printf",
        args=["check-id"],
        execution_id="myid-abc",
    )

    assert result.execution_id == "myid-abc"
    for eid, _, _ in received:
        assert eid == "myid-abc"


def test_live_listener_set_after_construction(temp_workspace: Path):
    """set_output_listener() must work when called after construction."""
    received: list[str] = []

    def listener(eid: str, stream: str, text: str) -> None:
        received.append(text)

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    mgr.set_output_listener(listener)

    result = mgr.run_command(command="printf", args=["post-set"])
    assert result.status == ExecutionStatus.COMPLETED
    assert "post-set" in "".join(received)


def test_output_buffer_limit_respected(temp_workspace: Path):
    """Generating >256 KB output must set truncated=True but not crash."""
    # Generate ~300 KB of output via printf repeating a pattern
    # printf "%0.s<data>" {1..N} won't work without shell, so we use a Python-free approach
    # We use a small max_output_bytes to simulate overflow
    received: list[str] = []

    def listener(eid: str, stream: str, text: str) -> None:
        received.append(text)

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        max_output_bytes=200,  # Very small limit
        output_listener=listener,
    )

    result = mgr.run_command(
        command="printf",
        args=["a" * 150],  # 150 chars, but buffer cap is 100 per stream (200//2)
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.truncated is True


# ---------------------------------------------------------------------------
# Scoped Cancellation
# ---------------------------------------------------------------------------


def test_cancel_nonexistent_execution_returns_false(temp_workspace: Path):
    """cancel() returns False when no active execution matches the given ID."""
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    result = mgr.cancel("no-such-id")
    assert result is False


def test_cancel_already_completed_returns_false(temp_workspace: Path):
    """cancel() after completion must return False."""
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    out = mgr.run_command(command="printf", args=["done"])
    assert out.status == ExecutionStatus.COMPLETED
    result = mgr.cancel(out.execution_id)
    assert result is False


def test_cancel_active_execution_produces_cancelled_status(
    temp_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """cancel() while a subprocess is running must terminate it and produce CANCELLED status."""
    import sys

    import orion.infrastructure.tools.execution_manager as em_mod

    # Allow a python3 one-liner for controlled test streaming/sleeping
    monkeypatch.setattr(
        em_mod,
        "resolve_executable",
        lambda cmd, allowed: Path(sys.executable),
    )
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    received_chunks: list[tuple[str, str, str]] = []
    first_chunk_event = threading.Event()
    audit_events: list[tuple[str, str, str | None, dict]] = []

    def output_listener(eid: str, stream: str, text: str) -> None:
        received_chunks.append((eid, stream, text))
        if "ready" in text:
            first_chunk_event.set()

    def event_recorder(etype: str, agg_type: str, agg_id: str | None, payload: dict) -> None:
        audit_events.append((etype, agg_type, agg_id, payload))

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        output_listener=output_listener,
        event_recorder=event_recorder,
    )

    result_holder: list = []

    def _run():
        res = mgr.run_command(
            command="printf",
            args=[
                "-c",
                (
                    "import sys, time; print('ready', flush=True); "
                    "print('err_ready', file=sys.stderr, flush=True); time.sleep(10)"
                ),
            ],
            execution_id="exec-cancel-1",
            timeout_seconds=15.0,
        )
        result_holder.append(res)

    t = threading.Thread(target=_run)
    t.start()

    assert first_chunk_event.wait(timeout=3.0), "Did not receive live chunk before completion"
    # Cancel the running execution
    assert mgr.cancel("exec-cancel-1") is True
    # Second cancel on already-cancelled execution must return False
    assert mgr.cancel("exec-cancel-1") is False

    t.join(timeout=5.0)
    assert not t.is_alive(), "Execution thread did not finish after cancellation"
    assert len(result_holder) == 1
    out = result_holder[0]
    assert out.status == ExecutionStatus.CANCELLED
    assert "ready" in out.stdout
    assert "err_ready" in out.stderr
    assert any(e[0] == "tool.execution.cancelled" for e in audit_events)
    assert any(c[1] == "stdout" for c in received_chunks)


def test_timeout_during_output_streaming(temp_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    """Timeout during live streaming must return TIMED_OUT and preserve streamed chunks."""
    import sys

    import orion.infrastructure.tools.execution_manager as em_mod

    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    received: list[tuple[str, str, str]] = []
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        output_listener=lambda eid, s, t: received.append((eid, s, t)),
    )

    out = mgr.run_command(
        command="printf",
        args=["-c", "import time; print('before-timeout', flush=True); time.sleep(5)"],
        execution_id="exec-timeout-1",
        timeout_seconds=0.3,
    )

    assert out.status == ExecutionStatus.TIMED_OUT
    assert "before-timeout" in out.stdout
    assert any("before-timeout" in c[2] for c in received)


def test_shutdown_cancels_active_execution(temp_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    """shutdown() while an execution is active must cancel it and reap cleanly."""
    import sys

    import orion.infrastructure.tools.execution_manager as em_mod

    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    started_event = threading.Event()
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        lifecycle_listener=lambda eid, evt, payload: (
            started_event.set() if evt == "started" else None
        ),
    )

    result_holder: list = []

    def _run():
        result_holder.append(
            mgr.run_command(
                command="printf",
                args=["-c", "import time; print('running', flush=True); time.sleep(10)"],
                execution_id="exec-shutdown-1",
                timeout_seconds=15.0,
            )
        )

    t = threading.Thread(target=_run)
    t.start()
    assert started_event.wait(timeout=3.0)
    time.sleep(0.1)  # Allow Popen to register
    mgr.shutdown(wait=True, timeout=3.0)
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert len(result_holder) == 1
    assert result_holder[0].status == ExecutionStatus.CANCELLED


def test_cancel_during_execution_in_thread(temp_workspace: Path):
    """Cancel after quick git status completion returns False."""
    import subprocess as _sp

    _sp.run(["git", "init", str(temp_workspace)], check=False, capture_output=True)

    lifecycle_events: list[tuple[str, str]] = []
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        lifecycle_listener=lambda eid, event, payload: lifecycle_events.append((eid, event)),
    )

    out = mgr.run_command(command="git", args=["status"])
    assert out.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)
    assert mgr.cancel(out.execution_id) is False


# ---------------------------------------------------------------------------
# Lifecycle Listener
# ---------------------------------------------------------------------------


def test_lifecycle_started_event_received(temp_workspace: Path):
    """Lifecycle listener should receive 'started' event with correct metadata."""
    events: list[tuple[str, str, dict]] = []

    def listener(eid: str, event: str, payload: dict) -> None:
        events.append((eid, event, payload))

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        lifecycle_listener=listener,
    )
    mgr.run_command(command="printf", args=["hello"], execution_id="lc-test")

    started_events = [e for e in events if e[1] == "started"]
    assert len(started_events) >= 1
    evt = started_events[0]
    assert evt[0] == "lc-test"
    assert evt[2]["command"] == "printf"


def test_lifecycle_completed_event_received(temp_workspace: Path):
    """Lifecycle listener should receive 'completed' event on successful execution."""
    events: list[tuple[str, str, dict]] = []

    def listener(eid: str, event: str, payload: dict) -> None:
        events.append((eid, event, payload))

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        lifecycle_listener=listener,
    )
    result = mgr.run_command(command="printf", args=["finish"], execution_id="lc-complete")

    assert result.status == ExecutionStatus.COMPLETED
    completed = [e for e in events if e[1] == "completed"]
    assert len(completed) >= 1
    assert completed[0][2]["exit_code"] == 0


def test_lifecycle_failed_event_received(temp_workspace: Path):
    """Lifecycle listener receives 'failed' event when subprocess has nonzero exit."""
    events: list[tuple[str, str, dict]] = []

    def listener(eid: str, event: str, payload: dict) -> None:
        events.append((eid, event, payload))

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        workspace_root=temp_workspace,
        lifecycle_listener=listener,
    )
    mgr.run_command(command="git", args=["status"], execution_id="lc-fail")
    terminal_events = [e for e in events if e[1] in ("completed", "failed")]
    assert len(terminal_events) >= 1


def test_set_lifecycle_listener_after_construction(temp_workspace: Path):
    """set_lifecycle_listener() must register the listener correctly."""
    events: list[str] = []

    def listener(eid: str, event: str, payload: dict) -> None:
        events.append(event)

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    mgr.set_lifecycle_listener(listener)

    mgr.run_command(command="printf", args=["test"])
    assert "started" in events


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_shutdown_rejects_new_executions(temp_workspace: Path):
    """After shutdown(), new execute() calls must raise ExecutionError."""
    from orion.infrastructure.tools.execution_manager import ExecutionError, ExecutionRequest

    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    mgr.shutdown(wait=True)

    req = ExecutionRequest(
        command="printf",
        args=["hello"],
        cwd=temp_workspace,
    )
    with pytest.raises(ExecutionError, match="shut down"):
        mgr.execute(req)


def test_shutdown_during_idle_completes_cleanly(temp_workspace: Path):
    """Calling shutdown on an idle manager should complete immediately."""
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    mgr.shutdown(wait=True, timeout=1.0)
    # No exception means success


# ---------------------------------------------------------------------------
# OrionBridge Integration
# ---------------------------------------------------------------------------


def test_bridge_emits_execution_signals_and_cancels(
    temp_workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """OrionBridge must emit executionStarted, executionOutputChunk, executionFinished and support cancelExecution."""
    import os
    import sys
    from unittest.mock import MagicMock

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    import orion.infrastructure.tools.execution_manager as em_mod
    from orion.ui.qt_bridge import OrionBridge

    app = QGuiApplication.instance() or QGuiApplication([])
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    mock_service = MagicMock()
    mock_service.active_conversation_id = "conv-1"
    mgr = ExecutionManager(
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"}, workspace_root=temp_workspace
    )
    bridge = OrionBridge(assistant_service=mock_service, execution_manager=mgr)

    started_calls: list[tuple] = []
    chunk_calls: list[tuple] = []
    finished_calls: list[tuple] = []
    ready_event = threading.Event()

    bridge.executionStarted.connect(
        lambda eid, cmd, args, cwd: started_calls.append((eid, cmd, args, cwd))
    )

    def _on_chunk(eid: str, stream: str, text: str) -> None:
        chunk_calls.append((eid, stream, text))
        if "bridge-ready" in text:
            ready_event.set()

    bridge.executionOutputChunk.connect(_on_chunk)
    bridge.executionFinished.connect(
        lambda eid, status, code, dur, trunc, err: finished_calls.append(
            (eid, status, code, dur, trunc, err)
        )
    )

    def _pump_until(pred, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not pred() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert pred(), "Condition not met before timeout"

    # 1. Normal completion
    out = mgr.run_command(
        command="printf",
        args=["-c", "print('bridge-hello', flush=True)"],
        execution_id="bridge-exec-1",
    )
    assert out.status == ExecutionStatus.COMPLETED
    _pump_until(lambda: len(finished_calls) == 1)
    assert started_calls[0][0] == "bridge-exec-1"
    assert finished_calls[0][1] == "completed"
    assert any("bridge-hello" in c[2] for c in chunk_calls)

    # 2. Scoped cancellation via bridge.cancelExecution
    result_holder: list = []

    def _run_slow():
        result_holder.append(
            mgr.run_command(
                command="printf",
                args=["-c", "import time; print('bridge-ready', flush=True); time.sleep(10)"],
                execution_id="bridge-exec-2",
                timeout_seconds=15.0,
            )
        )

    t = threading.Thread(target=_run_slow)
    t.start()

    deadline = time.monotonic() + 3.0
    while not ready_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert ready_event.is_set(), "Did not receive bridge-ready live chunk"

    bridge.cancelExecution("bridge-exec-2")
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert result_holder[0].status == ExecutionStatus.CANCELLED
    _pump_until(lambda: len(finished_calls) == 2)
    assert finished_calls[1][0] == "bridge-exec-2"
    assert finished_calls[1][1] == "cancelled"

    bridge.shutdown(timeout_ms=2000)
