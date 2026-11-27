"""Phase E tests: offline voice service, text cleaner, HeavyWorkGate, errors, and zero network."""

import socket
import wave
from pathlib import Path

import pytest

from orion.application.voice.contracts import VoiceErrorCode, VoiceSettings, VoiceState
from orion.application.voice.heavy_work_gate import HeavyWorkGate
from orion.application.voice.text_cleaner import clean_text_for_speech, split_sentences_for_tts
from orion.application.voice.voice_service import VoiceService, VoiceServiceError
from orion.infrastructure.voice.piper_tts import PiperTtsAdapter
from orion.infrastructure.voice.whispercpp_stt import WhisperCppSttAdapter


class _FakeAudioIn:
    def __init__(self, *, available: bool = True, wav_bytes: bytes = b"RIFF") -> None:
        self._available = available
        self._wav_bytes = wav_bytes
        self._out: Path | None = None
        self.cancelled = False

    def is_available(self) -> bool:
        return self._available

    def backend_name(self) -> str:
        return "pipewire"

    def start_recording(self, output_wav: Path, device: str = "default") -> None:
        self._out = output_wav
        output_wav.write_bytes(self._wav_bytes)

    def stop_recording(self) -> Path:
        assert self._out is not None
        return self._out

    def cancel_recording(self) -> None:
        self.cancelled = True
        if self._out is not None:
            self._out.unlink(missing_ok=True)


class _FakeAudioOut:
    def __init__(self) -> None:
        self.played: list[Path] = []
        self.stopped = False

    def play_wav(self, wav_path: Path) -> None:
        self.played.append(wav_path)

    def stop_playback(self) -> None:
        self.stopped = True

    def is_playing(self) -> bool:
        return False


class _FakeStt:
    def __init__(self, *, available: bool = True, text: str = "hello world") -> None:
        self._available = available
        self._text = text
        self.unloaded = False

    def is_model_available(self) -> bool:
        return self._available

    def model_path(self) -> Path:
        return Path("/tmp/fake-stt.bin")

    def transcribe(self, wav_path: Path) -> str:
        return self._text

    def unload(self) -> None:
        self.unloaded = True


class _FakeTts:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.synthesized: list[str] = []
        self.unloaded = False

    def is_model_available(self) -> bool:
        return self._available

    def model_path(self) -> Path:
        return Path("/tmp/fake-tts.onnx")

    def synthesize_to_wav(self, text: str, output_wav: Path) -> Path:
        self.synthesized.append(text)
        output_wav.write_bytes(b"RIFF")
        return output_wav

    def unload(self) -> None:
        self.unloaded = True


def test_clean_text_for_speech_and_sentence_splitting():
    raw = "See **DBMS** [S1] at https://example.com and [docs](https://a.b). Next sentence! `code`."
    cleaned = clean_text_for_speech(raw)
    assert "https://" not in cleaned
    assert "[S1]" not in cleaned
    assert "**" not in cleaned
    assert "docs" in cleaned
    sents = split_sentences_for_tts(raw)
    assert len(sents) >= 2


def test_voice_service_error_categories():
    # Disabled
    svc_dis = VoiceService(
        stt=_FakeStt(),
        tts=_FakeTts(),
        audio_in=_FakeAudioIn(),
        audio_out=_FakeAudioOut(),
        settings=VoiceSettings(enabled=False),
    )
    with pytest.raises(VoiceServiceError) as exc1:
        svc_dis.start_recording()
    assert exc1.value.code == VoiceErrorCode.VOICE_DISABLED

    # Model missing
    svc_miss = VoiceService(
        stt=_FakeStt(available=False),
        tts=_FakeTts(available=False),
        audio_in=_FakeAudioIn(),
        audio_out=_FakeAudioOut(),
    )
    with pytest.raises(VoiceServiceError) as exc2:
        svc_miss.start_recording()
    assert exc2.value.code == VoiceErrorCode.MODEL_MISSING
    with pytest.raises(VoiceServiceError) as exc3:
        svc_miss.speak_text("Hello.")
    assert exc3.value.code == VoiceErrorCode.MODEL_MISSING

    # Mic unavailable
    svc_mic = VoiceService(
        stt=_FakeStt(),
        tts=_FakeTts(),
        audio_in=_FakeAudioIn(available=False),
        audio_out=_FakeAudioOut(),
    )
    with pytest.raises(VoiceServiceError) as exc4:
        svc_mic.start_recording()
    assert exc4.value.code == VoiceErrorCode.MICROPHONE_UNAVAILABLE

    # Empty transcription
    svc_empty = VoiceService(
        stt=_FakeStt(text="   "),
        tts=_FakeTts(),
        audio_in=_FakeAudioIn(),
        audio_out=_FakeAudioOut(),
    )
    svc_empty.start_recording()
    with pytest.raises(VoiceServiceError) as exc5:
        svc_empty.stop_recording_and_transcribe()
    assert exc5.value.code == VoiceErrorCode.EMPTY_TRANSCRIPTION


