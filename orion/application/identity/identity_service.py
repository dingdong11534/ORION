"""Authoritative ORION identity service and deterministic intent router."""

import re

from orion.application.identity.capabilities import CapabilityRegistry

_EXTERNAL_ORION_RE = re.compile(
    r"\b(?:constellation|telescope|nebula|spacecraft|star|stars|mythology|greek|nasa|esa|"
    r"in\s+my\s+documents?|from\s+my\s+documents?|mentions?\s+of|search\s+the\s+web\s+for|look\s+up\s+online)\b",
    re.IGNORECASE,
)

_IDENTITY_QUERY_RE = re.compile(
    r"(?:"
    r"who\s+are\s+you|"
    r"what\s+is\s+your\s+name|"
    r"what\s+are\s+you|"
    r"what\s+can\s+(?:you|orion)\s+do|"
    r"things\s+that\s+can\s+be\s+done\s+with\s+orion|"
    r"what\s+are\s+your\s+capabilit|"
    r"tell\s+me\s+about\s+(?:yourself|orion(?:\s+assistant)?)\b|"
    r"can\s+you\s+(?:read\s+documents|manage\s+(?:my\s+)?tasks|track\s+(?:my\s+)?habits|"
    r"search\s+the\s+web|browse\s+the\s+web|speak|talk|hear|listen|use\s+voice|remember)"
    r")",
    re.IGNORECASE,
)


class OrionIdentityService:
    """Provides trusted ORION identity and live runtime capabilities without LLM/RAG guessing."""

    NAME = "ORION"
    DESCRIPTION = "Local-first personal desktop AI assistant"
    PURPOSE = (
        "Document understanding, productivity management, personal knowledge, "
        "workspace browsing, local offline voice, and public web research where available."
    )

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def is_identity_or_capability_query(self, message: str) -> bool:
        raw = (message or "").strip()
        if not raw or len(raw) > 300:
            return False
        if _EXTERNAL_ORION_RE.search(raw):
            return False
        return bool(_IDENTITY_QUERY_RE.search(raw))

    def answer(self, message: str) -> str:
        caps = self._registry.list_capabilities()
        available_lines = [f"- {c.label}: {c.detail}" for c in caps if c.available]
        unavailable_lines = [f"- {c.label}: {c.detail}" for c in caps if not c.available]

        parts = [
            f"I am {self.NAME}, your {self.DESCRIPTION.lower()}.",
            f"Purpose: {self.PURPOSE}",
        ]
        if available_lines:
            parts.append("Currently available capabilities:\n" + "\n".join(available_lines))
        if unavailable_lines:
            parts.append("Currently unavailable:\n" + "\n".join(unavailable_lines))
        return "\n\n".join(parts)
