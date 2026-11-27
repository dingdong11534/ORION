"""Tests for Phase J: DailyBriefingService grounded in actual SQLite records."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config

from alembic import command
from orion.application.productivity.contracts import TaskPriority
from orion.application.productivity.daily_briefing import DailyBriefingService
from orion.application.productivity.habit_service import HabitService
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


def test_daily_briefing_uses_real_tasks_and_habits(tmp_path: Path):
    db_path = tmp_path / "brief.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0010")
    eng = create_db_engine(db_path)
    sf = create_session_factory(eng)

    clock = _Clock(datetime(2026, 11, 27, 6, 0, tzinfo=UTC), "Asia/Kolkata")
    tasks = TaskService(SqliteTaskStore(sf), clock)
    habits = HabitService(SqliteHabitStore(sf), clock)
    tasks.create_task("DBMS Unit 4", priority=TaskPriority.HIGH, due_at=clock.now())
    t_done = tasks.create_task("OS Notes", priority=TaskPriority.MEDIUM)
    tasks.complete_task(t_done.id)
    habits.create_habit("Reading")

    svc = DailyBriefingService(
        task_service=tasks, habit_service=habits, clock=clock, session_factory=sf
    )
    assert svc.is_briefing_query("What should I work on today?")
    plan = svc.answer("What should I work on today?")
    assert "DBMS Unit 4" in plan
    assert "Reading" in plan

    dl = svc.answer("What's my next important deadline?")
    assert "DBMS Unit 4" in dl

    wk = svc.answer("What have I completed this week?")
    assert "OS Notes" in wk
    eng.dispose()
