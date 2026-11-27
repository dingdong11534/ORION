"""Tests for application wiring and startup verification in main.py."""

from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from orion.infrastructure.persistence.database import create_db_engine, init_database
from orion.main import (
    _EXPECTED_ALEMBIC_REVISION,
    check_database_migration,
    create_model_provider,
)


def _apply_migrations(db_url: str, revision: str = "head") -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, revision)


def test_check_migration_raises_when_database_file_absent(tmp_path: Path):
    """Missing database file raises RuntimeError with migration instructions."""
    nonexistent_file = tmp_path / "absent.db"
    engine = create_db_engine(nonexistent_file)

    with pytest.raises(RuntimeError) as exc_info:
        check_database_migration(engine)

    assert "Database file not found" in str(exc_info.value)
    assert ".venv/bin/alembic upgrade head" in str(exc_info.value)


def test_check_migration_raises_when_uninitialized(tmp_path: Path):
    """Empty database file without alembic_version raises RuntimeError."""
    db_file = tmp_path / "empty.db"
    engine = create_db_engine(db_file)
    init_database(engine, apply_wal=False)

    with pytest.raises(RuntimeError) as exc_info:
        check_database_migration(engine)

    assert "has not been initialized with Alembic" in str(exc_info.value)
    assert ".venv/bin/alembic upgrade head" in str(exc_info.value)


def test_check_migration_raises_when_outdated_revision(tmp_path: Path):
    """Database at outdated revision (e.g. 0001 instead of 0002) raises RuntimeError."""
    db_file = tmp_path / "outdated.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url, revision="0001")

    engine = create_db_engine(db_file)
    with pytest.raises(RuntimeError) as exc_info:
        check_database_migration(engine, expected_revision=_EXPECTED_ALEMBIC_REVISION)

    assert "is at revision '0001'" in str(exc_info.value)
    assert f"requires revision '{_EXPECTED_ALEMBIC_REVISION}'" in str(exc_info.value)
    assert ".venv/bin/alembic upgrade head" in str(exc_info.value)


def test_check_migration_succeeds_at_expected_revision(tmp_path: Path):
    """Database migrated to head revision succeeds without error."""
    db_file = tmp_path / "migrated.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url, revision="head")

    engine = create_db_engine(db_file)
    # Must not raise
    check_database_migration(engine, expected_revision=_EXPECTED_ALEMBIC_REVISION)


def test_check_migration_dynamically_resolves_head_revision(tmp_path: Path):
    """check_database_migration dynamically resolves head when expected_revision is omitted."""
    db_file = tmp_path / "dynamic_head.db"
    db_url = f"sqlite:///{db_file}"
    _apply_migrations(db_url, revision="head")

    engine = create_db_engine(db_file)
    # expected_revision=None dynamically inspects Alembic script directory
    check_database_migration(engine)


def test_create_model_provider_defaults_to_ollama(monkeypatch):
    """Default configuration returns OllamaModelProvider with qwen3:1.7b."""
    monkeypatch.delenv("ORION_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("ORION_OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("ORION_OLLAMA_MODEL", raising=False)

    from orion.infrastructure.ollama_model_provider import OllamaModelProvider

    provider = create_model_provider()
    assert isinstance(provider, OllamaModelProvider)
    assert provider.base_url == "http://127.0.0.1:11434"
    assert provider.model_name == "qwen3:1.7b"
    assert provider.think is False


def test_create_model_provider_custom_think_env(monkeypatch):
    """ORION_OLLAMA_THINK environment variable controls the think parameter."""
    monkeypatch.setenv("ORION_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("ORION_OLLAMA_THINK", "true")

    from orion.infrastructure.ollama_model_provider import OllamaModelProvider

    provider = create_model_provider()
    assert isinstance(provider, OllamaModelProvider)
    assert provider.think is True


def test_create_model_provider_fake(monkeypatch):
    """Setting ORION_MODEL_PROVIDER=fake returns FakeModelProvider."""
    monkeypatch.setenv("ORION_MODEL_PROVIDER", "fake")

    from orion.infrastructure.fake_model_provider import FakeModelProvider

    provider = create_model_provider()
    assert isinstance(provider, FakeModelProvider)


def test_create_model_provider_custom_ollama_env(monkeypatch):
    """Custom base URL and model name are respected."""
    monkeypatch.setenv("ORION_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("ORION_OLLAMA_BASE_URL", "http://192.168.1.100:11434/")
    monkeypatch.setenv("ORION_OLLAMA_MODEL", "qwen3:1.7b")

    from orion.infrastructure.ollama_model_provider import OllamaModelProvider

    provider = create_model_provider()
    assert isinstance(provider, OllamaModelProvider)
    assert provider.base_url == "http://192.168.1.100:11434"
    assert provider.model_name == "qwen3:1.7b"


def test_create_model_provider_unsupported_raises(monkeypatch):
    """Unsupported provider names raise a clear ValueError."""
    monkeypatch.setenv("ORION_MODEL_PROVIDER", "openai")

    with pytest.raises(ValueError) as exc_info:
        create_model_provider()

    assert "Unsupported ORION_MODEL_PROVIDER" in str(exc_info.value)
    assert "openai" in str(exc_info.value)


def test_ollama_provider_display_and_simulated_detection(tmp_path: Path):
    """AssistantService reports real Ollama model vs Simulation Mode accurately."""
    from orion.application.assistant_service import AssistantService
    from orion.infrastructure.fake_model_provider import FakeModelProvider
    from orion.infrastructure.ollama_model_provider import OllamaModelProvider
    from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
    from orion.infrastructure.persistence.repositories import (
        SqliteConversationStore,
        SqliteSettingsStore,
    )

    db_file = tmp_path / "prov.db"
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")
    eng = create_db_engine(db_file)
    fac = create_session_factory(eng)
    conv, settings = SqliteConversationStore(fac), SqliteSettingsStore(fac)

    svc_ollama = AssistantService(OllamaModelProvider(model_name="qwen3:1.7b"), conv, settings)
    assert svc_ollama.is_simulated_provider() is False
    assert svc_ollama.get_provider_display() == "Ollama · qwen3:1.7b"

    svc_fake = AssistantService(FakeModelProvider(), conv, settings)
    assert svc_fake.is_simulated_provider() is True
    assert svc_fake.get_provider_display() == "Simulation Mode"
    eng.dispose()
