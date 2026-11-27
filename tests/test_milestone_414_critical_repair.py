"""Critical Repair Integration Tests: Manual Unlock, Independent Orb, Voice, Wake Word."""

import math
import os
import struct
import wave
from pathlib import Path

from alembic.config import Config
from PySide6.QtCore import QPoint, Qt, QUrl
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
from orion.infrastructure.voice.audio_io import (
    PipeWireAudioInput,
    PipeWireAudioOutput,
    measure_wav_stats,
)
from orion.infrastructure.voice.piper_tts import PiperTtsAdapter
from orion.infrastructure.voice.wake_word import LocalSpeakerVerifier, VoskWakeWordListener
from orion.infrastructure.voice.whispercpp_stt import WhisperCppSttAdapter
from orion.main import create_model_provider
from orion.ui.qt_bridge import OrionBridge


def _make_bridge(tmp_path: Path, *, voice_service: VoiceService | None = None):
    os.environ["ORION_MODEL_PROVIDER"] = "fake"
    db_path = tmp_path / "repair.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    session = InteractionSession(enabled=True)
    assistant = AssistantService(
        model_provider=create_model_provider(),
        conversation_store=SqliteConversationStore(sf),
        settings_store=SqliteSettingsStore(sf),
    )
    bridge = OrionBridge(
        assistant_service=assistant,
        session_factory=sf,
        interaction_session=session,
        voice_service=voice_service,
    )
    return engine, session, bridge


def test_manual_unlock_and_lock_via_real_qml_button_click(tmp_path: Path) -> None:
    """Clicking sessionLockButton in QML transitions LOCKED -> UNLOCKED -> LOCKED."""
    app = QApplication.instance() or QApplication(["orion-repair-unlock"])
    engine, session, bridge = _make_bridge(tmp_path)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("orion", bridge)
    qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
    root = qml.rootObjects()[0]
    app.processEvents()

    btn = root.findChild(QQuickItem, "sessionLockButton")
    assert btn is not None
    assert btn.isVisible()
    assert session.state == SessionState.LOCKED
    assert btn.property("text") == "Unlock"

    pt = btn.mapToScene(QPoint(int(btn.width() / 2), int(btn.height() / 2))).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, pt)
    app.processEvents()
    assert session.state == SessionState.UNLOCKED
    assert bridge.sessionState == "unlocked"
    assert btn.property("text") == "Lock"

    # Click again to lock
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, pt)
    app.processEvents()
    assert session.state == SessionState.LOCKED
    assert btn.property("text") == "Unlock"

    # Compact rail mode (< 1000px) still keeps sessionLockButton visible and clickable
    root.setWidth(900)
    app.processEvents()
    assert btn.isVisible()
    pt_compact = btn.mapToScene(QPoint(int(btn.width() / 2), int(btn.height() / 2))).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, pt_compact)
    app.processEvents()
    assert session.state == SessionState.UNLOCKED

    bridge.shutdown()
    del qml
    del bridge
    engine.dispose()


