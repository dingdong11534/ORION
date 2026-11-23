"""Tests for SQLAlchemy ORM models, constraints, and relationships."""

import json
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    Base,
    ConversationModel,
    MessageModel,
    UserSettingModel,
    generate_conversation_id,
    generate_event_id,
    generate_message_id,
    generate_request_id,
)


@pytest.fixture
def session(tmp_path: Path):
    db_file = tmp_path / "models_test.db"
    engine = create_db_engine(db_file)
    init_database(engine)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as sess:
        yield sess


# ---------------------------------------------------------------------------
# Identifier lengths
# ---------------------------------------------------------------------------


def test_conversation_id_length():
    """conv_ + 32 hex = 37 characters."""
    cid = generate_conversation_id()
    assert cid.startswith("conv_")
    assert len(cid) == 37


def test_message_id_length():
    """msg_ + 32 hex = 36 characters."""
    mid = generate_message_id()
    assert mid.startswith("msg_")
    assert len(mid) == 36


def test_event_id_length():
    """evt_ + 32 hex = 36 characters."""
    eid = generate_event_id()
    assert eid.startswith("evt_")
    assert len(eid) == 36


def test_request_id_length():
    """req_ + 32 hex = 36 characters."""
    rid = generate_request_id()
    assert rid.startswith("req_")
    assert len(rid) == 36


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def test_create_conversation_defaults(session):
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    assert conv.id.startswith("conv_")
    assert len(conv.id) == 37
    assert conv.title == "New Conversation"
    assert conv.next_sequence == 0
    assert conv.created_at is not None
    assert conv.updated_at is not None


def test_conversation_next_sequence_exists(session):
    """next_sequence column is present and defaults to 0."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()
    assert conv.next_sequence == 0


def test_conversation_next_sequence_cannot_be_negative(session):
    conv = ConversationModel(next_sequence=-1)
    session.add(conv)
    with pytest.raises(IntegrityError):
        session.commit()


def test_conversation_title_not_blank_constraint(session):
    conv = ConversationModel(title="   ")
    session.add(conv)
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Messages — client_request_id
# ---------------------------------------------------------------------------


def test_null_client_request_id_rejected(session):
    """client_request_id is NOT NULL."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=None,  # type: ignore[arg-type]
        role="user",
        status="completed",
        content="Hello",
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_blank_client_request_id_rejected(session):
    """Blank client_request_id violates check constraint."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id="   ",
        role="user",
        status="completed",
        content="Hello",
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_request_id_uniqueness_per_role(session):
    """UNIQUE(conversation_id, client_request_id, role) is enforced."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    req_id = generate_request_id()

    msg1 = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=req_id,
        role="user",
        status="completed",
        content="Original query",
    )
    # Same conv + same req_id + same role = duplicate
    msg2 = MessageModel(
        conversation_id=conv.id,
        sequence_number=1,
        client_request_id=req_id,
        role="user",
        status="completed",
        content="Duplicate",
    )
    session.add_all([msg1, msg2])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Same req_id but different role is allowed
    msg3 = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=req_id,
        role="user",
        status="completed",
        content="User message",
    )
    msg4 = MessageModel(
        conversation_id=conv.id,
        sequence_number=1,
        client_request_id=req_id,
        role="assistant",
        status="completed",
        content="Assistant response",
    )
    session.add_all([msg3, msg4])
    session.commit()  # Must succeed


# ---------------------------------------------------------------------------
# Messages — role/status/content valid combinations
# ---------------------------------------------------------------------------


def test_user_message_valid(session):
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="user",
        status="completed",
        content="Hello assistant",
    )
    session.add(msg)
    session.commit()
    assert msg.id.startswith("msg_")


def test_assistant_pending_valid(session):
    """Assistant pending: NULL content and NULL error_message."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="pending",
        content=None,
        error_message=None,
    )
    session.add(msg)
    session.commit()
    assert msg.id.startswith("msg_")


def test_assistant_completed_valid(session):
    """Assistant completed: non-empty content, no error."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="completed",
        content="I am ORION.",
        error_message=None,
    )
    session.add(msg)
    session.commit()


