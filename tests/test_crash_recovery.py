"""Tests for crash recovery of abandoned pending requests (Milestone 3.6 Part D)."""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import generate_request_id
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.models import MessageModel
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)


@pytest.fixture
def recovery_env(tmp_path: Path):
    """Provide a fresh SQLite environment for crash recovery testing."""
    db_file = tmp_path / "recovery_test.db"
    db_url = f"sqlite:///{db_file}"
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    provider = FakeModelProvider()
    service = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    return {
        "engine": engine,
        "factory": factory,
        "conv_store": conv_store,
        "settings_store": settings_store,
        "service": service,
    }


def test_abandoned_pending_assistant_message_recovered(recovery_env):
    """A pending assistant message left by a crash becomes interrupted on recovery."""
    conv_store: SqliteConversationStore = recovery_env["conv_store"]
    factory = recovery_env["factory"]

    # Create a conversation and start a request (creates user=completed and assistant=pending)
    conv = conv_store.create_conversation("Crash Thread")
    req_id = generate_request_id()
    conv_store.begin_request(
        client_request_id=req_id,
        user_content="What happened before the crash?",
        conversation_id=conv.id,
    )

    # Verify initial state: user is completed, assistant is pending
    with factory() as session:
        asst = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req_id,
                MessageModel.role == "assistant",
            )
        )
        assert asst is not None
        assert asst.status == "pending"
        assert asst.content is None
        assert asst.error_message is None

    # Run crash recovery
    recovered_count = conv_store.recover_pending_requests(
        reason="Previous ORION process ended before generation completed."
    )
    assert recovered_count == 1

    # Verify recovered state: assistant is now interrupted with non-empty reason
    with factory() as session:
        user = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req_id,
                MessageModel.role == "user",
            )
        )
        asst = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req_id,
                MessageModel.role == "assistant",
            )
        )
        # User message remains COMPLETED
        assert user.status == "completed"
        # Assistant message is now INTERRUPTED
        assert asst.status == "interrupted"
        assert asst.content is None  # Partial content not invented
        assert asst.error_message == "Previous ORION process ended before generation completed."

    # Verify activity events: assistant.response.interrupted event was recorded
    events = conv_store.load_activity(aggregate_type="message", aggregate_id=asst.id)
    assert any(e.event_type == "assistant.response.interrupted" for e in events)


def test_completed_failed_and_interrupted_messages_are_not_modified(recovery_env):
    """Recovery touches ONLY pending messages; completed, failed, and interrupted messages remain intact."""
    conv_store: SqliteConversationStore = recovery_env["conv_store"]
    factory = recovery_env["factory"]

    conv = conv_store.create_conversation("Mixed Thread")

    # 1. Completed request
    req1 = generate_request_id()
    conv_store.begin_request(
        client_request_id=req1, user_content="Completed Prompt", conversation_id=conv.id
    )
    conv_store.complete_request(conv.id, req1, "Completed Response")

    # 2. Failed request
    req2 = generate_request_id()
    conv_store.begin_request(
        client_request_id=req2, user_content="Failed Prompt", conversation_id=conv.id
    )
    conv_store.fail_request(conv.id, req2, "Provider exploded")

    # 3. Already interrupted request
    req3 = generate_request_id()
    conv_store.begin_request(
        client_request_id=req3, user_content="Interrupted Prompt", conversation_id=conv.id
    )
    conv_store.interrupt_request(
        conv.id, req3, partial_content="Half response", reason="User stopped"
    )

    # 4. Abandoned pending request
    req4 = generate_request_id()
    conv_store.begin_request(
        client_request_id=req4, user_content="Abandoned Prompt", conversation_id=conv.id
    )

    # Run recovery
    count = conv_store.recover_pending_requests()
    assert count == 1  # Only req4 was recovered

    # Verify other messages were not touched
    with factory() as session:
        asst1 = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req1, MessageModel.role == "assistant"
            )
        )
        assert asst1.status == "completed"
        assert asst1.content == "Completed Response"

        asst2 = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req2, MessageModel.role == "assistant"
            )
        )
        assert asst2.status == "failed"
        assert asst2.error_message == "Provider exploded"

        asst3 = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req3, MessageModel.role == "assistant"
            )
        )
        assert asst3.status == "interrupted"
        assert asst3.content == "Half response"
        assert asst3.error_message == "User stopped"

        asst4 = session.scalar(
            select(MessageModel).where(
                MessageModel.client_request_id == req4, MessageModel.role == "assistant"
            )
        )
        assert asst4.status == "interrupted"


def test_recovery_is_idempotent(recovery_env):
    """Running recovery when no messages are pending returns 0 and is safe."""
    conv_store: SqliteConversationStore = recovery_env["conv_store"]

    conv = conv_store.create_conversation("Idempotency Thread")
    req_id = generate_request_id()
    conv_store.begin_request(
        client_request_id=req_id, user_content="Pending prompt", conversation_id=conv.id
    )

    count1 = conv_store.recover_pending_requests()
    assert count1 == 1

    # Second run immediately after
    count2 = conv_store.recover_pending_requests()
    assert count2 == 0


def test_service_delegates_recover_pending_requests(recovery_env):
    """AssistantService.recover_pending_requests delegates to conversation store."""
    service: AssistantService = recovery_env["service"]
    conv_store: SqliteConversationStore = recovery_env["conv_store"]

    conv = conv_store.create_conversation("Service Thread")
    req_id = generate_request_id()
    conv_store.begin_request(
        client_request_id=req_id, user_content="Service pending", conversation_id=conv.id
    )

    count = service.recover_pending_requests()
    assert count == 1
