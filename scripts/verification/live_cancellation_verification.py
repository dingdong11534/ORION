import os
import sys
import time
from pathlib import Path

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from alembic.config import Config
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.infrastructure.ollama_model_provider import OllamaModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.ui.qt_bridge import OrionBridge


def main():
    print("=== LIVE CANCELLATION VERIFICATION (Milestone 3.5) ===")

    # 1. Setup temporary database with migrations
    db_file = Path("/tmp/orion_live_cancellation_test.db")
    if db_file.exists():
        db_file.unlink()

    db_url = f"sqlite:///{db_file}"
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)

    # 2. Setup real Ollama provider with qwen3:1.7b and think=False
    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        model_name="qwen3:1.7b",
        think=False,
        num_ctx=2048,
        num_predict=512,
    )

    service = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)

    bridge = OrionBridge(assistant_service=service)

    qml_engine = QQmlApplicationEngine()
    qml_engine.rootContext().setContextProperty("orion", bridge)

    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    qml_engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))

    if not qml_engine.rootObjects():
        print("FAIL: Failed to load QML root object.")
        sys.exit(1)

    root_window = qml_engine.rootObjects()[0]
    chat_model = root_window.findChild(object, "chatModel")
    composer = root_window.findChild(object, "composer")
    stop_button = root_window.findChild(object, "stopButton")
    send_button = root_window.findChild(object, "sendButton")

    # Initial state
    bridge.loadInitialState()
    for _ in range(50):
        app.processEvents()
        time.sleep(0.01)

    # Tracking signals
    chunks = []
    interrupted_events = []
    ready_events = []
    errors = []

    bridge.responseChunk.connect(lambda c, tid: chunks.append((c, tid)))
    bridge.responseInterrupted.connect(lambda p, tid: interrupted_events.append((p, tid)))
    bridge.responseReady.connect(lambda t: ready_events.append(t))
    bridge.errorOccurred.connect(lambda e: errors.append(e))

    print("\n--- STEP 1: Submit long prompt to trigger streaming generation ---")
    prompt = "Write a comprehensive and long historical account of the Roman Republic."
    composer.setProperty("text", prompt)
    root_window.sendMessage()

    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)

    print(f"Busy state immediately after sending: {root_window.property('busy')}")
    print(f"Stop button visible: {stop_button.property('visible')}")
    print(f"Send button visible: {send_button.property('visible')}")
    assert root_window.property("busy") is True, "Root should be busy"
    assert stop_button.property("visible") is True, "Stop button should be visible"

    print("\n--- STEP 2: Wait for first few streaming chunks from Qwen3:1.7B ---")
    start_time = time.time()
    while len(chunks) < 5 and time.time() - start_time < 15.0:
        app.processEvents()
        time.sleep(0.02)

    print(f"Chunks received so far: {len(chunks)}")
    partial_so_far = "".join([c[0] for c in chunks])
    print(f"Partial text so far: {partial_so_far!r}")
    assert len(chunks) >= 5, "Expected at least 5 chunks before stopping"

    print("\n--- STEP 3: Trigger Stop Generation via stopButton ---")
    stop_click_time = time.time()
    stop_button.clicked.emit()

    # Wait for completion of interruption
    while bridge.isBusy and time.time() - stop_click_time < 5.0:
        app.processEvents()
        time.sleep(0.02)

    interruption_latency = time.time() - stop_click_time
    print(f"Interruption completed in {interruption_latency:.3f} seconds!")
    assert interruption_latency < 2.0, "Interruption should complete in under 2 seconds"

    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)

    print("\n--- STEP 4: Verify UI State after Interruption ---")
    print(f"Root busy state: {root_window.property('busy')}")
    print(f"Stop button visible: {stop_button.property('visible')}")
    print(f"Send button visible: {send_button.property('visible')}")
    assert root_window.property("busy") is False, "Root should no longer be busy"
    assert stop_button.property("visible") is False, "Stop button should be hidden"
    assert send_button.property("visible") is True, "Send button should be visible again"

    print(f"Interrupted events received: {len(interrupted_events)}")
    print(f"Ready events received: {len(ready_events)}")
    print(f"Error events received: {len(errors)}")
    assert len(interrupted_events) == 1, "Exactly one interrupted event expected"
    assert len(ready_events) == 0, "No ready event should be emitted on interruption"
    assert len(errors) == 0, "No error event should be emitted for user cancellation"

    partial_text_interrupted, interrupted_req_id = interrupted_events[0]
    print(f"Interrupted request ID: {interrupted_req_id}")
    print(f"Persisted partial content: {partial_text_interrupted!r}")

    # Verify QML chat model
    qml_count = chat_model.property("count")
    print(f"QML chat model message count: {qml_count}")
    assert qml_count == 2, f"Expected 2 messages (user + assistant), got {qml_count}"

    asst_item = chat_model.get(1)
    asst_status = asst_item.property("status").toString()
    asst_text = asst_item.property("text").toString()
    asst_is_error = asst_item.property("isError").toBool()
    print(f"QML assistant status: {asst_status}")
    print(f"QML assistant isError: {asst_is_error}")
    print(f"QML assistant text: {asst_text!r}")
    assert asst_status == "interrupted", "QML message status must be 'interrupted'"
    assert asst_is_error is True, "QML message isError must be True for interrupted styling"
    assert asst_text == partial_text_interrupted

    print("\n--- STEP 5: Verify SQLite Persistence Record ---")
    cid = service.active_conversation_id
    record = conv_store.get_request(cid, interrupted_req_id)
    assert record is not None
    print(f"DB User message status: {record.user_message.status}")
    print(f"DB Assistant message status: {record.assistant_message.status}")
    print(f"DB Assistant content: {record.assistant_message.content!r}")
    print(f"DB Assistant error_message: {record.assistant_message.error_message!r}")
    assert record.user_message.status.value == "completed"
    assert record.assistant_message.status.value == "interrupted"
    assert record.assistant_message.content == partial_text_interrupted
    assert record.assistant_message.error_message == "Generation cancelled by user."

    print("\n--- STEP 6: Verify follow-up message can be sent and completes normally ---")
    composer.setProperty("text", "What is 2 + 2?")
    root_window.sendMessage()

    send2_start = time.time()
    while bridge.isBusy and time.time() - send2_start < 15.0:
        app.processEvents()
        time.sleep(0.02)

    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)

    print(f"Follow-up completed. Ready events: {len(ready_events)}")
    assert len(ready_events) == 1, "Follow up response should complete normally"
    print(f"Follow-up answer: {ready_events[0]!r}")

    qml_count = chat_model.property("count")
    print(f"QML chat model count after follow up: {qml_count}")
    assert qml_count == 4, "Expected 4 messages (2 user + 2 assistant)"
    turn2_asst = chat_model.get(3)
    assert turn2_asst.property("status").toString() == "completed"

    print("\n=== ALL LIVE VERIFICATION CHECKS PASSED SUCCESSFULLY! ===")


if __name__ == "__main__":
    main()
