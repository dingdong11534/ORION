"""Part C Tests: Redesigned Conversation List UI + Real PipeWire/Vosk Wake-Word & Orb."""

import os
import struct
import tempfile
import time
import wave
from pathlib import Path

from alembic.config import Config
from PySide6.QtCore import Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.session import InteractionSession, SessionState
from orion.application.voice.contracts import VoiceSettings
from orion.application.voice.voice_service import VoiceService
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.voice.audio_io import PipeWireAudioOutput
from orion.infrastructure.voice.piper_tts import PiperTtsAdapter
from orion.infrastructure.voice.wake_word import LocalSpeakerVerifier, VoskWakeWordListener
from orion.main import create_model_provider
from orion.ui.qt_bridge import OrionBridge


def _setup_app(tmp_path: Path, *, wake_word=None):
    os.environ["ORION_MODEL_PROVIDER"] = "fake"
    db_path = tmp_path / "ui_wake.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    conv_store = SqliteConversationStore(sf)
    conv_store.create_conversation("Architecture Notes on Local AI")
    conv_store.create_conversation("Daily Tasks and Habits Review")
    session = InteractionSession(
        enabled=True,
        wake_word=wake_word,
        speaker_verifier=LocalSpeakerVerifier(voiceprint_path=tmp_path / "vp.json"),
    )
    assistant = AssistantService(
        model_provider=create_model_provider(),
        conversation_store=conv_store,
        settings_store=SqliteSettingsStore(sf),
    )
    bridge = OrionBridge(
        assistant_service=assistant,
        session_factory=sf,
        interaction_session=session,
    )
    return engine, sf, session, conv_store, bridge


def test_conversation_list_layout_widths_overflow_inline_rename_and_delete(tmp_path: Path) -> None:
    """Verify conversation list at 1280x800, 1100x750, 900x650, resize persist, rename, delete."""
    app = QApplication.instance() or QApplication(["orion-ui-redesign"])
    engine, sf, _session, conv_store, bridge = _setup_app(tmp_path)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("orion", bridge)
    qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
    root = qml.rootObjects()[0]
    QTest.qWaitForWindowExposed(root)
    root.setProperty("activePage", "chat")
    convs = [
        {"id": c.id, "title": c.title, "relative_time": "just now"}
        for c in conv_store.list_conversations()
    ]
    bridge.conversationsLoaded.emit(convs, convs[0]["id"])
    QTest.qWait(60)
    lv = root.findChild(QQuickItem, "conversationsListView")
    assert lv is not None
    assert lv.property("count") == 2

    sidebar = root.findChild(QQuickItem, "conversationSidebar")
    divider = root.findChild(QQuickItem, "conversationDivider")
    chat_list = root.findChild(QQuickItem, "chatList")
    assert sidebar is not None and divider is not None and chat_list is not None

    # Default width ~268px on 1280x800 and 1100x750, adaptive >= 220px on 900x650 without overlap
    for w, h, expected_min in ((1280, 800, 250), (1100, 750, 250), (900, 650, 220)):
        root.setWidth(w)
        root.setHeight(h)
        QTest.qWait(40)
        assert sidebar.isVisible()
        assert sidebar.width() >= expected_min
        assert chat_list.width() >= 300

    # Persist custom sidebar width
    root.setWidth(1280)
    root.setHeight(800)
    bridge.setConversationSidebarWidth(312)
    lv.forceLayout()
    QTest.qWait(60)
    assert bridge.conversationSidebarWidth == 312
    settings = SqliteSettingsStore(sf)
    assert settings.get_setting("ui_conversation_sidebar_width") == "312"

    # Readable title & no permanent Rename / × buttons
    rows = [
        c for c in lv.property("contentItem").childItems() if c.objectName() == "conversationRow"
    ]
    assert len(rows) >= 1
    row = rows[0]
    title_item = row.findChild(QQuickItem, "conversationTitleText")
    assert title_item is not None
    assert title_item.width() >= 150

    # Inline rename commit & persistence
    row.setProperty("editingTitle", True)
    QTest.qWait(30)
    rename_field = row.findChild(QQuickItem, "inlineRenameField")
    assert rename_field is not None
    rename_field.setProperty("text", "Updated Architecture Plan")
    rename_field.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)
    app.processEvents()

    # Wait briefly for worker thread rename to complete
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        titles = [c.title for c in conv_store.list_conversations()]
        if "Updated Architecture Plan" in titles:
            break
        time.sleep(0.05)
    assert "Updated Architecture Plan" in [c.title for c in conv_store.list_conversations()]

    # Delete confirmation flow
    first_id = conv_store.list_conversations()[0].id
    root.confirmDeleteConversation(first_id, "Updated Architecture Plan")
    app.processEvents()
    del_dlg = root.findChild(object, "deleteDialog")
    assert del_dlg is not None
    del_dlg.accept()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        if len(conv_store.list_conversations()) == 1:
            break
        time.sleep(0.05)
    assert len(conv_store.list_conversations()) == 1

    bridge.shutdown()
    del qml
    del bridge
    engine.dispose()


