"""Phase B tests: productivity consistency, Today query, Asia/Kolkata, and persistent context."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config

from alembic import command
from orion.application.productivity.action_policy import ProductivityActionCoordinator
from orion.application.productivity.contracts import TaskPriority
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.task_service import TaskService
from orion.application.productivity.today_query import build_today_overview
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.models import ConversationModel
from orion.infrastructure.productivity.repository import (
    SqliteHabitStore,
    SqliteProductivityActionStore,
    SqliteProductivityContextStore,
    SqliteTaskStore,
)


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


def test_alembic_0008_reversible_on_populated_db(tmp_path: Path):
    db_path = tmp_path / "orion.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "0007")
    engine = create_db_engine(db_path)
    with engine.begin() as conn:
        import sqlalchemy as sa

        conn.execute(
            sa.text(
                "INSERT INTO conversations (id, title, next_sequence, created_at, updated_at) "
                "VALUES ('conv_keep', 'Keep Me', 0, '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)
    ctx_store = SqliteProductivityContextStore(sf)
    ctx_store.advance_user_turn("conv_keep")
    ctx_store.set_last_entity("conv_keep", "task", "tsk_1", "Test")
    assert ctx_store.resolve_reference("conv_keep", "task") == "tsk_1"
    engine.dispose()

    command.downgrade(cfg, "0007")
    engine = create_db_engine(db_path)
    with engine.connect() as conn:
        import sqlalchemy as sa

        title = conn.execute(
            sa.text("SELECT title FROM conversations WHERE id='conv_keep'")
        ).scalar_one()
        assert title == "Keep Me"
    engine.dispose()

    command.upgrade(cfg, "0009")


def test_persistent_context_survives_restart_and_expires_after_5_turns(tmp_path: Path):
    db_path = tmp_path / "ctx.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")

    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)
    with sf() as session, session.begin():
        session.add(ConversationModel(id="conv_1", title="Work"))
        session.add(ConversationModel(id="conv_2", title="Other"))

    clock = _Clock(datetime(2026, 11, 27, 6, 0, tzinfo=UTC), "Asia/Kolkata")
    tasks = TaskService(SqliteTaskStore(sf), clock)
    habits = HabitService(SqliteHabitStore(sf), clock)
    coord = ProductivityActionCoordinator(
        task_service=tasks,
        habit_service=habits,
        clock=clock,
        action_store=SqliteProductivityActionStore(sf),
        context_store=SqliteProductivityContextStore(sf),
    )

    res = coord.handle_message("add task DBMS Unit 4", conversation_id="conv_1")
    assert res is not None and res.status == "executed"
    created_id = res.entity_id
    assert created_id is not None

    # Simulate restart: new coordinator, same SQLite DB
    coord2 = ProductivityActionCoordinator(
        task_service=tasks,
        habit_service=habits,
        clock=clock,
        action_store=SqliteProductivityActionStore(sf),
        context_store=SqliteProductivityContextStore(sf),
    )
    # Different conversation must NOT see conv_1's context
    res_other = coord2.handle_message("make it high priority", conversation_id="conv_2")
    assert res_other is not None and res_other.status == "clarify"

    # Same conversation within 5 turns resolves "it"
    res_upd = coord2.handle_message("make it high priority", conversation_id="conv_1")
    assert res_upd is not None and res_upd.status == "executed"
    assert res_upd.entity_id == created_id
    assert tasks.get_task(created_id).priority == TaskPriority.HIGH

    # Advance 6 unrelated user turns -> pronoun expires
    for _ in range(6):
        coord2.handle_message("hello there", conversation_id="conv_1")
    res_expired = coord2.handle_message("mark it done", conversation_id="conv_1")
    assert res_expired is not None and res_expired.status == "clarify"
    engine.dispose()


def test_today_overview_asia_kolkata_midnight_and_date_only(tmp_path: Path):
    db_path = tmp_path / "tz.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)

    # 2026-11-27 18:15 UTC == 2026-11-27 23:45 IST (still Nov 27 in Asia/Kolkata!)
    clock = _Clock(datetime(2026, 11, 27, 18, 15, tzinfo=UTC), "Asia/Kolkata")
    tasks = TaskService(SqliteTaskStore(sf), clock)
    habits = HabitService(SqliteHabitStore(sf), clock)

    # Date-only task for Nov 27 IST (stored at 00:00 IST = Nov 26 18:30 UTC): NOT overdue at 23:45 IST!
    t_date_only = tasks.create_task(
        "**DBMS** Unit 4",
        due_at=datetime(2026, 11, 27, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC),
        due_is_date_only=True,
    )
    # Timed task due at 16:00 IST (10:30 UTC): IS overdue at 23:45 IST
    t_timed_past = tasks.create_task(
        "COA Assignment",
        due_at=datetime(2026, 11, 27, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC),
        due_is_date_only=False,
    )
    # Unscheduled task
    t_unsched = tasks.create_task("Read notes")

    ov = build_today_overview(tasks, habits, clock)
    assert [t.id for t in ov.due_today] == [t_date_only.id]
    assert [t.id for t in ov.overdue] == [t_timed_past.id]
    assert [t.id for t in ov.unscheduled] == [t_unsched.id]
    engine.dispose()


def test_ordinal_selection_from_ambiguous_list(tmp_path: Path):
    db_path = tmp_path / "ord.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0009")
    engine = create_db_engine(db_path)
    sf = create_session_factory(engine)
    with sf() as session, session.begin():
        session.add(ConversationModel(id="conv_ord", title="Ord"))

    clock = _Clock(datetime(2026, 11, 27, 6, 0, tzinfo=UTC), "Asia/Kolkata")
    tasks = TaskService(SqliteTaskStore(sf), clock)
    habits = HabitService(SqliteHabitStore(sf), clock)
    coord = ProductivityActionCoordinator(
        task_service=tasks,
        habit_service=habits,
        clock=clock,
        context_store=SqliteProductivityContextStore(sf),
    )
    t1 = tasks.create_task("DBMS Lab 1")
    t2 = tasks.create_task("DBMS Lab 2")

    res1 = coord.handle_message("make my DBMS task high priority", conversation_id="conv_ord")
    assert res1 is not None and res1.status == "clarify"
    assert len(res1.candidates) == 2

    res2 = coord.handle_message("make the second one high priority", conversation_id="conv_ord")
    assert res2 is not None and res2.status == "executed"
    assert res2.entity_id == t2.id
    assert tasks.get_task(t2.id).priority == TaskPriority.HIGH
    assert tasks.get_task(t1.id).priority == TaskPriority.MEDIUM
    engine.dispose()
