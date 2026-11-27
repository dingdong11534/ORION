"""Application-layer HabitService and pure streak/statistics functions."""

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from orion.application.productivity.contracts import (
    HabitCompletionRecord,
    HabitFrequency,
    HabitRecord,
    HabitStatistics,
)
from orion.application.productivity.ports import ClockPort, HabitStorePort

_UNSET = object()


def _prev_month(ym: tuple[int, int]) -> tuple[int, int]:
    y, m = ym
    return (y - 1, 12) if m == 1 else (y, m - 1)


def _next_month(ym: tuple[int, int]) -> tuple[int, int]:
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


def compute_habit_streaks(
    completion_dates: Iterable[date],
    *,
    frequency: HabitFrequency,
    target_count: int,
    today: date,
) -> tuple[int, int]:
    """Pure function computing (current_streak, longest_streak) from completion dates."""
    unique_dates = sorted(set(completion_dates))
    if not unique_dates:
        return 0, 0

    if frequency == HabitFrequency.DAILY:
        date_set = set(unique_dates)
        # Current streak: consecutive days ending today or yesterday
        anchor = (
            today
            if today in date_set
            else (today - timedelta(days=1) if (today - timedelta(days=1)) in date_set else None)
        )
        current = 0
        if anchor is not None:
            cursor = anchor
            while cursor in date_set:
                current += 1
                cursor -= timedelta(days=1)

        longest = 0
        run = 0
        prev: date | None = None
        for d in unique_dates:
            if prev is not None and (d - prev).days == 1:
                run += 1
            else:
                run = 1
            longest = max(longest, run)
            prev = d
        return current, longest

    if frequency == HabitFrequency.WEEKLY:
        week_counts: Counter[date] = Counter()
        for d in unique_dates:
            monday = d - timedelta(days=d.weekday())
            week_counts[monday] += 1
        qualifying = sorted(w for w, cnt in week_counts.items() if cnt >= target_count)
        if not qualifying:
            return 0, 0
        q_set = set(qualifying)
        this_week = today - timedelta(days=today.weekday())
        last_week = this_week - timedelta(days=7)
        anchor_w = this_week if this_week in q_set else (last_week if last_week in q_set else None)
        current = 0
        if anchor_w is not None:
            cw = anchor_w
            while cw in q_set:
                current += 1
                cw -= timedelta(days=7)
        longest = 0
        run = 0
        prev_w: date | None = None
        for w in qualifying:
            if prev_w is not None and (w - prev_w).days == 7:
                run += 1
            else:
                run = 1
            longest = max(longest, run)
            prev_w = w
        return current, longest

    # MONTHLY
    month_counts: Counter[tuple[int, int]] = Counter((d.year, d.month) for d in unique_dates)
    qual_m = sorted(ym for ym, cnt in month_counts.items() if cnt >= target_count)
    if not qual_m:
        return 0, 0
    q_m_set = set(qual_m)
    this_m = (today.year, today.month)
    prev_m = _prev_month(this_m)
    anchor_m = this_m if this_m in q_m_set else (prev_m if prev_m in q_m_set else None)
    current = 0
    if anchor_m is not None:
        cm = anchor_m
        while cm in q_m_set:
            current += 1
            cm = _prev_month(cm)
    longest = 0
    run = 0
    prev_ym: tuple[int, int] | None = None
    for ym in qual_m:
        if prev_ym is not None and _next_month(prev_ym) == ym:
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev_ym = ym
    return current, longest


