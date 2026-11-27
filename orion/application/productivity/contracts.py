"""Application-layer contracts for ORION personal productivity (tasks, habits, NL actions)."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    """Lifecycle states for a personal task."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """Explicit priority levels ordered from highest (0) to lowest (3)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_ORDER: dict[TaskPriority, int] = {
    TaskPriority.CRITICAL: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.MEDIUM: 2,
    TaskPriority.LOW: 3,
}


class HabitFrequency(StrEnum):
    """Supported habit cadence intervals."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class TaskRecord(BaseModel):
    """Persisted task record."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    due_at: datetime | None = None
    due_is_date_only: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    archived_at: datetime | None = None


class TaskFilter(BaseModel):
    """Query filter for listing/searching tasks."""

    model_config = ConfigDict(frozen=True)

    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    query: str | None = None
    bucket: str | None = None  # "today" | "overdue" | "upcoming" | "completed" | None
    include_archived: bool = False
    sort_by: str = "priority_due"  # "priority_due" | "due_at" | "priority" | "updated_at"


class HabitRecord(BaseModel):
    """Persisted habit definition."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str | None = None
    frequency: HabitFrequency = HabitFrequency.DAILY
    target_count: int = 1
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class HabitCompletionRecord(BaseModel):
    """Authoritative completion entry for a habit on a local calendar date."""

    model_config = ConfigDict(frozen=True)

    id: str
    habit_id: str
    completed_at: datetime
    completed_date: date
    notes: str | None = None


class HabitStatistics(BaseModel):
    """Derived statistics recomputed from authoritative completion history."""

    model_config = ConfigDict(frozen=True)

    habit_id: str
    habit_name: str
    frequency: HabitFrequency
    target_count: int
    completed_today: bool = False
    current_streak: int = 0
    longest_streak: int = 0
    completion_rate: float = 0.0
    weekly_completed: int = 0
    weekly_target: int = 0
    monthly_completed: int = 0
    monthly_target: int = 0
    total_completions: int = 0
    recent_completions: tuple[HabitCompletionRecord, ...] = ()


class ProductivityIntent(StrEnum):
    """Constrained intent enum for natural-language productivity actions."""

    NONE = "none"
    TASK_CREATE = "task_create"
    TASK_LIST = "task_list"
    TASK_UPDATE = "task_update"
    TASK_COMPLETE = "task_complete"
    TASK_REOPEN = "task_reopen"
    TASK_CANCEL = "task_cancel"
    TASK_ARCHIVE = "task_archive"
    HABIT_CREATE = "habit_create"
    HABIT_LIST = "habit_list"
    HABIT_UPDATE = "habit_update"
    HABIT_COMPLETE = "habit_complete"
    HABIT_UNDO_COMPLETION = "habit_undo_completion"
    HABIT_STATISTICS = "habit_statistics"
    HABIT_ARCHIVE = "habit_archive"
    CLARIFY = "clarify"


class ActionRiskLevel(StrEnum):
    """Deterministic risk classification for productivity proposals."""

    LEVEL_1 = "level_1"  # Clear, reversible
    LEVEL_2 = "level_2"  # Ambiguous -> clarify
    LEVEL_3 = "level_3"  # Higher-risk -> explicit confirmation


class ParsedDateResult(BaseModel):
    """Validated local-timezone date/time resolution result."""

    model_config = ConfigDict(frozen=True)

    raw_expression: str
    resolved_at: datetime | None = None
    is_date_only: bool = True
    display_text: str = ""
    ambiguous: bool = False
    error: str | None = None


class ProductivityProposal(BaseModel):
    """Strict Pydantic contract for NL productivity intent extraction.

    The LLM/interpreter may ONLY propose search queries and field values — NEVER database IDs,
    permission flags, or arbitrary tool/function names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: ProductivityIntent = ProductivityIntent.NONE
    target_query: str | None = None
    title: str | None = None
    description: str | None = None
    due_expression: str | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    frequency: HabitFrequency | None = None
    target_count: int | None = Field(default=None, ge=1, le=31)
    task_bucket: str | None = None
    clarification_question: str | None = None
    uses_context_reference: bool = False
    ordinal_index: int | None = None  # 1-based index ("the second one" -> 2)


class TargetResolutionStatus(StrEnum):
    """Deterministic target-resolution outcome."""

    MATCHED = "matched"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class TargetResolutionResult(BaseModel):
    """Result of deterministic target resolution against persisted records."""

    model_config = ConfigDict(frozen=True)

    status: TargetResolutionStatus
    entity_type: str  # "task" | "habit"
    matched_id: str | None = None
    matched_label: str | None = None
    candidates: tuple[dict[str, str], ...] = ()
    message: str = ""


class ProductivityActionResult(BaseModel):
    """Verified result of executing or gating a productivity action."""

    model_config = ConfigDict(frozen=True)

    action_id: str | None = None
    intent: ProductivityIntent
    risk_level: ActionRiskLevel
    status: str  # "executed" | "clarify" | "pending_confirmation" | "failed" | "none"
    entity_type: str | None = None  # "task" | "habit"
    entity_id: str | None = None
    entity_label: str | None = None
    summary: str
    changed_fields: dict[str, Any] = Field(default_factory=dict)
    undo_supported: bool = False
    candidates: tuple[dict[str, str], ...] = ()
    error: str | None = None
