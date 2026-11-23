"""QML interface tests for conversation management (Milestone 2.4)."""

import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.ui.qt_bridge import OrionBridge

os.environ["QT_QPA_PLATFORM"] = "offscreen"


def _apply_migrations(db_url: str) -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def _make_service(provider, tmp_path: Path) -> AssistantService:
    db_file = tmp_path / f"qml_test_{uuid.uuid4().hex}.db"
    _apply_migrations(f"sqlite:///{db_file}")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    return AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )


@pytest.fixture(scope="session")
def qgui_app():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def qml_context(qgui_app, tmp_path):
    provider = FakeModelProvider(simulated_delay=0.01)
    service = _make_service(provider, tmp_path)
    bridge = OrionBridge(assistant_service=service)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    root = engine.rootObjects()[0]
    bridge._thread_pool.waitForDone(3000)
    qgui_app.processEvents()

    yield {
        "engine": engine,
        "root": root,
        "bridge": bridge,
        "service": service,
        "app": qgui_app,
    }

    bridge.shutdown()


def test_qml_initial_loading_and_empty_workspace(qml_context):
    """QML loads initial state and shows empty workspace when no conversations exist."""
    root = qml_context["root"]
    chat_model = root.findChild(QObject, "chatModel")
    conv_model = root.findChild(QObject, "conversationsModel")

    assert root.property("isInitializing") is False
    assert conv_model.property("count") == 0
    assert chat_model.property("count") == 0
    assert root.property("activeConversationId") == ""


def test_qml_conversation_list_updates_after_creation(qml_context):
    """Creating a new conversation updates conversationsModel and sets active ID."""
    root = qml_context["root"]
    bridge = qml_context["bridge"]
    app = qml_context["app"]

    conv_model = root.findChild(QObject, "conversationsModel")
    chat_model = root.findChild(QObject, "chatModel")

    bridge.createNewConversation()
    bridge._thread_pool.waitForDone(3000)
    app.processEvents()

    assert conv_model.property("count") == 1
    active_id = root.property("activeConversationId")
    assert active_id != ""
    assert chat_model.property("count") == 0


def test_qml_chat_sending_and_history_population(qml_context):
    """Sending a message in QML appends user and assistant messages to chatModel."""
    root = qml_context["root"]
    bridge = qml_context["bridge"]
    app = qml_context["app"]

    chat_model = root.findChild(QObject, "chatModel")
    composer = root.findChild(QObject, "composer")

    composer.setProperty("text", "Hello QML test")
    root.sendMessage()

    # User message and assistant placeholder added immediately
    assert chat_model.property("count") == 2
    assert chat_model.get(0).property("role").toVariant() == "user"
    assert chat_model.get(1).property("role").toVariant() == "assistant"
    assert chat_model.get(1).property("status").toVariant() == "pending"

    bridge._thread_pool.waitForDone(3000)
    app.processEvents()

    # Assistant reply completed in-place without duplicate message items
    assert chat_model.property("count") == 2
    assert chat_model.get(1).property("status").toVariant() == "completed"
    assert "Echoing message" in chat_model.get(1).property("text").toVariant()


def test_qml_selecting_conversation_populates_chat_model(qml_context):
    """Switching conversations updates chatModel with the selected conversation history."""
    service: AssistantService = qml_context["service"]
    bridge: OrionBridge = qml_context["bridge"]
    root = qml_context["root"]
    app = qml_context["app"]

    # Pre-populate two conversations with messages
    c1 = service.create_new_conversation("Topic A")
    service.send_message("Question A", conversation_id=c1.id)

    c2 = service.create_new_conversation("Topic B")
    service.send_message("Question B", conversation_id=c2.id)

    # Load initial state
    bridge.loadInitialState()
    bridge._thread_pool.waitForDone(3000)
    app.processEvents()

    chat_model = root.findChild(QObject, "chatModel")
    # Active is Topic B
    assert root.property("activeConversationId") == c2.id
    assert chat_model.property("count") == 2

    # Switch to Topic A
    bridge.selectConversation(c1.id)
    bridge._thread_pool.waitForDone(3000)
    app.processEvents()

    assert root.property("activeConversationId") == c1.id
    assert chat_model.property("count") == 2


def test_qml_new_chat_resets_visible_messages(qml_context):
    """Clicking New Chat creates a conversation and clears visible chat messages."""
    service: AssistantService = qml_context["service"]
    bridge: OrionBridge = qml_context["bridge"]
    root = qml_context["root"]
    app = qml_context["app"]

    c1 = service.create_new_conversation("First Chat")
    service.send_message("First message", conversation_id=c1.id)

    bridge.loadInitialState()
    bridge._thread_pool.waitForDone(3000)
    app.processEvents()

    chat_model = root.findChild(QObject, "chatModel")
    assert chat_model.property("count") == 2

    # Trigger New Chat
    bridge.createNewConversation()
    bridge._thread_pool.waitForDone(3000)
    app.processEvents()

    assert chat_model.property("count") == 0
    assert root.property("activeConversationId") != c1.id


def test_qml_deletion_requires_confirmation_dialog(qml_context):
    """openDeleteConfirmation shows deleteDialog with title and pending ID."""
    root = qml_context["root"]
    delete_dialog = root.findChild(QObject, "deleteDialog")

    assert delete_dialog.property("visible") is False

    root.openDeleteConfirmation("conv_test_123", "Important Chat")
    assert delete_dialog.property("visible") is True
    assert root.property("pendingDeleteId") == "conv_test_123"
    assert root.property("pendingDeleteTitle") == "Important Chat"


def test_qml_error_state_displays_correctly(qml_context):
    """Error signal appends an error message to chatModel."""
    root = qml_context["root"]
    bridge = qml_context["bridge"]
    app = qml_context["app"]

    chat_model = root.findChild(QObject, "chatModel")
    initial_count = chat_model.property("count")

    bridge.errorOccurred.emit("Simulated connection timeout")
    app.processEvents()

    assert chat_model.property("count") == initial_count + 1