def test_floating_orb_independent_lifecycle_expand_hide_and_restore(tmp_path: Path) -> None:
    """Orb stays visible with transientParent=None when main window hides; expand/hide/restore work."""
    app = QApplication.instance() or QApplication(["orion-repair-orb"])
    engine, session, bridge = _make_bridge(tmp_path)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("orion", bridge)
    qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
    root = qml.rootObjects()[0]
    app.processEvents()

    orb = root.findChild(QQuickWindow, "orionOrbWindow")
    assert orb is not None
    assert orb.transientParent() is None
    assert root.isVisible()
    assert not orb.isVisible()

    # Click Orb button -> main window hides, independent Orb becomes visible
    hide_btn = root.findChild(QQuickItem, "hideToBackgroundButton")
    assert hide_btn is not None
    pt = hide_btn.mapToScene(
        QPoint(int(hide_btn.width() / 2), int(hide_btn.height() / 2))
    ).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, pt)
    app.processEvents()

    assert bridge.backgroundMode is True
    assert not root.isVisible()
    assert orb.isVisible()
    assert orb.transientParent() is None

    # Left-click Orb -> expands compact assistant panel
    orb_mouse = orb.findChild(QQuickItem, "orbMouseArea")
    assert orb_mouse is not None
    QTest.mouseClick(orb, Qt.LeftButton, Qt.NoModifier, QPoint(36, 36))
    app.processEvents()
    assert root.property("orbExpanded") is True

    # Click orbSessionLockButton inside expanded panel -> unlocks session
    orb_lock_btn = orb.findChild(QQuickItem, "orbSessionLockButton")
    assert orb_lock_btn is not None and orb_lock_btn.isVisible()
    pt_lock = orb_lock_btn.mapToScene(
        QPoint(int(orb_lock_btn.width() / 2), int(orb_lock_btn.height() / 2))
    ).toPoint()
    QTest.mouseClick(orb, Qt.LeftButton, Qt.NoModifier, pt_lock)
    app.processEvents()
    assert session.state == SessionState.UNLOCKED

    # Click orbHideButton -> hides Orb, then restore orbHidden = false brings it back
    orb_hide = orb.findChild(QQuickItem, "orbHideButton")
    assert orb_hide is not None
    pt_hide = orb_hide.mapToScene(
        QPoint(int(orb_hide.width() / 2), int(orb_hide.height() / 2))
    ).toPoint()
    QTest.mouseClick(orb, Qt.LeftButton, Qt.NoModifier, pt_hide)
    app.processEvents()
    assert not orb.isVisible()

    root.setProperty("orbHidden", False)
    app.processEvents()
    assert orb.isVisible()

    # Expand and click orbRestoreWindowButton -> restores main window
    QTest.mouseClick(orb, Qt.LeftButton, Qt.NoModifier, QPoint(36, 36))
    app.processEvents()
    restore_btn = orb.findChild(QQuickItem, "orbRestoreWindowButton")
    assert restore_btn is not None
    pt_res = restore_btn.mapToScene(
        QPoint(int(restore_btn.width() / 2), int(restore_btn.height() / 2))
    ).toPoint()
    QTest.mouseClick(orb, Qt.LeftButton, Qt.NoModifier, pt_res)
    app.processEvents()
    assert root.isVisible()
    assert not orb.isVisible()

    bridge.shutdown()
    del qml
    del bridge
    engine.dispose()


