"""Security-critical path resolver confining file access to the authorized workspace root."""

import errno
import logging
import os
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

# Sensitive path components and file names strictly blocked from inspection
SENSITIVE_NAMES: frozenset[str] = frozenset(
    [
        ".ssh",
        ".gnupg",
        ".bashrc",
        ".bash_profile",
        ".profile",
        ".zshrc",
        ".env",
        ".netrc",
        "id_rsa",
        "id_ed25519",
        "shadow",
        "passwd",
    ]
)

# Standard directories excluded from default directory listing / recursive search
DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    [
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        "dist",
        "build",
    ]
)


class WorkspaceSecurityError(Exception):
    """Raised when a path violates workspace confinement or security constraints."""


def get_configured_workspace_root() -> Path:
    """Resolve and validate the configured workspace root from environment.

    Defaults to ~/Projects/ORION for local development and live verification.
    Raises RuntimeError if directory does not exist or is not a directory.
    """
    env_val = os.environ.get("ORION_WORKSPACE_ROOT")
    if env_val:
        root = Path(env_val).expanduser().resolve()
    else:
        root = Path("~/Projects/ORION").expanduser().resolve()

    if not root.exists():
        raise RuntimeError(
            f"Configured ORION_WORKSPACE_ROOT '{root}' does not exist. "
            "Please configure a valid workspace directory via ORION_WORKSPACE_ROOT."
        )
    if not root.is_dir():
        raise RuntimeError(f"Configured ORION_WORKSPACE_ROOT '{root}' is not a directory.")
    return root


