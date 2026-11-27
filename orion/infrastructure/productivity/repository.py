"""SQLite/SQLAlchemy repositories and local-timezone clock for personal productivity."""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, tzinfo
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from orion.application.productivity.contracts import (
    PRIORITY_ORDER,
    HabitCompletionRecord,
    HabitFrequency,
    HabitRecord,
    TaskFilter,
    TaskPriority,
    TaskRecord,
    TaskStatus,
)
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    HabitCompletionModel,
    HabitModel,
    ProductivityActionRecordModel,
    TaskModel,
)


class SystemClock:
    """Timezone-aware local clock."""

    def __init__(self, tz: tzinfo | None = None) -> None:
        self._tz = tz if tz is not None else datetime.now().astimezone().tzinfo or UTC

    @property
    def timezone(self) -> tzinfo:
        return self._tz

    def now(self) -> datetime:
        return datetime.now(self._tz)

    def today(self) -> date:
        return self.now().date()


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _record_event(
    session: Any,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    now: datetime,
) -> None:
    session.add(
        ActivityEventModel(
            id=f"evt_{uuid.uuid4().hex}",
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=payload,
            created_at=_ensure_utc(now),
        )
    )


def _task_to_record(m: TaskModel) -> TaskRecord:
    return TaskRecord(
        id=m.id,
        title=m.title,
        description=m.description,
        status=TaskStatus(m.status),
        priority=TaskPriority(m.priority),
        due_at=_ensure_utc(m.due_at),
        due_is_date_only=bool(m.due_is_date_only),
        created_at=_ensure_utc(m.created_at) or datetime.now(UTC),
        updated_at=_ensure_utc(m.updated_at) or datetime.now(UTC),
        completed_at=_ensure_utc(m.completed_at),
        archived_at=_ensure_utc(m.archived_at),
    )


def _habit_to_record(m: HabitModel) -> HabitRecord:
    return HabitRecord(
        id=m.id,
        name=m.name,
        description=m.description,
        frequency=HabitFrequency(m.frequency),
        target_count=int(m.target_count),
        created_at=_ensure_utc(m.created_at) or datetime.now(UTC),
        updated_at=_ensure_utc(m.updated_at) or datetime.now(UTC),
        archived_at=_ensure_utc(m.archived_at),
    )


def _completion_to_record(m: HabitCompletionModel) -> HabitCompletionRecord:
    return HabitCompletionRecord(
        id=m.id,
        habit_id=m.habit_id,
        completed_at=_ensure_utc(m.completed_at) or datetime.now(UTC),
        completed_date=date.fromisoformat(m.completed_date),
        notes=m.notes,
    )


