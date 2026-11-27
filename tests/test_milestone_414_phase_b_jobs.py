"""Tests for Milestone 4.14 Phase B: migration 0011, BackgroundJobSupervisor, recovery, approval."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from orion.application.jobs.contracts import JobStatus, StepStatus
from orion.application.jobs.supervisor import ApprovalRequiredSignal, BackgroundJobSupervisor
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)


def test_alembic_0011_reversible(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    command.downgrade(cfg, "0010")
    command.upgrade(cfg, "0011")


def test_job_checkpointing_approval_pause_and_non_idempotent_recovery(tmp_path: Path):
    db_path = tmp_path / "sup.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    eng = create_db_engine(db_path)
    init_database(eng, apply_wal=True)
    sf = create_session_factory(eng)
    store = SqliteBackgroundJobStore(sf)

    notifications: list[tuple[str, str]] = []

    class _SpyNotifier:
        def notify(self, title: str, body: str) -> None:
            notifications.append((title, body))

    sup = BackgroundJobSupervisor(store, notifier=_SpyNotifier())
    calls: list[str] = []

    def step1(job, ctx):
        calls.append("s1")
        ctx["prepared"] = True
        return "prepared"

    def step2(job, ctx):
        if not ctx.get("approved"):
            raise ApprovalRequiredSignal("Need user approval before external action")
        calls.append("s2")
        return "sent"

    sup.register_workflow("send_flow", [("prepare", True, step1), ("send", False, step2)])
    job = sup.submit_job(user_request_summary="Test approval flow", workflow_type="send_flow")

    # Run until WAITING_FOR_APPROVAL
    res1 = sup.run_next_job()
    assert res1 is not None and res1.status == JobStatus.WAITING_FOR_APPROVAL
    assert calls == ["s1"]

    # Approve and resume -> step1 is NOT re-run because it already SUCCEEDED!
    store.approve_job(job.id)
    res2 = sup.run_next_job()
    assert res2 is not None and res2.status == JobStatus.SUCCEEDED
    assert calls == ["s1", "s2"]
    assert any("Completed" in t for t, _ in notifications)

    # Non-idempotent step interrupted mid-flight -> recovery must NOT replay it
    job2 = sup.submit_job(
        user_request_summary="Crash mid non-idempotent", workflow_type="send_flow"
    )
    store.mark_step_status(job2.id, 0, StepStatus.SUCCEEDED)
    store.mark_step_status(job2.id, 1, StepStatus.RUNNING)
    with eng.begin() as conn:
        stale_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        conn.execute(
            sa.text(
                "UPDATE automation_jobs SET status='RUNNING', last_heartbeat_at=:ts WHERE id=:jid"
            ),
            {"ts": stale_ts, "jid": job2.id},
        )
    recovered = sup.recover_on_startup(stale_seconds=60.0)
    assert job2.id in recovered
    res_crash = sup.run_next_job()
    assert res_crash is not None and res_crash.status == JobStatus.FAILED
    assert "Non-idempotent step" in (res_crash.error_summary or "")
    assert calls == ["s1", "s2"]  # step2 was NOT replayed!
    eng.dispose()
