"""Milestone 2.2.1 correctness and edge-case tests.

Covers:
- Part 1: Global request idempotency (conversation_id=None, conflict checks, concurrent deduplication)
- Part 2: conversations.updated_at tracking across request lifecycle and ordering in list_conversations
- Part 3: load_history limit, recency selection, and ascending sequence ordering
- Part 4: _with_busy_retry error code distinction, retry bounds, rollback, and no IntegrityError retry
- Part 5: Contract consistency, RequestRecord return type, and Pydantic immutability
- Part 6: Duplicate state transitions (idempotency vs InvalidStateTransitionError)
- Part 7: Session and transaction isolation (atomic rollback of entities and events)
"""

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

from alembic import command
from orion.application.contracts import MessageRole, MessageStatus, RequestRecord
from orion.application.ports import (
    InvalidStateTransitionError,
    PersistenceError,
    PersistenceIntegrityError,
)
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    ConversationModel,
    MessageModel,
    generate_request_id,
)
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    _with_busy_retry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _apply_migrations(db_url: str) -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


@pytest.fixture
def store(tmp_path: Path) -> SqliteConversationStore:
    db_file = tmp_path / "orion" / "correctness_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory)


@pytest.fixture
def store_with_factory(tmp_path: Path):
    db_file = tmp_path / "orion" / "correctness_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory), factory


# ---------------------------------------------------------------------------
# Part 1: Global Request Idempotency
# ---------------------------------------------------------------------------


def test_begin_request_payload_consistency_none_conversation_id(store_with_factory):
    """Milestone 2.2.2: begin_request enforces payload consistency with conversation_id=None."""
    store, factory = store_with_factory
    req_id = "req_test_123"
    content = "Explain C++ pointers"

    # 1. Create initial request
    res1 = store.begin_request(req_id, content, conversation_id=None)
    assert res1.created is True
    assert res1.request.user_message.content == content
    conv_id = res1.request.conversation_id

    # 2. Resubmit with identical content and conversation_id=None -> created=False, existing record
    res2 = store.begin_request(req_id, content, conversation_id=None)
    assert res2.created is False
    assert res2.request.conversation_id == conv_id
    assert res2.request.user_message.content == content

    # 3. Resubmit with different content and conversation_id=None -> PersistenceIntegrityError
    with pytest.raises(PersistenceIntegrityError) as exc_info:
        store.begin_request(req_id, "Explain Python decorators", conversation_id=None)

    assert "already exists with different user content" in str(exc_info.value)

    with factory() as session:
        msgs = session.execute(select(MessageModel)).scalars().all()
        assert len(msgs) == 2

    # Verify only 1 conversation and 2 messages exist in DB
    with factory() as session:
        convs = session.execute(select(ConversationModel)).scalars().all()
        assert len(convs) == 1
        msgs = session.execute(select(MessageModel)).scalars().all()
        assert len(msgs) == 2


def test_begin_request_payload_consistency_matching_conversation_id(store_with_factory):
    """Milestone 2.2.2: begin_request enforces payload consistency with matching conversation_id."""
    store, factory = store_with_factory
    conv = store.create_conversation("C++ Pointers")
    req_id = "req_test_matching_123"
    content = "Explain C++ pointers"

    # 1. Create request with matching conversation_id
    res1 = store.begin_request(req_id, content, conv.id)
    assert res1.created is True
    assert res1.request.conversation_id == conv.id

    # 2. Resubmit with identical content and matching conversation_id -> created=False
    res2 = store.begin_request(req_id, content, conv.id)
    assert res2.created is False
    assert res2.request.conversation_id == conv.id
    assert res2.request.user_message.content == content

    # 3. Resubmit with different content and matching conversation_id -> PersistenceIntegrityError
    with pytest.raises(PersistenceIntegrityError) as exc_info:
        store.begin_request(req_id, "Explain C++ smart pointers instead", conv.id)

    assert "already exists with different user content" in str(exc_info.value)

    with factory() as session:
        msgs = session.execute(select(MessageModel)).scalars().all()
        assert len(msgs) == 2


