"""Tests for database engine configuration and SQLite PRAGMAs."""

import stat
from pathlib import Path

from sqlalchemy import text

from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)


def test_engine_connection_pragmas(tmp_path: Path):
    db_file = tmp_path / "pragma_test.db"
    engine = create_db_engine(db_file)

    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        sync = conn.execute(text("PRAGMA synchronous;")).scalar()

        assert fk == 1, "foreign_keys should be ON (1)"
        assert busy == 5000, "busy_timeout should be 5000"
        # In SQLite, PRAGMA synchronous returns 1 for NORMAL
        assert sync == 1, "synchronous should be NORMAL (1)"


def test_init_database_applies_wal_and_permissions(tmp_path: Path):
    db_file = tmp_path / "wal_test.db"
    engine = create_db_engine(db_file)
    init_database(engine, apply_wal=True)

    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert str(journal_mode).lower() == "wal"

    assert db_file.exists()
    mode = stat.S_IMODE(db_file.stat().st_mode)
    assert mode == 0o600


def test_session_factory_expire_on_commit(tmp_path: Path):
    engine = create_db_engine(tmp_path / "session_test.db")
    factory = create_session_factory(engine)
    assert factory.kw.get("expire_on_commit") is False