def test_real_pipewire_capture_whisper_transcription_cancel_and_error(tmp_path: Path) -> None:
    """Real pw-record capture + Piper->16kHz Whisper STT + QML composer delivery + cancel."""
    audio_in = PipeWireAudioInput()
    stt = WhisperCppSttAdapter()
    tts = PiperTtsAdapter()

    # 1. Real PipeWire capture if mic available
    if audio_in.is_available():
        wav_out = tmp_path / "mic_real.wav"
        audio_in.start_recording(wav_out)
        import time

        time.sleep(0.4)
        recorded = audio_in.stop_recording()
        stats = measure_wav_stats(recorded)
        assert stats.sample_rate == 16000
        assert stats.channels == 1
        assert stats.sample_width == 2
        assert stats.duration_seconds > 0.1
        assert stats.peak > 0

        # Cancel recording cleans up subprocess and WAV file
        wav_cancel = tmp_path / "mic_cancel.wav"
        audio_in.start_recording(wav_cancel)
        time.sleep(0.1)
        audio_in.cancel_recording()
        assert not wav_cancel.exists()

    # 2. Real WhisperCppSttAdapter on synthesized 16kHz speech (via ffmpeg/sox or linear resample)
    if stt.is_model_available() and tts.is_model_available():
        raw_22k = tmp_path / "piper_22k.wav"
        wav_16k = tmp_path / "piper_16k.wav"
        tts.synthesize_to_wav("Hey Orion, check my tasks.", raw_22k)
        with wave.open(str(raw_22k), "rb") as src:
            r_in = src.getframerate()
            frames = src.readframes(src.getnframes())
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        # Linear resample to 16000 Hz mono s16le
        n_out = int(len(samples) * 16000 / r_in)
        resampled = []
        for i in range(n_out):
            pos = i * (r_in / 16000.0)
            idx = min(int(pos), len(samples) - 1)
            resampled.append(samples[idx])
        with wave.open(str(wav_16k), "wb") as dst:
            dst.setnchannels(1)
            dst.setsampwidth(2)
            dst.setframerate(16000)
            dst.writeframes(struct.pack(f"<{len(resampled)}h", *resampled))

        text = stt.transcribe(wav_16k)
        assert "orion" in text.lower() or "task" in text.lower()
        stt.unload()

    # 3. Verify voiceTranscriptReady and voiceError propagate to QML composer & banner
    app = QApplication.instance() or QApplication(["orion-repair-voice-qml"])
    vs = VoiceService(
        stt=stt,
        tts=tts,
        audio_in=audio_in,
        audio_out=PipeWireAudioOutput(),
        settings=VoiceSettings(enabled=True),
    )
    engine, _session, bridge = _make_bridge(tmp_path, voice_service=vs)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("orion", bridge)
    qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
    root = qml.rootObjects()[0]
    app.processEvents()

    bridge.voiceTranscriptReady.emit("hello from voice")
    app.processEvents()
    composer = root.findChild(QQuickItem, "composer")
    assert composer is not None
    assert "hello from voice" in composer.property("text")

    bridge.voiceError.emit("device_error", "Microphone capture failed")
    app.processEvents()
    assert root.property("voiceErrorMessage") == "Microphone capture failed"

    bridge.shutdown()
    del qml
    del bridge
    engine.dispose()


def test_wake_word_and_local_speaker_verifier_lifecycle(tmp_path: Path) -> None:
    """Wake-word starts while locked, detects 'hey orion', never unlocks unverified."""
    listener = VoskWakeWordListener(phrase="hey orion")
    assert listener.matches_phrase("hey orion") is True
    assert listener.matches_phrase("hello world") is False

    # Unenrolled verifier must never unlock
    verifier = LocalSpeakerVerifier(voiceprint_path=tmp_path / "owner_voiceprint.json")
    assert verifier.is_enrolled() is False

    class _FakeWake:
        def __init__(self) -> None:
            self.running = False

        def is_available(self) -> bool:
            return True

        def start(self) -> None:
            self.running = True

        def stop(self) -> None:
            self.running = False

    fake_wake = _FakeWake()
    session = InteractionSession(enabled=True, wake_word=fake_wake, speaker_verifier=verifier)
    assert session.state == SessionState.LOCKED
    assert fake_wake.running is True

    # Wake phrase detected, but unenrolled verifier keeps session LOCKED
    assert session.on_wake_detected() == SessionState.WAKE_DETECTED
    assert session.verify_and_unlock(b"\x10\x20" * 2000) is False
    assert session.state == SessionState.LOCKED

    # Explicit local enrollment
    owner_pcm = struct.pack(
        "<" + "h" * 8000,
        *[int(2000 * math.sin(2 * math.pi * 220 * i / 16000)) for i in range(8000)],
    )
    verifier.enroll_from_pcm(owner_pcm)
    assert verifier.is_enrolled() is True

    # Silent/mismatched sample fails verification
    assert session.on_wake_detected() == SessionState.WAKE_DETECTED
    assert session.verify_and_unlock(b"\x00\x00" * 4000) is False
    assert session.state == SessionState.LOCKED

    # Matching owner sample unlocks and stops wake listener
    assert session.on_wake_detected() == SessionState.WAKE_DETECTED
    assert session.verify_and_unlock(owner_pcm) is True
    assert session.state == SessionState.UNLOCKED
    assert fake_wake.running is False

    # Manual lock restarts wake listener
    session.lock()
    assert session.state == SessionState.LOCKED
    assert fake_wake.running is True
    session.shutdown()
    assert fake_wake.running is False