def test_begin_request_conflicting_conversation_id_rejected(store):
    """Milestone 2.2.2: conflicting conversation_id is rejected regardless of content."""
    conv1 = store.create_conversation("Conv 1")
    conv2 = store.create_conversation("Conv 2")
    req_id = "req_test_conflict_123"
    content = "Explain C++ pointers"

    store.begin_request(req_id, content, conv1.id)

    # Identical content but conflicting conversation_id
    with pytest.raises(PersistenceIntegrityError) as exc1:
        store.begin_request(req_id, content, conv2.id)
    assert "already belongs to conversation" in str(exc1.value)

    # Different content and conflicting conversation_id
    with pytest.raises(PersistenceIntegrityError) as exc2:
        store.begin_request(req_id, "Different content", conv2.id)
    assert "already belongs to conversation" in str(exc2.value)


def test_concurrent_identical_submissions_produce_one_conversation_and_one_pair(
    tmp_path: Path,
):
    """Milestone 2.2.2: Concurrent identical submissions produce exactly 1 conversation and 1 message pair."""
    db_file = tmp_path / "orion" / "concurrent_payload.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    store = SqliteConversationStore(factory)

    shared_req_id = "req_concurrent_payload_123"
    shared_content = "Explain C++ pointers concurrently"
    results: list = []
    errors: list = []
    num_threads = 4
    barrier = threading.Barrier(num_threads)

    def worker():
        try:
            barrier.wait()
            res = store.begin_request(shared_req_id, shared_content, conversation_id=None)
            results.append(res)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Errors: {errors}"
    assert len(results) == num_threads

    created_count = sum(1 for r in results if r.created)
    retrieved_count = sum(1 for r in results if not r.created)
    assert created_count == 1
    assert retrieved_count == num_threads - 1

    # All threads received the identical conversation_id and user content
    first_conv_id = results[0].request.conversation_id
    for r in results:
        assert r.request.conversation_id == first_conv_id
        assert r.request.user_message.content == shared_content

    # Verify database holds exactly 1 conversation and 2 messages
    with factory() as session:
        convs = session.execute(select(ConversationModel)).scalars().all()
        assert len(convs) == 1
        msgs = session.execute(select(MessageModel)).scalars().all()
        assert len(msgs) == 2


def test_begin_request_none_conversation_id_idempotent(store_with_factory):
    """Calling begin_request twice with conversation_id=None returns the same conversation."""
    store, factory = store_with_factory
    req_id = generate_request_id()

    # Turn 1: create conversation and request
    res1 = store.begin_request(req_id, "Hello from caller", conversation_id=None)
    assert res1.created is True
    conv_id = res1.request.conversation_id

    # Turn 2: simulate lost response, retry with same request_id and conversation_id=None
    res2 = store.begin_request(req_id, "Hello from caller", conversation_id=None)
    assert res2.created is False
    assert res2.request.conversation_id == conv_id
    assert res2.request.client_request_id == req_id

    # Verify only ONE conversation exists in the database
    with factory() as session:
        convs = session.execute(select(ConversationModel)).scalars().all()
        assert len(convs) == 1
        assert convs[0].id == conv_id

        # Verify exactly TWO messages exist in the database (user + assistant)
        msgs = session.execute(select(MessageModel)).scalars().all()
        assert len(msgs) == 2


def test_begin_request_conflicting_conversation_id_raises_integrity_error(store):
    """Submitting an existing request ID under a different conversation ID raises PersistenceIntegrityError."""
    conv1 = store.create_conversation("Conversation 1")
    conv2 = store.create_conversation("Conversation 2")
    req_id = generate_request_id()

    # Associate with conv1
    res1 = store.begin_request(req_id, "Original request", conv1.id)
    assert res1.created is True

    # Attempt to associate same req_id with conv2
    with pytest.raises(PersistenceIntegrityError) as exc_info:
        store.begin_request(req_id, "Conflict request", conv2.id)

    assert "already belongs to conversation" in str(exc_info.value)


