"""Tests for SqliteConversationStore — conversations, messages, activity, idempotency."""

import threading
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from orion.application.contracts import MessageStatus
from orion.application.ports import (
    ConversationNotFoundError,
    InvalidStateTransitionError,
    RequestNotFoundError,
)
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    generate_request_id,
)
from orion.infrastructure.persistence.repositories import SqliteConversationStore

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
    db_file = tmp_path / "orion" / "repo_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory)


@pytest.fixture
def store_with_factory(tmp_path: Path):
    """Returns (store, factory) sharing the same database."""
    db_file = tmp_path / "orion" / "repo_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory), factory


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def test_create_conversation(store: SqliteConversationStore):
    conv = store.create_conversation("My first conversation")
    assert conv.id.startswith("conv_")
    assert len(conv.id) == 37
    assert conv.title == "My first conversation"
    assert conv.created_at is not None
    assert conv.updated_at is not None
    assert conv.created_at.tzinfo is not None


def test_create_conversation_blank_title_rejected(store: SqliteConversationStore):
    with pytest.raises(ValueError, match="blank"):
        store.create_conversation("   ")


def test_list_conversations_empty(store: SqliteConversationStore):
    result = store.list_conversations()
    assert list(result) == []


def test_list_conversations_ordered_by_updated_at(store: SqliteConversationStore):
    c1 = store.create_conversation("Alpha")
    store.create_conversation("Beta")
    c3 = store.create_conversation("Gamma")
    result = store.list_conversations()
    ids = [c.id for c in result]
    # Most recent first
    assert ids[0] == c3.id
    assert ids[-1] == c1.id


def test_list_conversations_limit(store: SqliteConversationStore):
    for i in range(5):
        store.create_conversation(f"Conv {i}")
    result = store.list_conversations(limit=3)
    assert len(result) == 3


def test_delete_conversation(store: SqliteConversationStore):
    conv = store.create_conversation("To be deleted")
    store.delete_conversation(conv.id)
    remaining = store.list_conversations()
    assert all(c.id != conv.id for c in remaining)


def test_delete_conversation_not_found(store: SqliteConversationStore):
    with pytest.raises(ConversationNotFoundError):
        store.delete_conversation("conv_" + "0" * 32)


def test_delete_conversation_cascades_messages(store: SqliteConversationStore, tmp_path: Path):
    """Messages are deleted when their conversation is deleted."""
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Will delete")
    store.begin_request(generate_request_id(), "Hello", conv.id)
    store.delete_conversation(conv.id)

    with factory() as session:
        from orion.infrastructure.persistence.models import MessageModel

        msgs = (
            session.execute(select(MessageModel).where(MessageModel.conversation_id == conv.id))
            .scalars()
            .all()
        )
        assert msgs == []


# ---------------------------------------------------------------------------
# Begin request
# ---------------------------------------------------------------------------


def test_begin_request_creates_new_conversation_when_none(store: SqliteConversationStore):
    req_id = generate_request_id()
    result = store.begin_request(req_id, "Hello!")
    assert result.created is True
    assert result.request.user_message.content == "Hello!"
    assert result.request.user_message.role == "user"
    assert result.request.user_message.status == MessageStatus.COMPLETED
    assert result.request.assistant_message.role == "assistant"
    assert result.request.assistant_message.status == MessageStatus.PENDING
    assert result.request.assistant_message.content is None


def test_begin_request_uses_supplied_conversation(store: SqliteConversationStore):
    conv = store.create_conversation("My chat")
    req_id = generate_request_id()
    result = store.begin_request(req_id, "Hello", conv.id)
    assert result.created is True
    assert result.request.conversation_id == conv.id


def test_begin_request_raises_if_conversation_not_found(store: SqliteConversationStore):
    with pytest.raises(ConversationNotFoundError):
        store.begin_request(generate_request_id(), "Hello", "conv_" + "0" * 32)


def test_begin_request_blank_content_rejected(store: SqliteConversationStore):
    with pytest.raises(ValueError, match="blank"):
        store.begin_request(generate_request_id(), "   ")


def test_begin_request_sequence_numbers_consecutive(store: SqliteConversationStore):
    conv = store.create_conversation("Seq test")
    req1 = store.begin_request(generate_request_id(), "First", conv.id)
    req2 = store.begin_request(generate_request_id(), "Second", conv.id)
    u1 = req1.request.user_message.sequence_number
    a1 = req1.request.assistant_message.sequence_number
    u2 = req2.request.user_message.sequence_number
    a2 = req2.request.assistant_message.sequence_number
    assert a1 == u1 + 1
    assert u2 == a1 + 1
    assert a2 == u2 + 1


