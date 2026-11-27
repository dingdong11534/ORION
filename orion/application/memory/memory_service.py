"""PersonalMemoryService: explicit user memory commands, review, and context building."""

import re
from typing import Any

from orion.application.memory.contracts import (
    MemoryCategory,
    MemoryStatus,
    PersonalMemoryRecord,
    UserProfileRecord,
)

_REMEMBER_RE = re.compile(
    r"^(?:please\s+)?remember(?:\s+that)?\s+(.+?)[.!?]*$",
    re.IGNORECASE,
)
_PRIORITY_RE = re.compile(
    r"^my\s+(?:current\s+)?(?:main\s+)?(priority|goal|project|routine)\s+is\s+(.+?)[.!?]*$",
    re.IGNORECASE,
)
_RECALL_RE = re.compile(
    r"^(?:what\s+do\s+you\s+(?:remember|know)\s+(?:about\s+)?(.+?)|"
    r"what\s+are\s+my\s+(?:current\s+)?(projects|goals|memories|routines))[.?]*$",
    re.IGNORECASE,
)
_FORGET_RE = re.compile(
    r"^(?:please\s+)?forget\s+(?:about\s+|that\s+)?(.+?)[.!?]*$",
    re.IGNORECASE,
)


def _infer_category(text: str) -> MemoryCategory:
    low = text.lower()
    if any(w in low for w in ("project", "repo", "codebase", "app", "building")):
        return MemoryCategory.PROJECT
    if any(w in low for w in ("goal", "priority", "internship", "exam", "target", "prepare")):
        return MemoryCategory.GOAL
    if any(w in low for w in ("every day", "morning", "evening", "routine", "schedule")):
        return MemoryCategory.ROUTINE
    return MemoryCategory.FACT


class PersonalMemoryService:
    """Manages local, reviewable personal memories and trusted user profile."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_profile(self) -> UserProfileRecord:
        return self._store.get_profile()

    def save_profile(
        self,
        *,
        preferred_name: str | None = None,
        timezone: str = "Asia/Kolkata",
        response_preferences: str | None = None,
        work_study_preferences: str | None = None,
    ) -> UserProfileRecord:
        return self._store.save_profile(
            preferred_name=preferred_name,
            timezone=timezone,
            response_preferences=response_preferences,
            work_study_preferences=work_study_preferences,
        )

    def list_memories(
        self,
        *,
        category: MemoryCategory | None = None,
        include_forgotten: bool = False,
    ) -> list[PersonalMemoryRecord]:
        return self._store.list_memories(category=category, include_forgotten=include_forgotten)

    def add_memory(
        self,
        *,
        category: MemoryCategory,
        title: str,
        content: str,
        source: str = "settings_ui",
    ) -> PersonalMemoryRecord:
        return self._store.add_memory(
            category=category, title=title, content=content, source=source
        )

    def update_memory(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        category: MemoryCategory | None = None,
        status: MemoryStatus | None = None,
    ) -> PersonalMemoryRecord:
        return self._store.update_memory(
            memory_id, title=title, content=content, category=category, status=status
        )

    def delete_memory(self, memory_id: str) -> None:
        self._store.delete_memory(memory_id)

    def handle_message(self, message: str) -> str | None:
        """Handle explicit user memory commands; return None for normal conversation."""
        raw = (message or "").strip()
        if not raw or len(raw) > 500:
            return None
        low = raw.lower()
        if "=== begin untrusted" in low or "ignore previous instructions" in low:
            return None

        m_rem = _REMEMBER_RE.match(raw)
        if m_rem:
            fact = m_rem.group(1).strip()
            if not fact:
                return None
            cat = _infer_category(fact)
            title = fact[:60] + ("..." if len(fact) > 60 else "")
            rec = self._store.add_memory(
                category=cat, title=title, content=fact, source="user_explicit"
            )
            return f"Remembered ({rec.category.value}): {rec.content}"

        m_prio = _PRIORITY_RE.match(raw)
        if m_prio:
            kind = m_prio.group(1).lower()
            val = m_prio.group(2).strip()
            cat = {
                "priority": MemoryCategory.GOAL,
                "goal": MemoryCategory.GOAL,
                "project": MemoryCategory.PROJECT,
                "routine": MemoryCategory.ROUTINE,
            }[kind]
            rec = self._store.add_memory(
                category=cat,
                title=f"{kind.capitalize()}: {val[:50]}",
                content=f"Current {kind}: {val}",
                source="user_explicit",
            )
            return f"Saved to {rec.category.value}s: {rec.content}"

        m_rec = _RECALL_RE.match(raw)
        if m_rec:
            topic = (m_rec.group(1) or m_rec.group(2) or "").strip().lower()
            profile = self.get_profile()
            mems = self.list_memories()
            if topic and topic not in ("me", "my work", "everything", "memories"):
                tokens = [t for t in re.sub(r"[^a-z0-9\s]", " ", topic).split() if len(t) > 2]
                if topic.startswith(("project", "my current project")):
                    mems = [m for m in mems if m.category == MemoryCategory.PROJECT]
                elif topic.startswith(("goal", "priorit")):
                    mems = [m for m in mems if m.category == MemoryCategory.GOAL]
                elif topic.startswith("routine"):
                    mems = [m for m in mems if m.category == MemoryCategory.ROUTINE]
                elif tokens:
                    mems = [
                        m
                        for m in mems
                        if any(tok in m.title.lower() or tok in m.content.lower() for tok in tokens)
                    ]

            lines: list[str] = []
            if profile.preferred_name:
                lines.append(f"Profile: {profile.preferred_name} (timezone: {profile.timezone})")
            for m in mems[:12]:
                stale_tag = " [stale]" if m.status == MemoryStatus.STALE else ""
                lines.append(f"- [{m.category.value}{stale_tag}] {m.content}")
            if not lines:
                return "I don't have any saved personal memories matching that yet."
            return "Here is what I have saved locally:\n" + "\n".join(lines)

        m_fgt = _FORGET_RE.match(raw)
        if m_fgt:
            topic = m_fgt.group(1).strip().lower()
            tokens = [t for t in re.sub(r"[^a-z0-9\s]", " ", topic).split() if len(t) > 2]
            mems = self.list_memories()
            matched = [
                m
                for m in mems
                if tokens
                and any(tok in m.title.lower() or tok in m.content.lower() for tok in tokens)
            ]
            if not matched:
                return f"I couldn't find an active memory matching '{m_fgt.group(1).strip()}'."
            for m in matched:
                self._store.update_memory(m.id, status=MemoryStatus.FORGOTTEN)
            return f"Forgot {len(matched)} matching memory item(s)."

        return None

    def build_prompt_context(self, max_items: int = 6) -> str:
        """Return compact, trusted personal context for the system prompt."""
        profile = self.get_profile()
        mems = [m for m in self.list_memories() if m.status == MemoryStatus.ACTIVE][:max_items]
        parts: list[str] = []
        if profile.preferred_name:
            parts.append(f"User preferred name: {profile.preferred_name} (tz: {profile.timezone})")
        if profile.work_study_preferences:
            parts.append(f"Work/study preferences: {profile.work_study_preferences}")
        for m in mems:
            parts.append(f"[{m.category.value}] {m.content}")
        return "\n".join(parts)
