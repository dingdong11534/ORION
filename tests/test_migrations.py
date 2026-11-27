"""Tests for Alembic migrations: upgrade, downgrade, constraint and index verification."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from orion.infrastructure.persistence.database import create_db_engine
from orion.infrastructure.persistence.models import Base


def _make_alembic_cfg(db_url: str) -> Config:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_alembic_upgrade_and_downgrade_cycle(tmp_path: Path):
    db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_file}"
    alembic_cfg = _make_alembic_cfg(db_url)

    # 1. Upgrade from empty to head
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(db_file)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected_tables = {
        "conversations",
        "messages",
        "activity_events",
        "user_settings",
        "alembic_version",
    }
    assert expected_tables.issubset(tables)

    # Verify conversations columns
    conv_cols = {col["name"] for col in inspector.get_columns("conversations")}
    assert {"id", "title", "next_sequence", "created_at", "updated_at"}.issubset(conv_cols)

    # Verify messages columns (token_count and model_name must NOT exist)
    msg_cols = {col["name"] for col in inspector.get_columns("messages")}
    assert {
        "id",
        "conversation_id",
        "sequence_number",
        "client_request_id",
        "role",
        "status",
        "content",
        "error_message",
        "created_at",
        "updated_at",
    }.issubset(msg_cols)
    assert "token_count" not in msg_cols, "token_count must not exist in this schema version"
    assert "model_name" not in msg_cols, "model_name must not exist in this schema version"

    # Verify activity_events columns (no conversation_id FK)
    evt_cols = {col["name"] for col in inspector.get_columns("activity_events")}
    assert {
        "id",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "payload_json",
        "created_at",
    }.issubset(evt_cols)
    evt_fks = inspector.get_foreign_keys("activity_events")
    assert len(evt_fks) == 0, "activity_events must have NO foreign keys"

    # Messages FK to conversations
    msg_fks = inspector.get_foreign_keys("messages")
    assert len(msg_fks) == 1
    assert msg_fks[0]["referred_table"] == "conversations"

    # Required indexes
    conv_indexes = {idx["name"] for idx in inspector.get_indexes("conversations")}
    assert "idx_conversations_updated_at" in conv_indexes

    # messages must NOT have idx_messages_created_at (redundant)
    msg_indexes = {idx["name"] for idx in inspector.get_indexes("messages")}
    assert "idx_messages_created_at" not in msg_indexes, (
        "idx_messages_created_at is redundant and must not exist"
    )

    evt_indexes = {idx["name"] for idx in inspector.get_indexes("activity_events")}
    assert "idx_activity_events_created_at" in evt_indexes
    assert "idx_activity_events_aggregate" in evt_indexes
    assert "idx_activity_events_event_type" in evt_indexes

    # 2. Downgrade to base
    command.downgrade(alembic_cfg, "base")

    inspector_after_down = inspect(engine)
    tables_after_down = inspector_after_down.get_table_names()
    assert "conversations" not in tables_after_down
    assert "messages" not in tables_after_down
    assert "activity_events" not in tables_after_down
    assert "user_settings" not in tables_after_down

    # 3. Upgrade back to head (idempotency)
    command.upgrade(alembic_cfg, "head")
    inspector_reup = inspect(engine)
    assert expected_tables.issubset(set(inspector_reup.get_table_names()))


def test_orm_metadata_matches_migrated_schema(tmp_path: Path):
    """ORM metadata and migrated database schema must agree on all table names and columns."""
    db_file = tmp_path / "schema_check.db"
    db_url = f"sqlite:///{db_file}"
    alembic_cfg = _make_alembic_cfg(db_url)

    command.upgrade(alembic_cfg, "head")
    engine = create_db_engine(db_file)
    inspector = inspect(engine)

    orm_tables = set(Base.metadata.tables.keys())
    all_db_tables = set(inspector.get_table_names())
    assert "document_chunks_fts" in all_db_tables
    db_tables = {
        t for t in all_db_tables - {"alembic_version"} if not t.startswith("document_chunks_fts")
    }
    assert orm_tables == db_tables, f"ORM tables {orm_tables} != DB tables {db_tables}"

    for table_name in orm_tables:
        orm_cols = {col.name for col in Base.metadata.tables[table_name].columns}
        db_cols = {col["name"] for col in inspector.get_columns(table_name)}
        assert orm_cols == db_cols, f"Column mismatch for {table_name}: ORM={orm_cols} DB={db_cols}"


def test_migration_client_request_id_not_null(tmp_path: Path):
    """client_request_id NOT NULL constraint is in migrated schema."""
    db_file = tmp_path / "crid_check.db"
    db_url = f"sqlite:///{db_file}"
    command.upgrade(_make_alembic_cfg(db_url), "head")
    engine = create_db_engine(db_file)

    # Verify client_request_id is NOT NULL in the migrated schema
    with engine.connect() as conn:
        schema = conn.execute(text("SELECT sql FROM sqlite_master WHERE name='messages'")).scalar()
    assert schema is not None
    # Both NOT NULL and the blank check constraint should be present
    assert "client_request_id" in schema
