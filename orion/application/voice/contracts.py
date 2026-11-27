"""Application-layer contracts for ORION offline voice (STT, TTS, Audio I/O)."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class VoiceState(StrEnum):
    """Lifecycle states of the voice subsystem."""

    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    SPEAKING = "speaking"
    ERROR = "error"


class VoiceErrorCode(StrEnum):
    """Distinct, user-actionable voice error categories."""

    VOICE_DISABLED = "voice_disabled"
    MODEL_MISSING = "model_missing"
    MICROPHONE_UNAVAILABLE = "microphone_unavailable"
    DEVICE_ERROR = "device_error"
    TRANSCRIPTION_FAILED = "transcription_failed"
    EMPTY_TRANSCRIPTION = "empty_transcription"
    SPEECH_GENERATION_FAILED = "speech_generation_failed"


@dataclass(frozen=True)
class VoiceSettings:
    """User-configurable offline voice settings."""

    enabled: bool = True
    input_device: str = "default"
    tts_voice: str = "en_US-lessac-medium"
    auto_speak_responses: bool = False
    idle_unload_seconds: float = 120.0


@dataclass(frozen=True)
class VoiceAssetStatus:
    """Availability status of local offline STT/TTS models and audio tools."""

    stt_ready: bool
    stt_model_path: Path
    tts_ready: bool
    tts_model_path: Path
    mic_available: bool
    audio_backend: str
    fix_hint: str = ""


@dataclass(frozen=True)
class TranscriptionResult:
    """Result of an offline STT transcription."""

    text: str
    duration_seconds: float
    error_code: VoiceErrorCode | None = None
    error_message: str = ""