def test_concurrent_duplicate_requests_with_none_conversation_id(tmp_path: Path):
    """Two concurrent threads calling begin_request with conversation_id=None create only 1 conversation."""
    db_file = tmp_path / "orion" / "concurrent_none.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    store = SqliteConversationStore(factory)

    shared_req_id = generate_request_id()
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            res = store.begin_request(shared_req_id, "Concurrent prompt", conversation_id=None)
            results.append(res)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Errors: {errors}"
    assert len(results) == 2

    # Exactly one thread created, the other retrieved
    created_flags = [r.created for r in results]
    assert True in created_flags
    assert False in created_flags

    # Both results must refer to the exact same conversation ID
    assert results[0].request.conversation_id == results[1].request.conversation_id

    # Verify only 1 conversation was persisted in the database
    with factory() as session:
        convs = session.execute(select(ConversationModel)).scalars().all()
        assert len(convs) == 1
        msgs = session.execute(select(MessageModel)).scalars().all()
        assert len(msgs) == 2


def test_migration_0002_unique_constraint(tmp_path: Path):
    """Verify uq_messages_client_request_role is active after migration."""
    db_file = tmp_path / "crid_uniq.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("messages")
    uq_names = {uc["name"] for uc in unique_constraints}
    assert "uq_messages_client_request_role" in uq_names
    assert "uq_messages_conversation_request_role" not in uq_names


# ---------------------------------------------------------------------------
# Part 2: Conversation updated_at
# ---------------------------------------------------------------------------


def test_conversation_updated_at_updated_on_begin_request(store):
    """begin_request updates conversations.updated_at."""
    conv = store.create_conversation("Updated_at test")
    t0 = conv.updated_at
    time.sleep(0.01)

    req_id = generate_request_id()
    store.begin_request(req_id, "Hello", conv.id)

    convs = store.list_conversations()
    updated_conv = next(c for c in convs if c.id == conv.id)
    assert updated_conv.updated_at > t0


def test_conversation_updated_at_updated_on_complete_request(store):
    """complete_request updates conversations.updated_at."""
    conv = store.create_conversation("Complete updated_at")
    req_id = generate_request_id()
    store.begin_request(req_id, "Hello", conv.id)

    convs_after_begin = store.list_conversations()
    t_after_begin = next(c for c in convs_after_begin if c.id == conv.id).updated_at
    time.sleep(0.01)

    store.complete_request(conv.id, req_id, "Completed response")

    convs_after_complete = store.list_conversations()
    t_after_complete = next(c for c in convs_after_complete if c.id == conv.id).updated_at
    assert t_after_complete > t_after_begin


def test_conversation_updated_at_updated_on_fail_request(store):
    """fail_request updates conversations.updated_at."""
    conv = store.create_conversation("Fail updated_at")
    req_id = generate_request_id()
    store.begin_request(req_id, "Hello", conv.id)

    convs_after_begin = store.list_conversations()
    t_after_begin = next(c for c in convs_after_begin if c.id == conv.id).updated_at
    time.sleep(0.01)

    store.fail_request(conv.id, req_id, "Failed error")

    convs_after_fail = store.list_conversations()
    t_after_fail = next(c for c in convs_after_fail if c.id == conv.id).updated_at
    assert t_after_fail > t_after_begin


def test_list_conversations_recent_activity_moves_to_top(store):
    """Active conversation moves to the top of list_conversations."""
    conv_a = store.create_conversation("Conversation A")
    time.sleep(0.01)
    conv_b = store.create_conversation("Conversation B")

    # Initially B is on top because it was created second
    convs = store.list_conversations()
    assert convs[0].id == conv_b.id
    assert convs[1].id == conv_a.id

    # Add message to Conversation A
    time.sleep(0.01)
    req_id = generate_request_id()
    store.begin_request(req_id, "Activity in A", conv_a.id)

    # Conversation A must now be on top
    convs_after = store.list_conversations()
    assert convs_after[0].id == conv_a.id
    assert convs_after[1].id == conv_b.id