def test_voice_service_happy_path_and_idle_unload():
    stt = _FakeStt(text="Make my DBMS task high priority.")
    tts = _FakeTts()
    ain = _FakeAudioIn()
    aout = _FakeAudioOut()
    svc = VoiceService(stt=stt, tts=tts, audio_in=ain, audio_out=aout)

    svc.start_recording()
    assert svc.state == VoiceState.RECORDING
    res = svc.stop_recording_and_transcribe()
    assert res.text == "Make my DBMS task high priority."
    assert svc.state == VoiceState.IDLE

    svc.speak_text("First sentence. Second sentence.")
    assert tts.synthesized == ["First sentence.", "Second sentence."]
    assert len(aout.played) == 2

    svc.unload_idle_models()
    assert stt.unloaded and tts.unloaded


def test_heavy_work_gate_tracks_active_kind():
    import threading

    gate = HeavyWorkGate()
    assert gate.active_kind is None

    # 1 & 2 & 3. Reading active_kind inside active context, transition, and clear on success
    with gate.acquire("stt"):
        assert gate.active_kind == "stt"
    assert gate.active_kind is None

    # 4 & 7. Clearing state and no lock leakage after exception
    with pytest.raises(RuntimeError, match="boom"), gate.acquire("tts"):
        assert gate.active_kind == "tts"
        raise RuntimeError("boom")
    assert gate.active_kind is None
    with gate.acquire("stt", timeout=0.2):
        assert gate.active_kind == "stt"
    assert gate.active_kind is None

    # 5 & 8. Mutual exclusion between competing operations + bounded waiting
    entered = threading.Event()
    release = threading.Event()
    results: list[str] = []

    def _holder() -> None:
        with gate.acquire("llm"):
            entered.set()
            release.wait(timeout=2.0)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert entered.wait(timeout=1.0)
    assert gate.active_kind == "llm"

    with pytest.raises(TimeoutError), gate.acquire("stt", timeout=0.05):
        results.append("should_not_enter")

    release.set()
    t.join(timeout=1.0)
    assert gate.active_kind is None
    assert results == []

    # 6. Correct release after cancellation / KeyboardInterrupt-like BaseException
    class _Cancelled(BaseException):
        pass

    with pytest.raises(_Cancelled), gate.acquire("stt"):
        assert gate.active_kind == "stt"
        raise _Cancelled()
    assert gate.active_kind is None
    with gate.acquire("tts", timeout=0.2):
        assert gate.active_kind == "tts"
    assert gate.active_kind is None


def test_offline_stt_and_tts_make_zero_network_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    stt = WhisperCppSttAdapter()
    tts = PiperTtsAdapter()
    if not stt.is_model_available() or not tts.is_model_available():
        pytest.skip("Local voice models not downloaded on this test runner")

    def _forbid_network(*args, **kwargs):
        raise AssertionError("Non-loopback network access attempted during offline voice!")

    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", _forbid_network)

    wav_out = tmp_path / "tts.wav"
    tts.synthesize_to_wav("Offline test sentence.", wav_out)
    tts.unload()
    assert wav_out.is_file() and wav_out.stat().st_size > 100

    # Resample to 16kHz and transcribe with zero network
    import numpy as np

    with wave.open(str(wav_out), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
    n_out = int(len(pcm) * 16000 / sr)
    res = np.interp(np.linspace(0, len(pcm) - 1, n_out), np.arange(len(pcm)), pcm).astype(np.int16)
    wav_16k = tmp_path / "stt_16k.wav"
    with wave.open(str(wav_16k), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(res.tobytes())

    text = stt.transcribe(wav_16k)
    assert "offline" in text.lower() or "test" in text.lower()
    stt.unload()
    tts.unload()
