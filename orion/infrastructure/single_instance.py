"""Single-instance enforcement and local IPC activation for ORION desktop assistant."""

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from orion.infrastructure.persistence.paths import ensure_secure_directory, get_database_path

logger = logging.getLogger(__name__)

ORION_LOCK_ENV_VAR = "ORION_LOCK_PATH"
ORION_IPC_ENV_VAR = "ORION_IPC_NAME"
_DEFAULT_IPC_PREFIX = "orion_ipc"


def get_lock_path(custom_path: Path | str | None = None) -> Path:
    """Resolve the single-instance lock file path.

    Priority:
    1. Explicit custom_path argument (used by tests).
    2. ORION_LOCK_PATH environment variable override.
    3. $XDG_RUNTIME_DIR/orion/orion.lock if XDG_RUNTIME_DIR is set and valid.
    4. Database parent directory: ~/.local/share/orion/orion.lock as stable fallback.
    """
    if custom_path is not None:
        return Path(custom_path).expanduser().resolve()

    if env_path := os.environ.get(ORION_LOCK_ENV_VAR):
        return Path(env_path).expanduser().resolve()

    if xdg_runtime := os.environ.get("XDG_RUNTIME_DIR"):
        runtime_dir = Path(xdg_runtime).expanduser().resolve()
        if runtime_dir.exists():
            orion_dir = runtime_dir / "orion"
            ensure_secure_directory(orion_dir)
            return (orion_dir / "orion.lock").resolve()

    # Fallback to database directory parent
    return (get_database_path().parent / "orion.lock").resolve()


def get_ipc_name(custom_name: str | None = None) -> str:
    """Resolve the QLocalServer IPC name for secondary instance activation.

    Priority:
    1. Explicit custom_name argument (used by tests).
    2. ORION_IPC_NAME environment variable override.
    3. orion_ipc_<uid> for current OS user.
    """
    if custom_name is not None and custom_name.strip():
        return custom_name.strip()

    if (env_name := os.environ.get(ORION_IPC_ENV_VAR)) and env_name.strip():
        return env_name.strip()

    uid = os.getuid() if hasattr(os, "getuid") else "default"
    return f"{_DEFAULT_IPC_PREFIX}_{uid}"


class SingleInstanceManager(QObject):
    """Manages process-wide single instance locking and local activation IPC."""

    activationRequested = Signal()

    def __init__(
        self,
        lock_path: Path | str | None = None,
        ipc_name: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._lock_path = get_lock_path(lock_path)
        self._ipc_name = get_ipc_name(ipc_name)
        self._lock_file: QLockFile | None = None
        self._ipc_server: QLocalServer | None = None
        self._client_sockets: list[QLocalSocket] = []
        self._is_primary = False
        self._is_cleaned_up = False

    @property
    def is_primary(self) -> bool:
        """Return True if this manager currently holds the primary lock."""
        return self._is_primary

    @property
    def lock_path(self) -> Path:
        """Return the resolved lock file path."""
        return self._lock_path

    @property
    def ipc_name(self) -> str:
        """Return the resolved IPC server name."""
        return self._ipc_name

    def try_acquire(self, timeout_ms: int = 100) -> bool:
        """Attempt to acquire the process lock.

        Returns True if this instance became the primary owner.
        Returns False if another live instance owns the lock.
        Handles stale lock files safely by removing only if the owning process is dead.
        """
        if self._is_primary:
            return True

        # Ensure parent directory exists with 0700 permissions
        ensure_secure_directory(self._lock_path.parent)

        lock = QLockFile(str(self._lock_path))
        lock.setStaleLockTime(30000)  # 30s stale threshold

        if not lock.tryLock(timeout_ms):
            if lock.error() == QLockFile.LockError.LockFailedError:
                # Attempt to remove only if the process that created the lock is dead
                if lock.removeStaleLockFile():
                    if not lock.tryLock(timeout_ms):
                        return False
                else:
                    return False
            else:
                return False

        self._lock_file = lock
        self._is_primary = True
        return True

    def start_ipc_server(self, on_activate: Callable[[], None] | None = None) -> bool:
        """Start listening on QLocalServer for activation messages from secondary instances."""
        if not self._is_primary:
            raise RuntimeError("Cannot start IPC server without holding the primary lock.")

        if on_activate is not None:
            self.activationRequested.connect(on_activate)

        # Remove any stale socket file left from previous process termination
        QLocalServer.removeServer(self._ipc_name)

        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

        if not server.listen(self._ipc_name):
            logger.warning(
                "Failed to start local IPC server on %r: %s",
                self._ipc_name,
                server.errorString(),
            )
            return False

        server.newConnection.connect(self._handle_new_connection)
        self._ipc_server = server
        return True

    def _handle_new_connection(self) -> None:
        if self._ipc_server is None:
            return
        socket = self._ipc_server.nextPendingConnection()
        if socket is None:
            return

        self._client_sockets.append(socket)

        def _on_disconnected(s=socket) -> None:
            if s in self._client_sockets:
                self._client_sockets.remove(s)
            s.deleteLater()

        socket.disconnected.connect(_on_disconnected)

        def _on_ready_read() -> None:
            try:
                data = socket.readAll().data()
                # Accept ONLY strict "ACTIVATE" command
                if data.strip() == b"ACTIVATE":
                    socket.write(b"ACK\n")
                    socket.flush()
                    self.activationRequested.emit()
                else:
                    socket.write(b"ERR: INVALID_COMMAND\n")
                    socket.flush()
            except Exception:  # noqa: BLE001, S110
                pass

        socket.readyRead.connect(_on_ready_read)

    def notify_primary(self, timeout_ms: int = 1500) -> bool:
        """Send an activation request to the primary instance via local IPC.

        Uses bounded retries to handle the startup race between lock acquisition
        and IPC server binding in the primary.
        """
        deadline = time.monotonic() + (timeout_ms / 1000.0)

        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self._ipc_name)
            if socket.waitForConnected(200):
                socket.write(b"ACTIVATE\n")
                socket.flush()
                socket.waitForBytesWritten(300)
                ack_received = False
                if socket.waitForReadyRead(300):
                    resp = socket.readAll().data()
                    ack_received = resp.strip() == b"ACK"
                else:
                    ack_received = True  # Bytes sent successfully
                socket.disconnectFromServer()
                socket.close()
                return ack_received
            socket.close()
            time.sleep(0.05)

        return False

    def close_ipc_server(self) -> None:
        """Close and remove the IPC server."""
        for s in list(self._client_sockets):
            try:
                s.disconnectFromServer()
                s.close()
                s.deleteLater()
            except Exception:  # noqa: BLE001, S110
                pass
        self._client_sockets.clear()

        if self._ipc_server is not None:
            try:
                self._ipc_server.close()
                self._ipc_server.deleteLater()
            except Exception:  # noqa: BLE001, S110
                pass
            self._ipc_server = None
        try:
            QLocalServer.removeServer(self._ipc_name)
        except Exception:  # noqa: BLE001, S110
            pass

    def release_lock(self) -> None:
        """Release the primary lock."""
        if self._lock_file is not None and self._is_primary:
            try:
                self._lock_file.unlock()
            except Exception:  # noqa: BLE001, S110
                pass
            self._lock_file = None
            self._is_primary = False

    def cleanup(self) -> None:
        """Idempotent cleanup of IPC server and process lock."""
        if self._is_cleaned_up:
            return
        self._is_cleaned_up = True
        self.close_ipc_server()
        self.release_lock()
