"""InteractionSession: starts LOCKED, no inactivity timeout, decoupled from background jobs."""

import logging
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

logger = logging.getLogger(__name__)


class SessionState(StrEnum):
    """Explicit interaction session states."""

    DISABLED = "disabled"
    LOCKED = "locked"
    WAKE_DETECTED = "wake_detected"
    VERIFYING_OWNER = "verifying_owner"
    UNLOCKED = "unlocked"
    LOCKING = "locking"
    ERROR = "error"


class WakeWordPort(Protocol):
    """Optional wake-word detector interface (distinct from speaker verification)."""

    def is_available(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


class SpeakerVerifierPort(Protocol):
    """Optional owner speaker verification interface."""

    def is_enrolled(self) -> bool: ...

    def verify_owner(self, audio_sample: bytes) -> bool: ...


class InteractionSession:
    """Manages interactive command access without affecting background job execution.

    - Starts in LOCKED state by default.
    - Has zero inactivity timeout (never auto-locks).
    - Locking/unlocking never cancels or pauses already-authorized background jobs.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        wake_word: WakeWordPort | None = None,
        speaker_verifier: SpeakerVerifierPort | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._state: SessionState = SessionState.LOCKED if enabled else SessionState.DISABLED
        self._wake_word = wake_word
        self._speaker_verifier = speaker_verifier
        self._listeners: list[Callable[[SessionState], None]] = []
        self._last_error: str = ""
        if self._state == SessionState.LOCKED:
            self._start_wake_word_if_available()

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def wake_word_status(self) -> str:
        if self._wake_word is None:
            return "Wake-word listener not configured"
        if not self._wake_word.is_available():
            return "Wake-word model or microphone unavailable"
        if self.state == SessionState.LOCKED:
            return "Listening for 'Hey ORION'"
        return "Wake-word paused (session unlocked)"

    def speaker_verification_status(self) -> str:
        if self._speaker_verifier is None:
            return "Voice unlock disabled: no speaker verifier configured"
        if hasattr(self._speaker_verifier, "status"):
            return str(self._speaker_verifier.status().reason)
        return (
            "Owner voiceprint enrolled locally."
            if self._speaker_verifier.is_enrolled()
            else "Voice unlock disabled: no enrolled owner voiceprint"
        )

    def _start_wake_word_if_available(self) -> None:
        if self._wake_word is not None and self._wake_word.is_available():
            try:
                self._wake_word.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to start wake-word listener: %s", exc)
                with self._lock:
                    self._last_error = str(exc)

    def _stop_wake_word(self) -> None:
        if self._wake_word is not None:
            try:
                self._wake_word.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error stopping wake-word listener: %s", exc)

    def shutdown(self) -> None:
        """Stop wake-word listener cleanly on application shutdown."""
        self._stop_wake_word()

    def can_accept_interactive_command(self) -> bool:
        with self._lock:
            return self._state == SessionState.UNLOCKED

    def add_listener(self, callback: Callable[[SessionState], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def _emit(self, state: SessionState) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(state)
            except Exception:
                logger.debug("InteractionSession listener failed", exc_info=True)

    def set_enabled(self, enabled: bool) -> SessionState:
        with self._lock:
            prev = self._state
            self._state = SessionState.LOCKED if enabled else SessionState.DISABLED
            current = self._state
        logger.info("InteractionSession transition: %s -> %s (reason=set_enabled)", prev, current)
        if current == SessionState.LOCKED:
            self._start_wake_word_if_available()
        else:
            self._stop_wake_word()
        self._emit(current)
        return current

    def unlock_manual(self) -> SessionState:
        """Explicit user-initiated unlock from desktop UI."""
        with self._lock:
            if self._state == SessionState.DISABLED:
                return self._state
            prev = self._state
            self._state = SessionState.UNLOCKED
            current = self._state
        logger.info("InteractionSession transition: %s -> %s (reason=manual_unlock)", prev, current)
        self._stop_wake_word()
        self._emit(current)
        return current

    def lock(self) -> SessionState:
        """Explicit user-initiated lock. Never cancels background jobs."""
        with self._lock:
            if self._state == SessionState.DISABLED:
                return self._state
            prev = self._state
            self._state = SessionState.LOCKING
        self._emit(SessionState.LOCKING)
        with self._lock:
            self._state = SessionState.LOCKED
            current = self._state
        logger.info("InteractionSession transition: %s -> %s (reason=manual_lock)", prev, current)
        self._start_wake_word_if_available()
        self._emit(current)
        return current

    def on_wake_detected(self) -> SessionState:
        """Record wake-word detection; requires owner verification before unlocking."""
        with self._lock:
            if self._state != SessionState.LOCKED:
                return self._state
            prev = self._state
            self._state = SessionState.WAKE_DETECTED
            current = self._state
        logger.info("InteractionSession transition: %s -> %s (reason=wake_detected)", prev, current)
        self._emit(current)
        return current

    def verify_and_unlock(self, audio_sample: bytes) -> bool:
        """Verify owner voiceprint after wake detection. Never unlocks unverified."""
        with self._lock:
            if self._state not in (SessionState.WAKE_DETECTED, SessionState.LOCKED):
                return self._state == SessionState.UNLOCKED
            if self._speaker_verifier is None or not self._speaker_verifier.is_enrolled():
                self._state = SessionState.LOCKED
                emit_state = self._state
                verifier = None
            else:
                self._state = SessionState.VERIFYING_OWNER
                emit_state = self._state
                verifier = self._speaker_verifier

        if verifier is None:
            logger.info(
                "InteractionSession verification denied -> %s (reason=no_enrolled_voiceprint)",
                emit_state,
            )
            self._emit(emit_state)
            return False

        self._emit(emit_state)
        try:
            ok = bool(verifier.verify_owner(audio_sample))
        except Exception:
            logger.debug("Speaker verification failed", exc_info=True)
            with self._lock:
                self._state = SessionState.ERROR
            self._emit(SessionState.ERROR)
            return False

        with self._lock:
            self._state = SessionState.UNLOCKED if ok else SessionState.LOCKED
            final_state = self._state
        logger.info("InteractionSession verification result -> %s (verified=%s)", final_state, ok)
        if ok:
            self._stop_wake_word()
        self._emit(final_state)
        return ok
