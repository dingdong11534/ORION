"""Tests for AssistantService persistence integration (Milestone 2.3)."""

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from pydantic import ValidationError

from alembic import command
from orion.application.assistant_service import AssistantService, AssistantServiceError
from orion.application.contracts import (
    ChatMessage,
    MessageStatus,
    generate_request_id,
)
from orion.application.ports import (
    ConversationStore,
    PersistenceError,
)
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def _apply_migrations(db_url: str) -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


class RecordingModelProvider:
    """Mock model provider tracking prompts and historical context."""

    def __init__(self, response_text: str = "Test response") -> None:
        self.response_text = response_text
        self.calls: list[dict] = []

    def generate_response(self, prompt: str, history: Sequence[ChatMessage] | None = None) -> str:
        self.calls.append({"prompt": prompt, "history": list(history or [])})
        return self.response_text


class CrashingModelProvider:
    """Mock provider that always raises an error during generation."""

    def generate_response(self, prompt: str, history: Sequence[ChatMessage] | None = None) -> str:
        raise ConnectionResetError("Connection to inference runtime lost.")


@pytest.fixture
def service_env(tmp_path: Path):
    """Provide a fresh AssistantService backed by temporary SQLite DB."""
    db_file = tmp_path / "asst_service_test.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url)
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    provider = RecordingModelProvider("Echo: scheduled")
    service = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )
    return {
        "service": service,
        "conv_store": conv_store,
        "settings_store": settings_store,
        "provider": provider,
        "factory": factory,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Core Workflow & Persistence Tests
# ---------------------------------------------------------------------------


def test_new_user_request_is_persisted(service_env):
    """User request and completed assistant message are persisted to SQLite."""
    service: AssistantService = service_env["service"]
    conv_store: SqliteConversationStore = service_env["conv_store"]

    reply = service.send_message("Plan my schedule")

    assert reply.status == MessageStatus.COMPLETED
    assert reply.content == "Echo: scheduled"
    assert reply.is_new is True

    cid = service.active_conversation_id
    assert cid is not None

    # Verify history in SQLite
    persisted = conv_store.load_history(cid)
    assert len(persisted) == 2
    assert persisted[0].role == "user"
    assert persisted[0].content == "Plan my schedule"
    assert persisted[0].status == MessageStatus.COMPLETED
    assert persisted[1].role == "assistant"
    assert persisted[1].content == "Echo: scheduled"
    assert persisted[1].status == MessageStatus.COMPLETED


def test_assistant_completion_is_persisted(service_env):
    """Assistant completion status and content are persisted."""
    service: AssistantService = service_env["service"]
    conv_store: SqliteConversationStore = service_env["conv_store"]

    req_id = generate_request_id()
    reply = service.send_message("Hello", client_request_id=req_id)

    record = conv_store.get_request(service.active_conversation_id, req_id)
    assert record is not None
    assert record.assistant_message.status == MessageStatus.COMPLETED
    assert record.assistant_message.content == reply.content


def test_in_memory_history_is_no_longer_authoritative(service_env):
    """History is loaded from persistent storage, not in-memory state."""
    service1: AssistantService = service_env["service"]
    conv_store = service_env["conv_store"]
    settings_store = service_env["settings_store"]
    provider = service_env["provider"]

    service1.send_message("Persisted turn 1")
    cid = service1.active_conversation_id

    # Recreate AssistantService with fresh in-memory state pointing to same DB
    service2 = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    history = service2.get_history(cid)
    assert len(history) == 2
    assert history[0].content == "Persisted turn 1"
    assert history[1].content == "Echo: scheduled"


def test_historical_context_is_retrieved_from_sqlite(service_env):
    """Subsequent requests pass completed previous history from SQLite to model provider."""
    service: AssistantService = service_env["service"]
    provider: RecordingModelProvider = service_env["provider"]

    service.send_message("First question")
    service.send_message("Second question")

    assert len(provider.calls) == 2
    second_call = provider.calls[1]
    assert second_call["prompt"] == "Second question"

    history_passed = second_call["history"]
    assert len(history_passed) == 2
    assert history_passed[0].role == "user"
    assert history_passed[0].content == "First question"
    assert history_passed[1].role == "assistant"
    assert history_passed[1].content == "Echo: scheduled"


def test_only_completed_history_is_passed_to_provider(service_env):
    """Pending or failed messages are excluded from historical context passed to model."""
    service: AssistantService = service_env["service"]
    conv_store: SqliteConversationStore = service_env["conv_store"]
    provider: RecordingModelProvider = service_env["provider"]

    service.send_message("Turn 1")
    cid = service.active_conversation_id

    # Simulate a failed turn in persistence
    failed_req_id = generate_request_id()
    conv_store.begin_request(failed_req_id, "Bad turn", cid)
    conv_store.fail_request(cid, failed_req_id, "Syntax error")

    # Send next turn
    service.send_message("Turn 2")

    last_call = provider.calls[-1]
    history = last_call["history"]
    # Only Turn 1 (user + asst) must be in history, NOT "Bad turn"
    contents = [m.content for m in history]
    assert "Turn 1" in contents
    assert "Bad turn" not in contents


def test_current_prompt_is_not_duplicated_in_history(service_env):
    """The current prompt being sent is excluded from the history passed to generate_response."""
    service: AssistantService = service_env["service"]
    provider: RecordingModelProvider = service_env["provider"]

    service.send_message("Current prompt")

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["prompt"] == "Current prompt"
    assert len(call["history"]) == 0


def test_failed_generation_persists_failure_state(service_env):
    """Model provider failure updates assistant message to FAILED in SQLite."""
    conv_store: SqliteConversationStore = service_env["conv_store"]
    settings_store: SqliteSettingsStore = service_env["settings_store"]
    service = AssistantService(
        model_provider=CrashingModelProvider(),
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    req_id = generate_request_id()
    with pytest.raises(AssistantServiceError) as exc_info:
        service.send_message("Generate report", client_request_id=req_id)

    assert "Model provider generation failed" in str(exc_info.value)

    cid = service.active_conversation_id
    record = conv_store.get_request(cid, req_id)
    assert record is not None
    assert record.assistant_message.status == MessageStatus.FAILED
    assert "Connection to inference runtime lost." in (record.assistant_message.error_message or "")


def test_failed_generation_preserves_user_message(service_env):
    """When model generation fails, the user message remains completed in SQLite."""
    conv_store: SqliteConversationStore = service_env["conv_store"]
    settings_store: SqliteSettingsStore = service_env["settings_store"]
    service = AssistantService(
        model_provider=CrashingModelProvider(),
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    req_id = generate_request_id()
    with pytest.raises(AssistantServiceError):
        service.send_message("Preserve my question", client_request_id=req_id)

    cid = service.active_conversation_id
    record = conv_store.get_request(cid, req_id)
    assert record is not None
    assert record.user_message.role == "user"
    assert record.user_message.content == "Preserve my question"
    assert record.user_message.status == MessageStatus.COMPLETED


# ---------------------------------------------------------------------------
# Duplicate Request & Idempotency Tests
# ---------------------------------------------------------------------------


def test_duplicate_completed_request_does_not_invoke_model(service_env):
    """Duplicate completed request returns existing response without calling provider."""
    service: AssistantService = service_env["service"]
    provider: RecordingModelProvider = service_env["provider"]

    req_id = generate_request_id()
    res1 = service.send_message("Explain recursion", client_request_id=req_id)
    assert res1.is_new is True
    assert len(provider.calls) == 1

    # Resubmit with identical content and request ID
    res2 = service.send_message("Explain recursion", client_request_id=req_id)
    assert res2.is_new is False
    assert res2.content == res1.content
    assert res2.status == MessageStatus.COMPLETED

    # Model provider was NOT invoked again
    assert len(provider.calls) == 1


def test_duplicate_pending_request_does_not_invoke_model(service_env):
    """Duplicate pending request returns pending status without calling provider."""
    service: AssistantService = service_env["service"]
    conv_store: SqliteConversationStore = service_env["conv_store"]
    provider: RecordingModelProvider = service_env["provider"]

    conv = conv_store.create_conversation("Pending test")
    req_id = generate_request_id()
    # Manually begin request without completing it
    conv_store.begin_request(req_id, "In-progress query", conv.id)

    res = service.send_message(
        "In-progress query",
        client_request_id=req_id,
        conversation_id=conv.id,
    )
    assert res.is_new is False
    assert res.status == MessageStatus.PENDING
    assert len(provider.calls) == 0


def test_duplicate_failed_request_does_not_invoke_model(service_env):
    """Duplicate failed request returns failure status without calling provider."""
    conv_store: SqliteConversationStore = service_env["conv_store"]
    settings_store: SqliteSettingsStore = service_env["settings_store"]
    provider = RecordingModelProvider()

    service = AssistantService(
        model_provider=CrashingModelProvider(),
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    req_id = generate_request_id()
    with pytest.raises(AssistantServiceError):
        service.send_message("Fail me", client_request_id=req_id)

    # Switch provider to working provider
    service._model_provider = provider

    # Resubmit identical failed request
    res = service.send_message("Fail me", client_request_id=req_id)
    assert res.is_new is False
    assert res.status == MessageStatus.FAILED
    assert len(provider.calls) == 0


def test_explicit_retry_with_new_id_creates_new_request(service_env):
    """Retrying with a new request ID creates a new turn after a prior failure."""
    conv_store: SqliteConversationStore = service_env["conv_store"]
    settings_store: SqliteSettingsStore = service_env["settings_store"]

    crashing_provider = CrashingModelProvider()
    service = AssistantService(
        model_provider=crashing_provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    req1_id = generate_request_id()
    with pytest.raises(AssistantServiceError):
        service.send_message("Query", client_request_id=req1_id)

    # Replace with working provider
    service._model_provider = FakeModelProvider()

    # Retry with a NEW request ID
    req2_id = generate_request_id()
    res = service.send_message("Query", client_request_id=req2_id)
    assert res.is_new is True
    assert res.status == MessageStatus.COMPLETED


# ---------------------------------------------------------------------------
# Active Conversation Management Tests
# ---------------------------------------------------------------------------


def test_active_conversation_is_remembered(service_env):
    """Successive send_message calls reuse the active conversation."""
    service: AssistantService = service_env["service"]

    service.send_message("Message 1")
    cid1 = service.active_conversation_id
    assert cid1 is not None

    service.send_message("Message 2")
    cid2 = service.active_conversation_id
    assert cid1 == cid2


def test_active_conversation_is_loaded_from_settings(service_env):
    """AssistantService loads the active conversation from SettingsStore on startup."""
    service1: AssistantService = service_env["service"]
    conv_store = service_env["conv_store"]
    settings_store = service_env["settings_store"]
    provider = service_env["provider"]

    service1.send_message("Start thread")
    cid = service1.active_conversation_id

    # New instance using the same stores
    service2 = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )
    assert service2.active_conversation_id == cid


def test_invalid_active_conversation_handled_safely(service_env):
    """An active conversation ID referencing a nonexistent thread is cleared safely."""
    conv_store = service_env["conv_store"]
    settings_store: SqliteSettingsStore = service_env["settings_store"]
    provider = service_env["provider"]

    settings_store.set_setting("active_conversation_id", "conv_nonexistent_123")

    service = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )
    assert service.active_conversation_id is None
    # Stale setting should be cleared
    assert settings_store.get_setting("active_conversation_id") is None


# ---------------------------------------------------------------------------
# Failure & Recovery Tests
# ---------------------------------------------------------------------------


def test_database_failure_prevents_model_invocation(service_env):
    """Persistence failure in begin_request prevents model provider from being called."""
    mock_store = MagicMock(spec=ConversationStore)
    mock_store.begin_request.side_effect = PersistenceError("Disk full")
    mock_store.list_conversations.return_value = []
    provider = RecordingModelProvider()

    service = AssistantService(
        model_provider=provider,
        conversation_store=mock_store,
    )

    with pytest.raises(AssistantServiceError) as exc_info:
        service.send_message("Should fail before model")

    assert "Failed to begin persistent request" in str(exc_info.value)
    assert len(provider.calls) == 0


def test_final_response_persistence_failure_does_not_trigger_generation_again(service_env):
    """Failure in complete_request raises error without re-invoking model provider."""
    mock_store = MagicMock(spec=ConversationStore)
    provider = RecordingModelProvider("Model answer")

    # begin_request succeeds
    mock_record = MagicMock()
    mock_record.conversation_id = "conv_mock_1"
    mock_start = MagicMock()
    mock_start.created = True
    mock_start.request = mock_record
    mock_store.begin_request.return_value = mock_start
    mock_store.load_history.return_value = []
    mock_store.list_conversations.return_value = []

    # complete_request fails
    mock_store.complete_request.side_effect = PersistenceError("Commit failed")

    service = AssistantService(
        model_provider=provider,
        conversation_store=mock_store,
    )

    with pytest.raises(AssistantServiceError) as exc_info:
        service.send_message("Trigger failure")

    assert "Failed to persist completed response" in str(exc_info.value)
    # Model was called only once
    assert len(provider.calls) == 1


@pytest.mark.parametrize("blank_input", ["", "   ", "\t\t"])
def test_assistant_service_rejects_blank_message(service_env, blank_input: str):
    """Blank input is rejected by ChatRequest validation before any persistence or model work."""
    service: AssistantService = service_env["service"]
    provider: RecordingModelProvider = service_env["provider"]

    with pytest.raises(ValidationError):
        service.send_message(blank_input)

    assert len(provider.calls) == 0
