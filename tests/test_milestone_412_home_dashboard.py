"""Tests for Phase E: HomeDashboardQuery using real SQLite tasks, habits, and activity."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config

from alembic import command
from orion.application.productivity.contracts import TaskPriority
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.home_dashboard_query import build_home_dashboard
from orion.application.productivity.task_service import TaskService
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.productivity.repository import SqliteHabitStore, SqliteTaskStore


class _Clock:
    def __init__(self, dt: datetime, tz_name: str = "Asia/Kolkata") -> None:
        self._dt = dt
        self._tz = ZoneInfo(tz_name)

    def now(self) -> datetime:
        return self._dt

    def today(self):
        return self._dt.astimezone(self._tz).date()

    @property
    def timezone(self):
        return self._tz


def test_home_dashboard_uses_real_sqlite_records_and_charts(tmp_path: Path):
    db_path = tmp_path / "home.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0010")
    eng = create_db_engine(db_path)
    sf = create_session_factory(eng)

    clock = _Clock(datetime(2026, 11, 27, 6, 0, tzinfo=UTC), "Asia/Kolkata")
    tasks = TaskService(SqliteTaskStore(sf), clock)
    habits = HabitService(SqliteHabitStore(sf), clock)

    t1 = tasks.create_task("DBMS Unit 4", priority=TaskPriority.HIGH, due_at=clock.now())
    tasks.create_task("Read chapter", priority=TaskPriority.MEDIUM)
    tasks.complete_task(t1.id)
    h = habits.create_habit("Reading")
    habits.complete_habit(h.id)

    dash = build_home_dashboard(
        task_service=tasks,
        habit_service=habits,
        clock=clock,
        session_factory=sf,
    )
    assert dash["counts"]["completed_today"] == 1
    assert dash["counts"]["unscheduled"] == 1
    assert dash["charts"]["weekly_tasks_done"][-1] == 1
    assert dash["charts"]["status_distribution"]["done"] == 1
    assert dash["charts"]["status_distribution"]["todo"] == 1
    assert dash["habits"][0]["rate_7d"] >= 14
    assert len(dash["recent_activity"]) >= 3
    eng.dispose()
