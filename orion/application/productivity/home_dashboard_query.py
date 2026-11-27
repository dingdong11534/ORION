"""Read-only HomeDashboardQuery aggregating real SQLite tasks, habits, activity, and memory."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from orion.application.productivity.contracts import TaskFilter, TaskStatus
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.ports import ClockPort
from orion.application.productivity.task_service import TaskService
from orion.application.productivity.today_query import build_today_overview
from orion.infrastructure.persistence.models import ActivityEventModel


def _greeting_for_hour(hour: int, preferred_name: str | None = None) -> str:
    if hour < 12:
        base = "Good morning"
    elif hour < 17:
        base = "Good afternoon"
    else:
        base = "Good evening"
    return f"{base}, {preferred_name}" if preferred_name else base


def build_home_dashboard(
    *,
    task_service: TaskService,
    habit_service: HabitService,
    clock: ClockPort,
    session_factory: sessionmaker,
    conversation_store: Any = None,
    knowledge_service: Any = None,
    memory_service: Any = None,
) -> dict[str, Any]:
    """Assemble genuine local Home dashboard metrics from SQLite."""
    now_local = clock.now()
    tz = clock.timezone
    local_today = clock.today()

    preferred_name = None
    projects: list[dict[str, str]] = []
    if memory_service is not None:
        prof = memory_service.get_profile()
        preferred_name = prof.preferred_name
        from orion.application.memory.contracts import MemoryCategory

        mems = memory_service.list_memories(category=MemoryCategory.PROJECT)
        projects = [{"id": m.id, "title": m.title, "content": m.content} for m in mems[:5]]

    overview = build_today_overview(task_service, habit_service, clock)
    all_tasks = list(task_service.list_tasks(TaskFilter(include_archived=False)))

    # 1. Task status distribution
    status_counts = {s.value: 0 for s in TaskStatus}
    for t in all_tasks:
        status_counts[t.status.value] = status_counts.get(t.status.value, 0) + 1

    # 2. Weekly task completion & daily activity (last 7 local days)
    day_labels: list[str] = []
    day_dates: list[Any] = []
    for offset in range(6, -1, -1):
        d = local_today - timedelta(days=offset)
        day_dates.append(d)
        day_labels.append(d.strftime("%a"))

    weekly_tasks_done = [0] * 7
    for t in all_tasks:
        if t.status == TaskStatus.DONE and t.completed_at is not None:
            cd = t.completed_at.astimezone(tz).date()
            if cd in day_dates:
                weekly_tasks_done[day_dates.index(cd)] += 1

    # 3. Habit 7-day and 30-day completion rates
    habit_rates: list[dict[str, Any]] = []
    for h, st in overview.habits:
        comps = habit_service._store.list_completions(h.id, limit=120)
        comp_dates = {c.completed_date for c in comps}
        d7 = sum(1 for off in range(7) if (local_today - timedelta(days=off)) in comp_dates)
        d30 = sum(1 for off in range(30) if (local_today - timedelta(days=off)) in comp_dates)
        habit_rates.append(
            {
                "id": h.id,
                "name": h.name,
                "completed_today": st.completed_today,
                "current_streak": st.current_streak,
                "rate_7d": round((d7 / 7) * 100),
                "rate_30d": round((d30 / 30) * 100),
            }
        )

    # 4. Recent activity events & 7-day daily activity counts
    daily_activity = [0] * 7
    recent_activity: list[dict[str, str]] = []
    week_start_utc = datetime.combine(day_dates[0], datetime.min.time(), tzinfo=tz).astimezone(UTC)
    with session_factory() as session:
        events = (
            session.execute(
                select(ActivityEventModel).order_by(ActivityEventModel.created_at.desc()).limit(50)
            )
            .scalars()
            .all()
        )
        for ev in events:
            ev_dt = ev.created_at if ev.created_at.tzinfo else ev.created_at.replace(tzinfo=UTC)
            if ev_dt >= week_start_utc:
                ev_day = ev_dt.astimezone(tz).date()
                if ev_day in day_dates:
                    daily_activity[day_dates.index(ev_day)] += 1
            if len(recent_activity) < 10:
                recent_activity.append(
                    {
                        "id": ev.id,
                        "event_type": ev.event_type,
                        "aggregate_type": ev.aggregate_type,
                        "time_display": ev_dt.astimezone(tz).strftime("%b %-d, %-I:%M %p"),
                    }
                )

    recent_conversations: list[dict[str, str]] = []
    if conversation_store is not None:
        for c in list(conversation_store.list_conversations(limit=5)):
            recent_conversations.append({"id": c.id, "title": c.title})

    recent_documents: list[dict[str, str]] = []
    if knowledge_service is not None:
        try:
            for d in list(knowledge_service.list_sources())[:5]:
                recent_documents.append({"id": d.id, "title": d.display_name})
        except Exception:  # noqa: BLE001
            recent_documents = []

    summary_parts = [
        f"{len(overview.due_today)} due today",
        f"{len(overview.overdue)} overdue",
        f"{len(overview.unscheduled)} unscheduled",
        f"{sum(1 for _, st in overview.habits if st.completed_today)}/{len(overview.habits)} habits done",
    ]

    return {
        "greeting": _greeting_for_hour(now_local.hour, preferred_name),
        "date_display": now_local.strftime("%A, %B %-d, %Y"),
        "summary": " · ".join(summary_parts),
        "counts": {
            "due_today": len(overview.due_today),
            "overdue": len(overview.overdue),
            "unscheduled": len(overview.unscheduled),
            "completed_today": len(overview.completed_today),
            "upcoming": len(overview.upcoming),
        },
        "due_today_items": [
            {"id": t.id, "title": t.title, "priority": t.priority.value}
            for t in overview.due_today[:6]
        ],
        "overdue_items": [
            {"id": t.id, "title": t.title, "priority": t.priority.value}
            for t in overview.overdue[:6]
        ],
        "upcoming_items": [
            {
                "id": t.id,
                "title": t.title,
                "due": t.due_at.astimezone(tz).strftime("%a, %b %-d") if t.due_at else "",
            }
            for t in overview.upcoming[:6]
        ],
        "habits": habit_rates,
        "charts": {
            "day_labels": day_labels,
            "weekly_tasks_done": weekly_tasks_done,
            "daily_activity": daily_activity,
            "status_distribution": status_counts,
            "date_range": f"{day_dates[0].strftime('%b %-d')} – {day_dates[-1].strftime('%b %-d')}",
        },
        "recent_activity": recent_activity,
        "recent_conversations": recent_conversations,
        "recent_documents": recent_documents,
        "projects": projects,
    }
