"""Shared Today overview query used by Productivity UI, Chat commands, and tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from orion.application.productivity.contracts import (
    PRIORITY_ORDER,
    HabitRecord,
    HabitStatistics,
    TaskFilter,
    TaskRecord,
    TaskStatus,
)
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.ports import ClockPort
from orion.application.productivity.task_service import TaskService


@dataclass(frozen=True)
class TodayOverview:
    """Consistent snapshot of tasks and habits for the user's local Today."""

    overdue: list[TaskRecord]
    due_today: list[TaskRecord]
    unscheduled: list[TaskRecord]
    upcoming: list[TaskRecord]
    completed_today: list[TaskRecord]
    habits: list[tuple[HabitRecord, HabitStatistics]]


def _is_task_overdue(task: TaskRecord, now_utc: datetime, tz: tzinfo) -> bool:
    if task.due_at is None or task.status not in (TaskStatus.TODO, TaskStatus.IN_PROGRESS):
        return False
    local_today = now_utc.astimezone(tz).date()
    local_due_date = task.due_at.astimezone(tz).date()
    if task.due_is_date_only:
        return local_due_date < local_today
    return task.due_at < now_utc


def build_today_overview(
    task_service: TaskService,
    habit_service: HabitService,
    clock: ClockPort,
) -> TodayOverview:
    """Compute Today's tasks and habits in the user's configured local timezone."""
    now_local = clock.now()
    tz = clock.timezone
    now_utc = now_local.astimezone(UTC)
    local_today = now_local.astimezone(tz).date()

    all_tasks = list(task_service.list_tasks(TaskFilter(include_archived=False)))

    overdue: list[TaskRecord] = []
    due_today: list[TaskRecord] = []
    unscheduled: list[TaskRecord] = []
    upcoming: list[TaskRecord] = []
    completed_today: list[TaskRecord] = []

    for t in all_tasks:
        if t.status == TaskStatus.DONE:
            if t.completed_at is not None and t.completed_at.astimezone(tz).date() == local_today:
                completed_today.append(t)
            continue
        if t.status not in (TaskStatus.TODO, TaskStatus.IN_PROGRESS):
            continue
        if t.due_at is None:
            unscheduled.append(t)
            continue
        if _is_task_overdue(t, now_utc, tz):
            overdue.append(t)
            continue
        local_due_date = t.due_at.astimezone(tz).date()
        if local_due_date == local_today:
            due_today.append(t)
        elif local_due_date > local_today:
            upcoming.append(t)

    def _task_sort_key(t: TaskRecord) -> tuple:
        return (
            PRIORITY_ORDER[t.priority],
            t.due_at is None,
            t.due_at or datetime.max.replace(tzinfo=UTC),
        )

    overdue.sort(key=_task_sort_key)
    due_today.sort(key=_task_sort_key)
    unscheduled.sort(key=_task_sort_key)
    upcoming.sort(key=_task_sort_key)
    completed_today.sort(key=lambda t: t.completed_at or t.updated_at, reverse=True)

    habits_out: list[tuple[HabitRecord, HabitStatistics]] = []
    for h in habit_service.list_habits():
        habits_out.append((h, habit_service.get_habit_statistics(h.id)))

    return TodayOverview(
        overdue=overdue,
        due_today=due_today,
        unscheduled=unscheduled,
        upcoming=upcoming,
        completed_today=completed_today,
        habits=habits_out,
    )


def _fmt_task_line(t: TaskRecord, tz: tzinfo) -> str:
    due_s = ""
    if t.due_at is not None:
        local_dt = t.due_at.astimezone(tz)
        if t.due_is_date_only:
            due_s = f" · Due {local_dt.strftime('%a, %b %-d')}"
        else:
            due_s = f" · Due {local_dt.strftime('%a, %b %-d at %-I:%M %p')}"
    return f"- [{t.priority.value.upper()}] {t.title} ({t.status.value}){due_s}"


def format_today_chat_summary(overview: TodayOverview, tz: tzinfo) -> str:
    """Format TodayOverview for chat: lists every non-empty section and unscheduled count."""
    sections: list[str] = []

    if overview.overdue:
        lines = [_fmt_task_line(t, tz) for t in overview.overdue[:10]]
        sections.append("Overdue:\n" + "\n".join(lines))

    if overview.due_today:
        lines = [_fmt_task_line(t, tz) for t in overview.due_today[:10]]
        sections.append("Due today:\n" + "\n".join(lines))
    else:
        sections.append("No tasks due today.")

    if overview.unscheduled:
        lines = [_fmt_task_line(t, tz) for t in overview.unscheduled[:10]]
        sections.append(f"Unscheduled ({len(overview.unscheduled)}):\n" + "\n".join(lines))

    if overview.upcoming:
        lines = [_fmt_task_line(t, tz) for t in overview.upcoming[:5]]
        sections.append(f"Upcoming ({len(overview.upcoming)}):\n" + "\n".join(lines))

    if overview.completed_today:
        lines = [f"- {t.title}" for t in overview.completed_today[:5]]
        sections.append(f"Completed today ({len(overview.completed_today)}):\n" + "\n".join(lines))

    if overview.habits:
        h_lines = []
        for h, st in overview.habits:
            mark = "Done today" if st.completed_today else "Pending today"
            h_lines.append(
                f"- {h.name} ({h.frequency.value}) · Streak: {st.current_streak} · {mark}"
            )
        sections.append("Today's habits:\n" + "\n".join(h_lines))

    return "\n\n".join(sections)
