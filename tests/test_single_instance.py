"""Tests for single-instance enforcement and local IPC activation (Milestone 3.6 Part B)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication

from orion.infrastructure.single_instance import (
    SingleInstanceManager,
    get_ipc_name,
    get_lock_path,
)


@pytest.fixture(scope="session")
def qapp():
    """Ensure a QGuiApplication exists for Qt event handling."""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def test_lock_path_resolution(tmp_path: Path, monkeypatch):
    """Custom path, environment variable, and fallback resolution rules."""
    custom = tmp_path / "custom.lock"
    assert get_lock_path(custom) == custom.resolve()

    monkeypatch.setenv("ORION_LOCK_PATH", str(tmp_path / "env.lock"))
    assert get_lock_path() == (tmp_path / "env.lock").resolve()

    monkeypatch.delenv("ORION_LOCK_PATH", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    (tmp_path / "runtime").mkdir()
    resolved = get_lock_path()
    assert resolved == (tmp_path / "runtime" / "orion" / "orion.lock").resolve()


def test_ipc_name_resolution(monkeypatch):
    """Custom name, environment variable, and user fallback resolution."""
    assert get_ipc_name("custom_ipc") == "custom_ipc"

    monkeypatch.setenv("ORION_IPC_NAME", "env_ipc_channel")
    assert get_ipc_name() == "env_ipc_channel"

    monkeypatch.delenv("ORION_IPC_NAME", raising=False)
    uid = os.getuid() if hasattr(os, "getuid") else "default"
    assert get_ipc_name() == f"orion_ipc_{uid}"


def test_first_instance_acquires_lock(qapp, tmp_path: Path):
    """The first instance acquires the lock successfully and becomes primary."""
    lock_file = tmp_path / "test1.lock"
    mgr = SingleInstanceManager(lock_path=lock_file, ipc_name="test_ipc_1")
    try:
        assert mgr.try_acquire() is True
        assert mgr.is_primary is True
        assert lock_file.exists()
    finally:
        mgr.cleanup()

    assert mgr.is_primary is False


def test_second_instance_cannot_acquire_lock(qapp, tmp_path: Path):
    """A second instance targeting the same lock path cannot acquire the lock."""
    lock_file = tmp_path / "test2.lock"
    mgr1 = SingleInstanceManager(lock_path=lock_file, ipc_name="test_ipc_2")
    mgr2 = SingleInstanceManager(lock_path=lock_file, ipc_name="test_ipc_2")

    try:
        assert mgr1.try_acquire() is True
        assert mgr1.is_primary is True

        assert mgr2.try_acquire(timeout_ms=50) is False
        assert mgr2.is_primary is False
    finally:
        mgr1.cleanup()
        qapp.processEvents()
        mgr2.cleanup()


def test_secondary_activation_via_ipc(qapp, tmp_path: Path):
    """Secondary instance running in a separate process notifies primary via IPC."""
    lock_file = tmp_path / "test3.lock"
    ipc_name = f"test_ipc_act_{os.getpid()}_{int(time.time() * 1000)}"
    mgr1 = SingleInstanceManager(lock_path=lock_file, ipc_name=ipc_name)

    activations = []
    try:
        assert mgr1.try_acquire() is True
        assert mgr1.start_ipc_server(on_activate=lambda: activations.append(True)) is True

        # Launch secondary instance as clean subprocess
        script = f"""
from orion.infrastructure.single_instance import SingleInstanceManager
mgr = SingleInstanceManager(lock_path={str(lock_file)!r}, ipc_name={ipc_name!r})
if not mgr.try_acquire(timeout_ms=50):
    notified = mgr.notify_primary(timeout_ms=2000)
    print("NOTIFIED" if notified else "FAILED")
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        start = time.time()
        while time.time() - start < 3.0 and len(activations) == 0:
            qapp.processEvents()
            time.sleep(0.05)

        stdout, _ = proc.communicate(timeout=3.0)
        assert "NOTIFIED" in stdout
        assert len(activations) == 1
    finally:
        mgr1.cleanup()
        qapp.processEvents()


def test_ipc_rejects_invalid_commands(qapp, tmp_path: Path):
    """IPC channel only accepts 'ACTIVATE' and ignores arbitrary or malformed input."""
    lock_file = tmp_path / "test4.lock"
    ipc_name = f"test_ipc_sec_{os.getpid()}_{int(time.time() * 1000)}"
    mgr = SingleInstanceManager(lock_path=lock_file, ipc_name=ipc_name)
    activations = []

    try:
        assert mgr.try_acquire() is True
        assert mgr.start_ipc_server(on_activate=lambda: activations.append(True)) is True

        script = f"""
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalSocket
app = QGuiApplication(["-platform", "offscreen"])
sock = QLocalSocket()
sock.connectToServer({ipc_name!r})
if sock.waitForConnected(1000):
    sock.write(b"EXEC rm -rf /\\n")
    sock.flush()
    sock.waitForBytesWritten(500)
    if sock.waitForReadyRead(1000):
        print(sock.readAll().data().decode("utf-8", errors="replace"))
sock.close()
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        start = time.time()
        while time.time() - start < 3.0 and proc.poll() is None:
            qapp.processEvents()
            time.sleep(0.05)

        stdout, _ = proc.communicate(timeout=3.0)
        assert "INVALID_COMMAND" in stdout
        assert len(activations) == 0
    finally:
        mgr.cleanup()


def test_cleanup_is_idempotent(qapp, tmp_path: Path):
    """Calling cleanup multiple times is safe and releases the lock."""
    lock_file = tmp_path / "test5.lock"
    mgr = SingleInstanceManager(lock_path=lock_file, ipc_name="test_ipc_5")
    assert mgr.try_acquire() is True
    assert mgr.start_ipc_server() is True

    mgr.cleanup()
    assert mgr.is_primary is False

    # Second cleanup call must not raise
    mgr.cleanup()
    assert mgr.is_primary is False


def test_live_owners_lock_is_never_removed(qapp, tmp_path: Path):
    """A lock held by a live process is never removed by another instance."""
    lock_file = tmp_path / "live_proc.lock"
    script = f"""
import time
from orion.infrastructure.single_instance import SingleInstanceManager
mgr = SingleInstanceManager(lock_path={str(lock_file)!r}, ipc_name="ipc_live")
if mgr.try_acquire():
    print("LOCKED")
    sys.stdout.flush()
    time.sleep(1.5)
    mgr.cleanup()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", f"import sys\n{script}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait until child has acquired lock
        line = proc.stdout.readline()
        assert "LOCKED" in line

        mgr = SingleInstanceManager(lock_path=lock_file, ipc_name="ipc_secondary")
        # try_acquire must fail because the owner process is actively alive
        acquired = mgr.try_acquire(timeout_ms=50)
        assert acquired is False
        assert mgr.is_primary is False
    finally:
        proc.wait(timeout=3.0)
        # After process termination, a new instance can acquire
        mgr = SingleInstanceManager(lock_path=lock_file, ipc_name="ipc_new")
        assert mgr.try_acquire(timeout_ms=200) is True
        mgr.cleanup()
