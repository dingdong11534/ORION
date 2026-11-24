"""Security-critical unit tests for WorkspacePathResolver."""

from pathlib import Path

import pytest

from orion.infrastructure.tools.path_resolver import (
    WorkspacePathResolver,
    WorkspaceSecurityError,
)


@pytest.fixture
def workspace_env(tmp_path: Path):
    """Create a temporary workspace directory structure with files, dirs, and symlinks."""
    ws = tmp_path / "test_workspace"
    ws.mkdir()

    sub = ws / "subdir"
    sub.mkdir()
    (sub / "hello.txt").write_text("Hello from subdir", encoding="utf-8")

    secret_outside = tmp_path / "secret_outside.txt"
    secret_outside.write_text("SUPER_SECRET_DATA", encoding="utf-8")

    dir_outside = tmp_path / "dir_outside"
    dir_outside.mkdir()
    (dir_outside / "ext.txt").write_text("external", encoding="utf-8")

    # Symlink pointing outside workspace
    symlink_file = ws / "symlink_to_secret.txt"
    symlink_file.symlink_to(secret_outside)

    # Symlink directory pointing outside workspace
    symlink_dir = ws / "symlink_dir"
    symlink_dir.symlink_to(dir_outside)

    # Broken symlink
    broken_sym = ws / "broken_symlink.txt"
    broken_sym.symlink_to(tmp_path / "non_existent_target.txt")

    return {
        "root": ws,
        "outside": secret_outside,
        "dir_outside": dir_outside,
    }


def test_normal_relative_path(workspace_env):
    """Normal relative paths resolve safely to canonical paths within workspace."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    p = resolver.resolve_path("subdir/hello.txt", must_exist=True, require_file=True)
    assert p.is_file()
    assert p.name == "hello.txt"

    # Root path
    root_p = resolver.resolve_path("", must_exist=True, require_dir=True)
    assert root_p == workspace_env["root"]


def test_absolute_path_rejection(workspace_env):
    """Absolute paths are rejected unconditionally."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="Absolute paths are rejected"):
        resolver.resolve_path("/etc/passwd")

    with pytest.raises(WorkspaceSecurityError, match="Absolute paths are rejected"):
        resolver.resolve_path("/subdir/hello.txt")


def test_path_traversal_rejection(workspace_env):
    """Paths containing '..' traversal are strictly rejected."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="Path traversal detected"):
        resolver.resolve_path("../secret_outside.txt")

    with pytest.raises(WorkspaceSecurityError, match="Path traversal detected"):
        resolver.resolve_path("subdir/../../secret_outside.txt")


def test_prefix_confusion_rejection(tmp_path: Path):
    """Sibling directories with matching prefix cannot bypass containment."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    ws_evil = tmp_path / "workspace_evil"
    ws_evil.mkdir()
    (ws_evil / "leak.txt").write_text("evil", encoding="utf-8")

    resolver = WorkspacePathResolver(ws)
    # Even if someone constructs a path attempting prefix confusion
    with pytest.raises(WorkspaceSecurityError):
        resolver.resolve_path("../workspace_evil/leak.txt")


def test_symlink_pointing_outside_rejected(workspace_env):
    """Symlinks pointing outside the workspace are detected and rejected."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="Symlinks are prohibited"):
        resolver.resolve_path("symlink_to_secret.txt", must_exist=True)


def test_intermediate_symlink_directory_rejected(workspace_env):
    """Intermediate symlink directories are detected during walk and rejected."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="Symlinks are prohibited"):
        resolver.resolve_path("symlink_dir/ext.txt", must_exist=True)


def test_broken_symlink_rejected(workspace_env):
    """Broken symlinks are identified and rejected without unhandled crash."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="Symlinks are prohibited"):
        resolver.resolve_path("broken_symlink.txt")


def test_sensitive_path_rejected(workspace_env):
    """Attempts to access .ssh, .env, .bashrc are rejected."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="sensitive"):
        resolver.resolve_path(".ssh/id_rsa")

    with pytest.raises(WorkspaceSecurityError, match="sensitive"):
        resolver.resolve_path(".env")


def test_null_bytes_rejected(workspace_env):
    """Null bytes in paths are rejected immediately."""
    resolver = WorkspacePathResolver(workspace_env["root"])
    with pytest.raises(WorkspaceSecurityError, match="null bytes"):
        resolver.resolve_path("subdir/hello.txt\0.jpg")


def test_unicode_filename_resolution(workspace_env):
    """Unicode filenames resolve accurately within the workspace."""
    ws = workspace_env["root"]
    unicode_file = ws / "тест_документ.txt"
    unicode_file.write_text("Привет мир", encoding="utf-8")

    resolver = WorkspacePathResolver(ws)
    res = resolver.resolve_path("тест_документ.txt", must_exist=True, require_file=True)
    assert res.exists()
    assert res.read_text(encoding="utf-8") == "Привет мир"


def test_open_file_safely_descriptor(workspace_env):
    """open_file_safely opens valid files and rejects symlinks via O_NOFOLLOW."""
    resolver = WorkspacePathResolver(workspace_env["root"])

    # Valid file read
    with resolver.open_file_safely("subdir/hello.txt") as fp:
        assert fp.read() == b"Hello from subdir"

    # Symlink target fails
    with pytest.raises(WorkspaceSecurityError, match="symlink"):
        resolver.open_file_safely("symlink_to_secret.txt")

    # Non-existent file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        resolver.open_file_safely("subdir/does_not_exist.txt")
