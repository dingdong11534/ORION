"""Application-layer ports for ORION personal productivity."""

from collections.abc import Sequence
from datetime import date, datetime, tzinfo
from typing import Any, Protocol

from orion.application.productivity.contracts import (
    HabitCompletionRecord,
    HabitFrequency,
    HabitRecord,
    TaskFilter,
    TaskPriority,
    TaskRecord,
)


class ClockPort(Protocol):
    """Injected clock and local timezone provider for deterministic testing."""

    def now(self) -> datetime:
        """Return timezone-aware current datetime in the user's local timezone."""
        ...

    def today(self) -> date:
        """Return current calendar date in the user's local timezone."""
        ...

    @property
    def timezone(self) -> tzinfo:
        """Return configured local tzinfo."""
        ...


class TaskStorePort(Protocol):
    """Persistence port for tasks."""

    def create_task(
        self,
        *,
        title: str,
        description: str | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        due_at: datetime | None = None,
        due_is_date_only: bool = False,
        now: datetime,
    ) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(
        self,
        task_filter: TaskFilter | None = None,
        *,
        local_today_start: datetime | None = None,
        local_today_end: datetime | None = None,
    ) -> Sequence[TaskRecord]: ...

    def update_task(
        self,
        task_id: str,
        *,
        updates: dict[str, Any],
        now: datetime,
    ) -> TaskRecord: ...


class HabitStorePort(Protocol):
    """Persistence port for habits and authoritative completion history."""

    def create_habit(
        self,
        *,
        name: str,
        description: str | None = None,
        frequency: HabitFrequency = HabitFrequency.DAILY,
        target_count: int = 1,
        now: datetime,
    ) -> HabitRecord: ...

    def get_habit(self, habit_id: str) -> HabitRecord | None: ...

    def list_habits(self, *, include_archived: bool = False) -> Sequence[HabitRecord]: ...

    def update_habit(
        self,
        habit_id: str,
        *,
        updates: dict[str, Any],
        now: datetime,
    ) -> HabitRecord: ...

    def record_completion(
        self,
        habit_id: str,
        *,
        completed_at: datetime,
        completed_date: date,
        notes: str | None = None,
    ) -> HabitCompletionRecord: ...

    def delete_completion(
        self,
        habit_id: str,
        *,
        completed_date: date | None = None,
    ) -> HabitCompletionRecord | None: ...

    def list_completions(
        self,
        habit_id: str,
        *,
        limit: int | None = None,
    ) -> Sequence[HabitCompletionRecord]: ...


class ProductivityActionStorePort(Protocol):
    """Persistence port for idempotent NL productivity action records."""

    def get_by_request(self, client_request_id: str, intent: str) -> dict[str, Any] | None: ...

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
    ) -> str: ...

    def get_record(self, action_id: str) -> dict[str, Any] | None: ...

    def update_status(self, action_id: str, status: str) -> None: ...
