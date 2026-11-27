"""Tests for Milestone 4.14 Phase E: InteractionSession & background job decoupling."""

from pathlib import Path

from alembic.config import Config

from alembic import command
from orion.application.jobs.contracts import JobStatus
from orion.application.jobs.supervisor import BackgroundJobSupervisor
from orion.application.session import InteractionSession, SessionState
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)


class _StubVerifier:
    def __init__(self, *, enrolled: bool, valid_token: bytes) -> None:
        self._enrolled = enrolled
        self._valid_token = valid_token

    def is_enrolled(self) -> bool:
        return self._enrolled

    def verify_owner(self, audio_sample: bytes) -> bool:
        return audio_sample == self._valid_token


def test_interaction_session_starts_locked_verifies_owner_and_has_no_auto_lock() -> None:
    verifier = _StubVerifier(enrolled=True, valid_token=b"owner-voice")
    session = InteractionSession(enabled=True, speaker_verifier=verifier)

    assert session.state == SessionState.LOCKED
    assert not session.can_accept_interactive_command()

    # Wake word alone must NOT unlock
    assert session.on_wake_detected() == SessionState.WAKE_DETECTED
    assert not session.can_accept_interactive_command()

    # Wrong voice sample returns to LOCKED
    assert session.verify_and_unlock(b"imposter") is False
    assert session.state == SessionState.LOCKED
    assert not session.can_accept_interactive_command()

    # Enrolled owner sample unlocks
    assert session.on_wake_detected() == SessionState.WAKE_DETECTED
    assert session.verify_and_unlock(b"owner-voice") is True
    assert session.state == SessionState.UNLOCKED
    assert session.can_accept_interactive_command()

    # Manual lock transitions back to LOCKED
    assert session.lock() == SessionState.LOCKED
    assert not session.can_accept_interactive_command()


def test_locking_interaction_session_never_cancels_background_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "session_jobs.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)

    store = SqliteBackgroundJobStore(sf)
    sup = BackgroundJobSupervisor(store)
    session = InteractionSession(enabled=True)
    session.unlock_manual()

    def step_work(job, ctx):
        # Simulate user locking the interaction session mid-job
        session.lock()
        ctx["completed_while_locked"] = True
        return "done"

    sup.register_workflow("locked_ok", [("work", True, step_work)])
    job = sup.submit_job(user_request_summary="Run across lock", workflow_type="locked_ok")

    assert session.state == SessionState.UNLOCKED
    final_job = sup.run_next_job()
    assert final_job is not None
    assert final_job.id == job.id
    assert session.state == SessionState.LOCKED
    assert final_job.status == JobStatus.SUCCEEDED

    engine.dispose()
