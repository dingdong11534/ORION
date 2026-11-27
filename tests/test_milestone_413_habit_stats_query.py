"""Unit tests for HabitStatsQuery (month boundaries, leap years, +05:30 Asia/Kolkata)."""

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config

from alembic import command
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.habit_stats_query import HabitStatsQuery
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.productivity.repository import SqliteHabitStore


class _Clock:
    def __init__(self, dt: datetime, tz_name: str = "Asia/Kolkata") -> None:
        self._dt = dt
        self._tz = ZoneInfo(tz_name)

    def now(self) -> datetime:
        return self._dt

    def today(self) -> date:
        return self._dt.astimezone(self._tz).date()

    @property
    def timezone(self):
        return self._tz


def test_habit_stats_query_leap_year_and_ist(tmp_path: Path):
    db_path = tmp_path / "stats.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)

    # 2024-02-28 20:00 UTC == 2024-02-29 01:30 IST (leap day!)
    clock = _Clock(datetime(2024, 2, 28, 20, 0, tzinfo=UTC), "Asia/Kolkata")
    store = SqliteHabitStore(sf)
    svc = HabitService(store, clock)
    h = svc.create_habit("Reading")
    svc.complete_habit(h.id)

    q = HabitStatsQuery(svc, clock)
    ms = q.get_habit_month_stats(h.id, 2024, 2)
    assert ms is not None
    assert ms.days_in_month == 29
    assert ms.elapsed_days == 29
    assert ms.completed_dates == ("2024-02-29",)
    assert ms.scheduled_days == 1
    assert ms.completion_percent == 100

    yr = q.get_habit_year_completed_dates(h.id, 2024)
    assert yr == ("2024-02-29",)

    series = q.get_monthly_daily_series(2024, 2)
    assert len(series) == 29
    assert series[-1].date_iso == "2024-02-29"
    assert series[-1].completed_count == 1
    assert series[-1].ratio == 1.0
    engine.dispose()
