"""Offline Piper TTS adapter (zero network calls at runtime)."""

import threading
import wave
from pathlib import Path
from typing import Any

from orion.infrastructure.voice.audio_io import _voice_data_dir


class PiperTtsAdapter:
    """Loads local Piper ONNX voice lazily and unloads on idle timeout."""

    def __init__(
        self,
        models_dir: Path | None = None,
        voice_name: str = "en_US-lessac-medium",
    ) -> None:
        self._models_dir = models_dir or (_voice_data_dir() / "piper")
        self._voice_name = voice_name
        self._voice: Any = None
        self._lock = threading.Lock()

    def model_path(self) -> Path:
        return self._models_dir / f"{self._voice_name}.onnx"

    def is_model_available(self) -> bool:
        p = self.model_path()
        cfg = Path(f"{p}.json")
        return p.is_file() and p.stat().st_size > 0 and cfg.is_file()

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._voice is not None:
                return self._voice
            if not self.is_model_available():
                raise FileNotFoundError(f"Missing Piper voice model: {self.model_path()}")
            import piper

            self._voice = piper.PiperVoice.load(str(self.model_path()))
            return self._voice

    def synthesize_to_wav(self, text: str, output_wav: Path) -> Path:
        voice = self._ensure_loaded()
        with wave.open(str(output_wav), "wb") as wf:
            voice.synthesize_wav(text, wf)
        return output_wav

    def unload(self) -> None:
        with self._lock:
            self._voice = None