# ---------------------------------------------------------------------------
# Part 3: History Limit & Ordering
# ---------------------------------------------------------------------------


def test_load_history_limit_and_ascending_order(store):
    """load_history(limit=N) returns the N most recent messages in ascending sequence order."""
    conv = store.create_conversation("History limit test")
    # Create 5 turns (10 messages, sequence_number 0 to 9)
    for i in range(5):
        rid = generate_request_id()
        store.begin_request(rid, f"Question {i}", conv.id)
        store.complete_request(conv.id, rid, f"Answer {i}")

    # Total messages: 10
    all_msgs = store.load_history(conv.id, limit=100)
    assert len(all_msgs) == 10
    assert [m.sequence_number for m in all_msgs] == list(range(10))

    # Request limit=4: must return the 4 most recent messages (sequences 6, 7, 8, 9)
    history = store.load_history(conv.id, limit=4)
    assert len(history) == 4
    seqs = [m.sequence_number for m in history]
    assert seqs == [6, 7, 8, 9]

    # Verify content matches
    assert history[0].content == "Question 3"
    assert history[1].content == "Answer 3"
    assert history[2].content == "Question 4"
    assert history[3].content == "Answer 4"


def test_load_history_limit_larger_than_total(store):
    """load_history with limit > total returns all messages in ascending order."""
    conv = store.create_conversation("Small history")
    rid = generate_request_id()
    store.begin_request(rid, "Only question", conv.id)
    store.complete_request(conv.id, rid, "Only answer")

    history = store.load_history(conv.id, limit=50)
    assert len(history) == 2
    assert history[0].sequence_number == 0
    assert history[1].sequence_number == 1


# ---------------------------------------------------------------------------
# Part 4: Busy Retry
# ---------------------------------------------------------------------------


def test_with_busy_retry_retries_on_sqlite_busy():
    """_with_busy_retry retries on SQLITE_BUSY (errorcode 5) and succeeds."""
    mock_op = MagicMock()
    # Create simulated OperationalError with sqlite_errorcode = 5
    orig_exc = sqlite3.OperationalError("database is locked")
    orig_exc.sqlite_errorcode = 5
    orig_exc.sqlite_errorname = "SQLITE_BUSY"
    sa_exc = OperationalError("SELECT 1", {}, orig_exc)

    # Fail twice, succeed on 3rd try
    mock_op.side_effect = [sa_exc, sa_exc, "SUCCESS"]
    result = _with_busy_retry(mock_op)
    assert result == "SUCCESS"
    assert mock_op.call_count == 3


def test_with_busy_retry_retries_on_sqlite_locked():
    """_with_busy_retry retries on SQLITE_LOCKED (errorcode 6) and succeeds."""
    mock_op = MagicMock()
    orig_exc = sqlite3.OperationalError("database table is locked")
    orig_exc.sqlite_errorcode = 6
    orig_exc.sqlite_errorname = "SQLITE_LOCKED"
    sa_exc = OperationalError("SELECT 1", {}, orig_exc)

    mock_op.side_effect = [sa_exc, "SUCCESS"]
    result = _with_busy_retry(mock_op)
    assert result == "SUCCESS"
    assert mock_op.call_count == 2


def test_with_busy_retry_bounds_retries():
    """_with_busy_retry raises PersistenceError after max retries (4 total attempts)."""
    mock_op = MagicMock()
    orig_exc = sqlite3.OperationalError("database is locked")
    orig_exc.sqlite_errorcode = 5
    orig_exc.sqlite_errorname = "SQLITE_BUSY"
    sa_exc = OperationalError("SELECT 1", {}, orig_exc)

    mock_op.side_effect = sa_exc
    with pytest.raises(PersistenceError) as exc_info:
        _with_busy_retry(mock_op)

    assert "Database operation failed" in str(exc_info.value)
    # Initial attempt + 3 retries = 4 calls total
    assert mock_op.call_count == 4


