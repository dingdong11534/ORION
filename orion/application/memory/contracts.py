"""Personal memory, user profile, and local reminder contracts."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MemoryCategory(StrEnum):
    FACT = "fact"
    PROJECT = "project"
    GOAL = "goal"
    ROUTINE = "routine"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    FORGOTTEN = "forgotten"


class UserProfileRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = "default"
    preferred_name: str | None = None
    timezone: str = "Asia/Kolkata"
    response_preferences: str | None = None
    work_study_preferences: str | None = None
    updated_at: datetime


class PersonalMemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    category: MemoryCategory = MemoryCategory.FACT
    title: str
    content: str
    source: str = "user_explicit"
    status: MemoryStatus = MemoryStatus.ACTIVE
    confidence: float = 1.0
    created_at: datetime
    updated_at: datetime


class ReminderRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    entity_type: str = "checkin"
    entity_id: str | None = None
    remind_time: str = "09:00"
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    enabled: bool = True
    last_triggered_date: str | None = None
    created_at: datetime
    updated_at: datetime
