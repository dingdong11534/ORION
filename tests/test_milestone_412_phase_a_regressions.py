"""Phase A failing regression tests reproducing Milestone 4.12 Section 3 failures.

Marked xfail(strict=True) during Phase A so the reproduction is proven and tracked;
Phases B and C will fix each root cause and remove the xfail markers.
"""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from orion.application.productivity.action_policy import ProductivityActionCoordinator
from orion.application.productivity.contracts import (
    ProductivityIntent,
    TaskPriority,
)
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.productivity_intent import ProductivityIntentInterpreter
from orion.application.productivity.target_resolver import resolve_task_target
from orion.application.productivity.task_service import TaskService
from orion.infrastructure.productivity.repository import SqliteHabitStore, SqliteTaskStore


class _FixedClock:
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


@pytest.fixture()
def prod_env(tmp_path: Path):
    from orion.infrastructure.persistence.database import (
        create_session_factory,
        create_sqlite_engine,
    )
    from orion.infrastructure.persistence.models import Base

    engine = create_sqlite_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    sf = create_session_factory(engine)
    clock = _FixedClock(datetime(2026, 11, 27, 5, 30, tzinfo=UTC), "Asia/Kolkata")
    tasks = TaskService(SqliteTaskStore(sf), clock)
    habits = HabitService(SqliteHabitStore(sf), clock)
    coord = ProductivityActionCoordinator(task_service=tasks, habit_service=habits, clock=clock)
    yield tasks, habits, coord, clock
    engine.dispose()


@pytest.mark.xfail(
    strict=True, reason="3.1: 'add one as taask' falls through to NONE instead of clarify"
)
def test_repro_3_1_vague_task_creation_clarifies(prod_env):
    _, _, coord, _ = prod_env
    res = coord.handle_message("add one as taask", conversation_id="conv_1")
    assert res is not None
    assert res.status == "clarify"


@pytest.mark.xfail(
    strict=True, reason="3.1: 'Show me my tasks and habits for today.' omits unscheduled/habits"
)
def test_repro_3_1_today_query_includes_unscheduled_and_habits(prod_env):
    tasks, habits, coord, _ = prod_env
    tasks.create_task("Complete coa unit 4 by 4pm", priority=TaskPriority.MEDIUM)
    habits.create_habit("reading")
    res = coord.handle_message("Show me my tasks and habits for today.", conversation_id="conv_1")
    assert res is not None
    assert "Complete coa unit 4" in res.summary
    assert "reading" in res.summary


@pytest.mark.xfail(strict=True, reason="3.1: typo tolerance edit distance 1 not yet supported")
def test_repro_3_1_target_resolution_typo_tolerance(prod_env):
    tasks, _, _, _ = prod_env
    t = tasks.create_task("Complete DBMS Unit 4", priority=TaskPriority.MEDIUM)
    res = resolve_task_target("DBSM", [t])
    assert res.matched_id is None  # 4 chars: should NOT typo-match
    t2 = tasks.create_task("Algorithm Homework", priority=TaskPriority.MEDIUM)
    res2 = resolve_task_target("Algoritm", [t, t2])
    assert res2.matched_id == t2.id


@pytest.mark.xfail(strict=True, reason="3.2: habit/task extraction spans sentence boundaries")
def test_repro_3_2_sentence_splitting_before_extraction():
    interp = ProductivityIntentInterpreter()
    p = interp.interpret("I completed my reading today.I completed my reading today.")
    assert p.intent == ProductivityIntent.HABIT_COMPLETE
    assert p.target_query == "reading"


@pytest.mark.xfail(strict=True, reason="3.3: Main.qml calls nonexistent orion.newConversation()")
def test_repro_3_3_main_qml_calls_valid_new_conversation_slot():
    qml = Path("orion/ui/qml/Main.qml").read_text(encoding="utf-8")
    assert "orion.newConversation()" not in qml
