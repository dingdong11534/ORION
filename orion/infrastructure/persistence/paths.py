"""Path resolution and file permission management for ORION database.

Permission enforcement policy:
- Only the ORION-owned application directory (the direct parent of the database
  file) is restricted to 0700.
- Parent directories that ORION did not create (e.g. ~/.local, ~/.local/share,
  or externally configured XDG_DATA_HOME parents) are never chmod'd.
- Database files created by ORION are restricted to 0600 on creation.
- If a custom or environment-overridden path is provided, the immediate parent
  directory is NOT chmod'd; only ORION's own application directory is managed.
"""

import os
import stat
from pathlib import Path

ORION_DB_ENV_VAR = "ORION_DB_PATH"

# The name of the ORION-owned application directory within XDG data home.
_ORION_APP_DIR_NAME = "orion"


def get_database_path(custom_path: Path | str | None = None) -> Path:
    """Resolve the absolute SQLite database path according to priority rules:

    1. Explicit custom_path argument (used by tests).
    2. ORION_DB_PATH environment variable override.
    3. $XDG_DATA_HOME/orion/orion.db if XDG_DATA_HOME is set and non-empty.
    4. ~/.local/share/orion/orion.db as default fallback.
    """
    if custom_path is not None:
        return Path(custom_path).expanduser().resolve()

    if env_path := os.environ.get(ORION_DB_ENV_VAR):
        return Path(env_path).expanduser().resolve()

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home and xdg_data_home.strip():
        base_dir = Path(xdg_data_home).expanduser().resolve()
    else:
        base_dir = Path.home() / ".local" / "share"

    return (base_dir / _ORION_APP_DIR_NAME / "orion.db").resolve()


def _is_orion_owned_directory(directory: Path) -> bool:
    """Return True if the directory is the ORION-owned application directory.

    Only the directory named 'orion' under a XDG/home data directory is
    considered ORION-owned. Parent directories such as ~/.local/share or
    custom XDG_DATA_HOME paths are not ORION-owned and must not be chmod'd.
    """
    return directory.name == _ORION_APP_DIR_NAME


def ensure_secure_directory(directory: Path) -> Path:
    """Ensure the ORION application directory exists and restrict to 0700.

    Only enforces 0700 on the ORION-owned application directory itself.
    Parent directories (e.g. ~/.local/share or any externally configured path)
    are created if missing but their permissions are NEVER modified.
    """
    if not directory.exists():
        # Create the directory tree. Use mode=0o700 only for the final directory.
        # We walk from the first missing ancestor to avoid chmod'ing pre-existing dirs.
        _create_directory_tree(directory)
    elif _is_orion_owned_directory(directory):
        # Only enforce 0700 on the ORION app directory itself.
        try:
            current_mode = stat.S_IMODE(directory.stat().st_mode)
            if current_mode != 0o700:
                directory.chmod(0o700)
        except OSError:
            pass
    return directory


def _create_directory_tree(target: Path) -> None:
    """Create a directory tree, enforcing 0700 only on the ORION app directory.

    For each directory that needs to be created:
    - If it is named 'orion' (the ORION app directory), create it with 0700.
    - All other missing ancestors are created with the process umask default.
    Parent directories that already exist are never chmod'd.
    """
    # Collect directories that do not yet exist (bottom-up order, reversed for top-down)
    missing: list[Path] = []
    current = target
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent

    # Create missing directories from top to bottom
    for directory in reversed(missing):
        if _is_orion_owned_directory(directory):
            directory.mkdir(mode=0o700, exist_ok=True)
        else:
            directory.mkdir(exist_ok=True)


def ensure_secure_file(file_path: Path) -> Path:
    """Ensure owner-only read/write permissions (0600) on an existing database file."""
    if file_path.exists():
        try:
            current_mode = stat.S_IMODE(file_path.stat().st_mode)
            if current_mode != 0o600:
                file_path.chmod(0o600)
        except OSError:
            pass
    return file_path
