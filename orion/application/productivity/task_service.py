"""Application-layer TaskService with strict partial updates and archive-not-delete policy."""

from collections.abc import Sequence
from datetime import datetime, time
from typing import Any

from orion.application.productivity.contracts import (
    TaskFilter,
    TaskPriority,
    TaskRecord,
    TaskStatus,
)
from orion.application.productivity.ports import ClockPort, TaskStorePort

_UNSET = object()


class TaskService:
    """Manages personal tasks with partial updates, local-timezone filtering, and archiving."""

    def __init__(self, store: TaskStorePort, clock: ClockPort) -> None:
        self._store = store
        self._clock = clock

    def create_task(
        self,
        title: str,
        *,
        description: str | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        due_at: datetime | None = None,
        due_is_date_only: bool = False,
    ) -> TaskRecord:
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Task title is required.")
        return self._store.create_task(
            title=clean_title,
            description=description,
            priority=priority,
            due_at=due_at,
            due_is_date_only=due_is_date_only,
            now=self._clock.now(),
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._store.get_task(task_id)

    def list_tasks(self, task_filter: TaskFilter | None = None) -> Sequence[TaskRecord]:
        local_today = self._clock.today()
        tz = self._clock.timezone
        today_start = datetime.combine(local_today, time.min, tzinfo=tz)
        today_end = datetime.combine(local_today, time.max, tzinfo=tz)
        return self._store.list_tasks(
            task_filter,
            local_today_start=today_start,
            local_today_end=today_end,
        )

    def search_tasks(self, query: str, *, include_archived: bool = False) -> Sequence[TaskRecord]:
        return self.list_tasks(TaskFilter(query=query, include_archived=include_archived))

    def update_task(
        self,
        task_id: str,
        *,
        title: Any = _UNSET,
        description: Any = _UNSET,
        status: Any = _UNSET,
        priority: Any = _UNSET,
        due_at: Any = _UNSET,
        due_is_date_only: Any = _UNSET,
    ) -> TaskRecord:
        """Apply partial updates: only explicitly provided fields are modified."""
        existing = self._store.get_task(task_id)
        if existing is None:
            raise ValueError(f"Task {task_id!r} not found.")
        if existing.archived_at is not None:
            raise ValueError(f"Task '{existing.title}' is archived.")

        updates: dict[str, Any] = {}
        now = self._clock.now()
        if title is not _UNSET:
            updates["title"] = title
        if description is not _UNSET:
            updates["description"] = description
        if priority is not _UNSET:
            updates["priority"] = priority
        if due_at is not _UNSET:
            updates["due_at"] = due_at
        if due_is_date_only is not _UNSET:
            updates["due_is_date_only"] = due_is_date_only
        if status is not _UNSET:
            st = TaskStatus(status) if not isinstance(status, TaskStatus) else status
            updates["status"] = st
            if st == TaskStatus.DONE and existing.completed_at is None:
                updates["completed_at"] = now
            elif st in (TaskStatus.TODO, TaskStatus.IN_PROGRESS):
                updates["completed_at"] = None

        if not updates:
            return existing
        return self._store.update_task(task_id, updates=updates, now=now)

    def complete_task(self, task_id: str) -> TaskRecord:
        return self.update_task(task_id, status=TaskStatus.DONE)

    def reopen_task(self, task_id: str) -> TaskRecord:
        return self.update_task(task_id, status=TaskStatus.TODO)

    def cancel_task(self, task_id: str) -> TaskRecord:
        return self.update_task(task_id, status=TaskStatus.CANCELLED)

    def archive_task(self, task_id: str) -> TaskRecord:
        existing = self._store.get_task(task_id)
        if existing is None:
            raise ValueError(f"Task {task_id!r} not found.")
        if existing.archived_at is not None:
            return existing
        now = self._clock.now()
        return self._store.update_task(task_id, updates={"archived_at": now}, now=now)
