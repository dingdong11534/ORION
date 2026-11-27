"""Grounded DailyBriefingService using actual tasks, habits, memory, and activity."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from orion.application.productivity.contracts import PRIORITY_ORDER, TaskFilter, TaskStatus
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.ports import ClockPort
from orion.application.productivity.task_service import TaskService
from orion.application.productivity.today_query import build_today_overview
from orion.infrastructure.persistence.models import ActivityEventModel

_BRIEFING_RE = re.compile(
    r"(?:"
    r"what\s+should\s+i\s+work\s+on\s+today|"
    r"plan\s+my\s+(?:day|afternoon|morning|evening)|"
    r"what\s+have\s+i\s+completed\s+this\s+week|"
    r"what(?:'s|\s+is)\s+my\s+next\s+(?:important\s+)?deadline|"
    r"summarize\s+what\s+i\s+have\s+been\s+working\s+on"
    r")",
    re.IGNORECASE,
)


class DailyBriefingService:
    """Answers daily planning/summary questions from real persisted records."""

    def __init__(
        self,
        *,
        task_service: TaskService,
        habit_service: HabitService,
        clock: ClockPort,
        session_factory: sessionmaker,
        memory_service: Any = None,
    ) -> None:
        self._tasks = task_service
        self._habits = habit_service
        self._clock = clock
        self._sf = session_factory
        self._memory = memory_service

    def is_briefing_query(self, message: str) -> bool:
        return bool(_BRIEFING_RE.search((message or "").strip()))

    def answer(self, message: str) -> str:
        low = (message or "").lower()
        tz = self._clock.timezone
        local_today = self._clock.today()
        ov = build_today_overview(self._tasks, self._habits, self._clock)

        if "completed this week" in low:
            week_start = local_today - timedelta(days=local_today.weekday())
            all_tasks = list(self._tasks.list_tasks(TaskFilter(include_archived=False)))
            done_week = [
                t
                for t in all_tasks
                if t.status == TaskStatus.DONE
                and t.completed_at is not None
                and t.completed_at.astimezone(tz).date() >= week_start
            ]
            lines = [f"- {t.title}" for t in done_week]
            if not lines:
                return f"You have not completed any tasks since {week_start.strftime('%b %-d')}."
            return f"Completed this week (since {week_start.strftime('%b %-d')}):\n" + "\n".join(
                lines
            )

        if "next" in low and "deadline" in low:
            all_tasks = [
                t
                for t in self._tasks.list_tasks(TaskFilter(include_archived=False))
                if t.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS) and t.due_at is not None
            ]
            all_tasks.sort(key=lambda t: t.due_at or datetime.max.replace(tzinfo=UTC))
            if not all_tasks:
                return "You have no active tasks with upcoming deadlines."
            nxt = all_tasks[0]
            due_s = nxt.due_at.astimezone(tz).strftime("%a, %b %-d at %-I:%M %p")
            return f"Next deadline: {nxt.title} (Priority: {nxt.priority.value.capitalize()}, Due: {due_s})."

        if "working on" in low:
            parts: list[str] = []
            if self._memory is not None:
                from orion.application.memory.contracts import MemoryCategory

                projs = self._memory.list_memories(category=MemoryCategory.PROJECT)
                if projs:
                    parts.append(
                        "Saved projects:\n"
                        + "\n".join(f"- {p.title}: {p.content}" for p in projs[:5])
                    )
            with self._sf() as session:
                evts = (
                    session.execute(
                        select(ActivityEventModel)
                        .order_by(ActivityEventModel.created_at.desc())
                        .limit(8)
                    )
                    .scalars()
                    .all()
                )
            if evts:
                parts.append(
                    "Recent activity:\n"
                    + "\n".join(
                        f"- {e.event_type.replace('_', ' ')} ({e.created_at.astimezone(tz).strftime('%b %-d, %-I:%M %p')})"
                        for e in evts[:6]
                    )
                )
            return "\n\n".join(parts) if parts else "No recent projects or activity recorded yet."

        # Default: Today / afternoon plan
        prioritized = ov.overdue + ov.due_today + ov.unscheduled
        prioritized.sort(key=lambda t: PRIORITY_ORDER[t.priority])
        lines: list[str] = []
        if prioritized:
            lines.append(
                "Priority tasks:\n"
                + "\n".join(f"- [{t.priority.value.upper()}] {t.title}" for t in prioritized[:6])
            )
        else:
            lines.append("No pending tasks right now.")

        pending_habits = [h.name for h, st in ov.habits if not st.completed_today]
        if pending_habits:
            lines.append("Pending habits today: " + ", ".join(pending_habits))
        return "\n\n".join(lines)
