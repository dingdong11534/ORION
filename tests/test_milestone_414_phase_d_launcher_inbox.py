"""Tests for Milestone 4.14 Phase D: ApplicationLauncher, ORION-Inbox, and Audit."""

import stat

import pytest
from alembic.config import Config

from alembic import command
from orion.application.launcher import (
    ApplicationApprovalError,
    ApplicationRegistryService,
)
from orion.infrastructure.downloads import InboxPolicyError, OrionInboxService
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)
from orion.infrastructure.persistence.models import AutomationAuditEventModel


def test_application_launcher_requires_approval_allowlists_args_and_audits(tmp_path) -> None:
    db_path = tmp_path / "orion.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")
    engine = create_db_engine(db_path)
    init_database(engine)
    session_factory = create_session_factory(engine)
    store = SqliteBackgroundJobStore(session_factory)

    fake_exe = tmp_path / "sample-app"
    fake_exe.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    fake_exe.chmod(0o755)

    captured_calls: list[tuple[list[str], bool]] = []

    class _FakeProc:
        pid = 42424

    def _fake_popen(argv: list[str], *, shell: bool = False, **kwargs: object) -> _FakeProc:
        captured_calls.append((argv, shell))
        return _FakeProc()

    svc = ApplicationRegistryService(session_factory, audit_store=store, popen_factory=_fake_popen)
    svc.register_application(
        "sample-app",
        "Sample App",
        str(fake_exe),
        allowed_args=("--safe-flag",),
        approved_by_user=False,
    )

    # Unapproved launch must fail & be audited
    with pytest.raises(ApplicationApprovalError):
        svc.launch_application("sample-app")

    # Approve app, then disallowed arg must fail & be audited
    svc.approve_application("sample-app", approved=True)
    with pytest.raises(ApplicationApprovalError):
        svc.launch_application("sample-app", args=("--evil-flag",))

    # Approved app with allowed arg launches with shell=False
    res = svc.launch_application("sample-app", args=("--safe-flag",))
    assert res.pid == 42424
    assert captured_calls == [([str(fake_exe.resolve()), "--safe-flag"], False)]

    with session_factory() as session:
        events = session.query(AutomationAuditEventModel).all()
        assert len(events) == 3
        assert events[-1].status == "succeeded"
        assert events[-1].approval_state == "approved"

    engine.dispose()


def test_orion_inbox_stages_safely_and_blocks_executables_and_oversized(tmp_path) -> None:
    inbox = OrionInboxService(inbox_dir=tmp_path / "ORION-Inbox", max_file_bytes=64)

    staged = inbox.stage_bytes(b"hello world", "../../report.txt", content_type="text/plain")
    assert staged.path.parent == inbox.inbox_dir
    assert staged.path.name.startswith("report_")
    assert staged.path.suffix == ".txt"
    assert stat.S_IMODE(inbox.inbox_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(staged.path.stat().st_mode) == 0o600

    with pytest.raises(InboxPolicyError, match="exceeds limit"):
        inbox.stage_bytes(b"x" * 100, "big.txt", content_type="text/plain")

    with pytest.raises(InboxPolicyError, match="Blocked executable extension"):
        inbox.stage_bytes(b"#!/bin/sh", "run.sh", content_type="text/plain")

    with pytest.raises(InboxPolicyError, match="Blocked executable content type"):
        inbox.stage_bytes(b"binary", "payload.dat", content_type="application/x-executable")
