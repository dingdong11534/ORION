"""Tests for database path resolution and secure filesystem permissions."""

import stat
from pathlib import Path

import pytest

from orion.infrastructure.persistence.paths import (
    ORION_DB_ENV_VAR,
    ensure_secure_directory,
    ensure_secure_file,
    get_database_path,
)


def test_get_database_path_custom_override(tmp_path: Path):
    custom = tmp_path / "custom" / "test.db"
    resolved = get_database_path(custom)
    assert resolved == custom.resolve()


def test_get_database_path_env_var(monkeypatch, tmp_path: Path):
    env_target = tmp_path / "env_orion.db"
    monkeypatch.setenv(ORION_DB_ENV_VAR, str(env_target))

    resolved = get_database_path()
    assert resolved == env_target.resolve()


def test_get_database_path_xdg_data_home(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(ORION_DB_ENV_VAR, raising=False)
    xdg_home = tmp_path / "xdg_share"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))

    resolved = get_database_path()
    expected = (xdg_home / "orion" / "orion.db").resolve()
    assert resolved == expected


def test_get_database_path_default_fallback(monkeypatch):
    monkeypatch.delenv(ORION_DB_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    resolved = get_database_path()
    expected = (Path.home() / ".local" / "share" / "orion" / "orion.db").resolve()
    assert resolved == expected


def test_ensure_secure_directory_sets_0700(tmp_path: Path):
    target_dir = tmp_path / "orion"
    target_dir.mkdir(parents=True, exist_ok=True)
    # Start with open permissions
    target_dir.chmod(0o755)

    ensure_secure_directory(target_dir)
    mode = stat.S_IMODE(target_dir.stat().st_mode)
    assert mode == 0o700


def test_ensure_secure_file_sets_0600(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db_file.write_text("dummy")
    db_file.chmod(0o644)

    ensure_secure_file(db_file)
    mode = stat.S_IMODE(db_file.stat().st_mode)
    assert mode == 0o600


def test_default_path_not_inside_git_repo():
    resolved = get_database_path()
    repo_root = Path(__file__).parent.parent.resolve()
    assert not str(resolved).startswith(str(repo_root / "orion"))
    assert not str(resolved).startswith(str(repo_root / "data" / "orion.db"))


def test_unrelated_parent_directory_permissions_unchanged(tmp_path: Path):
    """ensure_secure_directory must NOT chmod pre-existing unrelated parent directories.

    Scenario: ~/.local/share already exists with 0755.
    Only the ORION-owned 'orion' subdirectory should become 0700.
    """
    # Simulate ~/.local/share pre-existing with standard permissions
    share_dir = tmp_path / "share"
    share_dir.mkdir()
    share_dir.chmod(0o755)
    original_share_mode = stat.S_IMODE(share_dir.stat().st_mode)

    # Create the ORION-owned application directory
    orion_dir = share_dir / "orion"
    ensure_secure_directory(orion_dir)

    # 'share' permissions must be unchanged
    share_mode_after = stat.S_IMODE(share_dir.stat().st_mode)
    assert share_mode_after == original_share_mode, (
        f"ensure_secure_directory must not chmod pre-existing parent; "
        f"was {oct(original_share_mode)}, now {oct(share_mode_after)}"
    )

    # 'orion' directory must be 0700
    orion_mode = stat.S_IMODE(orion_dir.stat().st_mode)
    assert orion_mode == 0o700, f"orion dir should be 0700, got {oct(orion_mode)}"


def test_multiple_ancestor_directories_permissions_unchanged(tmp_path: Path):
    """Non-ORION parent directories at multiple levels must not be chmod'd.

    Scenario: /tmp/base (0755) and /tmp/base/data (0775) pre-exist.
    Only 'orion' subdirectory should be restricted.
    """
    base = tmp_path / "base"
    base.mkdir()
    base.chmod(0o755)

    data = base / "data"
    data.mkdir()
    data.chmod(0o775)

    orion_dir = data / "orion"
    ensure_secure_directory(orion_dir)

    assert stat.S_IMODE(base.stat().st_mode) == 0o755, "base dir permissions must be unchanged"
    assert stat.S_IMODE(data.stat().st_mode) == 0o775, "data dir permissions must be unchanged"
    assert stat.S_IMODE(orion_dir.stat().st_mode) == 0o700


def test_ensure_secure_directory_creates_orion_dir_with_0700(tmp_path: Path):
    """When orion dir does not exist, it is created with 0700."""
    orion_dir = tmp_path / "newparent" / "orion"
    assert not orion_dir.exists()

    ensure_secure_directory(orion_dir)

    assert orion_dir.exists()
    assert stat.S_IMODE(orion_dir.stat().st_mode) == 0o700


def test_ensure_secure_directory_does_not_chmod_non_orion_target(tmp_path: Path):
    """If the target directory is not named 'orion', existing permissions are preserved.

    This guards against accidental chmod of custom or test paths that happen
    to be passed directly (e.g. via ORION_DB_PATH pointing to /tmp/test.db,
    whose parent is /tmp).
    """
    non_orion_dir = tmp_path / "custom_data_dir"
    non_orion_dir.mkdir()
    non_orion_dir.chmod(0o755)

    ensure_secure_directory(non_orion_dir)

    mode = stat.S_IMODE(non_orion_dir.stat().st_mode)
    assert mode == 0o755, f"Non-ORION directory permissions must not be changed, got {oct(mode)}"


@pytest.mark.parametrize("bad_perm", [0o777, 0o755, 0o700])
def test_ensure_secure_directory_repairs_orion_dir_permissions(tmp_path: Path, bad_perm: int):
    """If 'orion' dir already exists with wrong perms, it is corrected to 0700."""
    orion_dir = tmp_path / "orion"
    orion_dir.mkdir()
    orion_dir.chmod(bad_perm)

    ensure_secure_directory(orion_dir)

    mode = stat.S_IMODE(orion_dir.stat().st_mode)
    assert mode == 0o700
