"""VoiceService: orchestrates offline STT, TTS, audio I/O, idle unload, and error states."""

import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from orion.application.voice.contracts import (
    TranscriptionResult,
    VoiceAssetStatus,
    VoiceErrorCode,
    VoiceSettings,
    VoiceState,
)
from orion.application.voice.heavy_work_gate import HeavyWorkGate
from orion.application.voice.ports import (
    AudioInputPort,
    AudioOutputPort,
    SpeechToTextPort,
    TextToSpeechPort,
)
from orion.application.voice.text_cleaner import split_sentences_for_tts


class VoiceServiceError(Exception):
    """Structured voice error with user-actionable VoiceErrorCode."""

    def __init__(self, code: VoiceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VoiceService:
    """Coordinates offline microphone recording, STT, sentence-by-sentence TTS, and idle unload."""

    def __init__(
        self,
        *,
        stt: SpeechToTextPort,
        tts: TextToSpeechPort,
        audio_in: AudioInputPort,
        audio_out: AudioOutputPort,
        gate: HeavyWorkGate | None = None,
        settings: VoiceSettings | None = None,
        on_ptt_active_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._audio_in = audio_in
        self._audio_out = audio_out
        self._gate = gate or HeavyWorkGate()
        self._settings = settings or VoiceSettings()
        self._on_ptt_active_changed = on_ptt_active_changed
        self._state = VoiceState.IDLE
        self._lock = threading.Lock()
        self._cancel_flag = threading.Event()
        self._recording_wav: Path | None = None
        self._record_started_at: float = 0.0
        self._last_used_at: float = 0.0
        self._idle_timer: threading.Timer | None = None

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    @property
    def settings(self) -> VoiceSettings:
        return self._settings

    def update_settings(self, settings: VoiceSettings) -> None:
        self._settings = settings

    def get_asset_status(self) -> VoiceAssetStatus:
        stt_ok = self._stt.is_model_available()
        tts_ok = self._tts.is_model_available()
        mic_ok = self._audio_in.is_available()
        hints = []
        if not stt_ok or not tts_ok:
            hints.append(
                "Run '.venv/bin/python -m orion.voice.setup' to download offline voice models."
            )
        if not mic_ok:
            hints.append("Connect a microphone or verify PipeWire/ALSA capture permissions.")
        return VoiceAssetStatus(
            stt_ready=stt_ok,
            stt_model_path=self._stt.model_path(),
            tts_ready=tts_ok,
            tts_model_path=self._tts.model_path(),
            mic_available=mic_ok,
            audio_backend=self._audio_in.backend_name(),
            fix_hint=" ".join(hints),
        )

    def _schedule_idle_unload(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        timeout = max(5.0, float(self._settings.idle_unload_seconds))
        self._idle_timer = threading.Timer(timeout, self.unload_idle_models)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def unload_idle_models(self) -> None:
        with self._lock:
            if self._state != VoiceState.IDLE:
                return
        self._stt.unload()
        self._tts.unload()

    def start_recording(self) -> None:
        if not self._settings.enabled:
            raise VoiceServiceError(VoiceErrorCode.VOICE_DISABLED, "Voice is disabled in Settings.")
        if not self._stt.is_model_available():
            raise VoiceServiceError(
                VoiceErrorCode.MODEL_MISSING,
                "Offline speech-to-text model is missing. Run 'python -m orion.voice.setup'.",
            )
        if not self._audio_in.is_available():
            raise VoiceServiceError(
                VoiceErrorCode.MICROPHONE_UNAVAILABLE,
                "No microphone capture device is available.",
            )
        # Starting a recording always stops active playback and pauses wake-word listener
        self.cancel_playback()
        if self._on_ptt_active_changed is not None:
            self._on_ptt_active_changed(True)
        self._cancel_flag.clear()

        fd, tmp_name = tempfile.mkstemp(prefix="orion_rec_", suffix=".wav")
        Path(tmp_name).unlink(missing_ok=True)
        import os

        os.close(fd)
        wav_path = Path(tmp_name)
        try:
            self._audio_in.start_recording(wav_path, device=self._settings.input_device)
        except Exception as exc:
            if self._on_ptt_active_changed is not None:
                self._on_ptt_active_changed(False)
            raise VoiceServiceError(
                VoiceErrorCode.DEVICE_ERROR, f"Microphone capture error: {exc}"
            ) from exc

        with self._lock:
            self._recording_wav = wav_path
            self._record_started_at = time.monotonic()
            self._state = VoiceState.RECORDING

    def cancel_recording(self) -> None:
        self._cancel_flag.set()
        try:
            self._audio_in.cancel_recording()
        finally:
            with self._lock:
                wav = self._recording_wav
                self._recording_wav = None
                self._state = VoiceState.IDLE
            if wav is not None:
                wav.unlink(missing_ok=True)
            if self._on_ptt_active_changed is not None:
                self._on_ptt_active_changed(False)

    def stop_recording_and_transcribe(self) -> TranscriptionResult:
        with self._lock:
            if self._state != VoiceState.RECORDING:
                raise VoiceServiceError(VoiceErrorCode.DEVICE_ERROR, "Not currently recording.")
            self._state = VoiceState.TRANSCRIBING
            started = self._record_started_at

        wav_path: Path | None = None
        try:
            wav_path = self._audio_in.stop_recording()
            if self._cancel_flag.is_set():
                return TranscriptionResult(text="", duration_seconds=0.0)
            dur = max(0.0, time.monotonic() - started)
            with self._gate.acquire("stt"):
                if self._cancel_flag.is_set():
                    return TranscriptionResult(text="", duration_seconds=dur)
                self._tts.unload()
                text = self._stt.transcribe(wav_path).strip()
            self._last_used_at = time.monotonic()
            self._schedule_idle_unload()
            if not text:
                raise VoiceServiceError(
                    VoiceErrorCode.EMPTY_TRANSCRIPTION,
                    "No speech was detected in the recording.",
                )
            return TranscriptionResult(text=text, duration_seconds=dur)
        except VoiceServiceError:
            raise
        except Exception as exc:
            raise VoiceServiceError(
                VoiceErrorCode.TRANSCRIPTION_FAILED, f"Transcription failed: {exc}"
            ) from exc
        finally:
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)
            with self._lock:
                self._recording_wav = None
                self._state = VoiceState.IDLE
            if self._on_ptt_active_changed is not None:
                self._on_ptt_active_changed(False)

    def speak_text(
        self,
        text: str,
        *,
        on_sentence_started: Callable[[int, int], None] | None = None,
    ) -> None:
        if not self._settings.enabled:
            raise VoiceServiceError(VoiceErrorCode.VOICE_DISABLED, "Voice is disabled in Settings.")
        if not self._tts.is_model_available():
            raise VoiceServiceError(
                VoiceErrorCode.MODEL_MISSING,
                "Offline speech model is missing. Run 'python -m orion.voice.setup'.",
            )
        sentences = split_sentences_for_tts(text)
        if not sentences:
            return

        self.cancel_playback()
        self._cancel_flag.clear()
        with self._lock:
            self._state = VoiceState.SPEAKING

        try:
            for idx, sent in enumerate(sentences, start=1):
                if self._cancel_flag.is_set():
                    break
                if on_sentence_started is not None:
                    on_sentence_started(idx, len(sentences))
                with tempfile.NamedTemporaryFile(
                    prefix="orion_tts_", suffix=".wav", delete=False
                ) as tf:
                    wav_path = Path(tf.name)
                try:
                    with self._gate.acquire("tts"):
                        if self._cancel_flag.is_set():
                            break
                        self._stt.unload()
                        self._tts.synthesize_to_wav(sent, wav_path)
                    if self._cancel_flag.is_set():
                        break
                    self._audio_out.play_wav(wav_path)
                finally:
                    wav_path.unlink(missing_ok=True)
            self._last_used_at = time.monotonic()
            self._schedule_idle_unload()
        except VoiceServiceError:
            raise
        except Exception as exc:
            raise VoiceServiceError(
                VoiceErrorCode.SPEECH_GENERATION_FAILED, f"Speech generation failed: {exc}"
            ) from exc
        finally:
            with self._lock:
                self._state = VoiceState.IDLE

    def cancel_playback(self) -> None:
        self._cancel_flag.set()
        self._audio_out.stop_playback()
        with self._lock:
            if self._state == VoiceState.SPEAKING:
                self._state = VoiceState.IDLE