def test_begin_request_idempotent_pending(store: SqliteConversationStore):
    """Calling begin_request again with same ID returns existing pending record."""
    conv = store.create_conversation("Idempotent")
    req_id = generate_request_id()
    r1 = store.begin_request(req_id, "Hello", conv.id)
    r2 = store.begin_request(req_id, "Hello", conv.id)
    assert r2.created is False
    assert r2.request.assistant_message.status == MessageStatus.PENDING
    assert r2.request.assistant_message.id == r1.request.assistant_message.id


def test_begin_request_idempotent_completed(store: SqliteConversationStore):
    """Calling begin_request after completion returns existing completed record."""
    conv = store.create_conversation("Idempotent completed")
    req_id = generate_request_id()
    store.begin_request(req_id, "Hello", conv.id)
    store.complete_request(conv.id, req_id, "Done")
    r2 = store.begin_request(req_id, "Hello", conv.id)
    assert r2.created is False
    assert r2.request.assistant_message.status == MessageStatus.COMPLETED


def test_begin_request_idempotent_failed(store: SqliteConversationStore):
    conv = store.create_conversation("Idempotent failed")
    req_id = generate_request_id()
    store.begin_request(req_id, "Hello", conv.id)
    store.fail_request(conv.id, req_id, "Some error")
    r2 = store.begin_request(req_id, "Hello", conv.id)
    assert r2.created is False
    assert r2.request.assistant_message.status == MessageStatus.FAILED


def test_begin_request_new_id_creates_new_request(store: SqliteConversationStore):
    """An explicit retry with a new request ID creates independent new messages."""
    conv = store.create_conversation("Retry")
    req1 = store.begin_request(generate_request_id(), "First attempt", conv.id)
    req2 = store.begin_request(generate_request_id(), "Second attempt", conv.id)
    assert req1.request.client_request_id != req2.request.client_request_id
    assert req2.created is True


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_load_history_ascending_sequence(store: SqliteConversationStore):
    conv = store.create_conversation("History")
    for i in range(3):
        req_id = generate_request_id()
        store.begin_request(req_id, f"Message {i}", conv.id)
        store.complete_request(conv.id, req_id, f"Reply {i}")
    history = store.load_history(conv.id)
    seqs = [m.sequence_number for m in history]
    assert seqs == sorted(seqs)


def test_load_history_limit(store: SqliteConversationStore):
    conv = store.create_conversation("Limit test")
    for i in range(4):
        req_id = generate_request_id()
        store.begin_request(req_id, f"Msg {i}", conv.id)
        store.complete_request(conv.id, req_id, f"Reply {i}")
    history = store.load_history(conv.id, limit=4)
    assert len(history) == 4


def test_load_history_respects_conversation_scope(store: SqliteConversationStore):
    c1 = store.create_conversation("Conv 1")
    c2 = store.create_conversation("Conv 2")
    store.begin_request(generate_request_id(), "In conv1", c1.id)
    store.begin_request(generate_request_id(), "In conv2", c2.id)
    h1 = store.load_history(c1.id)
    assert all(m.conversation_id == c1.id for m in h1)


# ---------------------------------------------------------------------------
# get_request
# ---------------------------------------------------------------------------


def test_get_request_returns_pair(store: SqliteConversationStore):
    conv = store.create_conversation("Get req")
    req_id = generate_request_id()
    store.begin_request(req_id, "Question", conv.id)
    record = store.get_request(conv.id, req_id)
    assert record is not None
    assert record.user_message.role == "user"
    assert record.assistant_message.role == "assistant"
    assert record.conversation_id == conv.id
    assert record.client_request_id == req_id


def test_get_request_missing_returns_none(store: SqliteConversationStore):
    conv = store.create_conversation("No req")
    result = store.get_request(conv.id, generate_request_id())
    assert result is None


# ---------------------------------------------------------------------------
# complete_request
# ---------------------------------------------------------------------------


def test_complete_request_success(store: SqliteConversationStore):
    conv = store.create_conversation("Complete")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    asst = store.complete_request(conv.id, req_id, "My answer")
    assert asst.status == MessageStatus.COMPLETED
    assert asst.content == "My answer"
    assert asst.error_message is None


def test_complete_request_blank_content_rejected(store: SqliteConversationStore):
    conv = store.create_conversation("Blank complete")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    with pytest.raises(ValueError, match="blank"):
        store.complete_request(conv.id, req_id, "   ")


