"""Read-only HabitStatsQuery reusing HabitService and zoneinfo local calendar dates."""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from orion.application.productivity.contracts import HabitFrequency, HabitRecord
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.ports import ClockPort


@dataclass(frozen=True)
class HabitMonthStats:
    habit_id: str
    habit_name: str
    year: int
    month: int
    days_in_month: int
    elapsed_days: int
    completed_dates: tuple[str, ...]
    scheduled_days: int
    current_streak: int
    longest_streak: int
    completion_percent: int


@dataclass(frozen=True)
class DailyCompletionPoint:
    day: int
    date_iso: str
    scheduled_count: int
    completed_count: int
    ratio: float


class HabitStatsQuery:
    """Computes read-only calendar and year-ring statistics from HabitService."""

    def __init__(self, habit_service: HabitService, clock: ClockPort) -> None:
        self._habits = habit_service
        self._clock = clock

    def get_habit_month_stats(self, habit_id: str, year: int, month: int) -> HabitMonthStats | None:
        habit = self._habits.get_habit(habit_id)
        if habit is None:
            return None

        local_today = self._clock.today()
        days_in_month = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)

        if (year, month) < (local_today.year, local_today.month):
            elapsed_days = days_in_month
        elif (year, month) > (local_today.year, local_today.month):
            elapsed_days = 0
        else:
            elapsed_days = local_today.day

        completions = self._habits._store.list_completions(habit.id, limit=1000)
        completed_in_month = sorted(
            {c.completed_date for c in completions if month_start <= c.completed_date <= month_end}
        )
        # Scheduled days in elapsed period (respecting habit creation date in local tz)
        created_local = habit.created_at.astimezone(self._clock.timezone).date()
        if elapsed_days == 0:
            scheduled_days = 0
        else:
            eval_end = date(year, month, elapsed_days)
            eff_start = max(month_start, created_local)
            if eff_start > eval_end:
                scheduled_days = 0
            elif habit.frequency == HabitFrequency.DAILY:
                scheduled_days = (eval_end - eff_start).days + 1
            elif habit.frequency == HabitFrequency.WEEKLY:
                weeks = max(1, ((eval_end - eff_start).days + 7) // 7)
                scheduled_days = min((eval_end - eff_start).days + 1, weeks * habit.target_count)
            else:
                scheduled_days = min(elapsed_days, habit.target_count)

        svc_stats = self._habits.get_habit_statistics(habit.id)
        denom = max(1, scheduled_days) if scheduled_days > 0 else 0
        pct = min(100, round((len(completed_in_month) / denom) * 100)) if denom > 0 else 0

        return HabitMonthStats(
            habit_id=habit.id,
            habit_name=habit.name,
            year=year,
            month=month,
            days_in_month=days_in_month,
            elapsed_days=elapsed_days,
            completed_dates=tuple(d.isoformat() for d in completed_in_month),
            scheduled_days=scheduled_days,
            current_streak=svc_stats.current_streak,
            longest_streak=svc_stats.longest_streak,
            completion_percent=pct,
        )

    def get_habit_year_completed_dates(self, habit_id: str, year: int) -> tuple[str, ...]:
        habit = self._habits.get_habit(habit_id)
        if habit is None:
            return ()
        y_start = date(year, 1, 1)
        y_end = date(year, 12, 31)
        completions = self._habits._store.list_completions(habit.id, limit=2000)
        dates = sorted(
            {c.completed_date for c in completions if y_start <= c.completed_date <= y_end}
        )
        return tuple(d.isoformat() for d in dates)

    def get_monthly_daily_series(self, year: int, month: int) -> list[DailyCompletionPoint]:
        local_today = self._clock.today()
        days_in_month = calendar.monthrange(year, month)[1]
        if (year, month) < (local_today.year, local_today.month):
            elapsed_days = days_in_month
        elif (year, month) > (local_today.year, local_today.month):
            elapsed_days = 0
        else:
            elapsed_days = local_today.day

        if elapsed_days == 0:
            return []

        habits: list[HabitRecord] = list(self._habits.list_habits())
        if not habits:
            return []

        # Map habit_id -> set of completed dates in month
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)
        comp_by_habit: dict[str, set[date]] = {}
        created_by_habit: dict[str, date] = {}
        for h in habits:
            comps = self._habits._store.list_completions(h.id, limit=1000)
            comp_by_habit[h.id] = {
                c.completed_date for c in comps if month_start <= c.completed_date <= month_end
            }
            created_by_habit[h.id] = h.created_at.astimezone(self._clock.timezone).date()

        points: list[DailyCompletionPoint] = []
        for day_num in range(1, elapsed_days + 1):
            d = month_start + timedelta(days=day_num - 1)
            sched = sum(1 for h in habits if created_by_habit[h.id] <= d)
            done = sum(
                1 for h in habits if created_by_habit[h.id] <= d and d in comp_by_habit[h.id]
            )
            ratio = (done / sched) if sched > 0 else 0.0
            points.append(
                DailyCompletionPoint(
                    day=day_num,
                    date_iso=d.isoformat(),
                    scheduled_count=sched,
                    completed_count=done,
                    ratio=round(ratio, 4),
                )
            )
        return points
