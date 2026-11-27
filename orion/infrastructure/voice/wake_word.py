"""Offline 'Hey ORION' / 'ORION' wake-word listener, rolling buffer, and local speaker verifier."""

import io
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WAKE_BOUNDARY_RE = re.compile(r"\b(?:hey\s+)?orion\b")


def _voice_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(base) if base else (Path.home() / ".local" / "share")
    return (root / "orion" / "voice").resolve()


def _normalize_phrase_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    return " ".join(cleaned.split())


def pcm_to_wav_bytes(pcm_s16le: bytes, *, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw 16-bit PCM bytes into an in-memory WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_s16le)
    return buf.getvalue()


class VoskWakeWordListener:
    """Single-stream PipeWire (pw-cat) + Vosk wake-word detector with rolling audio buffer.

    Supports both 'Hey ORION' and standalone 'ORION' anywhere in an utterance,
    preserving pre-wake audio in a bounded in-memory ring buffer and capturing
    post-wake speech until silence/VAD or max_command_seconds.
    """

    def __init__(
        self,
        *,
        model_dir: Path | None = None,
        phrase: str = "hey orion",
        cooldown_seconds: float = 3.0,
        pre_roll_seconds: float = 3.5,
        silence_timeout_seconds: float = 1.2,
        max_command_seconds: float = 10.0,
        vad_rms_threshold: float = 80.0,
        on_detected: Callable[[], None] | None = None,
        on_command_captured: Callable[[bytes], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._model_dir = model_dir or (_voice_data_dir() / "vosk" / "vosk-model-small-en-us-0.15")
        self._phrase = _normalize_phrase_text(phrase)
        self._cooldown_seconds = max(0.5, float(cooldown_seconds))
        self._chunk_seconds = 0.125  # 4000 bytes at 16kHz s16le mono
        self._pre_roll_chunks = max(4, int(pre_roll_seconds / self._chunk_seconds))
        self._silence_timeout_seconds = max(0.4, float(silence_timeout_seconds))
        self._max_command_seconds = max(2.0, float(max_command_seconds))
        self._vad_rms_threshold = float(vad_rms_threshold)

        self._on_detected = on_detected
        self._on_command_captured = on_command_captured
        self._on_error = on_error

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._model: Any = None
        self._running = False
        self._paused_for_ptt = False
        self._paused_by_user = False
        self._suppressed_for_tts = False

        self._rolling_buffer: deque[bytes] = deque(maxlen=self._pre_roll_chunks)
        self._capturing_command = False
        self._command_chunks: list[bytes] = []
        self._capture_started_at = 0.0
        self._last_speech_at = 0.0
        self._saw_post_wake_speech = False

        self._last_trigger_monotonic = 0.0
        self._last_wake_at = ""
        self._last_init_at = ""
        self._current_rms = 0.0
        self._last_error = ""
        self._capture_status = "stopped"

    def set_callbacks(
        self,
        *,
        on_detected: Callable[[], None] | None = None,
        on_command_captured: Callable[[bytes], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        with self._lock:
            if on_detected is not None:
                self._on_detected = on_detected
            if on_command_captured is not None:
                self._on_command_captured = on_command_captured
            if on_error is not None:
                self._on_error = on_error

    def _capture_backend(self) -> str:
        if shutil.which("pw-cat"):
            return "pipewire (pw-cat)"
        if shutil.which("arecord"):
            return "alsa (arecord)"
        return "none"

    def is_available(self) -> bool:
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        return self._capture_backend() != "none" and self._model_dir.is_dir()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_paused_by_user(self) -> bool:
        with self._lock:
            return self._paused_by_user

    def get_diagnostics(self) -> dict[str, Any]:
        with self._lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            if self._paused_by_user:
                status = "paused"
            elif self._paused_for_ptt:
                status = "paused_for_ptt"
            elif self._suppressed_for_tts:
                status = "suppressed_for_tts"
            elif self._capturing_command:
                status = "capturing_command"
            elif self._last_error:
                status = "error"
            elif self._running and thread_alive:
                status = "listening"
            elif not self.is_available():
                status = "unavailable"
            else:
                status = self._capture_status
            model_status = (
                "loaded"
                if self._model is not None
                else ("available" if self._model_dir.is_dir() else "missing")
            )
            return {
                "input_source": self._capture_backend(),
                "capture_status": status,
                "sample_rate": 16000,
                "channels": 1,
                "current_rms": round(self._current_rms, 1),
                "model_status": model_status,
                "model_path": str(self._model_dir),
                "thread_alive": thread_alive,
                "command_capturing": self._capturing_command,
                "paused_by_user": self._paused_by_user,
                "last_wake_at": self._last_wake_at,
                "last_init_at": self._last_init_at,
                "last_error": self._last_error,
            }

    def start(self) -> None:
        with self._lock:
            if self._paused_by_user:
                self._capture_status = "paused"
                return
            if self._running:
                return
            if not self.is_available():
                self._capture_status = "unavailable"
                self._last_error = f"Wake-word model or capture tool missing at {self._model_dir}"
                raise RuntimeError(self._last_error)
            self._paused_for_ptt = False
            self._last_error = ""
            self._capture_status = "starting"
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop, name="orion-wake-word", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            proc = self._proc
            thread = self._thread
            self._running = False
            self._capturing_command = False
            self._command_chunks.clear()
            self._rolling_buffer.clear()
            self._capture_status = "paused" if self._paused_by_user else "stopped"
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            self._proc = None
            self._thread = None

    def pause_by_user(self) -> None:
        """Explicit user pause: stops microphone capture and never silently reactivates."""
        with self._lock:
            self._paused_by_user = True
        self.stop()

    def resume_by_user(self) -> None:
        """Explicit user resume: clears paused state and restarts microphone capture."""
        with self._lock:
            self._paused_by_user = False
        if self.is_available():
            self.start()

    def pause_for_ptt(self) -> None:
        """Release microphone cleanly while push-to-talk is recording."""
        with self._lock:
            self._paused_for_ptt = True
        self.stop()

    def resume_after_ptt(self) -> None:
        """Resume wake-word listening after push-to-talk finishes (unless user paused)."""
        with self._lock:
            self._paused_for_ptt = False
            paused_user = self._paused_by_user
        if not paused_user and self.is_available():
            self.start()

    def suppress_wake_for_playback(self, suppressed: bool) -> None:
        """Prevent ORION's spoken TTS playback from triggering wake detection."""
        with self._lock:
            self._suppressed_for_tts = bool(suppressed)
            if suppressed:
                self._rolling_buffer.clear()

    def _build_capture_cmd(self) -> list[str]:
        if shutil.which("pw-cat"):
            return [
                "pw-cat",
                "--record",
                "--raw",
                "--rate",
                "16000",
                "--channels",
                "1",
                "--format",
                "s16",
                "-",
            ]
        if shutil.which("arecord"):
            return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1", "-"]
        raise RuntimeError("No audio capture backend found.")

    def _ensure_recognizer(self) -> Any:
        import vosk

        vosk.SetLogLevel(-1)
        if self._model is None:
            self._model = vosk.Model(str(self._model_dir))
        phrases = list({self._phrase, "orion", "[unk]"})
        grammar = json.dumps(phrases)
        return vosk.KaldiRecognizer(self._model, 16000, grammar)

    def matches_phrase(self, text: str) -> bool:
        """Word-boundary-aware match for 'Hey ORION' or standalone 'ORION'."""
        normalized = _normalize_phrase_text(text)
        if not normalized:
            return False
        return bool(_WAKE_BOUNDARY_RE.search(normalized))

    def _measure_pcm_rms(self, data: bytes) -> float:
        count = len(data) // 2
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", data[: count * 2])
        return math.sqrt(sum(s * s for s in samples) / count)

    def _begin_command_capture(self, rec: Any, now: float) -> bool:
        with self._lock:
            if self._capturing_command:
                return False
            if (now - self._last_trigger_monotonic) < self._cooldown_seconds:
                return False
            self._last_trigger_monotonic = now
            self._last_wake_at = datetime.now(UTC).isoformat()
            self._capturing_command = True
            self._command_chunks = list(self._rolling_buffer)
            self._rolling_buffer.clear()
            self._capture_started_at = now
            self._last_speech_at = now
            self._saw_post_wake_speech = False
            cb = self._on_detected

        rec.Reset()
        if cb is not None:
            cb()
        return True

    def _finish_command_capture(self, rec: Any) -> bytes | None:
        with self._lock:
            if not self._capturing_command:
                return None
            pcm_bytes = b"".join(self._command_chunks)
            self._command_chunks.clear()
            self._capturing_command = False
            cb = self._on_command_captured

        rec.Reset()
        if cb is not None and pcm_bytes:
            cb(pcm_bytes)
        return pcm_bytes

    def process_audio_chunk(
        self,
        rec: Any,
        data: bytes,
        *,
        now: float | None = None,
    ) -> bool:
        """Process one 16kHz s16le PCM chunk: rolling buffer, wake detection, or command VAD."""
        cur_time = time.monotonic() if now is None else now
        rms = self._measure_pcm_rms(data)

        with self._lock:
            self._current_rms = rms
            if self._suppressed_for_tts or self._paused_by_user:
                return False
            capturing = self._capturing_command

        if capturing:
            with self._lock:
                self._command_chunks.append(data)
                if rms >= self._vad_rms_threshold:
                    self._last_speech_at = cur_time
                    self._saw_post_wake_speech = True
                elapsed = cur_time - self._capture_started_at
                silence_elapsed = cur_time - self._last_speech_at
                should_finish = (
                    elapsed >= self._max_command_seconds
                    or (
                        self._saw_post_wake_speech
                        and silence_elapsed >= self._silence_timeout_seconds
                    )
                    or (
                        not self._saw_post_wake_speech
                        and silence_elapsed >= max(1.8, self._silence_timeout_seconds)
                    )
                )
            if should_finish:
                self._finish_command_capture(rec)
            return False

        with self._lock:
            self._rolling_buffer.append(data)

        matched = False
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result() or "{}")
            matched = self.matches_phrase(res.get("text", ""))
        else:
            part = json.loads(rec.PartialResult() or "{}")
            matched = self.matches_phrase(part.get("partial", ""))

        if matched:
            return self._begin_command_capture(rec, cur_time)
        return False

    def test_microphone_rms(self, duration_seconds: float = 0.5) -> float:
        """Capture a short raw PCM sample to verify microphone input and return RMS."""
        if self.is_running:
            return self.get_diagnostics()["current_rms"]
        cmd = self._build_capture_cmd()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            target_bytes = int(16000 * 2 * max(0.1, duration_seconds))
            assert proc.stdout is not None
            data = proc.stdout.read(target_bytes)
            rms = self._measure_pcm_rms(data)
            with self._lock:
                self._current_rms = rms
            return rms
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _run_loop(self) -> None:
        try:
            rec = self._ensure_recognizer()
            cmd = self._build_capture_cmd()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            with self._lock:
                self._proc = proc
                self._capture_status = "listening"
                self._last_init_at = datetime.now(UTC).isoformat()

            assert proc.stdout is not None
            chunk_bytes = 4000  # ~125 ms at 16kHz s16le mono
            while not self._stop_event.is_set():
                data = proc.stdout.read(chunk_bytes)
                if not data:
                    break
                self.process_audio_chunk(rec, data)

            if not self._stop_event.is_set() and proc.poll() not in (0, None):
                err = ""
                if proc.stderr is not None:
                    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                msg = err or f"Wake-word capture exited with code {proc.returncode}"
                with self._lock:
                    self._last_error = msg
                    self._capture_status = "error"
                if self._on_error is not None:
                    self._on_error(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wake-word listener error: %s", exc)
            with self._lock:
                self._last_error = str(exc)
                self._capture_status = "error"
            if self._on_error is not None:
                self._on_error(str(exc))
        finally:
            with self._lock:
                proc = self._proc
                self._proc = None
                self._running = False
                self._capturing_command = False
                self._command_chunks.clear()
                if self._capture_status == "listening":
                    self._capture_status = "paused" if self._paused_by_user else "stopped"
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()


@dataclass(frozen=True, slots=True)
class SpeakerVerificationStatus:
    """Honest status of local owner speaker verification."""

    enrolled: bool
    reason: str


class LocalSpeakerVerifier:
    """Explicit local speaker verifier (disabled by default until owner enrolls).

    Never auto-enrolls from arbitrary microphone input and never unlocks on
    wake-word detection alone.
    """

    def __init__(self, *, voiceprint_path: Path | None = None, threshold: float = 0.92) -> None:
        self._path = voiceprint_path or (_voice_data_dir() / "owner_voiceprint.json")
        self._threshold = threshold

    def is_enrolled(self) -> bool:
        if not self._path.is_file():
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            vec = data.get("profile")
            return isinstance(vec, list) and len(vec) == 8
        except (OSError, json.JSONDecodeError, ValueError):
            return False

    def status(self) -> SpeakerVerificationStatus:
        if self.is_enrolled():
            return SpeakerVerificationStatus(
                enrolled=True,
                reason="Owner voiceprint enrolled locally.",
            )
        return SpeakerVerificationStatus(
            enrolled=False,
            reason="Voice unlock disabled: no enrolled owner voiceprint (use manual Unlock).",
        )

    def enroll_from_pcm(self, pcm_s16le: bytes) -> None:
        """Explicitly enroll owner voiceprint from a 16kHz s16le PCM sample."""
        vec = self._extract_features(pcm_s16le)
        if vec is None:
            raise ValueError("Sample too quiet or short for voiceprint enrollment.")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"profile": vec}), encoding="utf-8")
        self._path.chmod(0o600)

    def verify_owner(self, audio_sample: bytes) -> bool:
        if not self.is_enrolled():
            return False
        data = json.loads(self._path.read_text(encoding="utf-8"))
        ref = [float(x) for x in data["profile"]]
        cand = self._extract_features(audio_sample)
        if cand is None:
            return False
        dot = sum(a * b for a, b in zip(ref, cand, strict=True))
        norm_a = math.sqrt(sum(a * a for a in ref))
        norm_b = math.sqrt(sum(b * b for b in cand))
        if norm_a == 0.0 or norm_b == 0.0:
            return False
        return (dot / (norm_a * norm_b)) >= self._threshold

    @staticmethod
    def _extract_features(pcm_s16le: bytes) -> list[float] | None:
        if len(pcm_s16le) < 3200:
            return None
        count = len(pcm_s16le) // 2
        samples = struct.unpack(f"<{count}h", pcm_s16le[: count * 2])
        rms = math.sqrt(sum(s * s for s in samples) / count)
        if rms < 50.0:
            return None
        bands = [0.0] * 8
        step = max(1, count // 8)
        for idx in range(8):
            seg = samples[idx * step : (idx + 1) * step]
            if not seg:
                continue
            seg_rms = math.sqrt(sum(s * s for s in seg) / len(seg))
            bands[idx] = seg_rms / (rms + 1e-6)
        return bands