def test_complete_request_idempotent_same_content(store: SqliteConversationStore):
    """Completing with identical content is idempotent."""
    conv = store.create_conversation("Idempotent complete")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    r1 = store.complete_request(conv.id, req_id, "Answer")
    r2 = store.complete_request(conv.id, req_id, "Answer")
    assert r1.id == r2.id
    assert r2.status == MessageStatus.COMPLETED


def test_complete_request_rejects_different_content(store: SqliteConversationStore):
    """Completing with different content on an already-completed message is rejected."""
    conv = store.create_conversation("Different content")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.complete_request(conv.id, req_id, "First answer")
    with pytest.raises(InvalidStateTransitionError):
        store.complete_request(conv.id, req_id, "Different answer")


def test_complete_request_rejects_failed_state(store: SqliteConversationStore):
    conv = store.create_conversation("Failed state")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.fail_request(conv.id, req_id, "Error occurred")
    with pytest.raises(InvalidStateTransitionError):
        store.complete_request(conv.id, req_id, "Late answer")


def test_complete_request_not_found(store: SqliteConversationStore):
    conv = store.create_conversation("Not found")
    with pytest.raises(RequestNotFoundError):
        store.complete_request(conv.id, generate_request_id(), "Answer")


# ---------------------------------------------------------------------------
# fail_request
# ---------------------------------------------------------------------------


def test_fail_request_success(store: SqliteConversationStore):
    conv = store.create_conversation("Fail")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    asst = store.fail_request(conv.id, req_id, "Model timed out")
    assert asst.status == MessageStatus.FAILED
    assert asst.error_message == "Model timed out"
    assert asst.content is None


def test_fail_request_with_partial_content(store: SqliteConversationStore):
    conv = store.create_conversation("Partial fail")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    asst = store.fail_request(conv.id, req_id, "Timeout", partial_content="partial answer")
    assert asst.status == MessageStatus.FAILED
    assert asst.content == "partial answer"
    assert asst.error_message == "Timeout"


def test_fail_request_blank_error_rejected(store: SqliteConversationStore):
    conv = store.create_conversation("Blank error")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    with pytest.raises(ValueError, match="blank"):
        store.fail_request(conv.id, req_id, "   ")


def test_fail_request_rejects_completed_state(store: SqliteConversationStore):
    conv = store.create_conversation("Fail completed")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.complete_request(conv.id, req_id, "Done")
    with pytest.raises(InvalidStateTransitionError):
        store.fail_request(conv.id, req_id, "Late error")


def test_fail_request_preserves_user_message(store: SqliteConversationStore):
    conv = store.create_conversation("Preserve user")
    req_id = generate_request_id()
    store.begin_request(req_id, "My question", conv.id)
    store.fail_request(conv.id, req_id, "Error")
    record = store.get_request(conv.id, req_id)
    assert record is not None
    assert record.user_message.content == "My question"
    assert record.user_message.status == MessageStatus.COMPLETED


def test_fail_request_not_found(store: SqliteConversationStore):
    conv = store.create_conversation("Not found fail")
    with pytest.raises(RequestNotFoundError):
        store.fail_request(conv.id, generate_request_id(), "Error")


# ---------------------------------------------------------------------------
# Activity events
# ---------------------------------------------------------------------------


def test_activity_event_conversation_created(store: SqliteConversationStore, tmp_path: Path):
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Event test")
    with factory() as session:
        events = (
            session.execute(
                select(ActivityEventModel)
                .where(ActivityEventModel.event_type == "conversation.created")
                .where(ActivityEventModel.aggregate_id == conv.id)
            )
            .scalars()
            .all()
        )
    assert len(events) >= 1


def test_activity_event_message_created(store: SqliteConversationStore, tmp_path: Path):
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Msg event")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)

    with factory() as session:
        events = (
            session.execute(
                select(ActivityEventModel).where(ActivityEventModel.event_type == "message.created")
            )
            .scalars()
            .all()
        )
    assert len(events) == 2  # user + assistant


