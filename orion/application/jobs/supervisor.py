"""BackgroundJobSupervisor: executes checkpointed jobs independently of UI/session state."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from orion.application.jobs.contracts import AutomationJobRecord, JobStatus, StepStatus
from orion.application.jobs.ports import BackgroundJobStorePort, NotificationPort

logger = logging.getLogger(__name__)


class ApprovalRequiredSignal(Exception):
    """Raised by a workflow step when user approval is required before proceeding."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


StepRunner = Callable[[AutomationJobRecord, dict[str, Any]], str | None]


class BackgroundJobSupervisor:
    """Runs queued automation jobs, handles safe checkpoint resume, cancellation, and leases."""

    def __init__(
        self,
        store: BackgroundJobStorePort,
        *,
        browser_service: Any = None,
        notifier: NotificationPort | None = None,
    ) -> None:
        self._store = store
        self._browser = browser_service
        self._notifier = notifier
        self._workflows: dict[str, list[tuple[str, bool, StepRunner]]] = {}

    def register_workflow(
        self, workflow_type: str, steps: list[tuple[str, bool, StepRunner]]
    ) -> None:
        """Register a workflow as ordered (step_name, idempotent, runner_fn) tuples."""
        self._workflows[workflow_type] = list(steps)

    def submit_job(
        self,
        *,
        user_request_summary: str,
        workflow_type: str,
        conversation_id: str | None = None,
        initial_checkpoint: dict[str, Any] | None = None,
    ) -> AutomationJobRecord:
        if workflow_type not in self._workflows:
            raise ValueError(f"Unknown workflow_type: {workflow_type!r}")
        step_defs = [(name, idem) for name, idem, _ in self._workflows[workflow_type]]
        return self._store.enqueue_job(
            user_request_summary=user_request_summary,
            workflow_type=workflow_type,
            conversation_id=conversation_id,
            checkpoint=initial_checkpoint or {},
            steps=step_defs,
        )

    def run_next_job(self) -> AutomationJobRecord | None:
        """Claim and execute the next QUEUED job. Returns the final job record, or None."""
        job = self._store.claim_next_queued()
        if job is None:
            return None
        return self._execute_job(job)

    def recover_on_startup(self, *, stale_seconds: float = 60.0) -> list[str]:
        """Recover stale RUNNING jobs after a worker crash without replaying non-idempotent steps."""
        return self._store.recover_stale_running_jobs(stale_seconds=stale_seconds)

    def approve_job(self, job_id: str) -> AutomationJobRecord:
        """Explicitly approve a WAITING_FOR_APPROVAL job so it returns to QUEUED."""
        return self._store.approve_job(job_id)

    def cancel_job(self, job_id: str) -> AutomationJobRecord:
        """Request cancellation for a queued, waiting, or running job."""
        return self._store.request_cancellation(job_id)

    def _notify(self, title: str, body: str) -> None:
        if self._notifier is not None:
            try:
                self._notifier.notify(title, body)
            except Exception:
                logger.debug("Notification delivery failed for %r", title, exc_info=True)

    def _release_lease_if_any(self, job_id: str) -> None:
        job = self._store.get_job(job_id)
        if job and job.browser_lease_id and self._browser is not None:
            try:
                self._browser.release_lease(job.browser_lease_id)
            finally:
                self._store.set_browser_lease(job_id, None)

    def _execute_job(self, job: AutomationJobRecord) -> AutomationJobRecord:
        workflow_steps = self._workflows.get(job.workflow_type)
        if not workflow_steps:
            res = self._store.set_status(
                job.id, JobStatus.FAILED, error_summary=f"Unknown workflow: {job.workflow_type}"
            )
            self._notify("ORION Job Failed", f"{job.user_request_summary}: unknown workflow")
            return res

        db_steps = {s.step_index: s for s in self._store.list_steps(job.id)}
        ctx = dict(job.checkpoint)

        try:
            for idx, (step_name, idempotent, runner) in enumerate(workflow_steps):
                current = self._store.get_job(job.id)
                if current is None or current.cancellation_requested:
                    self._release_lease_if_any(job.id)
                    res = self._store.set_status(job.id, JobStatus.CANCELLED)
                    self._notify("ORION Job Cancelled", job.user_request_summary)
                    return res

                st = db_steps.get(idx)
                if st is not None:
                    if st.status == StepStatus.SUCCEEDED:
                        continue
                    # Never replay a non-idempotent step that already started before a crash
                    if st.status == StepStatus.RUNNING and not idempotent:
                        self._release_lease_if_any(job.id)
                        msg = (
                            f"Non-idempotent step '{step_name}' was interrupted; "
                            "cannot replay safely without user review."
                        )
                        res = self._store.set_status(job.id, JobStatus.FAILED, error_summary=msg)
                        self._notify("ORION Job Failed", msg)
                        return res

                self._store.heartbeat(job.id)
                self._store.mark_step_status(job.id, idx, StepStatus.RUNNING)
                hb_stop = threading.Event()

                def _beat(jid: str = job.id, stop_ev: threading.Event = hb_stop) -> None:
                    while not stop_ev.wait(5.0):
                        try:
                            self._store.heartbeat(jid)
                        except Exception:
                            logger.debug("Background heartbeat failed", exc_info=True)

                hb_thread = threading.Thread(target=_beat, daemon=True)
                hb_thread.start()
                try:
                    out = runner(current, ctx)
                except ApprovalRequiredSignal as req:
                    self._store.update_checkpoint(job.id, ctx)
                    self._store.mark_step_status(
                        job.id, idx, StepStatus.PENDING, output_summary=req.reason
                    )
                    res = self._store.set_status(
                        job.id, JobStatus.WAITING_FOR_APPROVAL, error_summary=req.reason
                    )
                    self._notify("ORION Approval Required", req.reason)
                    return res
                finally:
                    hb_stop.set()
                    hb_thread.join(timeout=1.0)

                self._store.update_checkpoint(job.id, ctx)
                self._store.mark_step_status(job.id, idx, StepStatus.SUCCEEDED, output_summary=out)

            self._release_lease_if_any(job.id)
            res = self._store.set_status(job.id, JobStatus.SUCCEEDED)
            self._notify("ORION Job Completed", job.user_request_summary)
            return res
        except Exception as exc:  # noqa: BLE001
            self._release_lease_if_any(job.id)
            res = self._store.set_status(job.id, JobStatus.FAILED, error_summary=str(exc))
            self._notify("ORION Job Failed", f"{job.user_request_summary}: {exc}")
            return res