class WorkspacePathResolver:
    """Confines and resolves file paths strictly within an authorized workspace root.

    Security Guarantees:
    1. Rejects absolute paths (`/etc/passwd`, `C:\\foo`).
    2. Rejects path traversal components (`..`).
    3. Prevents prefix confusion (`/workspace_evil` vs `/workspace`).
    4. Rejects symbolic links at any path level (intermediate and terminal) using
       Linux directory descriptor `openat` and `O_NOFOLLOW` flag to prevent TOCTOU races.
    5. Blocks access to sensitive names and prohibited path fragments.
    6. Compatible with Ubuntu and Python 3.12.
    """

    def __init__(self, workspace_root: Path | str) -> None:
        root_path = Path(workspace_root).expanduser().resolve()
        if not root_path.exists():
            raise ValueError(f"Workspace root '{root_path}' does not exist.")
        if not root_path.is_dir():
            raise ValueError(f"Workspace root '{root_path}' is not a directory.")
        self._root = root_path

    @property
    def workspace_root(self) -> Path:
        """Return the canonical, resolved workspace root path."""
        return self._root

    def sanitize_relative_path(self, raw_path: str) -> tuple[str, ...]:
        """Validate and split a raw relative path string into safe components.

        Raises:
            WorkspaceSecurityError: If the path is absolute, traverses out, or contains sensitive names.
        """
        if not isinstance(raw_path, str):
            raise WorkspaceSecurityError("Path must be a string.")

        # Reject null bytes
        if "\0" in raw_path:
            raise WorkspaceSecurityError("Path contains null bytes.")

        trimmed = raw_path.strip()
        if not trimmed:
            return ()

        # Reject absolute paths
        if trimmed.startswith(("/", "\\")) or os.path.isabs(trimmed):
            raise WorkspaceSecurityError(f"Absolute paths are rejected: {raw_path!r}")

        # Check path components
        parts = Path(trimmed).parts
        for part in parts:
            if part in ("..", "/", "\\"):
                raise WorkspaceSecurityError(f"Path traversal detected in path: {raw_path!r}")
            if part.lower() in SENSITIVE_NAMES:
                raise WorkspaceSecurityError(
                    f"Access to sensitive path '{part}' is strictly prohibited."
                )

        return parts

    def resolve_path(
        self,
        raw_path: str,
        must_exist: bool = False,
        require_dir: bool = False,
        require_file: bool = False,
    ) -> Path:
        """Resolve a relative path, verifying confinement and rejecting symlinks.

        Performs:
        1. Syntactic validation (no abs, no ..).
        2. Prefix-confusion verification (`root in candidate.parents`).
        3. Descriptor-based component walk using `O_NOFOLLOW` on Linux to prevent TOCTOU symlink escapes.
        """
        parts = self.sanitize_relative_path(raw_path)

        if not parts:
            # Workspace root itself
            if require_file:
                raise WorkspaceSecurityError("Workspace root is a directory, not a file.")
            return self._root

        # 1. Dynamic descriptor-based walk verifying no intermediate or terminal symlinks
        self._verify_no_symlinks_walk(parts, must_exist=must_exist)

        # 2. Static prefix-confusion check on canonical path
        candidate = (self._root / Path(*parts)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise WorkspaceSecurityError(f"Path escapes workspace root: {raw_path!r}")

        if must_exist:
            if not candidate.exists():
                raise FileNotFoundError(f"Path '{raw_path}' does not exist in workspace.")
            if require_dir and not candidate.is_dir():
                raise NotADirectoryError(f"Path '{raw_path}' is not a directory.")
            if require_file and not candidate.is_file():
                raise IsADirectoryError(f"Path '{raw_path}' is not a regular file.")

        return candidate

    def _verify_no_symlinks_walk(self, parts: tuple[str, ...], must_exist: bool) -> None:
        """Walk path components using directory descriptors and O_NOFOLLOW to detect symlinks."""
        try:
            current_fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError as exc:
            raise WorkspaceSecurityError(f"Failed to open workspace root directory: {exc}") from exc

        fds_to_close = [current_fd]
        try:
            for idx, part in enumerate(parts):
                is_last = idx == len(parts) - 1
                # Try opening component with O_NOFOLLOW
                try:
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                    # If intermediate, must be directory
                    if not is_last:
                        flags |= os.O_DIRECTORY
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                    fds_to_close.append(next_fd)
                    current_fd = next_fd
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise WorkspaceSecurityError(
                            f"Symlinks are prohibited within workspace: component '{part}' is a symlink."
                        ) from exc
                    if exc.errno == errno.ENOENT:
                        if must_exist:
                            raise FileNotFoundError(f"Component '{part}' does not exist.") from exc
                        # Path does not exist yet; stop walk
                        break
                    if exc.errno == errno.ENOTDIR:
                        # In Linux POSIX openat, opening a symlink to a directory with
                        # O_NOFOLLOW | O_DIRECTORY raises ENOTDIR. Check if it is a symlink.
                        try:
                            st = os.lstat(part, dir_fd=current_fd)
                            import stat

                            if stat.S_ISLNK(st.st_mode):
                                raise WorkspaceSecurityError(
                                    f"Symlinks are prohibited within workspace: component '{part}' is a symlink."
                                ) from exc
                        except OSError:
                            pass

                        if not is_last:
                            raise NotADirectoryError(
                                f"Intermediate component '{part}' is not a directory."
                            ) from exc
                    else:
                        logger.debug("Component '%s' open check raised errno %s", part, exc.errno)
        finally:
            for fd in fds_to_close:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def open_file_safely(self, raw_path: str) -> BinaryIO:
        """Safely open a workspace file for reading without following symlinks.

        Uses directory descriptors and `O_NOFOLLOW` directly into `os.fdopen`.
        Eliminates Time-of-Check to Time-of-Use (TOCTOU) symlink substitution.
        """
        parts = self.sanitize_relative_path(raw_path)
        if not parts:
            raise IsADirectoryError("Cannot open workspace root as a file.")

        # Open root dir
        root_fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        current_fd = root_fd
        fds_to_close = [root_fd]

        try:
            # Walk directory components
            for part in parts[:-1]:
                try:
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=current_fd,
                    )
                    fds_to_close.append(next_fd)
                    current_fd = next_fd
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise WorkspaceSecurityError(
                            f"Component '{part}' is a symlink (symlinks are prohibited)."
                        ) from exc
                    if exc.errno == errno.ENOTDIR:
                        try:
                            st = os.lstat(part, dir_fd=current_fd)
                            import stat

                            if stat.S_ISLNK(st.st_mode):
                                raise WorkspaceSecurityError(
                                    f"Component '{part}' is a symlink (symlinks are prohibited)."
                                ) from exc
                        except OSError:
                            pass
                        raise NotADirectoryError(f"Component '{part}' is not a directory.") from exc
                    raise

            # Open target file
            filename = parts[-1]
            try:
                file_fd = os.open(
                    filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current_fd
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise WorkspaceSecurityError(
                        f"File '{filename}' is a symlink (symlinks are prohibited)."
                    ) from exc
                if exc.errno == errno.ENOENT:
                    raise FileNotFoundError(f"File '{raw_path}' not found in workspace.") from exc
                raise

            # Verify it is a regular file (not directory, socket, fifo)
            stat_res = os.fstat(file_fd)
            import stat

            if not stat.S_ISREG(stat_res.st_mode):
                os.close(file_fd)
                raise WorkspaceSecurityError(f"Target '{raw_path}' is not a regular file.")

            # Hand ownership of file_fd to the file object
            return os.fdopen(file_fd, "rb")
        finally:
            # Close intermediate directory descriptors
            for fd in fds_to_close:
                try:
                    os.close(fd)
                except OSError:
                    pass