def test_activity_event_completion(store: SqliteConversationStore, tmp_path: Path):
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Completion event")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.complete_request(conv.id, req_id, "Answer")

    with factory() as session:
        events = (
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.event_type == "assistant.response.completed"
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1


def test_activity_event_failure(store: SqliteConversationStore, tmp_path: Path):
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Failure event")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.fail_request(conv.id, req_id, "Error")

    with factory() as session:
        events = (
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.event_type == "assistant.response.failed"
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1


def test_activity_events_survive_conversation_deletion(
    store: SqliteConversationStore, tmp_path: Path
):
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Will be deleted")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    conv_id_saved = conv.id
    store.delete_conversation(conv.id)

    with factory() as session:
        events = (
            session.execute(
                select(ActivityEventModel).where(ActivityEventModel.aggregate_id == conv_id_saved)
            )
            .scalars()
            .all()
        )
    # Conversation events (created, deleted) have aggregate_id == conv_id_saved
    assert len(events) >= 2
    for evt in events:
        assert evt.aggregate_id == conv_id_saved

    # All activity events (conversation + message events) survive conversation deletion
    with factory() as session:
        all_events = session.execute(select(ActivityEventModel)).scalars().all()
    assert len(all_events) >= 4


def test_activity_payload_does_not_contain_message_body(
    store: SqliteConversationStore, tmp_path: Path
):
    """Activity payloads must not store full message content."""
    db_file = tmp_path / "orion" / "repo_test.db"
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)

    conv = store.create_conversation("Payload check")
    req_id = generate_request_id()
    secret_content = "SECRET_BODY_CONTENT_MUST_NOT_BE_IN_EVENTS"
    store.begin_request(req_id, secret_content, conv.id)
    store.complete_request(conv.id, req_id, "SHORT REPLY")

    with factory() as session:
        events = session.execute(select(ActivityEventModel)).scalars().all()
    for evt in events:
        payload_str = str(evt.payload_json)
        assert secret_content not in payload_str, (
            f"Full message content found in {evt.event_type} payload"
        )


def test_load_activity_ascending_order(store: SqliteConversationStore):
    conv = store.create_conversation("Activity order")
    req_id = generate_request_id()
    store.begin_request(req_id, "Q", conv.id)
    store.complete_request(conv.id, req_id, "A")
    events = store.load_activity()
    times = [e.created_at for e in events]
    assert times == sorted(times)


def test_load_activity_filtered_by_aggregate_type(store: SqliteConversationStore):
    store.create_conversation("Filter test")
    events = store.load_activity(aggregate_type="conversation")
    assert all(e.aggregate_type == "conversation" for e in events)


def test_load_activity_filtered_by_aggregate_id(store: SqliteConversationStore):
    conv = store.create_conversation("ID filter")
    events = store.load_activity(aggregate_type="conversation", aggregate_id=conv.id)
    assert all(e.aggregate_id == conv.id for e in events)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_requests_unique_sequence_numbers(tmp_path: Path):
    """Multiple threads must receive unique sequence numbers."""
    db_file = tmp_path / "orion" / "concurrent_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    store = SqliteConversationStore(factory)

    conv = store.create_conversation("Concurrent")
    n_threads = 6
    results: list = []
    errors: list = []
    barrier = threading.Barrier(n_threads)

    def worker():
        try:
            barrier.wait()
            req_id = generate_request_id()
            result = store.begin_request(req_id, "Concurrent message", conv.id)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == n_threads

    all_user_seqs = [r.request.user_message.sequence_number for r in results]
    all_asst_seqs = [r.request.assistant_message.sequence_number for r in results]
    all_seqs = all_user_seqs + all_asst_seqs
    assert len(set(all_seqs)) == len(all_seqs), f"Duplicate sequence numbers: {all_seqs}"


def test_concurrent_duplicate_request_ids_no_duplicates(tmp_path: Path):
    """Two threads submitting the same request ID must not create duplicates."""
    db_file = tmp_path / "orion" / "dedup_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    store = SqliteConversationStore(factory)

    conv = store.create_conversation("Dedup test")
    shared_req_id = generate_request_id()
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            result = store.begin_request(shared_req_id, "Same request", conv.id)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    # Both threads must have completed (one created, one found existing)
    assert not errors, f"Errors: {errors}"
    assert len(results) == 2
    # Regardless of which won, the assistant message IDs must be identical
    ids = {r.request.assistant_message.id for r in results}
    assert len(ids) == 1, f"Duplicate messages created: {ids}"


def test_separate_threads_get_separate_sessions(tmp_path: Path):
    """Each thread's repository call uses an independent Session."""
    db_file = tmp_path / "orion" / "sessions_test.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    store = SqliteConversationStore(factory)

    created_in_threads: list = []
    errors: list = []

    def worker():
        try:
            conv = store.create_conversation("Thread conv")
            created_in_threads.append(conv.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    # All 4 conversations created with distinct IDs (no session cross-contamination)
    assert len(set(created_in_threads)) == 4
