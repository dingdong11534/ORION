"""Deterministic target resolution for tasks and habits."""

import re
from collections.abc import Sequence

from orion.application.productivity.contracts import (
    HabitRecord,
    TargetResolutionResult,
    TargetResolutionStatus,
    TaskRecord,
)

_FILLER = {"my", "the", "a", "an", "task", "habit", "one", "to", "for", "of", "on"}
_MARKDOWN_RE = re.compile(r"[*_`~#>\[\]]")


def strip_markdown(text: str) -> str:
    """Strip common Markdown formatting characters and collapse whitespace."""
    cleaned = _MARKDOWN_RE.sub("", text or "")
    return " ".join(cleaned.split()).strip()


def _normalize(text: str) -> str:
    no_md = _MARKDOWN_RE.sub(" ", text or "")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", no_md.lower())
    return " ".join(cleaned.split())


def _core_tokens(text: str) -> list[str]:
    return [t for t in _normalize(text).split() if t and t not in _FILLER]


def _edit_distance_is_one(a: str, b: str) -> bool:
    """Return True iff Levenshtein edit distance between a and b is <= 1 (and a != b)."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(ca != cb for ca, cb in zip(a, b, strict=True)) == 1
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    # la + 1 == lb
    i = j = diffs = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diffs += 1
            if diffs > 1:
                return False
            j += 1
    return True


def _resolve_items(
    query: str,
    items: Sequence[tuple[str, str]],  # (id, label)
    *,
    entity_type: str,
) -> TargetResolutionResult:
    norm_q = _normalize(query)
    if not norm_q:
        return TargetResolutionResult(
            status=TargetResolutionStatus.NOT_FOUND,
            entity_type=entity_type,
            message=f"No {entity_type} name was provided.",
        )

    # 1) Exact normalized match
    exact = [(eid, lbl) for eid, lbl in items if _normalize(lbl) == norm_q]
    if len(exact) == 1:
        return TargetResolutionResult(
            status=TargetResolutionStatus.MATCHED,
            entity_type=entity_type,
            matched_id=exact[0][0],
            matched_label=exact[0][1],
        )
    if len(exact) > 1:
        return _ambiguous_result(query, exact, entity_type)

    q_tokens = _core_tokens(query)
    q_set = set(q_tokens)

    # 2) All query tokens contained in title tokens
    if q_set:
        contained = [
            (eid, lbl) for eid, lbl in items if q_set.issubset(set(_normalize(lbl).split()))
        ]
        if len(contained) == 1:
            return TargetResolutionResult(
                status=TargetResolutionStatus.MATCHED,
                entity_type=entity_type,
                matched_id=contained[0][0],
                matched_label=contained[0][1],
            )
        if len(contained) > 1:
            return _ambiguous_result(query, contained, entity_type)

        # 3) Unique prefix match (every query token is a prefix of some title token, or norm prefix)
        prefix_matches = []
        for eid, lbl in items:
            lbl_norm = _normalize(lbl)
            lbl_tokens = lbl_norm.split()
            if lbl_norm.startswith(norm_q) or all(
                any(lt.startswith(qt) for lt in lbl_tokens) for qt in q_tokens
            ):
                prefix_matches.append((eid, lbl))
        if len(prefix_matches) == 1:
            return TargetResolutionResult(
                status=TargetResolutionStatus.MATCHED,
                entity_type=entity_type,
                matched_id=prefix_matches[0][0],
                matched_label=prefix_matches[0][1],
            )
        if len(prefix_matches) > 1:
            return _ambiguous_result(query, prefix_matches, entity_type)

        # 4) Typo tolerance ONLY for single token of >= 5 chars at edit distance 1
        if len(q_tokens) == 1 and len(q_tokens[0]) >= 5:
            qt = q_tokens[0]
            typo_matches = [
                (eid, lbl)
                for eid, lbl in items
                if any(_edit_distance_is_one(qt, lt) for lt in _normalize(lbl).split())
            ]
            if len(typo_matches) == 1:
                return TargetResolutionResult(
                    status=TargetResolutionStatus.MATCHED,
                    entity_type=entity_type,
                    matched_id=typo_matches[0][0],
                    matched_label=typo_matches[0][1],
                )
            if len(typo_matches) > 1:
                return _ambiguous_result(query, typo_matches, entity_type)

    offer = " Would you like me to create one?" if entity_type == "task" else ""
    return TargetResolutionResult(
        status=TargetResolutionStatus.NOT_FOUND,
        entity_type=entity_type,
        message=f"I couldn't find an active {entity_type} matching '{query}'.{offer}",
    )


def _ambiguous_result(
    query: str, matches: list[tuple[str, str]], entity_type: str
) -> TargetResolutionResult:
    top = matches[:10]
    cands = tuple({"id": eid, "label": lbl} for eid, lbl in top)
    numbered = "\n".join(f"{idx}. {lbl}" for idx, (_, lbl) in enumerate(top, start=1))
    return TargetResolutionResult(
        status=TargetResolutionStatus.AMBIGUOUS,
        entity_type=entity_type,
        candidates=cands,
        message=f"Multiple {entity_type}s match '{query}':\n{numbered}\nWhich one do you mean?",
    )


def resolve_task_target(
    query: str,
    tasks: Sequence[TaskRecord],
) -> TargetResolutionResult:
    """Resolve a natural-language task reference against active persisted tasks."""
    active = [(t.id, t.title) for t in tasks if t.archived_at is None]
    return _resolve_items(query, active, entity_type="task")


def resolve_habit_target(
    query: str,
    habits: Sequence[HabitRecord],
) -> TargetResolutionResult:
    """Resolve a natural-language habit reference against active persisted habits."""
    active = [(h.id, h.name) for h in habits if h.archived_at is None]
    return _resolve_items(query, active, entity_type="habit")
