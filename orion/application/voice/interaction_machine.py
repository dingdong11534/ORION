"""Explicit, testable voice-first interaction state machine."""

import logging
import threading
from collections.abc import Callable
from enum import StrEnum

logger = logging.getLogger(__name__)


class VoiceInteractionState(StrEnum):
    """States of the voice-first ORION interaction lifecycle."""

    ORB_STANDBY = "orb_standby"
    WAKE_ACKNOWLEDGEMENT = "wake_acknowledgement"
    COMMAND_CAPTURE = "command_capture"
    COMMAND_PROCESSING = "command_processing"
    RESPONDING = "responding"
    FULL_WINDOW = "full_window"
    MICROPHONE_PAUSED = "microphone_paused"
    MICROPHONE_ERROR = "microphone_error"


class MicIndicatorState(StrEnum):
    """Distinct user-visible microphone indicator states."""

    WAKE_LISTENING = "wake_listening"
    COMMAND_CAPTURE = "command_capture"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    PAUSED = "paused"
    ERROR = "error"


_VALID_TRANSITIONS: dict[VoiceInteractionState, frozenset[VoiceInteractionState]] = {
    VoiceInteractionState.ORB_STANDBY: frozenset(
        {
            VoiceInteractionState.WAKE_ACKNOWLEDGEMENT,
            VoiceInteractionState.COMMAND_CAPTURE,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_PAUSED,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.WAKE_ACKNOWLEDGEMENT: frozenset(
        {
            VoiceInteractionState.COMMAND_CAPTURE,
            VoiceInteractionState.COMMAND_PROCESSING,
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_PAUSED,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.COMMAND_CAPTURE: frozenset(
        {
            VoiceInteractionState.COMMAND_PROCESSING,
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_PAUSED,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.COMMAND_PROCESSING: frozenset(
        {
            VoiceInteractionState.RESPONDING,
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_PAUSED,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.RESPONDING: frozenset(
        {
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_PAUSED,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.FULL_WINDOW: frozenset(
        {
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.WAKE_ACKNOWLEDGEMENT,
            VoiceInteractionState.COMMAND_CAPTURE,
            VoiceInteractionState.COMMAND_PROCESSING,
            VoiceInteractionState.RESPONDING,
            VoiceInteractionState.MICROPHONE_PAUSED,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.MICROPHONE_PAUSED: frozenset(
        {
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_ERROR,
        }
    ),
    VoiceInteractionState.MICROPHONE_ERROR: frozenset(
        {
            VoiceInteractionState.ORB_STANDBY,
            VoiceInteractionState.FULL_WINDOW,
            VoiceInteractionState.MICROPHONE_PAUSED,
        }
    ),
}


class InvalidVoiceTransitionError(ValueError):
    """Raised when an invalid voice interaction state transition is attempted."""


class VoiceInteractionMachine:
    """Thread-safe state machine coordinating voice interaction and mic visibility."""

    def __init__(
        self,
        *,
        initial_state: VoiceInteractionState = VoiceInteractionState.ORB_STANDBY,
    ) -> None:
        self._lock = threading.Lock()
        self._state = initial_state
        self._error_reason = ""
        self._window_visible = initial_state == VoiceInteractionState.FULL_WINDOW
        self._listeners: list[Callable[[VoiceInteractionState], None]] = []

    @property
    def state(self) -> VoiceInteractionState:
        with self._lock:
            return self._state

    @property
    def error_reason(self) -> str:
        with self._lock:
            return self._error_reason

    @property
    def window_visible(self) -> bool:
        with self._lock:
            return self._window_visible

    def set_window_visible(self, visible: bool) -> None:
        with self._lock:
            self._window_visible = bool(visible)

    def add_listener(self, callback: Callable[[VoiceInteractionState], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def transition(
        self,
        target: VoiceInteractionState,
        *,
        reason: str = "",
        error_reason: str = "",
    ) -> VoiceInteractionState:
        with self._lock:
            current = self._state
            if current == target:
                if target == VoiceInteractionState.MICROPHONE_ERROR and error_reason:
                    self._error_reason = error_reason
                return current
            allowed = _VALID_TRANSITIONS.get(current, frozenset())
            if target not in allowed:
                raise InvalidVoiceTransitionError(
                    f"Invalid voice transition: {current.value} -> {target.value}"
                )
            self._state = target
            if target == VoiceInteractionState.MICROPHONE_ERROR:
                self._error_reason = error_reason or reason or "Microphone unavailable"
            elif target in (
                VoiceInteractionState.ORB_STANDBY,
                VoiceInteractionState.FULL_WINDOW,
            ):
                self._error_reason = ""
            listeners = list(self._listeners)

        logger.info("VoiceInteractionMachine: %s -> %s (%s)", current.value, target.value, reason)
        for cb in listeners:
            try:
                cb(target)
            except Exception:
                logger.debug("VoiceInteractionMachine listener failed", exc_info=True)
        return target

    def return_to_standby(self, *, reason: str = "completed") -> VoiceInteractionState:
        with self._lock:
            target = (
                VoiceInteractionState.FULL_WINDOW
                if self._window_visible
                else VoiceInteractionState.ORB_STANDBY
            )
        return self.transition(target, reason=reason)

    def mic_indicator_state(self) -> MicIndicatorState:
        with self._lock:
            st = self._state
        if st == VoiceInteractionState.COMMAND_CAPTURE:
            return MicIndicatorState.COMMAND_CAPTURE
        if st == VoiceInteractionState.COMMAND_PROCESSING:
            return MicIndicatorState.PROCESSING
        if st == VoiceInteractionState.RESPONDING:
            return MicIndicatorState.SPEAKING
        if st == VoiceInteractionState.MICROPHONE_PAUSED:
            return MicIndicatorState.PAUSED
        if st == VoiceInteractionState.MICROPHONE_ERROR:
            return MicIndicatorState.ERROR
        return MicIndicatorState.WAKE_LISTENING

    def mic_status_description(self) -> str:
        ind = self.mic_indicator_state()
        if ind == MicIndicatorState.COMMAND_CAPTURE:
            return "Recording command — capturing speech locally."
        if ind == MicIndicatorState.PROCESSING:
            return "Processing audio — transcribing locally with Whisper."
        if ind == MicIndicatorState.SPEAKING:
            return "Speaking response — wake listening suppressed."
        if ind == MicIndicatorState.PAUSED:
            return "Microphone paused — no wake detection or recording active."
        if ind == MicIndicatorState.ERROR:
            reason = self.error_reason
            return f"Microphone unavailable: {reason}" if reason else "Microphone unavailable."
        return "Microphone active — local wake-word monitoring. No continuous audio is saved."
