"""Database engine configuration, connection settings, and lifecycle management for SQLite.

SQLite Concurrency and Write Serialization:
--------------------------------------------
1. Write Serialization:
   SQLite supports multiple concurrent readers in WAL (Write-Ahead Logging) mode,
   but only one active writer at any given time. Write operations acquire an exclusive
   write lock on the database file.

2. Purpose and Limitations of busy_timeout:
   `PRAGMA busy_timeout = 5000` instructs SQLite to sleep and retry internally for up to
   5000 milliseconds when an operation encounters a locked database (SQLITE_BUSY), rather
   than immediately failing. This smoothly handles transient write contention across threads
   or brief background processes.
   However, `busy_timeout` CANNOT prevent deadlocks if multiple transactions attempt to
   upgrade from read locks to write locks simultaneously without using BEGIN IMMEDIATE.
   Therefore, write transactions should acquire immediate write reservations upfront.

3. Thread Safety & check_same_thread:
   `check_same_thread=False` allows SQLAlchemy's connection pool to hand connections to
   different threads (e.g. background worker threads in QThreadPool). It DOES NOT make
   connections or Session instances safe for concurrent multi-threaded access.
   Application code must NEVER concurrently share a Session or Connection across threads;
   each worker task or repository operation must create and close its own independent Session.
"""

import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from orion.infrastructure.persistence.paths import (
    ensure_secure_directory,
    ensure_secure_file,
    get_database_path,
)


def _set_sqlite_connection_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Configure connection-level SQLite PRAGMAs.

    - foreign_keys = ON: Enforces referential integrity on every connection.
    - busy_timeout = 5000: Retries locked operations for up to 5000ms.
    - synchronous = NORMAL: Recommended under WAL mode for low I/O latency with full crash safety.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.close()


def create_db_engine(db_path: Path | str | None = None, echo: bool = False) -> Engine:
    """Create a configured SQLAlchemy Engine pointing to the resolved SQLite database file.

    Does not initialize tables or apply migrations.
    """
    resolved_path = get_database_path(db_path)
    # Ensure the parent directory exists. Permission hardening (0700) is only
    # applied to ORION-owned directories; unrelated parents are untouched.
    ensure_secure_directory(resolved_path.parent)

    url = f"sqlite:///{resolved_path}"
    engine = create_engine(
        url,
        echo=echo,
        connect_args={"check_same_thread": False},
    )

    event.listen(engine, "connect", _set_sqlite_connection_pragmas)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the engine with expire_on_commit=False."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_database(engine: Engine, apply_wal: bool = True) -> None:
    """Explicitly initialize the database file and set persistent file-level PRAGMAs.

    Persistent Settings:
    - journal_mode = WAL is set once during initialization because it persists in the
      database file header and applies across subsequent connections.

    Permissions:
    - Restricts newly created database files to owner-only permissions (0600).
    """
    with engine.connect() as conn:
        if apply_wal:
            conn.exec_driver_sql("PRAGMA journal_mode = WAL;")
        conn.commit()

    url_database = engine.url.database
    if url_database and url_database != ":memory:":
        db_file = Path(url_database)
        if db_file.exists():
            ensure_secure_file(db_file)
