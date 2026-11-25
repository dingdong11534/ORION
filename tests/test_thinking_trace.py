"""Tests for Optional Qwen3 Thinking Trace UI (provider, service, persistence, bridge, QML)."""

import gc
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from alembic.config import Config
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import (
    CancellationToken,
    GenerationCompleted,
    TextDelta,
    ThinkingDelta,
)
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.ollama_model_provider import (
    OllamaAPIError,
    OllamaCancelledError,
    OllamaModelProvider,
)
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.ui.qt_bridge import OrionBridge

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_stores(tmp_path: Path):
    db_file = tmp_path / "thinking.db"
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory), SqliteSettingsStore(factory), engine


class _FakeHTTPResp:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 1-2, 6-9, 11. Provider Contract & OllamaModelProvider Tests
# ---------------------------------------------------------------------------


def test_ollama_think_disabled_and_enabled_payloads():
    """Verify think=false and think=true are sent in /api/chat payload and channels stay separate."""
    captured_payloads: list[dict] = []

    def _fake_urlopen(req, timeout=30.0):
        captured_payloads.append(json.loads(req.data.decode("utf-8")))
        lines = [
            json.dumps({"message": {"thinking": "step 1 ", "content": ""}, "done": False}).encode()
            + b"\n",
            json.dumps(
                {"message": {"thinking": "step 2", "content": "Hello "}, "done": False}
            ).encode()
            + b"\n",
            json.dumps({"message": {"thinking": "", "content": "world"}, "done": True}).encode()
            + b"\n",
        ]
        return _FakeHTTPResp(lines)

    provider = OllamaModelProvider(model_name="qwen3:1.7b", think=False)
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        events_off = list(provider.stream_events("hi", think=False))
        events_on = list(provider.stream_events("hi", think=True))

    assert captured_payloads[0]["think"] is False
    assert not any(isinstance(e, ThinkingDelta) for e in events_off)
    assert "".join(e.text for e in events_off if isinstance(e, TextDelta)) == "Hello world"

    assert captured_payloads[1]["think"] is True
    assert captured_payloads[1]["options"]["num_predict"] >= 1024
    assert [e.text for e in events_on if isinstance(e, ThinkingDelta)] == ["step 1 ", "step 2"]
    assert [e.text for e in events_on if isinstance(e, TextDelta)] == ["Hello ", "world"]
    assert isinstance(events_on[-1], GenerationCompleted)


def test_ollama_empty_thinking_interleaved_and_error():
    """Handle empty thinking fields and mid-thinking errors cleanly."""

    def _fake_err(req, timeout=30.0):
        return _FakeHTTPResp(
            [
                json.dumps({"message": {"thinking": "hmm", "content": ""}, "done": False}).encode()
                + b"\n",
                json.dumps({"error": "model crashed mid-thinking"}).encode() + b"\n",
            ]
        )

    provider = OllamaModelProvider(model_name="qwen3:1.7b")
    with patch("urllib.request.urlopen", side_effect=_fake_err):
        gen = provider.stream_events("hi", think=True)
        first = next(gen)
        assert isinstance(first, ThinkingDelta)
        with pytest.raises(OllamaAPIError, match="model crashed"):
            next(gen)


def test_ollama_capability_detection_supported_and_unsupported():
    """/api/show distinguishes thinking-capable models from non-thinking models."""

    def _show_qwen3(req, timeout=10.0):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        resp.read.return_value = json.dumps(
            {"capabilities": ["completion", "tools", "thinking"]}
        ).encode()
        return resp

    def _show_qwen25(req, timeout=10.0):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        resp.read.return_value = json.dumps({"capabilities": ["completion", "tools"]}).encode()
        return resp

    p3 = OllamaModelProvider(model_name="qwen3:1.7b")
    with patch("urllib.request.urlopen", side_effect=_show_qwen3):
        assert p3.supports_thinking() is True

    p25 = OllamaModelProvider(model_name="qwen2.5:1.5b")
    with patch("urllib.request.urlopen", side_effect=_show_qwen25):
        assert p25.supports_thinking() is False


def test_cancellation_during_thinking():
    """CancellationToken triggered during thinking raises OllamaCancelledError cleanly."""
    provider = FakeModelProvider(
        fixed_response="Final answer",
        thinking_text="one two three four five",
        chunk_delay=0.05,
    )
    token = CancellationToken()
    gen = provider.stream_events("hi", cancellation_token=token, think=True)
    ev1 = next(gen)
    assert isinstance(ev1, ThinkingDelta)
    token.cancel()
    with pytest.raises(OllamaCancelledError):
        list(gen)


# ---------------------------------------------------------------------------
# 5, 10, 12, 13. AssistantService Persistence, Snapshot & Multi-Turn History
# ---------------------------------------------------------------------------


