"""SQLite repository for AutomationJobs, AutomationJobSteps, and AutomationAuditEvents."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from orion.application.jobs.contracts import (
    AutomationJobRecord,
    AutomationJobStepRecord,
    JobStatus,
    StepStatus,
)
from orion.infrastructure.persistence.models import (
    AutomationAuditEventModel,
    AutomationJobModel,
    AutomationJobStepModel,
)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _job_to_record(row: AutomationJobModel) -> AutomationJobRecord:
    return AutomationJobRecord(
        id=row.id,
        conversation_id=row.conversation_id,
        user_request_summary=row.user_request_summary,
        workflow_type=row.workflow_type,
        status=JobStatus(row.status),
        browser_lease_id=row.browser_lease_id,
        checkpoint=json.loads(row.checkpoint_json or "{}"),
        cancellation_requested=bool(row.cancellation_requested),
        created_at=_utc(row.created_at) or datetime.now(UTC),
        started_at=_utc(row.started_at),
        updated_at=_utc(row.updated_at) or datetime.now(UTC),
        completed_at=_utc(row.completed_at),
        last_heartbeat_at=_utc(row.last_heartbeat_at),
        error_summary=row.error_summary,
    )


def _step_to_record(row: AutomationJobStepModel) -> AutomationJobStepRecord:
    return AutomationJobStepRecord(
        id=row.id,
        job_id=row.job_id,
        step_index=row.step_index,
        step_name=row.step_name,
        idempotent=bool(row.idempotent),
        status=StepStatus(row.status),
        input_summary=row.input_summary,
        output_summary=row.output_summary,
        started_at=_utc(row.started_at),
        completed_at=_utc(row.completed_at),
        error_summary=row.error_summary,
    )


class SqliteBackgroundJobStore:
    """Short-lived transactional SQLite store safe for separate desktop + worker processes."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory

    def enqueue_job(
        self,
        *,
        user_request_summary: str,
        workflow_type: str,
        conversation_id: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        steps: list[tuple[str, bool]] | None = None,
    ) -> AutomationJobRecord:
        now = datetime.now(UTC)
        jid = f"job_{uuid.uuid4().hex[:16]}"
        with self._sf() as session, session.begin():
            row = AutomationJobModel(
                id=jid,
                conversation_id=conversation_id,
                user_request_summary=user_request_summary.strip(),
                workflow_type=workflow_type.strip(),
                status=JobStatus.QUEUED.value,
                checkpoint_json=json.dumps(checkpoint or {}),
                cancellation_requested=False,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            for idx, (sname, idem) in enumerate(steps or []):
                session.add(
                    AutomationJobStepModel(
                        id=f"stp_{uuid.uuid4().hex[:16]}",
                        job_id=jid,
                        step_index=idx,
                        step_name=sname,
                        idempotent=bool(idem),
                        status=StepStatus.PENDING.value,
                    )
                )
            session.flush()
            return _job_to_record(row)

    def get_job(self, job_id: str) -> AutomationJobRecord | None:
        with self._sf() as session:
            row = session.get(AutomationJobModel, job_id)
            return _job_to_record(row) if row is not None else None

    def list_jobs(self, *, limit: int = 50) -> list[AutomationJobRecord]:
        with self._sf() as session:
            rows = (
                session.execute(
                    select(AutomationJobModel)
                    .order_by(AutomationJobModel.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_job_to_record(r) for r in rows]

    def list_steps(self, job_id: str) -> list[AutomationJobStepRecord]:
        with self._sf() as session:
            rows = (
                session.execute(
                    select(AutomationJobStepModel)
                    .where(AutomationJobStepModel.job_id == job_id)
                    .order_by(AutomationJobStepModel.step_index.asc())
                )
                .scalars()
                .all()
            )
            return [_step_to_record(r) for r in rows]

    def claim_next_queued(self) -> AutomationJobRecord | None:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            # Atomic compare-and-swap claim safe across concurrent SQLite worker processes
            claimed_id = session.execute(
                text(
                    """
                    UPDATE automation_jobs
                    SET status = :running_status,
                        started_at = COALESCE(started_at, :now),
                        last_heartbeat_at = :now,
                        updated_at = :now
                    WHERE id = (
                        SELECT id FROM automation_jobs
                        WHERE status = :queued_status
                        ORDER BY created_at ASC
                        LIMIT 1
                    )
                    AND status = :queued_status
                    RETURNING id
                    """
                ),
                {
                    "running_status": JobStatus.RUNNING.value,
                    "queued_status": JobStatus.QUEUED.value,
                    "now": now.replace(tzinfo=None),
                },
            ).scalar_one_or_none()
            if claimed_id is None:
                return None
            row = session.get(AutomationJobModel, claimed_id)
            return _job_to_record(row) if row is not None else None

    def heartbeat(self, job_id: str) -> None:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(AutomationJobModel, job_id)
            if row is not None:
                row.last_heartbeat_at = now
                row.updated_at = now

    def update_checkpoint(self, job_id: str, checkpoint: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(AutomationJobModel, job_id)
            if row is not None:
                row.checkpoint_json = json.dumps(checkpoint, default=str)
                row.updated_at = now

    def set_browser_lease(self, job_id: str, lease_id: str | None) -> None:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(AutomationJobModel, job_id)
            if row is not None:
                row.browser_lease_id = lease_id
                row.updated_at = now

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_summary: str | None = None,
    ) -> AutomationJobRecord:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(AutomationJobModel, job_id)
            if row is None:
                raise ValueError(f"Job {job_id!r} not found.")
            row.status = status.value
            row.error_summary = error_summary
            row.updated_at = now
            if status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
                row.completed_at = now
            session.flush()
            return _job_to_record(row)

    def mark_step_status(
        self,
        job_id: str,
        step_index: int,
        status: StepStatus,
        *,
        output_summary: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = (
                session.execute(
                    select(AutomationJobStepModel).where(
                        AutomationJobStepModel.job_id == job_id,
                        AutomationJobStepModel.step_index == step_index,
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return
            row.status = status.value
            if status == StepStatus.RUNNING and row.started_at is None:
                row.started_at = now
            if status in (StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED):
                row.completed_at = now
            if output_summary is not None:
                row.output_summary = output_summary
            if error_summary is not None:
                row.error_summary = error_summary

    def request_cancellation(self, job_id: str) -> AutomationJobRecord:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(AutomationJobModel, job_id)
            if row is None:
                raise ValueError(f"Job {job_id!r} not found.")
            row.cancellation_requested = True
            if row.status in (JobStatus.QUEUED.value, JobStatus.WAITING_FOR_APPROVAL.value):
                row.status = JobStatus.CANCELLED.value
                row.completed_at = now
            row.updated_at = now
            session.flush()
            return _job_to_record(row)

    def approve_job(self, job_id: str) -> AutomationJobRecord:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            row = session.get(AutomationJobModel, job_id)
            if row is None:
                raise ValueError(f"Job {job_id!r} not found.")
            if row.status != JobStatus.WAITING_FOR_APPROVAL.value:
                raise ValueError(f"Job {job_id!r} is not waiting for approval ({row.status}).")
            cp = json.loads(row.checkpoint_json or "{}")
            cp["approved"] = True
            row.checkpoint_json = json.dumps(cp)
            row.status = JobStatus.QUEUED.value
            row.error_summary = None
            row.updated_at = now
            session.flush()
            return _job_to_record(row)

    def recover_stale_running_jobs(self, *, stale_seconds: float = 60.0) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
        recovered: list[str] = []
        with self._sf() as session, session.begin():
            rows = (
                session.execute(
                    select(AutomationJobModel).where(
                        AutomationJobModel.status == JobStatus.RUNNING.value
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                hb = _utc(row.last_heartbeat_at) or _utc(row.updated_at)
                if hb is not None and hb < cutoff:
                    row.status = JobStatus.QUEUED.value
                    row.updated_at = datetime.now(UTC)
                    recovered.append(row.id)
        return recovered

    def record_audit_event(
        self,
        *,
        job_id: str | None,
        capability_name: str,
        risk_level: str,
        request_origin: str,
        approval_state: str,
        status: str,
        error_summary: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self._sf() as session, session.begin():
            session.add(
                AutomationAuditEventModel(
                    id=f"aud_{uuid.uuid4().hex[:16]}",
                    job_id=job_id,
                    capability_name=capability_name,
                    risk_level=risk_level,
                    request_origin=request_origin,
                    approval_state=approval_state,
                    started_at=now,
                    ended_at=now,
                    status=status,
                    error_summary=error_summary,
                )
            )