def test_assistant_failed_with_error_valid(session):
    """Assistant failed: error_message only is valid."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="failed",
        content=None,
        error_message="Model timed out",
    )
    session.add(msg)
    session.commit()


def test_assistant_failed_with_partial_content_valid(session):
    """Assistant failed: partial content only is valid."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="failed",
        content="Partial before failure",
        error_message=None,
    )
    session.add(msg)
    session.commit()


def test_assistant_failed_with_both_valid(session):
    """Assistant failed: both partial content and error_message is valid."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="failed",
        content="Partial output",
        error_message="Connection reset",
    )
    session.add(msg)
    session.commit()


def test_assistant_interrupted_with_content_valid(session):
    """Assistant interrupted: partial content only is valid."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="interrupted",
        content="Partial text before shutdown...",
        error_message=None,
    )
    session.add(msg)
    session.commit()


def test_assistant_interrupted_with_error_valid(session):
    """Assistant interrupted: error_message only is valid."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="interrupted",
        content=None,
        error_message="User cancelled",
    )
    session.add(msg)
    session.commit()


# ---------------------------------------------------------------------------
# Messages — invalid combinations
# ---------------------------------------------------------------------------


def test_user_message_pending_rejected(session):
    """User message cannot have status=pending."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="user",
        status="pending",
        content="Pending user message",
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_user_message_empty_content_rejected(session):
    """User message cannot have blank/empty content."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="user",
        status="completed",
        content="   ",
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_assistant_pending_with_content_rejected(session):
    """Pending assistant message must NOT have content."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="pending",
        content="Should not be here",
        error_message=None,
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_assistant_pending_with_error_rejected(session):
    """Pending assistant message must NOT have error_message."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="pending",
        content=None,
        error_message="Should not be here",
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_assistant_completed_with_error_rejected(session):
    """Completed assistant message must NOT have error_message."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="completed",
        content="Good response",
        error_message="Should not be here",
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_assistant_failed_neither_content_nor_error_rejected(session):
    """Failed assistant with neither content nor error_message is rejected."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="failed",
        content=None,
        error_message=None,
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_assistant_interrupted_neither_content_nor_error_rejected(session):
    """Interrupted assistant with neither content nor error_message is rejected."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="assistant",
        status="interrupted",
        content=None,
        error_message=None,
    )
    session.add(msg)
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Messages — sequence
# ---------------------------------------------------------------------------


def test_message_sequence_uniqueness(session):
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg1 = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="user",
        status="completed",
        content="First",
    )
    msg2 = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,  # Duplicate!
        client_request_id=generate_request_id(),
        role="user",
        status="completed",
        content="Duplicate sequence",
    )
    session.add_all([msg1, msg2])
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Activity events
# ---------------------------------------------------------------------------


def test_activity_events_survive_conversation_deletion(session):
    """Activity events have no FK to conversations — they survive deletion."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()
    conv_id = conv.id

    evt = ActivityEventModel(
        event_type="conversation.created",
        aggregate_type="conversation",
        aggregate_id=conv_id,
        payload_json={"title": "Test conversation"},
    )
    session.add(evt)
    session.commit()
    evt_id = evt.id

    session.delete(conv)
    session.commit()

    # Event must survive — no cascade, no FK
    retained = session.scalar(select(ActivityEventModel).where(ActivityEventModel.id == evt_id))
    assert retained is not None
    assert retained.aggregate_id == conv_id  # Historical reference preserved


def test_activity_events_no_cascade_deletion(session):
    """Deleting a conversation must NOT delete any activity events."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    evts = [
        ActivityEventModel(
            event_type=f"test.event.{i}",
            aggregate_type="conversation",
            aggregate_id=conv.id,
            payload_json={"seq": i},
        )
        for i in range(3)
    ]
    session.add_all(evts)
    session.commit()
    evt_ids = [e.id for e in evts]

    session.delete(conv)
    session.commit()

    for eid in evt_ids:
        retained = session.scalar(select(ActivityEventModel).where(ActivityEventModel.id == eid))
        assert retained is not None, f"Event {eid} was incorrectly deleted"