def test_thinking_never_leaks_into_persisted_content_and_history(tmp_path: Path):
    """Thinking trace must never enter MessageModel.content or multi-turn history."""
    conv_store, settings_store, db_engine = _make_stores(tmp_path)
    provider = FakeModelProvider(
        fixed_response="Answer 1",
        thinking_text="Secret reasoning step",
        thinking_supported=True,
    )
    svc = AssistantService(provider, conv_store, settings_store)

    # Default is OFF
    assert svc.get_show_thinking() is False
    svc.set_show_thinking(True)
    assert svc.get_show_thinking() is True

    thinking_seen: list[str] = []
    answer_seen: list[str] = []
    list(
        svc.stream_message(
            "Turn 1",
            on_thinking=thinking_seen.append,
            on_chunk=answer_seen.append,
        )
    )
    assert "".join(thinking_seen) == "Secret reasoning step"
    assert "".join(answer_seen) == "Answer 1"

    # Persisted message must NOT contain thinking text
    history = svc.get_active_conversation_history()
    asst_msg = [m for m in history if m.role == "assistant"][-1]
    assert asst_msg.content == "Answer 1"
    assert "Secret reasoning" not in (asst_msg.content or "")

    # Turn 2 with thinking disabled still preserves valid multi-turn history
    svc.set_show_thinking(False)
    provider.fixed_response = "Answer 2"
    t2_thinking: list[str] = []
    out2 = "".join(svc.stream_message("Turn 2", on_thinking=t2_thinking.append))
    assert t2_thinking == []
    assert out2 == "Answer 2"

    # Restart simulation: new AssistantService reads persisted setting=False
    svc2 = AssistantService(provider, conv_store, settings_store)
    assert svc2.get_show_thinking() is False
    db_engine.dispose()


def test_toggle_mid_request_does_not_alter_active_request(tmp_path: Path):
    """Changing show_thinking mid-request must not alter an already-running request."""
    conv_store, settings_store, db_engine = _make_stores(tmp_path)
    provider = FakeModelProvider(
        fixed_response="Done",
        thinking_text="t1 t2 t3",
        chunk_delay=0.03,
        thinking_supported=True,
    )
    svc = AssistantService(provider, conv_store, settings_store)
    svc.set_show_thinking(True)

    thinking_seen: list[str] = []

    def _on_think(t: str) -> None:
        thinking_seen.append(t)
        # Toggle OFF during active request
        svc.set_show_thinking(False)

    out = "".join(svc.stream_message("Hello", on_thinking=_on_think))
    assert out == "Done"
    assert "".join(thinking_seen) == "t1 t2 t3"
    db_engine.dispose()


# ---------------------------------------------------------------------------
# 3, 4, 11, 13-16. OrionBridge & QML Thinking Trace UI Tests
# ---------------------------------------------------------------------------


def test_qml_thinking_toggle_collapsible_display_and_separation(tmp_path: Path):
    """Verify QML Show thinking toggle, unsupported model state, collapse/expand, plain text, and separation."""
    conv_store, settings_store, db_engine = _make_stores(tmp_path)
    provider = FakeModelProvider(
        fixed_response="Clean final answer",
        thinking_text="Step A\nStep B",
        thinking_supported=True,
    )
    svc = AssistantService(provider, conv_store, settings_store)
    bridge = OrionBridge(assistant_service=svc)

    app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])
    gc.collect()
    app.processEvents()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("orion", bridge)
    qml_file = Path(__file__).parent.parent / "orion" / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file.resolve())))
    root = engine.rootObjects()[0]

    init_dl = time.monotonic() + 3.0
    while root.property("isInitializing") and time.monotonic() < init_dl:
        app.processEvents()
        time.sleep(0.01)

    toggle = root.findChild(QObject, "showThinkingToggle")
    assert toggle is not None
    assert toggle.property("enabled") is True
    assert toggle.property("checked") is False

    # Enable Show thinking via bridge slot -> persists & updates QML
    bridge.setShowThinking(True)
    app.processEvents()
    assert toggle.property("checked") is True
    assert settings_store.get_setting("show_thinking_trace") == "true"

    # Simulate user message via real QML composer -> root.sendMessage() -> streaming thinking + answer
    chat_model = root.findChild(QObject, "chatModel")
    composer = root.findChild(QObject, "composer")
    assert chat_model is not None
    assert composer is not None
    composer.setProperty("text", "Explain 2+2")
    root.sendMessage()
    deadline = time.monotonic() + 3.0
    while bridge.isBusy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    # Verify thinkingText and text are stored separately in chatModel and collapsible UI is present
    assert chat_model.property("count") >= 2
    asst_item = chat_model.get(chat_model.property("count") - 1).toVariant()
    assert asst_item.property("thinkingText") == "Step A\nStep B"
    assert asst_item.property("text") == "Clean final answer"
    assert asst_item.property("thinkingExpanded") is False
    assert asst_item.property("isThinkingActive") is False

    # Verify plain-text rendering & collapsible toggle in Main.qml
    qml_src = qml_file.read_text()
    assert 'objectName: "thinkingToggleHeader"' in qml_src
    assert 'objectName: "thinkingPlainText"' in qml_src
    assert "textFormat: Text.PlainText" in qml_src

    # Check bounding & truncation on direct thinkingChunk
    asst_item.setProperty("status", "pending")
    bridge.thinkingChunk.emit("Z" * 40000, "req-x")
    app.processEvents()
    asst_after = chat_model.get(chat_model.property("count") - 1).toVariant()
    assert len(asst_after.property("thinkingText")) == 32768
    assert asst_after.property("thinkingTruncated") is True

    # Check unsupported model disables toggle
    unsupported_provider = FakeModelProvider(thinking_supported=False)
    svc_unsup = AssistantService(unsupported_provider, conv_store, settings_store)
    bridge_unsup = OrionBridge(assistant_service=svc_unsup)
    assert bridge_unsup.thinkingSupported is False

    bridge.shutdown(timeout_ms=1000)
    bridge_unsup.shutdown(timeout_ms=1000)
    engine.deleteLater()
    app.processEvents()
    del engine
    gc.collect()
    db_engine.dispose()