def compute_habit_statistics(
    habit: HabitRecord,
    completions: Sequence[HabitCompletionRecord],
    *,
    today: date,
    local_tz: Any,
    recent_limit: int = 14,
) -> HabitStatistics:
    """Pure function deriving complete habit statistics from authoritative history."""
    dates = [c.completed_date for c in completions]
    date_set = set(dates)
    current_streak, longest_streak = compute_habit_streaks(
        dates,
        frequency=habit.frequency,
        target_count=habit.target_count,
        today=today,
    )

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    weekly_completed = sum(1 for d in date_set if week_start <= d <= week_end)

    monthly_completed = sum(1 for d in date_set if d.year == today.year and d.month == today.month)

    if habit.frequency == HabitFrequency.DAILY:
        weekly_target = min(7, habit.target_count * 7) if habit.target_count == 1 else 7
        days_in_month = (
            (today.replace(month=today.month % 12 + 1, day=1) - timedelta(days=1)).day
            if today.month < 12
            else 31
        )
        monthly_target = days_in_month
    elif habit.frequency == HabitFrequency.WEEKLY:
        weekly_target = habit.target_count
        monthly_target = habit.target_count * 4
    else:
        weekly_target = max(1, habit.target_count)
        monthly_target = habit.target_count

    created_local = habit.created_at.astimezone(local_tz).date()
    start_date = min([created_local, *dates]) if dates else created_local
    if habit.frequency == HabitFrequency.DAILY:
        elapsed_days = max(1, (today - start_date).days + 1)
        rate = min(1.0, len(date_set) / elapsed_days)
    elif habit.frequency == HabitFrequency.WEEKLY:
        elapsed_weeks = max(1, ((today - start_date).days // 7) + 1)
        rate = min(1.0, len(date_set) / (elapsed_weeks * habit.target_count))
    else:
        elapsed_months = max(
            1, (today.year - start_date.year) * 12 + (today.month - start_date.month) + 1
        )
        rate = min(1.0, len(date_set) / (elapsed_months * habit.target_count))

    sorted_recent = tuple(
        sorted(completions, key=lambda c: c.completed_date, reverse=True)[:recent_limit]
    )

    return HabitStatistics(
        habit_id=habit.id,
        habit_name=habit.name,
        frequency=habit.frequency,
        target_count=habit.target_count,
        completed_today=today in date_set,
        current_streak=current_streak,
        longest_streak=longest_streak,
        completion_rate=round(rate, 4),
        weekly_completed=weekly_completed,
        weekly_target=weekly_target,
        monthly_completed=monthly_completed,
        monthly_target=monthly_target,
        total_completions=len(date_set),
        recent_completions=sorted_recent,
    )


class HabitService:
    """Manages habits, local-timezone completions, and derived statistics."""

    def __init__(self, store: HabitStorePort, clock: ClockPort) -> None:
        self._store = store
        self._clock = clock

    def create_habit(
        self,
        name: str,
        *,
        description: str | None = None,
        frequency: HabitFrequency = HabitFrequency.DAILY,
        target_count: int = 1,
    ) -> HabitRecord:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Habit name is required.")
        if target_count < 1:
            raise ValueError("Habit target_count must be >= 1.")
        return self._store.create_habit(
            name=clean_name,
            description=description,
            frequency=frequency,
            target_count=target_count,
            now=self._clock.now(),
        )

    def get_habit(self, habit_id: str) -> HabitRecord | None:
        return self._store.get_habit(habit_id)

    def list_habits(self, *, include_archived: bool = False) -> Sequence[HabitRecord]:
        return self._store.list_habits(include_archived=include_archived)

    def update_habit(
        self,
        habit_id: str,
        *,
        name: Any = _UNSET,
        description: Any = _UNSET,
        frequency: Any = _UNSET,
        target_count: Any = _UNSET,
    ) -> HabitRecord:
        existing = self._store.get_habit(habit_id)
        if existing is None:
            raise ValueError(f"Habit {habit_id!r} not found.")
        if existing.archived_at is not None:
            raise ValueError(f"Habit '{existing.name}' is archived.")
        updates: dict[str, Any] = {}
        if name is not _UNSET:
            updates["name"] = name
        if description is not _UNSET:
            updates["description"] = description
        if frequency is not _UNSET:
            updates["frequency"] = frequency
        if target_count is not _UNSET:
            updates["target_count"] = target_count
        if not updates:
            return existing
        return self._store.update_habit(habit_id, updates=updates, now=self._clock.now())

    def archive_habit(self, habit_id: str) -> HabitRecord:
        existing = self._store.get_habit(habit_id)
        if existing is None:
            raise ValueError(f"Habit {habit_id!r} not found.")
        if existing.archived_at is not None:
            return existing
        now = self._clock.now()
        return self._store.update_habit(habit_id, updates={"archived_at": now}, now=now)

    def complete_habit(
        self,
        habit_id: str,
        *,
        completed_date: date | None = None,
        notes: str | None = None,
    ) -> HabitCompletionRecord:
        now = self._clock.now()
        target_date = completed_date if completed_date is not None else now.date()
        return self._store.record_completion(
            habit_id,
            completed_at=now,
            completed_date=target_date,
            notes=notes,
        )

    def undo_completion(
        self,
        habit_id: str,
        *,
        completed_date: date | None = None,
    ) -> HabitCompletionRecord | None:
        return self._store.delete_completion(habit_id, completed_date=completed_date)

    def get_habit_history(
        self, habit_id: str, *, limit: int | None = 30
    ) -> Sequence[HabitCompletionRecord]:
        return self._store.list_completions(habit_id, limit=limit)

    def get_habit_statistics(self, habit_id: str) -> HabitStatistics:
        habit = self._store.get_habit(habit_id)
        if habit is None:
            raise ValueError(f"Habit {habit_id!r} not found.")
        completions = self._store.list_completions(habit_id)
        return compute_habit_statistics(
            habit,
            completions,
            today=self._clock.today(),
            local_tz=self._clock.timezone,
        )