def test_activity_event_payload_json_array_rejected(session):
    """JSON arrays are rejected — payload must be a JSON object."""
    evt = ActivityEventModel(
        event_type="test.event",
        aggregate_type="system",
        aggregate_id=None,
        payload_json=["item1", "item2"],
    )
    session.add(evt)
    with pytest.raises(IntegrityError):
        session.commit()


def test_activity_event_payload_json_scalar_rejected(session):
    """JSON scalars are rejected — payload must be a JSON object."""
    evt = ActivityEventModel(
        event_type="test.event",
        aggregate_type="system",
        aggregate_id=None,
        payload_json="scalar string",  # type: ignore[arg-type]
    )
    session.add(evt)
    with pytest.raises(IntegrityError):
        session.commit()


def test_activity_event_payload_stored_as_text(tmp_path: Path):
    """Verify payload_json is stored as TEXT (not SQLite BLOB or JSON type)."""
    db_file = tmp_path / "text_check.db"
    engine = create_db_engine(db_file)
    init_database(engine)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    payload = {"key": "value", "count": 42}
    with factory() as sess:
        evt = ActivityEventModel(
            event_type="test.stored_text",
            aggregate_type="system",
            aggregate_id=None,
            payload_json=payload,
        )
        sess.add(evt)
        sess.commit()
        evt_id = evt.id

    # Read raw value from SQLite directly
    with engine.connect() as conn:
        raw = conn.execute(
            text("SELECT payload_json FROM activity_events WHERE id = :id"), {"id": evt_id}
        ).scalar()
    assert isinstance(raw, str), "payload_json must be stored as TEXT string"
    assert json.loads(raw) == payload


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------


def test_conversation_deletion_cascades_to_messages(session):
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    msg = MessageModel(
        conversation_id=conv.id,
        sequence_number=0,
        client_request_id=generate_request_id(),
        role="user",
        status="completed",
        content="Will be deleted",
    )
    session.add(msg)
    session.commit()
    msg_id = msg.id

    session.delete(conv)
    session.commit()

    remaining = session.scalar(select(MessageModel).where(MessageModel.id == msg_id))
    assert remaining is None


# ---------------------------------------------------------------------------
# Indexes — no redundant message indexes
# ---------------------------------------------------------------------------


def test_no_redundant_message_indexes(tmp_path: Path):
    """messages table should NOT have an extra created_at index.

    uq_messages_conversation_sequence covers conversation-ordered retrieval.
    uq_messages_conversation_request_role covers request lookup.
    No standalone created_at index is needed.
    """
    db_file = tmp_path / "index_check.db"
    engine = create_db_engine(db_file)
    init_database(engine)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    # Get all index names (non-unique indexes only; unique constraints show separately)
    indexes = inspector.get_indexes("messages")
    index_names = {idx["name"] for idx in indexes if idx["name"] is not None}
    # There must be no standalone idx_messages_created_at
    assert "idx_messages_created_at" not in index_names, (
        "idx_messages_created_at is redundant and must not be created"
    )


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------


def test_user_settings_key_and_json_validation(session):
    # Blank key rejected
    setting_invalid = UserSettingModel(key="   ", value_json={"theme": "ivory"})
    session.add(setting_invalid)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Valid setting
    setting_valid = UserSettingModel(key="ui.theme", value_json={"color_scheme": "ivory-sage"})
    session.add(setting_valid)
    session.commit()

    retrieved = session.scalar(select(UserSettingModel).where(UserSettingModel.key == "ui.theme"))
    assert retrieved is not None
    assert retrieved.value_json == {"color_scheme": "ivory-sage"}


def test_assistant_message_pending_and_completed(session):
    """Full lifecycle: pending → completed update."""
    conv = ConversationModel()
    session.add(conv)
    session.commit()

    req_id = generate_request_id()

    asst_pending = MessageModel(
        conversation_id=conv.id,
        sequence_number=1,
        client_request_id=req_id,
        role="assistant",
        status="pending",
        content=None,
        error_message=None,
    )
    session.add(asst_pending)
    session.commit()

    asst_pending.status = "completed"
    asst_pending.content = "I am ORION."
    session.commit()

    retrieved = session.scalar(select(MessageModel).where(MessageModel.id == asst_pending.id))
    assert retrieved.status == "completed"
    assert retrieved.content == "I am ORION."