def test_with_busy_retry_unrelated_operational_error_fails_immediately():
    """Unrelated OperationalError (e.g. no such table, code 1) is never retried."""
    mock_op = MagicMock()
    orig_exc = sqlite3.OperationalError("no such table: fake")
    orig_exc.sqlite_errorcode = 1
    orig_exc.sqlite_errorname = "SQLITE_ERROR"
    sa_exc = OperationalError("SELECT * FROM fake", {}, orig_exc)

    mock_op.side_effect = sa_exc
    with pytest.raises(PersistenceError):
        _with_busy_retry(mock_op)

    assert mock_op.call_count == 1


def test_with_busy_retry_integrity_error_never_retried():
    """IntegrityError is never retried and re-raises PersistenceError immediately."""
    mock_op = MagicMock()
    sa_exc = IntegrityError("INSERT", {}, sqlite3.IntegrityError("CHECK constraint failed"))

    mock_op.side_effect = sa_exc
    with pytest.raises(PersistenceError):
        _with_busy_retry(mock_op)

    assert mock_op.call_count == 1


# ---------------------------------------------------------------------------
# Part 5: Contract Consistency
# ---------------------------------------------------------------------------


def test_get_request_returns_request_record_consistently(store):
    """get_request returns a valid RequestRecord satisfying Pydantic constraints."""
    conv = store.create_conversation("Contract consistency")
    req_id = generate_request_id()
    store.begin_request(req_id, "User query", conv.id)

    record = store.get_request(conv.id, req_id)
    assert record is not None
    assert isinstance(record, RequestRecord)
    assert record.conversation_id == conv.id
    assert record.client_request_id == req_id
    assert record.user_message.role == MessageRole.USER
    assert record.user_message.content == "User query"
    assert record.assistant_message.role == MessageRole.ASSISTANT
    assert record.assistant_message.status == MessageStatus.PENDING


def test_returned_records_are_frozen_immutable(store):
    """Pydantic models returned by repositories cannot be mutated."""
    conv = store.create_conversation("Frozen check")
    req_id = generate_request_id()
    store.begin_request(req_id, "Check immutability", conv.id)

    record = store.get_request(conv.id, req_id)
    assert record is not None

    with pytest.raises(ValidationError):
        record.client_request_id = "mutated"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        record.user_message.content = "mutated"  # type: ignore[misc]


def test_get_request_conflicting_conversation_id_raises(store):
    """get_request with mismatched conversation_id raises PersistenceIntegrityError."""
    conv1 = store.create_conversation("Conv 1")
    conv2 = store.create_conversation("Conv 2")
    req_id = generate_request_id()
    store.begin_request(req_id, "Hello", conv1.id)

    with pytest.raises(PersistenceIntegrityError):
        store.get_request(conv2.id, req_id)


# ---------------------------------------------------------------------------
# Part 6: Duplicate State Transitions
# ---------------------------------------------------------------------------


def test_duplicate_complete_request_identical_content(store):
    """Duplicate complete_request with identical content returns existing message (idempotent)."""
    conv = store.create_conversation("Dup complete")
    req_id = generate_request_id()
    store.begin_request(req_id, "Prompt", conv.id)

    m1 = store.complete_request(conv.id, req_id, "The Answer")
    m2 = store.complete_request(conv.id, req_id, "The Answer")

    assert m1.id == m2.id
    assert m2.status == MessageStatus.COMPLETED
    assert m2.content == "The Answer"


def test_duplicate_complete_request_different_content_raises(store):
    """Duplicate complete_request with different content raises InvalidStateTransitionError."""
    conv = store.create_conversation("Diff complete")
    req_id = generate_request_id()
    store.begin_request(req_id, "Prompt", conv.id)

    store.complete_request(conv.id, req_id, "The Answer")
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        store.complete_request(conv.id, req_id, "A Different Answer")

    assert "already completed with different content" in str(exc_info.value)


