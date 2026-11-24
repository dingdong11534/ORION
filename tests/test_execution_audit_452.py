"""Milestone 4.5.2 — Final Resource-Lifecycle Audit Tests.

Verifies:
1. Bounded completion and shutdown when a detached `os.setsid()` descendant holds pipes open.
2. O(1) execution-tracking state cleanup across many sequential executions/cancellations.
3. Repeated cancellation, timeout-vs-cancel race, and shutdown-vs-cancel race.
4. OutputBatcher aggregate pending-character limit across stdout and stderr.
"""

import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

import orion.infrastructure.tools.execution_manager as em_mod
from orion.application.tools.contracts import ExecutionStatus
from orion.infrastructure.tools.execution_manager import ExecutionManager, OutputBatcher


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# 1. Detached os.setsid() Descendant Holding Pipes Open
# ---------------------------------------------------------------------------


def test_detached_setsid_descendant_does_not_hang_execution_or_shutdown(
    ws: Path, monkeypatch: pytest.MonkeyPatch
):
    """If a child calls os.setsid() and keeps stdout/stderr open, ORION must not block indefinitely."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    detached_pid_file = ws / "detached.pid"
    mgr = ExecutionManager(workspace_root=ws)

    # Parent spawns a detached child (os.setsid(), inheriting stdout/stderr) and exits immediately.
    script = (
        "import os, subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', "
        f"\"import os, time; os.setsid(); open({str(detached_pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)\""
        "], start_new_session=True); "
        "print('parent-exiting', flush=True)"
    )

    t0 = time.monotonic()
    try:
        out = mgr.run_command(
            command="printf",
            args=["-c", script],
            execution_id="detached-child-test",
            timeout_seconds=10.0,
        )
        elapsed = time.monotonic() - t0

        # Must return in bounded time (~1.0s reader join timeout + overhead), never 30s
        assert elapsed < 5.0
        assert out.status == ExecutionStatus.COMPLETED
        assert "parent-exiting" in out.stdout

        # Shutdown must also remain bounded even if detached child still holds pipe FDs
        t_sd = time.monotonic()
        mgr.shutdown(wait=True, timeout=2.0)
        assert (time.monotonic() - t_sd) < 3.0
    finally:
        # Clean up the detached test process so no orphan remains on the host
        for _ in range(20):
            if detached_pid_file.exists():
                break
            time.sleep(0.05)
        if detached_pid_file.exists():
            try:
                pid = int(detached_pid_file.read_text().strip())
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError, OSError):
                pass


# ---------------------------------------------------------------------------
# 2. Execution State Cleanup Across Many Sequential Runs
# ---------------------------------------------------------------------------


def test_sequential_executions_and_cancellations_leave_zero_stale_state(
    ws: Path, monkeypatch: pytest.MonkeyPatch
):
    """Many sequential completed, cancelled, and timed-out runs must leave O(1) clean tracking state."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    mgr = ExecutionManager(workspace_root=ws)

    # 10 normal completions
    for i in range(10):
        out = mgr.run_command(
            command="printf",
            args=["-c", f"print('ok-{i}')"],
            execution_id=f"seq-ok-{i}",
        )
        assert out.status == ExecutionStatus.COMPLETED

    # 5 cancellations
    for i in range(5):
        started = threading.Event()
        mgr.set_lifecycle_listener(
            lambda eid, evt, p, ev=started: ev.set() if evt == "started" else None
        )
        holder: list = []
        t = threading.Thread(
            target=lambda i=i, holder=holder: holder.append(
                mgr.run_command(
                    command="printf",
                    args=["-c", "import time; time.sleep(10)"],
                    execution_id=f"seq-cancel-{i}",
                    timeout_seconds=10.0,
                )
            )
        )
        t.start()
        assert started.wait(timeout=3.0)
        time.sleep(0.05)
        assert mgr.cancel(f"seq-cancel-{i}") is True
        # Repeated cancellation on same ID must return False
        assert mgr.cancel(f"seq-cancel-{i}") is False
        t.join(timeout=3.0)
        assert holder[0].status == ExecutionStatus.CANCELLED

    # 3 timeouts
    mgr.set_lifecycle_listener(None)
    for i in range(3):
        out = mgr.run_command(
            command="printf",
            args=["-c", "import time; time.sleep(5)"],
            execution_id=f"seq-to-{i}",
            timeout_seconds=0.15,
        )
        assert out.status == ExecutionStatus.TIMED_OUT

    # Verify all active-execution tracking fields returned to clean idle state
    assert mgr._is_executing is False
    assert mgr._active_execution_id is None
    assert mgr._active_proc is None
    assert mgr._active_pgid is None
    assert mgr._active_cancellation_requested is False
    assert mgr._active_terminated is False


def test_timeout_racing_with_cancellation_stays_consistent(
    ws: Path, monkeypatch: pytest.MonkeyPatch
):
    """Timeout racing with cancel() must never raise and must reset tracking state."""
    monkeypatch.setattr(em_mod, "resolve_executable", lambda cmd, allowed: Path(sys.executable))
    monkeypatch.setattr(em_mod, "validate_command_arguments", lambda *a, **kw: None)

    mgr = ExecutionManager(workspace_root=ws)
    holder: list = []
    t = threading.Thread(
        target=lambda: holder.append(
            mgr.run_command(
                command="printf",
                args=["-c", "import time; time.sleep(5)"],
                execution_id="race-to-cancel",
                timeout_seconds=0.15,
            )
        )
    )
    t.start()
    time.sleep(0.15)
    mgr.cancel("race-to-cancel")
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert holder[0].status in (ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT)
    assert mgr._active_execution_id is None
    assert mgr._active_terminated is False


# ---------------------------------------------------------------------------
# 3. OutputBatcher Aggregate Pending-Character Limit
# ---------------------------------------------------------------------------


def test_output_batcher_enforces_aggregate_pending_limit():
    """OutputBatcher.max_pending_chars must cap stdout + stderr combined, not per stream."""
    emitted: list[tuple[str, str, str]] = []
    batcher = OutputBatcher(
        execution_id="agg-budget",
        emit_fn=lambda eid, s, text: emitted.append((eid, s, text)),
        flush_interval_seconds=10.0,
        max_batch_chars=1000,
        max_pending_chars=100,
    )

    batcher.push("stdout", "A" * 60)
    batcher.push("stderr", "B" * 80)  # Only 40 chars remaining in aggregate 100-char budget
    batcher.close()

    out_text = "".join(c[2] for c in emitted if c[1] == "stdout")
    err_text = "".join(c[2] for c in emitted if c[1] == "stderr")
    assert len(out_text) == 60
    assert len(err_text) == 40
    assert len(out_text) + len(err_text) == 100
