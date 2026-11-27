"""Application ports for BackgroundJobSupervisor, BrowserService, and Notifications."""

from typing import Any, Protocol

from orion.application.jobs.contracts import (
    AutomationJobRecord,
    AutomationJobStepRecord,
    JobStatus,
    StepStatus,
)


class BackgroundJobStorePort(Protocol):
    def enqueue_job(
        self,
        *,
        user_request_summary: str,
        workflow_type: str,
        conversation_id: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        steps: list[tuple[str, bool]] | None = None,
    ) -> AutomationJobRecord: ...

    def get_job(self, job_id: str) -> AutomationJobRecord | None: ...

    def list_jobs(self, *, limit: int = 50) -> list[AutomationJobRecord]: ...

    def list_steps(self, job_id: str) -> list[AutomationJobStepRecord]: ...

    def claim_next_queued(self) -> AutomationJobRecord | None: ...

    def heartbeat(self, job_id: str) -> None: ...

    def update_checkpoint(self, job_id: str, checkpoint: dict[str, Any]) -> None: ...

    def set_browser_lease(self, job_id: str, lease_id: str | None) -> None: ...

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_summary: str | None = None,
    ) -> AutomationJobRecord: ...

    def mark_step_status(
        self,
        job_id: str,
        step_index: int,
        status: StepStatus,
        *,
        output_summary: str | None = None,
        error_summary: str | None = None,
    ) -> None: ...

    def request_cancellation(self, job_id: str) -> AutomationJobRecord: ...

    def approve_job(self, job_id: str) -> AutomationJobRecord: ...

    def recover_stale_running_jobs(self, *, stale_seconds: float = 60.0) -> list[str]: ...

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
    ) -> None: ...


class NotificationPort(Protocol):
    def notify(self, title: str, body: str) -> None: ...
