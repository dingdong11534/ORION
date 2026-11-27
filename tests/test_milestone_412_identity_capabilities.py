"""Tests for Phase C: Trusted ORION identity, CapabilityRegistry, and RAG non-interception."""

from pathlib import Path

from alembic.config import Config

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.identity.capabilities import CapabilityRegistry
from orion.application.identity.identity_service import OrionIdentityService
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)


def test_identity_and_capability_routing_distinguishes_external_orion_queries():
    reg = CapabilityRegistry(model_provider=object(), workspace_enabled=True)
    svc = OrionIdentityService(reg)

    assert svc.is_identity_or_capability_query("Who are you?")
    assert svc.is_identity_or_capability_query("What is your name?")
    assert svc.is_identity_or_capability_query(
        "Okay, so tell me about all of the things that can be done with Orion."
    )
    assert svc.is_identity_or_capability_query("Can you search the web?")
    assert svc.is_identity_or_capability_query("Can you speak?")

    # External Orion queries MUST NOT be intercepted as identity
    assert not svc.is_identity_or_capability_query(
        "Find mentions of the Orion telescope in my documents."
    )
    assert not svc.is_identity_or_capability_query(
        "Search the web for Orion telescope specifications."
    )
    assert not svc.is_identity_or_capability_query("Tell me about the Orion constellation.")


def test_capability_registry_reports_disabled_features_honestly():
    reg = CapabilityRegistry(
        model_provider=object(),
        knowledge_service=None,
        workspace_enabled=False,
        voice_service=None,
        web_search_service=None,
    )
    svc = OrionIdentityService(reg)
    reply = svc.answer("What can you do?")
    assert "ORION" in reply
    assert "Currently available capabilities:" in reply
    assert "Local Chat" in reply
    assert "Currently unavailable:" in reply
    assert "Public Web Search" in reply
    assert "Voice Input (Offline STT)" in reply


def test_assistant_service_uses_identity_without_calling_rag_or_llm(tmp_path: Path):
    class _SpyProvider:
        called = False

        def generate_response(self, *args, **kwargs):
            self.called = True
            return "LLM"

        def supports_thinking(self):
            return False

    class _SpyRetrieval:
        called = False

        def retrieve(self, *args, **kwargs):
            self.called = True
            return []

    db_path = tmp_path / "id.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")
    eng = create_db_engine(db_path)
    sf = create_session_factory(eng)
    conv_store = SqliteConversationStore(sf)
    settings = SqliteSettingsStore(sf)

    provider = _SpyProvider()
    retrieval = _SpyRetrieval()
    identity = OrionIdentityService(CapabilityRegistry(model_provider=provider))
    asst = AssistantService(
        provider,
        conv_store,
        settings,
        retrieval_service=retrieval,
        identity_service=identity,
    )
    conv = asst.create_new_conversation("Identity")
    chunks = list(
        asst.stream_message(
            "Okay, so tell me about all of the things that can be done with Orion.",
            client_request_id="req_id_1",
            conversation_id=conv.id,
        )
    )
    full = "".join(chunks)
    assert "ORION" in full
    assert not provider.called
    assert not retrieval.called
    eng.dispose()
