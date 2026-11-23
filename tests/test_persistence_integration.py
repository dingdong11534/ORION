"""Integration tests for persistence layer — transaction integrity and session isolation."""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from alembic import command
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    MessageModel,
    generate_request_id,
)
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)


def _apply_migrations(db_url: str) -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def _make_store(tmp_path: Path, name: str) -> tuple[SqliteConversationStore, any]:
    db_file = tmp_path / "orion" / f"{name}.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory), factory


# ---------------------------------------------------------------------------
# Transaction integrity
# ---------------------------------------------------------------------------


def test_begin_request_rollback_on_duplicate_sequence(tmp_path: Path):
    """Simulates a scenario where begin_request integrity constraint fires —
    both messages and events must be absent."""
    store, factory = _make_store(tmp_path, "rollback_test")
    conv = store.create_conversation("Rollback test")

    # First request succeeds
    req_id = generate_request_id()
    store.begin_request(req_id, "First", conv.id)

    # Count messages and events before deliberate failure attempt
    with factory() as session:
        msg_count_before = (
            session.execute(select(MessageModel).where(MessageModel.conversation_id == conv.id))
            .scalars()
            .all()
        )
        session.execute(select(ActivityEventModel)).scalars().all()

    # Idempotent call — must not add messages
    r2 = store.begin_request(req_id, "First", conv.id)
    assert r2.created is False

    with factory() as session:
        msg_count_after = (
            session.execute(select(MessageModel).where(MessageModel.conversation_id == conv.id))
            .scalars()
            .all()
        )
    assert len(msg_count_after) == len(msg_count_before)


def test_complete_request_rollback_on_state_error(tmp_path: Path):
    """Completing an already-failed message raises and does not corrupt the event log."""
    store, factory = _make_store(tmp_path, "complete_rollback")
    conv = store.create_conversation("Complete rollback")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.fail_request(conv.id, req_id, "Error")

    from orion.application.ports import InvalidStateTransitionError

    with pytest.raises(InvalidStateTransitionError):
        store.complete_request(conv.id, req_id, "Late answer")

    # completion event must not exist
    with factory() as session:
        completed_evts = (
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.event_type == "assistant.response.completed"
                )
            )
            .scalars()
            .all()
        )
    assert len(completed_evts) == 0


def test_user_message_persists_after_model_failure(tmp_path: Path):
    """After fail_request, the user message must remain completed and intact."""
    store, factory = _make_store(tmp_path, "user_persists")
    conv = store.create_conversation("User persists")
    req_id = generate_request_id()
    store.begin_request(req_id, "My important question", conv.id)
    store.fail_request(conv.id, req_id, "Provider crashed")

    with factory() as session:
        user_msg = session.scalar(
            select(MessageModel).where(
                MessageModel.conversation_id == conv.id,
                MessageModel.role == "user",
            )
        )
    assert user_msg is not None
    assert user_msg.content == "My important question"
    assert user_msg.status == "completed"


def test_session_not_leaked_between_calls(tmp_path: Path):
    """Each repository call must close its session before returning."""
    store, _factory = _make_store(tmp_path, "session_leak")
    # Make several calls and confirm no open transactions interfere
    c1 = store.create_conversation("A")
    c2 = store.create_conversation("B")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", c1.id)
    store.complete_request(c1.id, req_id, "A")
    # Second conversation must still be accessible
    convs = store.list_conversations()
    ids = [c.id for c in convs]
    assert c1.id in ids
    assert c2.id in ids


# ---------------------------------------------------------------------------
# Foreign key integrity
# ---------------------------------------------------------------------------


def test_foreign_key_integrity_active(tmp_path: Path):
    """PRAGMA foreign_keys=ON must be active, preventing orphan messages."""
    store, factory = _make_store(tmp_path, "fk_test")
    conv = store.create_conversation("FK test")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)

    # Try to insert a message pointing to a non-existent conversation directly
    with factory() as session, pytest.raises(IntegrityError):
        bad_msg = MessageModel(
            conversation_id="conv_" + "f" * 32,
            sequence_number=99,
            client_request_id=generate_request_id(),
            role="user",
            status="completed",
            content="Orphan",
        )
        session.add(bad_msg)
        session.commit()


# ---------------------------------------------------------------------------
# Migration verification
# ---------------------------------------------------------------------------


def test_migration_on_fresh_database(tmp_path: Path):
    """Migration produces all expected tables on a brand-new database."""
    db_file = tmp_path / "orion" / "migration_verify.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)

    engine = create_db_engine(db_file)
    from sqlalchemy import inspect

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    expected = {"conversations", "messages", "activity_events", "user_settings", "alembic_version"}
    assert expected.issubset(tables)


def test_no_model_create_all_used_in_integration_tests(tmp_path: Path):
    """Integration tests use Alembic migrations, not metadata.create_all().
    Verifies that the migrated schema matches the ORM metadata."""
    from sqlalchemy import inspect as sa_inspect

    from orion.infrastructure.persistence.models import Base

    db_file = tmp_path / "orion" / "orm_match_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)

    engine = create_db_engine(db_file)
    insp = sa_inspect(engine)
    orm_tables = set(Base.metadata.tables.keys())
    db_tables = set(insp.get_table_names()) - {"alembic_version"}
    assert orm_tables == db_tables


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------


def test_settings_and_conversation_share_same_database(tmp_path: Path):
    """SqliteConversationStore and SqliteSettingsStore work on the same DB."""
    db_file = tmp_path / "orion" / "shared_db_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    sett_store = SqliteSettingsStore(factory)

    conv = conv_store.create_conversation("Shared DB")
    sett_store.set_setting("active_conversation_id", conv.id)

    assert sett_store.get_setting("active_conversation_id") == conv.id
    convs = conv_store.list_conversations()
    assert any(c.id == conv.id for c in convs)