def test_fail_after_complete_raises_invalid_transition(store):
    """Failing an already-completed message raises InvalidStateTransitionError."""
    conv = store.create_conversation("Fail after complete")
    req_id = generate_request_id()
    store.begin_request(req_id, "Prompt", conv.id)
    store.complete_request(conv.id, req_id, "Success")

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        store.fail_request(conv.id, req_id, "Error after success")

    assert "Cannot fail a message in state 'completed'" in str(exc_info.value)


def test_complete_after_fail_raises_invalid_transition(store):
    """Completing an already-failed message raises InvalidStateTransitionError."""
    conv = store.create_conversation("Complete after fail")
    req_id = generate_request_id()
    store.begin_request(req_id, "Prompt", conv.id)
    store.fail_request(conv.id, req_id, "Failed originally")

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        store.complete_request(conv.id, req_id, "Belated answer")

    assert "Cannot complete a message in state 'failed'" in str(exc_info.value)


def test_duplicate_fail_request_identical_error(store):
    """Duplicate fail_request with identical error message is idempotent."""
    conv = store.create_conversation("Dup fail")
    req_id = generate_request_id()
    store.begin_request(req_id, "Prompt", conv.id)

    f1 = store.fail_request(conv.id, req_id, "Timeout", partial_content="partial")
    f2 = store.fail_request(conv.id, req_id, "Timeout", partial_content="partial")

    assert f1.id == f2.id
    assert f2.status == MessageStatus.FAILED
    assert f2.error_message == "Timeout"
    assert f2.content == "partial"


def test_duplicate_fail_request_different_error_raises(store):
    """Duplicate fail_request with different error message raises InvalidStateTransitionError."""
    conv = store.create_conversation("Diff fail")
    req_id = generate_request_id()
    store.begin_request(req_id, "Prompt", conv.id)

    store.fail_request(conv.id, req_id, "Timeout")
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        store.fail_request(conv.id, req_id, "Connection refused")

    assert "already failed with different error" in str(exc_info.value)


def test_get_request_after_failure(store):
    """get_request returns correct RequestRecord after a failure."""
    conv = store.create_conversation("Get after fail")
    req_id = generate_request_id()
    store.begin_request(req_id, "Failing prompt", conv.id)
    store.fail_request(conv.id, req_id, "Out of memory", partial_content="Partial text")

    record = store.get_request(conv.id, req_id)
    assert record is not None
    assert record.assistant_message.status == MessageStatus.FAILED
    assert record.assistant_message.error_message == "Out of memory"
    assert record.assistant_message.content == "Partial text"
    assert record.user_message.status == MessageStatus.COMPLETED


# ---------------------------------------------------------------------------
# Part 7: Session and Transaction Isolation
# ---------------------------------------------------------------------------


def test_atomic_rollback_of_entity_and_activity_event_on_failure(store_with_factory):
    """On constraint failure, both entity records and activity events are rolled back."""
    store, factory = store_with_factory
    conv = store.create_conversation("Rollback test")

    with factory() as session:
        initial_events = session.execute(select(ActivityEventModel)).scalars().all()
        initial_msgs = session.execute(select(MessageModel)).scalars().all()

    initial_event_count = len(initial_events)
    initial_msg_count = len(initial_msgs)

    # Trigger a validation failure before transaction commit
    with pytest.raises(ValueError):
        store.begin_request(generate_request_id(), "   ", conv.id)

    # Verify no orphan events or messages were committed
    with factory() as session:
        after_events = session.execute(select(ActivityEventModel)).scalars().all()
        after_msgs = session.execute(select(MessageModel)).scalars().all()

    assert len(after_events) == initial_event_count
    assert len(after_msgs) == initial_msg_count
