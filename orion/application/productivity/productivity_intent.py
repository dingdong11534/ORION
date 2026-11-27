"""Constrained natural-language productivity intent interpreter."""

import re

from orion.application.productivity.contracts import (
    HabitFrequency,
    ProductivityIntent,
    ProductivityProposal,
    TaskPriority,
    TaskStatus,
)

_PRIORITY_MAP = {
    "critical": TaskPriority.CRITICAL,
    "urgent": TaskPriority.CRITICAL,
    "high": TaskPriority.HIGH,
    "medium": TaskPriority.MEDIUM,
    "normal": TaskPriority.MEDIUM,
    "low": TaskPriority.LOW,
}

_NUM_WORDS = {
    "one": 1,
    "once": 1,
    "two": 2,
    "twice": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_DATE_TAIL_RE = re.compile(
    r"\s+(?:"
    r"(?:(?:by|for|on|until|due(?:\s+on)?|to)\s+)?"
    r"(?:day\s+after\s+tomorrow"
    r"|tomorrow(?:\s+(?:morning|afternoon|evening|night|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?"
    r"|today(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
    r"|tonight"
    r"|in\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|fourteen|thirty)\s+days?"
    r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))"
    r"|(?:by|for|on|until|due(?:\s+on)?|to)\s+"
    r"(?:(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|end\s+of\s+(?:this|the)\s+week"
    r"|\d{4}-\d{2}-\d{2}"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)

_PRIORITY_TAIL_RE = re.compile(
    r"\s+(?:with\s+|as\s+)?(critical|urgent|high|medium|normal|low)\s+priority\s*[.!?]*$",
    re.IGNORECASE,
)

_NEGATIVE_GUARD_RE = re.compile(
    r"^(?:"
    r"what\s+(?:is|are)\s+(?:a\s+)?(?:task|habit)|"
    r"why\s+(?:do|should|are|is)|"
    r"how\s+(?:do|does)\s+(?:people|you|streaks?)|"
    r"if\s+i\s+|"
    r"maybe\s+i\s+should|"
    r"i\s+(?:might|could|should\s+probably|wish\s+i)|"
    r"explain\s+|tell\s+me\s+about\s+|"
    r"do\s+you\s+think"
    r")\b",
    re.IGNORECASE,
)


def _clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip(" .!?,;:\"'")).strip()


def _extract_priority(text: str) -> tuple[TaskPriority | None, str]:
    m = _PRIORITY_TAIL_RE.search(text)
    if m:
        pr = _PRIORITY_MAP.get(m.group(1).lower())
        return pr, text[: m.start()].strip()
    m_inline = re.search(r"\b(critical|urgent|high|medium|low)\s+priority\b", text, re.IGNORECASE)
    if m_inline:
        pr = _PRIORITY_MAP.get(m_inline.group(1).lower())
        stripped = (text[: m_inline.start()] + " " + text[m_inline.end() :]).strip()
        return pr, stripped
    return None, text


def _extract_due(text: str) -> tuple[str | None, str]:
    m = _DATE_TAIL_RE.search(text)
    if m:
        matched = m.group(0).strip(" .!?")
        matched = re.sub(
            r"^(?:by|for|on|until|due(?:\s+on)?|to)\s+", "", matched, flags=re.IGNORECASE
        )
        return matched.strip(), text[: m.start()].strip()
    return None, text


def _extract_freq_target(text: str) -> tuple[HabitFrequency | None, int | None]:
    low = text.lower()
    freq: HabitFrequency | None = None
    target: int | None = None
    if "daily" in low or "every day" in low or "each day" in low or "per day" in low:
        freq = HabitFrequency.DAILY
        target = 1
    elif "weekly" in low or "per week" in low or "a week" in low or "every week" in low:
        freq = HabitFrequency.WEEKLY
    elif "monthly" in low or "per month" in low or "a month" in low or "every month" in low:
        freq = HabitFrequency.MONTHLY

    m_num = re.search(
        r"\b(\d+|one|once|two|twice|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:times?|days?)\s+(?:per|a|each)\s+(?:week|month)\b",
        low,
    )
    if m_num:
        tok = m_num.group(1)
        target = int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 1)
    elif freq in (HabitFrequency.WEEKLY, HabitFrequency.MONTHLY) and target is None:
        if "twice" in low:
            target = 2
        elif "once" in low:
            target = 1
        else:
            target = 1
    return freq, target


