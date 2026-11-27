"""SQLite repositories for UserProfile, PersonalMemories, and LocalReminders."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from orion.application.memory.contracts import (
    MemoryCategory,
    MemoryStatus,
    PersonalMemoryRecord,
    ReminderRecord,
    UserProfileRecord,
)
from orion.infrastructure.persistence.models import (
    ActivityEventModel,
    LocalReminderModel,
    PersonalMemoryModel,
    UserProfileModel,
)


def _utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SqlitePersonalMemoryStore:
    """Transactional SQLite store for trusted user profile and reviewable memories."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory

    def get_profile(self) -> UserProfileRecord:
        with self._sf() as session:
            row = session.get(UserProfileModel, "default")
            if row is None:
                return UserProfileRecord(updated_at=datetime.now(UTC))
            return UserProfileRecord(
                id=row.id,
                preferred_name=row.preferred_name,
                timezone=row.timezone,
                response_preferences=row.response_preferences,
                work_study_preferences=row.work_study_preferences,
                updated_at=_utc(row.updated_at),
            )

    def save_profile(
        self,
        *,
        preferred_name: str | None = None,
        timezone: str = "Asia/Kolkata",
        response_preferences: str | None = None,
        work_study_preferences: str | None = None,
    ) -> UserProfileRecord:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(UserProfileModel, "default")
            if row is None:
                row = UserProfileModel(id="default", updated_at=now)
                session.add(row)
            row.preferred_name = (preferred_name or "").strip() or None
            row.timezone = (timezone or "Asia/Kolkata").strip()
            row.response_preferences = (response_preferences or "").strip() or None
            row.work_study_preferences = (work_study_preferences or "").strip() or None
            row.updated_at = now
            session.add(
                ActivityEventModel(
                    id=f"evt_{uuid.uuid4().hex}",
                    event_type="profile_updated",
                    aggregate_type="user_profile",
                    aggregate_id="default",
                    payload_json={"timezone": row.timezone},
                    created_at=now,
                )
            )
            session.flush()
            return UserProfileRecord(
                id=row.id,
                preferred_name=row.preferred_name,
                timezone=row.timezone,
                response_preferences=row.response_preferences,
                work_study_preferences=row.work_study_preferences,
                updated_at=_utc(row.updated_at),
            )

    def add_memory(
        self,
        *,
        category: MemoryCategory,
        title: str,
        content: str,
        source: str = "user_explicit",
    ) -> PersonalMemoryRecord:
        now = datetime.now(UTC)
        mid = f"mem_{uuid.uuid4().hex[:16]}"
        with self._sf() as session, session.begin():
            row = PersonalMemoryModel(
                id=mid,
                category=category.value,
                title=title.strip()[:200],
                content=content.strip(),
                source=source,
                status=MemoryStatus.ACTIVE.value,
                confidence=1.0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.add(
                ActivityEventModel(
                    id=f"evt_{uuid.uuid4().hex}",
                    event_type="memory_saved",
                    aggregate_type="personal_memory",
                    aggregate_id=mid,
                    payload_json={"category": category.value, "title": row.title},
                    created_at=now,
                )
            )
            session.flush()
            return self._to_record(row)

    def list_memories(
        self,
        *,
        category: MemoryCategory | None = None,
        include_forgotten: bool = False,
    ) -> list[PersonalMemoryRecord]:
        with self._sf() as session:
            stmt = select(PersonalMemoryModel)
            if category is not None:
                stmt = stmt.where(PersonalMemoryModel.category == category.value)
            if not include_forgotten:
                stmt = stmt.where(PersonalMemoryModel.status != MemoryStatus.FORGOTTEN.value)
            stmt = stmt.order_by(PersonalMemoryModel.updated_at.desc())
            return [self._to_record(r) for r in session.execute(stmt).scalars().all()]

    def update_memory(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        category: MemoryCategory | None = None,
        status: MemoryStatus | None = None,
    ) -> PersonalMemoryRecord:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(PersonalMemoryModel, memory_id)
            if row is None:
                raise ValueError(f"Memory {memory_id!r} not found.")
            if title is not None:
                row.title = title.strip()[:200]
            if content is not None:
                row.content = content.strip()
            if category is not None:
                row.category = category.value
            if status is not None:
                row.status = status.value
            row.updated_at = now
            session.flush()
            return self._to_record(row)

    def delete_memory(self, memory_id: str) -> None:
        with self._sf() as session, session.begin():
            row = session.get(PersonalMemoryModel, memory_id)
            if row is not None:
                session.delete(row)

    @staticmethod
    def _to_record(row: PersonalMemoryModel) -> PersonalMemoryRecord:
        return PersonalMemoryRecord(
            id=row.id,
            category=MemoryCategory(row.category),
            title=row.title,
            content=row.content,
            source=row.source,
            status=MemoryStatus(row.status),
            confidence=float(row.confidence),
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )


class SqliteReminderStore:
    """SQLite persistence for local reminders."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory

    def create_reminder(
        self,
        *,
        title: str,
        remind_time: str = "09:00",
        entity_type: str = "checkin",
        entity_id: str | None = None,
        quiet_start: str = "22:00",
        quiet_end: str = "07:00",
    ) -> ReminderRecord:
        now = datetime.now(UTC)
        rid = f"rem_{uuid.uuid4().hex[:16]}"
        with self._sf() as session, session.begin():
            row = LocalReminderModel(
                id=rid,
                title=title.strip()[:200],
                entity_type=entity_type,
                entity_id=entity_id,
                remind_time=remind_time,
                quiet_start=quiet_start,
                quiet_end=quiet_end,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return self._to_record(row)

    def list_reminders(self) -> list[ReminderRecord]:
        with self._sf() as session:
            rows = (
                session.execute(
                    select(LocalReminderModel).order_by(LocalReminderModel.remind_time.asc())
                )
                .scalars()
                .all()
            )
            return [self._to_record(r) for r in rows]

    def set_enabled(self, reminder_id: str, enabled: bool) -> ReminderRecord:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(LocalReminderModel, reminder_id)
            if row is None:
                raise ValueError(f"Reminder {reminder_id!r} not found.")
            row.enabled = bool(enabled)
            row.updated_at = now
            session.flush()
            return self._to_record(row)

    def delete_reminder(self, reminder_id: str) -> None:
        with self._sf() as session, session.begin():
            row = session.get(LocalReminderModel, reminder_id)
            if row is not None:
                session.delete(row)

    @staticmethod
    def _to_record(row: LocalReminderModel) -> ReminderRecord:
        return ReminderRecord(
            id=row.id,
            title=row.title,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            remind_time=row.remind_time,
            quiet_start=row.quiet_start,
            quiet_end=row.quiet_end,
            enabled=bool(row.enabled),
            last_triggered_date=row.last_triggered_date,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )
