"""Part 10 Tests: Voice-First State Machine, Rolling Buffer, Intents, Visibility, and PipeWire."""

import os
import struct
import tempfile
import time
import wave
from pathlib import Path

import pytest
from alembic.config import Config
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.session import InteractionSession, SessionState
from orion.application.voice.contracts import VoiceSettings
from orion.application.voice.intent_router import VoiceIntentKind, VoiceIntentRouter
from orion.application.voice.interaction_machine import (
    InvalidVoiceTransitionError,
    MicIndicatorState,
    VoiceInteractionMachine,
    VoiceInteractionState,
)
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
from orion.infrastructure.voice.audio_io import PipeWireAudioInput, PipeWireAudioOutput
from orion.infrastructure.voice.piper_tts import PiperTtsAdapter
from orion.infrastructure.voice.wake_word import VoskWakeWordListener, pcm_to_wav_bytes
from orion.infrastructure.voice.whispercpp_stt import WhisperCppSttAdapter
from orion.main import create_model_provider
from orion.ui.qt_bridge import OrionBridge


def test_voice_interaction_state_machine_transitions_and_indicators() -> None:
    m = VoiceInteractionMachine(initial_state=VoiceInteractionState.ORB_STANDBY)
    assert m.mic_indicator_state() == MicIndicatorState.WAKE_LISTENING

    m.transition(VoiceInteractionState.WAKE_ACKNOWLEDGEMENT, reason="wake")
    m.transition(VoiceInteractionState.COMMAND_CAPTURE, reason="capture")
    assert m.mic_indicator_state() == MicIndicatorState.COMMAND_CAPTURE

    m.transition(VoiceInteractionState.COMMAND_PROCESSING, reason="stt")
    assert m.mic_indicator_state() == MicIndicatorState.PROCESSING

    m.transition(VoiceInteractionState.RESPONDING, reason="tts")
    assert m.mic_indicator_state() == MicIndicatorState.SPEAKING

    m.return_to_standby(reason="done")
    assert m.state == VoiceInteractionState.ORB_STANDBY
    assert m.mic_indicator_state() == MicIndicatorState.WAKE_LISTENING

    m.transition(VoiceInteractionState.MICROPHONE_PAUSED, reason="user_pause")
    assert m.mic_indicator_state() == MicIndicatorState.PAUSED
    assert "paused" in m.mic_status_description().lower()

    m.transition(VoiceInteractionState.MICROPHONE_ERROR, error_reason="PipeWire missing")
    assert m.mic_indicator_state() == MicIndicatorState.ERROR
    assert "PipeWire missing" in m.mic_status_description()

    with pytest.raises(InvalidVoiceTransitionError):
        m.transition(VoiceInteractionState.RESPONDING, reason="invalid_from_error")


def test_deterministic_voice_intent_router_and_deduplication() -> None:
    router = VoiceIntentRouter(dedup_window_seconds=2.0)

    assert router.classify("Hey ORION, open your home screen.").kind == VoiceIntentKind.OPEN_HOME
    assert router.classify("ORION, open chat").kind == VoiceIntentKind.OPEN_CHAT
    assert router.classify("Show my knowledge base, ORION").kind == VoiceIntentKind.OPEN_KNOWLEDGE
    assert router.classify("Open workspace").kind == VoiceIntentKind.OPEN_WORKSPACE
    assert router.classify("Show my tasks").kind == VoiceIntentKind.OPEN_PRODUCTIVITY
    assert router.classify("Go to the side").kind == VoiceIntentKind.MINIMIZE_TO_ORB
    assert router.classify("Open ORION").kind == VoiceIntentKind.SHOW_MAIN_WINDOW
    assert router.classify("Pause the microphone").kind == VoiceIntentKind.PAUSE_LISTENING
    assert router.classify("ORION").kind == VoiceIntentKind.AMBIGUOUS

    first = router.classify("ORION, explain binary search.")
    assert first.kind == VoiceIntentKind.CONVERSATIONAL
    assert not first.is_duplicate
    assert first.normalized_text == "explain binary search"

    dup = router.classify("ORION, explain binary search.")
    assert dup.is_duplicate is True


def test_word_boundary_rolling_buffer_tts_suppression_and_pause() -> None:
    listener = VoskWakeWordListener(
        phrase="hey orion",
        pre_roll_seconds=3.5,
        silence_timeout_seconds=0.5,
        max_command_seconds=3.0,
    )
    # Word-boundary matching
    assert listener.matches_phrase("Hey ORION") is True
    assert listener.matches_phrase("Can you explain this, ORION?") is True
    assert listener.matches_phrase("orionis") is False
    assert listener.matches_phrase("notorious") is False

    # Synthesized audio rolling-buffer test: pre-roll before 'Orion' is preserved
    tts = PiperTtsAdapter()
    stt = WhisperCppSttAdapter()
    if listener.is_available() and tts.is_model_available() and stt.is_model_available():
        captured: list[bytes] = []
        listener.set_callbacks(on_command_captured=lambda pcm: captured.append(pcm))
        rec = listener._ensure_recognizer()

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

        pre_speech = _synth("Open home screen")
        wake_speech = _synth("Orion")
        silence = b"\x00\x00" * 4000

        # Feed pre-speech in 4000-byte chunks into rolling buffer
        for i in range(0, len(pre_speech), 4000):
            listener.process_audio_chunk(rec, pre_speech[i : i + 4000], now=10.0)
        # Feed wake word
        for i in range(0, len(wake_speech), 4000):
            listener.process_audio_chunk(rec, wake_speech[i : i + 4000], now=11.0)
        # Feed silence past timeout -> triggers on_command_captured
        listener.process_audio_chunk(rec, silence, now=11.1)
        listener.process_audio_chunk(rec, silence, now=13.5)

        assert len(captured) == 1
        assert len(captured[0]) > len(wake_speech)

        # Verify Whisper transcribes the preserved pre-roll + wake utterance
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_p = Path(tf.name)
            tf.write(pcm_to_wav_bytes(captured[0]))
        try:
            text = stt.transcribe(wav_p).lower()
            assert "home" in text or "open" in text or "orion" in text
        finally:
            wav_p.unlink(missing_ok=True)
            stt.unload()

        # TTS echo suppression prevents wake detection while speaking
        listener.suppress_wake_for_playback(True)
        assert listener.process_audio_chunk(rec, wake_speech, now=20.0) is False
        listener.suppress_wake_for_playback(False)

        # User pause prevents silent reactivation after PTT
        listener.pause_by_user()
        assert listener.is_paused_by_user is True
        listener.resume_after_ptt()
        assert listener.is_running is False


def test_qml_persistent_mic_visibility_navigation_and_locked_session(tmp_path: Path) -> None:
    """Persistent mic indicator visible in both Orb & Main window; voice navigation works locked."""
    os.environ["ORION_MODEL_PROVIDER"] = "fake"
    db_path = tmp_path / "vf.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)

    session = InteractionSession(enabled=True)
    machine = VoiceInteractionMachine(initial_state=VoiceInteractionState.ORB_STANDBY)
    vs = VoiceService(
        stt=WhisperCppSttAdapter(),
        tts=PiperTtsAdapter(),
        audio_in=PipeWireAudioInput(),
        audio_out=PipeWireAudioOutput(),
        settings=VoiceSettings(enabled=True),
    )
    assistant = AssistantService(
        model_provider=create_model_provider(),
        conversation_store=SqliteConversationStore(sf),
        settings_store=SqliteSettingsStore(sf),
    )
    bridge = OrionBridge(
        assistant_service=assistant,
        session_factory=sf,
        interaction_session=session,
        voice_service=vs,
        voice_machine=machine,
    )

    app = QApplication.instance() or QApplication(["orion-vf-qml"])
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("orion", bridge)
    qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
    root = qml.rootObjects()[0]
    bridge._thread_pool.waitForDone(3000)
    app.processEvents()
    QTest.qWait(40)

    # Persistent mic bar in Main window
    mic_bar = root.findChild(QQuickItem, "persistentMicBar")
    mic_txt = root.findChild(QQuickItem, "headerMicStatusText")
    assert mic_bar is not None and mic_bar.isVisible()
    assert mic_txt is not None and "local wake-word" in mic_txt.property("text").lower()

    # Pause mic -> updates both Main window & Collapsed Orb badge
    bridge.pauseVoiceListening()
    bridge.setBackgroundMode(True)
    QTest.qWait(50)
    orb = root.findChild(QQuickWindow, "orionOrbWindow")
    assert orb is not None and orb.isVisible()
    badge = orb.findChild(QQuickItem, "orbCollapsedMicBadge")
    assert badge is not None and badge.property("text") == "PAUSED"

    # Voice navigation while session is LOCKED: open_productivity restores window & switches page
    assert session.state == SessionState.LOCKED
    bridge.voiceNavigationRequested.emit("open_productivity")
    QTest.qWait(50)
    assert root.isVisible()
    assert root.property("activePage") == "productivity"
    assert session.state == SessionState.LOCKED

    # Minimize back to orb
    bridge.voiceNavigationRequested.emit("minimize_to_orb")
    QTest.qWait(50)
    assert not root.isVisible()
    assert orb.isVisible()

    root.close()
    bridge.shutdown()
    app.processEvents()
    del qml
    del bridge
    engine.dispose()


def test_real_pipewire_continuous_listener_lifecycle() -> None:
    """Integration test: real pw-cat stream starts, runs continuously, and stops cleanly."""
    listener = VoskWakeWordListener(phrase="hey orion")
    if not listener.is_available():
        return
    listener.start()
    try:
        time.sleep(0.6)
        diag = listener.get_diagnostics()
        assert listener.is_running is True
        assert diag["capture_status"] == "listening"
        assert diag["thread_alive"] is True
        assert diag["sample_rate"] == 16000
        assert diag["channels"] == 1
    finally:
        listener.stop()
    assert listener.is_running is False
    assert listener.get_diagnostics()["capture_status"] == "stopped"