class SqliteTaskStore:
    """Transactional SQLite store for tasks."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def create_task(
        self,
        *,
        title: str,
        description: str | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        due_at: datetime | None = None,
        due_is_date_only: bool = False,
        now: datetime,
    ) -> TaskRecord:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title cannot be blank.")
        utc_now = _ensure_utc(now) or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = TaskModel(
                title=clean_title,
                description=description.strip() if description and description.strip() else None,
                status=TaskStatus.TODO.value,
                priority=priority.value,
                due_at=_ensure_utc(due_at),
                due_is_date_only=bool(due_is_date_only),
                created_at=utc_now,
                updated_at=utc_now,
            )
            session.add(row)
            session.flush()
            _record_event(
                session,
                event_type="task_created",
                aggregate_type="task",
                aggregate_id=row.id,
                payload={"title": row.title, "priority": row.priority},
                now=utc_now,
            )
            return _task_to_record(row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._session_factory() as session:
            row = session.get(TaskModel, task_id)
            return _task_to_record(row) if row is not None else None

    def list_tasks(
        self,
        task_filter: TaskFilter | None = None,
        *,
        local_today_start: datetime | None = None,
        local_today_end: datetime | None = None,
    ) -> Sequence[TaskRecord]:
        f = task_filter or TaskFilter()
        with self._session_factory() as session:
            stmt = select(TaskModel)
            if not f.include_archived:
                stmt = stmt.where(TaskModel.archived_at.is_(None))
            if f.status is not None:
                stmt = stmt.where(TaskModel.status == f.status.value)
            if f.priority is not None:
                stmt = stmt.where(TaskModel.priority == f.priority.value)
            rows = [_task_to_record(r) for r in session.execute(stmt).scalars().all()]

        if f.query and f.query.strip():
            q = f.query.strip().lower()
            rows = [
                r
                for r in rows
                if q in r.title.lower() or (r.description and q in r.description.lower())
            ]

        if f.bucket:
            b = f.bucket.lower()
            t_start = _ensure_utc(local_today_start)
            t_end = _ensure_utc(local_today_end)
            if b == "completed":
                rows = [r for r in rows if r.status == TaskStatus.DONE]
            elif b == "today" and t_start is not None and t_end is not None:
                rows = [
                    r
                    for r in rows
                    if r.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
                    and r.due_at is not None
                    and t_start <= r.due_at <= t_end
                ]
            elif b == "overdue" and t_start is not None:
                rows = [
                    r
                    for r in rows
                    if r.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
                    and r.due_at is not None
                    and r.due_at < t_start
                ]
            elif b == "upcoming" and t_end is not None:
                rows = [
                    r
                    for r in rows
                    if r.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
                    and r.due_at is not None
                    and r.due_at > t_end
                ]

        if f.sort_by == "due_at":
            rows.sort(
                key=lambda r: (
                    r.due_at is None,
                    r.due_at or datetime.max.replace(tzinfo=UTC),
                    PRIORITY_ORDER[r.priority],
                )
            )
        elif f.sort_by == "priority":
            rows.sort(
                key=lambda r: (
                    PRIORITY_ORDER[r.priority],
                    r.due_at is None,
                    r.due_at or datetime.max.replace(tzinfo=UTC),
                )
            )
        elif f.sort_by == "updated_at":
            rows.sort(key=lambda r: r.updated_at, reverse=True)
        else:
            rows.sort(
                key=lambda r: (
                    r.status in (TaskStatus.DONE, TaskStatus.CANCELLED),
                    PRIORITY_ORDER[r.priority],
                    r.due_at is None,
                    r.due_at or datetime.max.replace(tzinfo=UTC),
                )
            )
        return rows

    def update_task(
        self,
        task_id: str,
        *,
        updates: dict[str, Any],
        now: datetime,
    ) -> TaskRecord:
        utc_now = _ensure_utc(now) or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = session.get(TaskModel, task_id)
            if row is None:
                raise ValueError(f"Task {task_id!r} not found.")
            changed: dict[str, Any] = {}
            for key, val in updates.items():
                if key == "title":
                    clean = str(val).strip()
                    if not clean:
                        raise ValueError("Task title cannot be blank.")
                    row.title = clean
                    changed["title"] = clean
                elif key == "description":
                    row.description = str(val).strip() if val and str(val).strip() else None
                    changed["description"] = row.description
                elif key == "status":
                    st = TaskStatus(val) if not isinstance(val, TaskStatus) else val
                    row.status = st.value
                    changed["status"] = st.value
                elif key == "priority":
                    pr = TaskPriority(val) if not isinstance(val, TaskPriority) else val
                    row.priority = pr.value
                    changed["priority"] = pr.value
                elif key == "due_at":
                    row.due_at = _ensure_utc(val)
                    changed["due_at"] = row.due_at.isoformat() if row.due_at else None
                elif key == "due_is_date_only":
                    row.due_is_date_only = bool(val)
                    changed["due_is_date_only"] = row.due_is_date_only
                elif key == "completed_at":
                    row.completed_at = _ensure_utc(val)
                elif key == "archived_at":
                    row.archived_at = _ensure_utc(val)
                    changed["archived"] = row.archived_at is not None
            row.updated_at = utc_now
            session.flush()
            _record_event(
                session,
                event_type="task_updated",
                aggregate_type="task",
                aggregate_id=row.id,
                payload=changed,
                now=utc_now,
            )
            return _task_to_record(row)


class SqliteHabitStore:
    """Transactional SQLite store for habits and authoritative completion records."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def create_habit(
        self,
        *,
        name: str,
        description: str | None = None,
        frequency: HabitFrequency = HabitFrequency.DAILY,
        target_count: int = 1,
        now: datetime,
    ) -> HabitRecord:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Habit name cannot be blank.")
        if target_count < 1:
            raise ValueError("Habit target_count must be >= 1.")
        utc_now = _ensure_utc(now) or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = HabitModel(
                name=clean_name,
                description=description.strip() if description and description.strip() else None,
                frequency=frequency.value,
                target_count=int(target_count),
                created_at=utc_now,
                updated_at=utc_now,
            )
            session.add(row)
            session.flush()
            _record_event(
                session,
                event_type="habit_created",
                aggregate_type="habit",
                aggregate_id=row.id,
                payload={
                    "name": row.name,
                    "frequency": row.frequency,
                    "target_count": row.target_count,
                },
                now=utc_now,
            )
            return _habit_to_record(row)

    def get_habit(self, habit_id: str) -> HabitRecord | None:
        with self._session_factory() as session:
            row = session.get(HabitModel, habit_id)
            return _habit_to_record(row) if row is not None else None

    def list_habits(self, *, include_archived: bool = False) -> Sequence[HabitRecord]:
        with self._session_factory() as session:
            stmt = select(HabitModel).order_by(HabitModel.created_at.asc())
            if not include_archived:
                stmt = stmt.where(HabitModel.archived_at.is_(None))
            return [_habit_to_record(r) for r in session.execute(stmt).scalars().all()]

    def update_habit(
        self,
        habit_id: str,
        *,
        updates: dict[str, Any],
        now: datetime,
    ) -> HabitRecord:
        utc_now = _ensure_utc(now) or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = session.get(HabitModel, habit_id)
            if row is None:
                raise ValueError(f"Habit {habit_id!r} not found.")
            changed: dict[str, Any] = {}
            for key, val in updates.items():
                if key == "name":
                    clean = str(val).strip()
                    if not clean:
                        raise ValueError("Habit name cannot be blank.")
                    row.name = clean
                    changed["name"] = clean
                elif key == "description":
                    row.description = str(val).strip() if val and str(val).strip() else None
                    changed["description"] = row.description
                elif key == "frequency":
                    fr = HabitFrequency(val) if not isinstance(val, HabitFrequency) else val
                    row.frequency = fr.value
                    changed["frequency"] = fr.value
                elif key == "target_count":
                    tc = int(val)
                    if tc < 1:
                        raise ValueError("Habit target_count must be >= 1.")
                    row.target_count = tc
                    changed["target_count"] = tc
                elif key == "archived_at":
                    row.archived_at = _ensure_utc(val)
                    changed["archived"] = row.archived_at is not None
            row.updated_at = utc_now
            session.flush()
            _record_event(
                session,
                event_type="habit_updated",
                aggregate_type="habit",
                aggregate_id=row.id,
                payload=changed,
                now=utc_now,
            )
            return _habit_to_record(row)

    def record_completion(
        self,
        habit_id: str,
        *,
        completed_at: datetime,
        completed_date: date,
        notes: str | None = None,
    ) -> HabitCompletionRecord:
        utc_now = _ensure_utc(completed_at) or datetime.now(UTC)
        date_str = completed_date.isoformat()
        with self._session_factory() as session, session.begin():
            habit = session.get(HabitModel, habit_id)
            if habit is None:
                raise ValueError(f"Habit {habit_id!r} not found.")
            if habit.archived_at is not None:
                raise ValueError(f"Habit '{habit.name}' is archived and cannot be completed.")
            habit_name = habit.name
            comp = HabitCompletionModel(
                habit_id=habit_id,
                completed_at=utc_now,
                completed_date=date_str,
                notes=notes.strip() if notes and notes.strip() else None,
            )
            session.add(comp)
            try:
                session.flush()
            except IntegrityError as exc:
                raise ValueError(
                    f"Habit '{habit_name}' is already completed for {date_str}."
                ) from exc
            _record_event(
                session,
                event_type="habit_completed",
                aggregate_type="habit",
                aggregate_id=habit_id,
                payload={"completed_date": date_str},
                now=utc_now,
            )
            return _completion_to_record(comp)

    def delete_completion(
        self,
        habit_id: str,
        *,
        completed_date: date | None = None,
    ) -> HabitCompletionRecord | None:
        with self._session_factory() as session, session.begin():
            stmt = select(HabitCompletionModel).where(HabitCompletionModel.habit_id == habit_id)
            if completed_date is not None:
                stmt = stmt.where(HabitCompletionModel.completed_date == completed_date.isoformat())
            else:
                stmt = stmt.order_by(HabitCompletionModel.completed_date.desc())
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            rec = _completion_to_record(row)
            session.delete(row)
            _record_event(
                session,
                event_type="habit_completion_undone",
                aggregate_type="habit",
                aggregate_id=habit_id,
                payload={"completed_date": rec.completed_date.isoformat()},
                now=datetime.now(UTC),
            )
            return rec

    def list_completions(
        self,
        habit_id: str,
        *,
        limit: int | None = None,
    ) -> Sequence[HabitCompletionRecord]:
        with self._session_factory() as session:
            stmt = (
                select(HabitCompletionModel)
                .where(HabitCompletionModel.habit_id == habit_id)
                .order_by(HabitCompletionModel.completed_date.desc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            return [_completion_to_record(r) for r in session.execute(stmt).scalars().all()]


class SqliteProductivityActionStore:
    """Idempotent persistence for NL productivity action records & undo state."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def get_by_request(self, client_request_id: str, intent: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(ProductivityActionRecordModel).where(
                        ProductivityActionRecordModel.client_request_id == client_request_id,
                        ProductivityActionRecordModel.intent == intent,
                    )
                )
                .scalars()
                .first()
            )
            return self._to_dict(row) if row is not None else None

    def save_record(
        self,
        *,
        client_request_id: str | None,
        conversation_id: str | None,
        intent: str,
        risk_level: str,
        status: str,
        entity_type: str | None,
        entity_id: str | None,
        summary: str,
        payload: dict[str, Any],
        undo_payload: dict[str, Any] | None,
        now: datetime,
    ) -> str:
        utc_now = _ensure_utc(now) or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = ProductivityActionRecordModel(
                client_request_id=client_request_id,
                conversation_id=conversation_id,
                intent=intent,
                risk_level=risk_level,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                payload_json=json.dumps(payload, default=str),
                undo_payload_json=json.dumps(undo_payload, default=str)
                if undo_payload is not None
                else None,
                created_at=utc_now,
            )
            session.add(row)
            session.flush()
            return row.id

    def get_record(self, action_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ProductivityActionRecordModel, action_id)
            return self._to_dict(row) if row is not None else None

    def update_status(self, action_id: str, status: str) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(ProductivityActionRecordModel, action_id)
            if row is not None:
                row.status = status

    @staticmethod
    def _to_dict(row: ProductivityActionRecordModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "client_request_id": row.client_request_id,
            "conversation_id": row.conversation_id,
            "intent": row.intent,
            "risk_level": row.risk_level,
            "status": row.status,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "summary": row.summary,
            "payload": json.loads(row.payload_json or "{}"),
            "undo_payload": json.loads(row.undo_payload_json) if row.undo_payload_json else None,
            "created_at": row.created_at,
        }


class SqliteProductivityContextStore:
    """SQLite-persisted per-conversation productivity context (IDs only, 5-turn expiry)."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def _get_or_create_row(self, session: Any, conversation_id: str) -> Any:
        from orion.infrastructure.persistence.models import (
            ConversationModel,
            ConversationProductivityContextModel,
        )

        row = session.get(ConversationProductivityContextModel, conversation_id)
        if row is not None:
            return row
        # Only insert if parent conversation exists (FK constraint)
        if session.get(ConversationModel, conversation_id) is None:
            return ConversationProductivityContextModel(
                conversation_id=conversation_id,
                last_list_ids_json="[]",
                user_turn_count=0,
            )
        row = ConversationProductivityContextModel(
            conversation_id=conversation_id,
            last_list_ids_json="[]",
            user_turn_count=0,
            updated_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()
        return row

    def get(self, conversation_id: str) -> Any:
        from orion.application.productivity.productivity_context import (
            ProductivityConversationContext,
        )
        from orion.infrastructure.persistence.models import ConversationProductivityContextModel

        with self._session_factory() as session:
            row = session.get(ConversationProductivityContextModel, conversation_id)
            if row is None:
                return ProductivityConversationContext()
            last_id = (
                row.last_task_id
                if row.last_entity_type == "task"
                else (row.last_habit_id if row.last_entity_type == "habit" else None)
            )
            return ProductivityConversationContext(
                last_entity_type=row.last_entity_type,
                last_entity_id=last_id,
                last_task_id=row.last_task_id,
                last_habit_id=row.last_habit_id,
                last_list_ids=json.loads(row.last_list_ids_json or "[]"),
                last_list_type=row.last_list_type,
                user_turn_count=row.user_turn_count,
                last_task_turn=row.last_task_turn,
                last_habit_turn=row.last_habit_turn,
                last_list_turn=row.last_list_turn,
                pending_confirmation_action_id=row.pending_confirmation_action_id,
            )

    def advance_user_turn(self, conversation_id: str) -> int:
        from orion.infrastructure.persistence.models import ConversationModel

        with self._session_factory() as session, session.begin():
            if session.get(ConversationModel, conversation_id) is None:
                return 0
            row = self._get_or_create_row(session, conversation_id)
            row.user_turn_count = (row.user_turn_count or 0) + 1
            row.updated_at = datetime.now(UTC)
            return row.user_turn_count

    def set_last_entity(
        self, conversation_id: str, entity_type: str, entity_id: str, entity_label: str
    ) -> None:
        from orion.infrastructure.persistence.models import ConversationModel

        with self._session_factory() as session, session.begin():
            if session.get(ConversationModel, conversation_id) is None:
                return
            row = self._get_or_create_row(session, conversation_id)
            row.last_entity_type = entity_type
            if entity_type == "task":
                row.last_task_id = entity_id
                row.last_task_turn = row.user_turn_count
            elif entity_type == "habit":
                row.last_habit_id = entity_id
                row.last_habit_turn = row.user_turn_count
            row.updated_at = datetime.now(UTC)

    def set_last_list(self, conversation_id: str, entity_type: str, entity_ids: list[str]) -> None:
        from orion.application.productivity.productivity_context import MAX_LIST_IDS
        from orion.infrastructure.persistence.models import ConversationModel

        with self._session_factory() as session, session.begin():
            if session.get(ConversationModel, conversation_id) is None:
                return
            row = self._get_or_create_row(session, conversation_id)
            row.last_list_ids_json = json.dumps(list(entity_ids[:MAX_LIST_IDS]))
            row.last_list_type = entity_type
            row.last_list_turn = row.user_turn_count
            row.updated_at = datetime.now(UTC)

    def resolve_reference(
        self,
        conversation_id: str,
        entity_type: str,
        *,
        ordinal_index: int | None = None,
    ) -> str | None:
        from orion.application.productivity.productivity_context import CONTEXT_TURN_EXPIRY

        ctx = self.get(conversation_id)
        if ordinal_index is not None:
            if (
                ctx.last_list_type == entity_type
                and ctx.last_list_turn is not None
                and (ctx.user_turn_count - ctx.last_list_turn) <= CONTEXT_TURN_EXPIRY
                and 1 <= ordinal_index <= len(ctx.last_list_ids)
            ):
                return ctx.last_list_ids[ordinal_index - 1]
            return None
        if entity_type == "task":
            if (
                ctx.last_task_id
                and ctx.last_task_turn is not None
                and (ctx.user_turn_count - ctx.last_task_turn) <= CONTEXT_TURN_EXPIRY
            ):
                return ctx.last_task_id
            return None
        if entity_type == "habit":
            if (
                ctx.last_habit_id
                and ctx.last_habit_turn is not None
                and (ctx.user_turn_count - ctx.last_habit_turn) <= CONTEXT_TURN_EXPIRY
            ):
                return ctx.last_habit_id
            return None
        return None

    def set_pending_confirmation(self, conversation_id: str, action_id: str | None) -> None:
        from orion.infrastructure.persistence.models import ConversationModel

        with self._session_factory() as session, session.begin():
            if session.get(ConversationModel, conversation_id) is None:
                return
            row = self._get_or_create_row(session, conversation_id)
            row.pending_confirmation_action_id = action_id
            row.updated_at = datetime.now(UTC)
