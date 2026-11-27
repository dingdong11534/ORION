"""Persistent background job runtime contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AutomationJobStepRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    job_id: str
    step_index: int
    step_name: str
    idempotent: bool = True
    status: StepStatus = StepStatus.PENDING
    input_summary: str | None = None
    output_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_summary: str | None = None


class AutomationJobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    conversation_id: str | None = None
    user_request_summary: str
    workflow_type: str
    status: JobStatus = JobStatus.QUEUED
    browser_lease_id: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    cancellation_requested: bool = False
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    error_summary: str | None = None
