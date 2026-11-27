"""Offline whisper.cpp STT adapter using pywhispercpp (zero network calls at runtime)."""

import threading
from pathlib import Path
from typing import Any

from orion.infrastructure.voice.audio_io import _voice_data_dir


class WhisperCppSttAdapter:
    """Loads local ggml-tiny.en.bin lazily and unloads on idle timeout."""

    def __init__(self, models_dir: Path | None = None, model_name: str = "tiny.en") -> None:
        self._models_dir = models_dir or (_voice_data_dir() / "whispercpp")
        self._model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def model_path(self) -> Path:
        return self._models_dir / f"ggml-{self._model_name}.bin"

    def is_model_available(self) -> bool:
        p = self.model_path()
        return p.is_file() and p.stat().st_size > 0

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            if not self.is_model_available():
                raise FileNotFoundError(f"Missing STT model: {self.model_path()}")
            from pywhispercpp.model import Model

            self._model = Model(
                str(self.model_path()),
                n_threads=2,
                print_realtime=False,
                print_progress=False,
            )
            return self._model

    def transcribe(self, wav_path: Path) -> str:
        model = self._ensure_loaded()
        segs = model.transcribe(str(wav_path))
        return " ".join(s.text.strip() for s in segs if getattr(s, "text", "").strip()).strip()

    def unload(self) -> None:
        with self._lock:
            self._model = None
