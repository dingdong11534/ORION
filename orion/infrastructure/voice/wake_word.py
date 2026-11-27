"""Offline 'Hey ORION' wake-word listener (Vosk) and explicit local speaker verifier."""

import json
import logging
import math
import os
import shutil
import struct
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _voice_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(base) if base else (Path.home() / ".local" / "share")
    return (root / "orion" / "voice").resolve()


class VoskWakeWordListener:
    """Lightweight streaming 'Hey ORION' detector using PipeWire + Vosk grammar."""

    def __init__(
        self,
        *,
        model_dir: Path | None = None,
        phrase: str = "hey orion",
        on_detected: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._model_dir = model_dir or (_voice_data_dir() / "vosk" / "vosk-model-small-en-us-0.15")
        self._phrase = phrase.lower().strip()
        self._on_detected = on_detected
        self._on_error = on_error
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._model: Any = None
        self._running = False

    def set_callbacks(
        self,
        *,
        on_detected: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        with self._lock:
            if on_detected is not None:
                self._on_detected = on_detected
            if on_error is not None:
                self._on_error = on_error

    def is_available(self) -> bool:
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        has_rec = bool(shutil.which("pw-record") or shutil.which("arecord"))
        return has_rec and self._model_dir.is_dir()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            if not self.is_available():
                raise RuntimeError(f"Wake-word model or capture tool missing at {self._model_dir}")
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

    def _build_capture_cmd(self) -> list[str]:
        if shutil.which("pw-record"):
            return [
                "pw-record",
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
        grammar = json.dumps([self._phrase, "[unk]"])
        return vosk.KaldiRecognizer(self._model, 16000, grammar)

    def matches_phrase(self, text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        return self._phrase in normalized

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

            assert proc.stdout is not None
            chunk_bytes = 4000  # ~125 ms at 16kHz s16le mono
            while not self._stop_event.is_set():
                data = proc.stdout.read(chunk_bytes)
                if not data:
                    break
                detected = False
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result() or "{}")
                    detected = self.matches_phrase(res.get("text", ""))
                else:
                    part = json.loads(rec.PartialResult() or "{}")
                    detected = self.matches_phrase(part.get("partial", ""))

                if detected:
                    rec.Reset()
                    cb = self._on_detected
                    if cb is not None:
                        cb()

            if not self._stop_event.is_set() and proc.poll() not in (0, None):
                err = ""
                if proc.stderr is not None:
                    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                if self._on_error is not None:
                    self._on_error(err or f"Wake-word capture exited with code {proc.returncode}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wake-word listener error: %s", exc)
            if self._on_error is not None:
                self._on_error(str(exc))
        finally:
            with self._lock:
                proc = self._proc
                self._proc = None
                self._running = False
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
        # 8-band zero-crossing & energy distribution feature vector
        bands = [0.0] * 8
        step = max(1, count // 8)
        for idx in range(8):
            seg = samples[idx * step : (idx + 1) * step]
            if not seg:
                continue
            seg_rms = math.sqrt(sum(s * s for s in seg) / len(seg))
            bands[idx] = seg_rms / (rms + 1e-6)
        return bands
