"""Deterministic voice intent classification for navigation, window, and mic controls."""

import re
import threading
import time
from dataclasses import dataclass
from enum import StrEnum


class VoiceIntentKind(StrEnum):
    """Deterministic voice command categories."""

    OPEN_HOME = "open_home"
    OPEN_CHAT = "open_chat"
    OPEN_KNOWLEDGE = "open_knowledge"
    OPEN_WORKSPACE = "open_workspace"
    OPEN_PRODUCTIVITY = "open_productivity"
    MINIMIZE_TO_ORB = "minimize_to_orb"
    SHOW_MAIN_WINDOW = "show_main_window"
    PAUSE_LISTENING = "pause_listening"
    AMBIGUOUS = "ambiguous"
    CONVERSATIONAL = "conversational"


@dataclass(frozen=True, slots=True)
class VoiceIntentResult:
    """Normalized intent classification result."""

    kind: VoiceIntentKind
    normalized_text: str
    clarification_prompt: str = ""
    is_duplicate: bool = False


def normalize_voice_transcript(text: str) -> str:
    """Normalize punctuation/case and strip leading/trailing address word 'ORION'."""
    cleaned = re.sub(r"[^a-z0-9\s']+", " ", (text or "").lower())
    cleaned = " ".join(cleaned.split())
    cleaned = re.sub(r"^(?:hey|ok|okay|hi)\s+orion\b[\s,]*", "", cleaned)
    cleaned = re.sub(r"^orion\b[\s,]*", "", cleaned)
    cleaned = re.sub(r"[\s,]*\borion\b$", "", cleaned)
    return " ".join(cleaned.split())


_INTENT_PATTERNS: list[tuple[VoiceIntentKind, tuple[re.Pattern[str], ...]]] = [
    (
        VoiceIntentKind.OPEN_HOME,
        (
            re.compile(
                r"^(?:please\s+)?(?:open|show|go to)\s+(?:the\s+|my\s+|your\s+)?home(?:\s+screen|\s+page)?$"
            ),
            re.compile(r"^(?:please\s+)?show\s+(?:the\s+)?main\s+screen$"),
        ),
    ),
    (
        VoiceIntentKind.OPEN_CHAT,
        (
            re.compile(
                r"^(?:please\s+)?(?:open|show|go to)\s+(?:the\s+|my\s+)?(?:chat|conversations?)$"
            ),
        ),
    ),
    (
        VoiceIntentKind.OPEN_KNOWLEDGE,
        (
            re.compile(
                r"^(?:please\s+)?(?:open|show|go to)\s+(?:the\s+|my\s+)?knowledge(?:\s+base|\s+page)?$"
            ),
        ),
    ),
    (
        VoiceIntentKind.OPEN_WORKSPACE,
        (
            re.compile(
                r"^(?:please\s+)?(?:open|show|go to)\s+(?:the\s+|my\s+)?workspace(?:\s+page)?$"
            ),
        ),
    ),
    (
        VoiceIntentKind.OPEN_PRODUCTIVITY,
        (
            re.compile(
                r"^(?:please\s+)?(?:open|show|go to)\s+(?:the\s+|my\s+)?(?:productivity|tasks|habits)(?:\s+page)?$"
            ),
        ),
    ),
    (
        VoiceIntentKind.MINIMIZE_TO_ORB,
        (
            re.compile(r"^(?:please\s+)?(?:go|move)\s+(?:back\s+)?to\s+(?:the\s+)?(?:side|orb)$"),
            re.compile(r"^(?:please\s+)?minimize\s+(?:to\s+)?(?:the\s+)?orb$"),
            re.compile(r"^(?:please\s+)?stay\s+as\s+(?:an?\s+)?orb$"),
        ),
    ),
    (
        VoiceIntentKind.SHOW_MAIN_WINDOW,
        (
            re.compile(r"^(?:please\s+)?open\s+(?:the\s+|your\s+)?main\s+(?:window|screen)$"),
            re.compile(r"^(?:please\s+)?show\s+(?:the\s+)?main\s+window$"),
            re.compile(r"^(?:please\s+)?open\s+orion$"),
            re.compile(r"^open$"),
        ),
    ),
    (
        VoiceIntentKind.PAUSE_LISTENING,
        (
            re.compile(r"^(?:please\s+)?stop\s+listening$"),
            re.compile(r"^(?:please\s+)?pause\s+(?:the\s+)?(?:mic|microphone|listening)$"),
        ),
    ),
]


class VoiceIntentRouter:
    """Deterministic intent classifier with duplicate-submission suppression."""

    def __init__(self, *, dedup_window_seconds: float = 2.5) -> None:
        self._dedup_window = max(0.5, float(dedup_window_seconds))
        self._lock = threading.Lock()
        self._last_text = ""
        self._last_ts = 0.0

    def classify(self, transcript: str) -> VoiceIntentResult:
        raw_lower = " ".join(re.sub(r"[^a-z0-9\s']+", " ", (transcript or "").lower()).split())
        # Special case: "open orion" without prefix stripping
        if raw_lower in ("open orion", "please open orion"):
            norm = raw_lower
        else:
            norm = normalize_voice_transcript(transcript)

        now = time.monotonic()
        with self._lock:
            if norm and norm == self._last_text and (now - self._last_ts) < self._dedup_window:
                return VoiceIntentResult(
                    kind=VoiceIntentKind.CONVERSATIONAL,
                    normalized_text=norm,
                    is_duplicate=True,
                )
            self._last_text = norm
            self._last_ts = now

        if not norm:
            return VoiceIntentResult(
                kind=VoiceIntentKind.AMBIGUOUS,
                normalized_text="",
                clarification_prompt="I heard my name, what would you like me to do?",
            )

        matched: list[VoiceIntentKind] = []
        for kind, patterns in _INTENT_PATTERNS:
            if any(p.match(norm) for p in patterns):
                matched.append(kind)

        if len(matched) == 1:
            return VoiceIntentResult(kind=matched[0], normalized_text=norm)
        if len(matched) > 1:
            return VoiceIntentResult(
                kind=VoiceIntentKind.AMBIGUOUS,
                normalized_text=norm,
                clarification_prompt="Could you clarify which screen or action you want?",
            )

        return VoiceIntentResult(kind=VoiceIntentKind.CONVERSATIONAL, normalized_text=norm)