def _uses_pronoun(target: str) -> bool:
    return target.lower().strip() in {
        "it",
        "that",
        "this",
        "that task",
        "this task",
        "the task",
        "that habit",
        "this habit",
        "the habit",
        "that completion",
    }


class ProductivityIntentInterpreter:
    """Constrained deterministic interpreter producing strict ProductivityProposal contracts.

    Never executes actions directly, never accepts database IDs or tool names, and ignores
    non-command conversational messages, hypotheticals, and untrusted document content.
    """

    def interpret(self, message: str) -> ProductivityProposal:
        raw = (message or "").strip()
        if not raw or len(raw) > 500:
            return ProductivityProposal(intent=ProductivityIntent.NONE)

        low = raw.lower()
        # Reject questions/hypotheticals/conversational mentions that are not commands
        if _NEGATIVE_GUARD_RE.match(low):
            return ProductivityProposal(intent=ProductivityIntent.NONE)

        # Reject if it looks like untrusted pasted excerpts / prompt injection
        if "=== begin untrusted" in low or "ignore previous instructions" in low:
            return ProductivityProposal(intent=ProductivityIntent.NONE)

        # 1) HABIT UNDO COMPLETION
        m_undo = re.match(
            r"^(?:undo|remove|revert|delete)\s+(?:today'?s\s+)?(?:the\s+|my\s+)?(.+?)\s+"
            r"(?:habit\s+)?completion[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_undo:
            target = _clean_title(m_undo.group(1))
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_UNDO_COMPLETION,
                target_query=None if _uses_pronoun(target) else target,
                uses_context_reference=_uses_pronoun(target),
            )
        if re.match(r"^undo\s+(?:that|the)\s+completion[.!?]*$", low):
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_UNDO_COMPLETION,
                uses_context_reference=True,
            )

        # 2) HABIT STATISTICS / STREAK
        if re.search(
            r"\b(?:streak|consistent|consistency|habit\s+stats|habit\s+statistics|habit\s+progress)\b",
            low,
        ):
            m_st = re.search(
                r"(?:with\s+my\s+|my\s+current\s+|current\s+|my\s+)(.+?)\s+(?:habit|streak|this\s+month|this\s+week)",
                low,
            )
            target = _clean_title(m_st.group(1)) if m_st else ""
            if target == "workouts":
                target = "workout"
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_STATISTICS,
                target_query=(
                    target
                    if target
                    and target
                    not in {
                        "my",
                        "habit",
                        "all",
                        "habit statistics",
                        "habit stats",
                        "habit progress",
                    }
                    else None
                ),
            )

        # 3) HABIT LIST
        if re.match(
            r"^(?:show|list|view|what\s+are)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?habits\b",
            low,
        ):
            return ProductivityProposal(intent=ProductivityIntent.HABIT_LIST)

        # 4) HABIT CREATE
        m_h_adj = re.match(
            r"^(?:create|add|start|new)\s+(?:a\s+)?(daily|weekly|monthly)\s+(.+?)\s+habit[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_h_adj:
            freq = HabitFrequency(m_h_adj.group(1).lower())
            name = _clean_title(m_h_adj.group(2))
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_CREATE,
                title=name,
                frequency=freq,
                target_count=1,
            )

        m_h_create = re.match(
            r"^(?:create|add|start|new)\s+(?:a\s+)?(?:(daily|weekly|monthly)\s+)?habit\s*(?:to|for|:)?\s*(.+?)[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_h_create:
            freq_word = (m_h_create.group(1) or "").lower()
            rest = m_h_create.group(2).strip()
            freq, target = _extract_freq_target(rest)
            if freq_word:
                freq = HabitFrequency(freq_word)
                target = target or 1
            name = re.sub(
                r"\b(?:every\s+day|daily|weekly|monthly|"
                r"(?:\d+|one|once|two|twice|three|four|five|six|seven|eight|nine|ten)\s+"
                r"(?:(?:times?|days?)\s+)?(?:per|a|each)\s+(?:week|month))\b",
                "",
                rest,
                flags=re.IGNORECASE,
            )
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_CREATE,
                title=_clean_title(name),
                frequency=freq or HabitFrequency.DAILY,
                target_count=target or 1,
            )
        if re.search(r"\bcreate\s+a\s+habit\b", low):
            # e.g. "I want to read for 30 minutes every day. Create a habit."
            freq, target = _extract_freq_target(raw)
            body = re.sub(r"\.?\s*create\s+a\s+habit[.!?]*$", "", raw, flags=re.IGNORECASE)
            body = re.sub(r"^i\s+want\s+to\s+", "", body, flags=re.IGNORECASE)
            body = re.sub(
                r"\b(?:for\s+\d+\s+\w+\s+)?(?:every\s+day|daily|weekly|monthly)\b",
                "",
                body,
                flags=re.IGNORECASE,
            )
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_CREATE,
                title=_clean_title(body) or "New Habit",
                frequency=freq or HabitFrequency.DAILY,
                target_count=target or 1,
            )

        # 5) HABIT COMPLETE
        m_h_done = re.match(
            r"^(?:i\s+(?:completed|finished|did|logged)\s+(?:my\s+|the\s+)?|log\s+(?:completion\s+(?:for|of)\s+)?(?:my\s+|the\s+)?)(.+?)(?:\s+habit)?(?:\s+today)?[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_h_done and (
            "habit" in low or "completed my" in low or "did my" in low or low.startswith("log ")
        ):
            target = _clean_title(m_h_done.group(1))
            if target and not _uses_pronoun(target):
                return ProductivityProposal(
                    intent=ProductivityIntent.HABIT_COMPLETE,
                    target_query=target,
                )

        # 6) HABIT UPDATE
        m_h_upd = re.match(
            r"^(?:change|update|increase|decrease|set)\s+(?:my\s+|the\s+)?(.+?)\s+habit(?:\s+target)?\s+to\s+(.+?)[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_h_upd:
            target = _clean_title(m_h_upd.group(1))
            spec = m_h_upd.group(2).strip()
            freq, count = _extract_freq_target(spec)
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_UPDATE,
                target_query=target,
                frequency=freq,
                target_count=count,
            )

        # 7) ARCHIVE (Level 3)
        m_arch_t = re.match(r"^archive\s+(?:the\s+|my\s+)?task\s+(.+?)[.!?]*$", raw, re.IGNORECASE)
        if m_arch_t:
            tq = _clean_title(m_arch_t.group(1))
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_ARCHIVE,
                target_query=None if _uses_pronoun(tq) else tq,
                uses_context_reference=_uses_pronoun(tq),
            )
        m_arch_h = re.match(r"^archive\s+(?:the\s+|my\s+)?habit\s+(.+?)[.!?]*$", raw, re.IGNORECASE)
        if m_arch_h:
            hq = _clean_title(m_arch_h.group(1))
            return ProductivityProposal(
                intent=ProductivityIntent.HABIT_ARCHIVE,
                target_query=None if _uses_pronoun(hq) else hq,
                uses_context_reference=_uses_pronoun(hq),
            )

        # 8) TASK LIST
        m_t_list = re.match(
            r"^(?:show|list|what\s+are|view)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(.+?)\s*tasks?\b",
            low,
        )
        if m_t_list or re.match(r"^(?:show|list)\s+(?:me\s+)?my\s+tasks\b", low):
            bucket = None
            if "today" in low:
                bucket = "today"
            elif "overdue" in low:
                bucket = "overdue"
            elif "upcoming" in low:
                bucket = "upcoming"
            elif "completed" in low or "done" in low:
                bucket = "completed"
            return ProductivityProposal(intent=ProductivityIntent.TASK_LIST, task_bucket=bucket)

        # 9) TASK COMPLETE / REOPEN / CANCEL
        m_t_done = re.match(
            r"^(?:mark|set|complete|finish)\s+(.+?)(?:\s+as)?\s*(?:complete|completed|done)?[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_t_done and (
            "complete" in low or "done" in low or low.startswith(("complete ", "finish "))
        ):
            target = _clean_title(
                re.sub(
                    r"\s+as\s+(?:complete|completed|done)$",
                    "",
                    m_t_done.group(1),
                    flags=re.IGNORECASE,
                )
            )
            use_ctx = _uses_pronoun(target)
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_COMPLETE,
                target_query=None if use_ctx else target,
                uses_context_reference=use_ctx,
                status=TaskStatus.DONE,
            )

        m_t_reopen = re.match(r"^reopen\s+(?:the\s+|my\s+)?(.+?)[.!?]*$", raw, re.IGNORECASE)
        if m_t_reopen:
            target = _clean_title(m_t_reopen.group(1))
            use_ctx = _uses_pronoun(target)
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_REOPEN,
                target_query=None if use_ctx else target,
                uses_context_reference=use_ctx,
                status=TaskStatus.TODO,
            )

        m_t_cancel = re.match(r"^cancel\s+(?:the\s+|my\s+)?task\s+(.+?)[.!?]*$", raw, re.IGNORECASE)
        if m_t_cancel:
            target = _clean_title(m_t_cancel.group(1))
            use_ctx = _uses_pronoun(target)
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_CANCEL,
                target_query=None if use_ctx else target,
                uses_context_reference=use_ctx,
                status=TaskStatus.CANCELLED,
            )

        # 10) TASK CREATE
        m_t_create = re.match(
            r"^(?:add|create|new)\s+(?:a\s+)?(?:(critical|urgent|high|medium|low)\s+priority\s+)?task(?:\s+to)?\s*:?\s*(.+?)[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_t_create:
            lead_pr = _PRIORITY_MAP.get((m_t_create.group(1) or "").lower())
            body = m_t_create.group(2).strip()
            pr, body = _extract_priority(body)
            due_expr, body = _extract_due(body)
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_CREATE,
                title=_clean_title(body),
                priority=lead_pr or pr or TaskPriority.MEDIUM,
                due_expression=due_expr,
            )

        # 11) TASK UPDATE (priority / deadline / rename)
        m_make_pr = re.match(
            r"^(?:make|set)\s+(?:the\s+|my\s+)?(.+?)\s+(?:task\s+)?(?:to\s+)?(critical|urgent|high|medium|low)\s+priority[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_make_pr:
            target = _clean_title(m_make_pr.group(1))
            pr = _PRIORITY_MAP.get(m_make_pr.group(2).lower())
            use_ctx = _uses_pronoun(target)
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_UPDATE,
                target_query=None if use_ctx else target,
                uses_context_reference=use_ctx,
                priority=pr,
            )

        m_move = re.match(
            r"^(?:move|reschedule|postpone|set\s+deadline\s+(?:for|of))\s+(?:the\s+|my\s+)?(.+?)\s+(?:to|for)\s+(.+?)[.!?]*$",
            raw,
            re.IGNORECASE,
        )
        if m_move:
            target = _clean_title(m_move.group(1))
            due_expr = m_move.group(2).strip()
            use_ctx = _uses_pronoun(target)
            return ProductivityProposal(
                intent=ProductivityIntent.TASK_UPDATE,
                target_query=None if use_ctx else target,
                uses_context_reference=use_ctx,
                due_expression=due_expr,
            )

        return ProductivityProposal(intent=ProductivityIntent.NONE)
