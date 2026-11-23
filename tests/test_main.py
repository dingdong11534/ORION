"""Tests for application wiring and startup verification in main.py."""

from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from orion.infrastructure.persistence.database import create_db_engine, init_database
from orion.main import _EXPECTED_ALEMBIC_REVISION, check_database_migration


def _apply_migrations(db_url: str, revision: str = "head") -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, revision)


def test_check_migration_raises_when_database_file_absent(tmp_path: Path):
    """Missing database file raises RuntimeError with migration instructions."""
    nonexistent_file = tmp_path / "absent.db"
    engine = create_db_engine(nonexistent_file)

    with pytest.raises(RuntimeError) as exc_info:
        check_database_migration(engine)

    assert "Database file not found" in str(exc_info.value)
    assert ".venv/bin/alembic upgrade head" in str(exc_info.value)


def test_check_migration_raises_when_uninitialized(tmp_path: Path):
    """Empty database file without alembic_version raises RuntimeError."""
    db_file = tmp_path / "empty.db"
    engine = create_db_engine(db_file)
    init_database(engine, apply_wal=False)

    with pytest.raises(RuntimeError) as exc_info:
        check_database_migration(engine)

    assert "has not been initialized with Alembic" in str(exc_info.value)
    assert ".venv/bin/alembic upgrade head" in str(exc_info.value)


def test_check_migration_raises_when_outdated_revision(tmp_path: Path):
    """Database at outdated revision (e.g. 0001 instead of 0002) raises RuntimeError."""
    db_file = tmp_path / "outdated.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url, revision="0001")

    engine = create_db_engine(db_file)
    with pytest.raises(RuntimeError) as exc_info:
        check_database_migration(engine, expected_revision=_EXPECTED_ALEMBIC_REVISION)

    assert "is at revision '0001'" in str(exc_info.value)
    assert f"requires revision '{_EXPECTED_ALEMBIC_REVISION}'" in str(exc_info.value)
    assert ".venv/bin/alembic upgrade head" in str(exc_info.value)


def test_check_migration_succeeds_at_expected_revision(tmp_path: Path):
    """Database migrated to head revision succeeds without error."""
    db_file = tmp_path / "migrated.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url, revision="head")

    engine = create_db_engine(db_file)
    # Must not raise
    check_database_migration(engine, expected_revision=_EXPECTED_ALEMBIC_REVISION)