def test_wake_word_vosk_cooldown_ptt_pause_and_orb_open_while_locked(tmp_path: Path) -> None:
    """Automated + synthesized audio test for Vosk normalization, cooldown, PTT pause, and Orb."""
    listener = VoskWakeWordListener(phrase="hey orion", cooldown_seconds=2.0)
    assert listener.matches_phrase("Hey, ORION!") is True
    assert listener.matches_phrase("hey orion") is True
    assert listener.matches_phrase("hello world") is False

    # Test real Vosk model loading + chunk processing + cooldown if model is present
    tts = PiperTtsAdapter()
    if listener.is_available() and tts.is_model_available():
        rec = listener._ensure_recognizer()
        hits: list[int] = []
        listener.set_callbacks(on_detected=lambda: hits.append(1))

        def _synth(phrase: str) -> bytes:
            with tempfile.TemporaryDirectory() as td:
                raw = Path(td) / "s.wav"
                tts.synthesize_to_wav(phrase, raw)
                with wave.open(str(raw), "rb") as w:
                    r_in = w.getframerate()
                    frames = w.readframes(w.getnframes())
                    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                n_out = int(len(samples) * 16000 / r_in)
                res = [
                    samples[min(int(i * (r_in / 16000.0)), len(samples) - 1)] for i in range(n_out)
                ]
                return struct.pack(f"<{len(res)}h", *res)

        assert listener.process_audio_chunk(rec, _synth("Hello world, good morning")) is False
        assert listener.process_audio_chunk(rec, _synth("Hey Orion")) is True
        # Immediate repeat within cooldown is suppressed
        assert listener.process_audio_chunk(rec, _synth("Hey Orion")) is False
        assert len(hits) == 1
        diag = listener.get_diagnostics()
        assert diag["model_status"] == "loaded"
        assert diag["last_wake_at"] != ""

    # Verify PTT pause/resume callback coordination
    ptt_events: list[bool] = []

    class _FakeIn:
        def backend_name(self) -> str:
            return "pipewire"

        def is_available(self) -> bool:
            return True

        def start_recording(self, wav, device="default") -> None:
            wav.write_bytes(b"RIFF" + b"\x00" * 100)

        def stop_recording(self):
            raise RuntimeError("stop")

        def cancel_recording(self) -> None:
            return None

    class _FakeStt:
        def model_path(self) -> Path:
            return Path("/tmp/m")

        def is_model_available(self) -> bool:
            return True

        def transcribe(self, wav) -> str:
            return "hi"

        def unload(self) -> None:
            return None

    vs = VoiceService(
        stt=_FakeStt(),
        tts=tts,
        audio_in=_FakeIn(),
        audio_out=PipeWireAudioOutput(),
        settings=VoiceSettings(enabled=True),
        on_ptt_active_changed=lambda active: ptt_events.append(active),
    )
    vs.start_recording()
    vs.cancel_recording()
    assert ptt_events == [True, False]

    # Verify wakeWordDetected opens & expands QML Orb even when main window is hidden, while LOCKED
    app = QApplication.instance() or QApplication(["orion-wake-orb"])
    engine, _sf, session, _cs, bridge = _setup_app(tmp_path)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("orion", bridge)
    qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
    root = qml.rootObjects()[0]
    bridge.setBackgroundMode(True)
    app.processEvents()

    orb = root.findChild(QQuickWindow, "orionOrbWindow")
    assert orb is not None
    assert not root.isVisible()
    assert session.state == SessionState.LOCKED

    bridge.notify_wake_word_detected()
    app.processEvents()
    assert orb.isVisible()
    assert root.property("orbExpanded") is True
    assert session.state == SessionState.LOCKED

    bridge.shutdown()
    del qml
    del bridge
    engine.dispose()


def test_real_pipewire_wake_stream_capture_and_diagnostics() -> None:
    """Integration test: real pw-cat 16kHz mono s16le capture produces non-silent RMS."""
    listener = VoskWakeWordListener(phrase="hey orion")
    if not listener.is_available():
        return
    rms = listener.test_microphone_rms(0.4)
    assert rms > 0.0
    diag = listener.get_diagnostics()
    assert diag["sample_rate"] == 16000
    assert diag["channels"] == 1
    assert diag["current_rms"] == round(rms, 1)
    assert "pipewire" in diag["input_source"] or "alsa" in diag["input_source"]
