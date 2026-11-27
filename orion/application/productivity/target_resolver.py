"""Deterministic target resolution for tasks and habits."""

import re
from collections.abc import Sequence

from orion.application.productivity.contracts import (
    HabitRecord,
    TargetResolutionResult,
    TargetResolutionStatus,
    TaskRecord,
)

_FILLER = {"my", "the", "a", "an", "task", "habit", "to", "for", "of", "on"}


def _normalize(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return " ".join(cleaned.split())


def _core_tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if t and t not in _FILLER}


def resolve_task_target(
    query: str,
    tasks: Sequence[TaskRecord],
) -> TargetResolutionResult:
    """Resolve a natural-language task reference against active persisted tasks."""
    active = [t for t in tasks if t.archived_at is None]
    norm_q = _normalize(query)
    if not norm_q:
        return TargetResolutionResult(
            status=TargetResolutionStatus.NOT_FOUND,
            entity_type="task",
            message="No task name was provided.",
        )

    # 1) Exact normalized match
    exact = [t for t in active if _normalize(t.title) == norm_q]
    if len(exact) == 1:
        t = exact[0]
        return TargetResolutionResult(
            status=TargetResolutionStatus.MATCHED,
            entity_type="task",
            matched_id=t.id,
            matched_label=t.title,
        )

    # 2) Core normalized match (stripping filler like "my", "task")
    q_tokens = _core_tokens(query)
    core_q = " ".join(sorted(q_tokens))
    if core_q:
        sub = [
            t
            for t in active
            if core_q in _normalize(t.title) or q_tokens.issubset(_core_tokens(t.title))
        ]
        if len(sub) == 1:
            t = sub[0]
            return TargetResolutionResult(
                status=TargetResolutionStatus.MATCHED,
                entity_type="task",
                matched_id=t.id,
                matched_label=t.title,
            )
        if len(sub) > 1:
            cands = tuple({"id": t.id, "label": t.title} for t in sub[:5])
            names = ", ".join(f'"{t.title}"' for t in sub[:5])
            return TargetResolutionResult(
                status=TargetResolutionStatus.AMBIGUOUS,
                entity_type="task",
                candidates=cands,
                message=f"Multiple tasks match '{query}': {names}. Which one do you mean?",
            )

    # 3) Substring match on raw normalized query
    raw_sub = [t for t in active if norm_q in _normalize(t.title)]
    if len(raw_sub) == 1:
        t = raw_sub[0]
        return TargetResolutionResult(
            status=TargetResolutionStatus.MATCHED,
            entity_type="task",
            matched_id=t.id,
            matched_label=t.title,
        )
    if len(raw_sub) > 1:
        cands = tuple({"id": t.id, "label": t.title} for t in raw_sub[:5])
        names = ", ".join(f'"{t.title}"' for t in raw_sub[:5])
        return TargetResolutionResult(
            status=TargetResolutionStatus.AMBIGUOUS,
            entity_type="task",
            candidates=cands,
            message=f"Multiple tasks match '{query}': {names}. Which one do you mean?",
        )

    return TargetResolutionResult(
        status=TargetResolutionStatus.NOT_FOUND,
        entity_type="task",
        message=f"I couldn't find an active task matching '{query}'.",
    )


def resolve_habit_target(
    query: str,
    habits: Sequence[HabitRecord],
) -> TargetResolutionResult:
    """Resolve a natural-language habit reference against active persisted habits."""
    active = [h for h in habits if h.archived_at is None]
    norm_q = _normalize(query)
    if not norm_q:
        return TargetResolutionResult(
            status=TargetResolutionStatus.NOT_FOUND,
            entity_type="habit",
            message="No habit name was provided.",
        )

    exact = [h for h in active if _normalize(h.name) == norm_q]
    if len(exact) == 1:
        h = exact[0]
        return TargetResolutionResult(
            status=TargetResolutionStatus.MATCHED,
            entity_type="habit",
            matched_id=h.id,
            matched_label=h.name,
        )

    q_tokens = _core_tokens(query)
    core_q = " ".join(sorted(q_tokens))
    if core_q:
        sub = [
            h
            for h in active
            if core_q in _normalize(h.name) or q_tokens.issubset(_core_tokens(h.name))
        ]
        if len(sub) == 1:
            h = sub[0]
            return TargetResolutionResult(
                status=TargetResolutionStatus.MATCHED,
                entity_type="habit",
                matched_id=h.id,
                matched_label=h.name,
            )
        if len(sub) > 1:
            cands = tuple({"id": h.id, "label": h.name} for h in sub[:5])
            names = ", ".join(f'"{h.name}"' for h in sub[:5])
            return TargetResolutionResult(
                status=TargetResolutionStatus.AMBIGUOUS,
                entity_type="habit",
                candidates=cands,
                message=f"Multiple habits match '{query}': {names}. Which one do you mean?",
            )

    raw_sub = [h for h in active if norm_q in _normalize(h.name)]
    if len(raw_sub) == 1:
        h = raw_sub[0]
        return TargetResolutionResult(
            status=TargetResolutionStatus.MATCHED,
            entity_type="habit",
            matched_id=h.id,
            matched_label=h.name,
        )
    if len(raw_sub) > 1:
        cands = tuple({"id": h.id, "label": h.name} for h in raw_sub[:5])
        names = ", ".join(f'"{h.name}"' for h in raw_sub[:5])
        return TargetResolutionResult(
            status=TargetResolutionStatus.AMBIGUOUS,
            entity_type="habit",
            candidates=cands,
            message=f"Multiple habits match '{query}': {names}. Which one do you mean?",
        )

    return TargetResolutionResult(
        status=TargetResolutionStatus.NOT_FOUND,
        entity_type="habit",
        message=f"I couldn't find an active habit matching '{query}'.",
    )
