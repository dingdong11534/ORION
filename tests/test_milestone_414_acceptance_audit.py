"""Milestone 4.14 Engineering Acceptance Audit Tests (real processes, real Chrome, real QML)."""

import http.server
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from alembic.config import Config
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.jobs.contracts import JobStatus
from orion.application.jobs.supervisor import ApprovalRequiredSignal, BackgroundJobSupervisor
from orion.application.session import InteractionSession, SessionState
from orion.infrastructure.browser import PlaywrightBrowserService
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_database,
)
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.main import create_model_provider
from orion.ui.qt_bridge import OrionBridge


def _init_db(db_path: Path) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0011")


def test_audit_1_background_job_independence_across_lock_orb_hide_and_window_close(
    tmp_path: Path, monkeypatch
) -> None:
    """Real worker subprocess continues & completes job while QML locks, hides orb, and closes."""
    db_path = tmp_path / "audit1.db"
    _init_db(db_path)

    marker_file = tmp_path / "job_completed.txt"
    worker_script = tmp_path / "run_worker.py"
    worker_script.write_text(
        f"""
import signal, sys, time
from pathlib import Path
from orion.application.jobs.supervisor import BackgroundJobSupervisor
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory, init_database

engine = create_db_engine({str(db_path)!r})
init_database(engine, apply_wal=True)
sf = create_session_factory(engine)
store = SqliteBackgroundJobStore(sf)
sup = BackgroundJobSupervisor(store)

def slow_step(job, ctx):
    time.sleep(1.0)
    Path({str(marker_file)!r}).write_text("done", encoding="utf-8")
    return "completed"

sup.register_workflow("audit_slow", [("slow", True, slow_step)])
stopping = False
signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("stopping", True))
while not stopping:
    ran = sup.run_next_job()
    if ran is not None:
        break
    time.sleep(0.05)
engine.dispose()
""",
        encoding="utf-8",
    )

    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    store = SqliteBackgroundJobStore(sf)
    sup = BackgroundJobSupervisor(store)
    sup.register_workflow("audit_slow", [("slow", True, lambda j, c: "unused")])
    job = sup.submit_job(user_request_summary="Slow independent job", workflow_type="audit_slow")

    worker = subprocess.Popen([sys.executable, str(worker_script)])
    try:
        # Wait until worker claims the job and transitions to RUNNING
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            cur = store.get_job(job.id)
            if cur and cur.status == JobStatus.RUNNING:
                break
            time.sleep(0.05)
        assert store.get_job(job.id).status == JobStatus.RUNNING

        # Launch real QML window, lock session, hide orb, and close main window
        monkeypatch.setenv("ORION_MODEL_PROVIDER", "fake")
        app = QApplication.instance() or QApplication(["orion-audit1"])
        session = InteractionSession(enabled=True)
        session.unlock_manual()
        assistant = AssistantService(
            model_provider=create_model_provider(),
            conversation_store=SqliteConversationStore(sf),
            settings_store=SqliteSettingsStore(sf),
        )
        bridge = OrionBridge(
            assistant_service=assistant,
            session_factory=sf,
            interaction_session=session,
            job_store=store,
            job_supervisor=sup,
        )
        qml = QQmlApplicationEngine()
        qml.rootContext().setContextProperty("orion", bridge)
        qml.load(QUrl.fromLocalFile(str(Path("orion/ui/qml/Main.qml").resolve())))
        root = qml.rootObjects()[0]

        bridge.lockSession()
        assert bridge.sessionState == "locked"
        bridge.setBackgroundMode(True)
        root.setProperty("orbHidden", True)
        root.close()
        app.processEvents()
        del qml
        del bridge

        # Worker must still finish the job
        rc = worker.wait(timeout=10)
        assert rc == 0
        assert marker_file.read_text(encoding="utf-8") == "done"
        final_job = store.get_job(job.id)
        assert final_job is not None
        assert final_job.status == JobStatus.SUCCEEDED
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=3)
        engine.dispose()


def test_audit_2_worker_crash_recovery_idempotent_vs_non_idempotent(tmp_path: Path) -> None:
    """Real SIGKILL crash recovery: idempotent step recovers; non-idempotent never replays."""
    db_path = tmp_path / "audit2.db"
    _init_db(db_path)

    idem_count_file = tmp_path / "idem_count.txt"
    non_idem_count_file = tmp_path / "non_idem_count.txt"
    started_flag = tmp_path / "started.flag"

    def _write_worker_script(
        path: Path, workflow_name: str, idempotent: bool, count_file: Path
    ) -> None:
        path.write_text(
            f"""
import time
from pathlib import Path
from orion.application.jobs.supervisor import BackgroundJobSupervisor
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory, init_database

engine = create_db_engine({str(db_path)!r})
init_database(engine, apply_wal=True)
sf = create_session_factory(engine)
store = SqliteBackgroundJobStore(sf)
sup = BackgroundJobSupervisor(store)
sup.recover_on_startup(stale_seconds=0.0)

def step_fn(job, ctx):
    cf = Path({str(count_file)!r})
    prev = int(cf.read_text()) if cf.exists() else 0
    cf.write_text(str(prev + 1))
    Path({str(started_flag)!r}).write_text("started")
    time.sleep(5.0)
    return "ok"

sup.register_workflow({workflow_name!r}, [("work", {idempotent!r}, step_fn)])
sup.run_next_job()
engine.dispose()
""",
            encoding="utf-8",
        )

    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    store = SqliteBackgroundJobStore(sf)
    sup = BackgroundJobSupervisor(store)
    sup.register_workflow("idem_wf", [("work", True, lambda j, c: "ok")])
    sup.register_workflow("non_idem_wf", [("work", False, lambda j, c: "ok")])

    # Case A: Idempotent step killed mid-execution -> recovered & completed on restart
    job_idem = sup.submit_job(user_request_summary="Idempotent crash", workflow_type="idem_wf")
    w1_script = tmp_path / "w_idem_crash.py"
    _write_worker_script(w1_script, "idem_wf", True, idem_count_file)
    p1 = subprocess.Popen([sys.executable, str(w1_script)])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not started_flag.exists():
        time.sleep(0.05)
    assert started_flag.exists()
    p1.kill()
    p1.wait(timeout=3)

    # Restart worker with fast step -> recovers stale job & completes
    w1_recover = tmp_path / "w_idem_recover.py"
    w1_recover.write_text(
        f"""
from pathlib import Path
from orion.application.jobs.supervisor import BackgroundJobSupervisor
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory, init_database

engine = create_db_engine({str(db_path)!r})
init_database(engine, apply_wal=True)
sf = create_session_factory(engine)
store = SqliteBackgroundJobStore(sf)
sup = BackgroundJobSupervisor(store)
sup.recover_on_startup(stale_seconds=0.0)
sup.register_workflow("idem_wf", [("work", True, lambda j, c: Path({str(idem_count_file)!r}).write_text("2") or "ok")])
sup.run_next_job()
engine.dispose()
""",
        encoding="utf-8",
    )
    assert subprocess.run([sys.executable, str(w1_recover)], check=True).returncode == 0
    assert store.get_job(job_idem.id).status == JobStatus.SUCCEEDED

    # Case B: Non-idempotent step killed mid-execution -> MUST NOT replay on restart
    started_flag.unlink()
    job_non = sup.submit_job(
        user_request_summary="Non-idempotent crash", workflow_type="non_idem_wf"
    )
    w2_script = tmp_path / "w_non_crash.py"
    _write_worker_script(w2_script, "non_idem_wf", False, non_idem_count_file)
    p2 = subprocess.Popen([sys.executable, str(w2_script)])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not started_flag.exists():
        time.sleep(0.05)
    assert started_flag.exists()
    p2.kill()
    p2.wait(timeout=3)
    assert non_idem_count_file.read_text() == "1"

    assert subprocess.run([sys.executable, str(w2_script)], check=True).returncode == 0
    # Side effect count MUST still be 1 (never replayed!)
    assert non_idem_count_file.read_text() == "1"
    failed_job = store.get_job(job_non.id)
    assert failed_job.status == JobStatus.FAILED
    assert "cannot replay safely" in (failed_job.error_summary or "")
    engine.dispose()


def test_audit_3_approval_security_requires_unlocked_session_and_resumes_once(
    tmp_path: Path, monkeypatch
) -> None:
    """WAITING_FOR_APPROVAL stays paused while locked; only explicit unlocked approval resumes once."""
    db_path = tmp_path / "audit3.db"
    _init_db(db_path)

    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    store = SqliteBackgroundJobStore(sf)
    sup = BackgroundJobSupervisor(store)

    exec_count = 0

    def step_gate(job, ctx):
        nonlocal exec_count
        if not ctx.get("approved"):
            raise ApprovalRequiredSignal("Explicit user approval required")
        exec_count += 1
        return "approved-and-done"

    sup.register_workflow("gated_wf", [("gated", False, step_gate)])
    job = sup.submit_job(user_request_summary="Gated action", workflow_type="gated_wf")

    # Worker runs until WAITING_FOR_APPROVAL
    res = sup.run_next_job()
    assert res is not None
    assert res.status == JobStatus.WAITING_FOR_APPROVAL

    monkeypatch.setenv("ORION_MODEL_PROVIDER", "fake")
    session = InteractionSession(enabled=True)  # starts LOCKED
    assert session.state == SessionState.LOCKED
    assistant = AssistantService(
        model_provider=create_model_provider(),
        conversation_store=SqliteConversationStore(sf),
        settings_store=SqliteSettingsStore(sf),
    )
    bridge = OrionBridge(
        assistant_service=assistant,
        session_factory=sf,
        interaction_session=session,
        job_store=store,
        job_supervisor=sup,
    )

    # Worker polling cannot auto-approve
    assert sup.run_next_job() is None
    assert store.get_job(job.id).status == JobStatus.WAITING_FOR_APPROVAL

    # Locked session refuses bridge.approveBackgroundJob
    bridge.approveBackgroundJob(job.id)
    assert store.get_job(job.id).status == JobStatus.WAITING_FOR_APPROVAL
    assert exec_count == 0

    # Unlock session & explicitly approve -> resumes and executes exactly once
    bridge.unlockSessionManual()
    assert bridge.sessionState == "unlocked"
    bridge.approveBackgroundJob(job.id)
    assert store.get_job(job.id).status == JobStatus.QUEUED

    res2 = sup.run_next_job()
    assert res2 is not None
    assert res2.status == JobStatus.SUCCEEDED
    assert exec_count == 1
    assert sup.run_next_job() is None
    assert exec_count == 1
    engine.dispose()


def test_audit_4_browser_redirect_subresource_ssrf_and_no_secret_logs(
    tmp_path: Path, caplog, monkeypatch
) -> None:
    """Real Playwright Chrome blocks redirect chains & private subresources before hitting private server."""
    if not PlaywrightBrowserService(profile_dir=tmp_path / "probe").is_available():
        return

    private_hits: list[str] = []

    class _PrivateHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            private_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"PRIVATE_SECRET")

        def log_message(self, format: str, *args: object) -> None:
            return

    priv_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PrivateHandler)
    priv_port = priv_srv.server_address[1]
    threading.Thread(target=priv_srv.serve_forever, daemon=True).start()

    class _PublicSimHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{priv_port}/stolen-redirect")
                self.end_headers()
                return
            body = (
                f"<html><head><title>Subresource Test</title></head>"
                f"<body><img src='http://127.0.0.1:{priv_port}/img-ssrf' />"
                f"<script>fetch('http://127.0.0.1:{priv_port}/fetch-ssrf').catch(()=>{{}});</script>"
                f"</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "session=SUPER_SECRET_COOKIE_123")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    pub_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PublicSimHandler)
    pub_port = pub_srv.server_address[1]
    threading.Thread(target=pub_srv.serve_forever, daemon=True).start()

    # Allow ONLY pub_port as simulated "public" origin; priv_port remains blocked as 127.0.0.1!
    from orion.application.web.url_guard import validate_public_http_url as real_validate

    def _selective_validate(url: str, *, resolve_dns: bool = True) -> str:
        if url.startswith(f"http://127.0.0.1:{pub_port}/"):
            return url
        return real_validate(url, resolve_dns=resolve_dns)

    monkeypatch.setattr(
        "orion.infrastructure.browser.playwright_browser_service.validate_public_http_url",
        _selective_validate,
    )

    caplog.set_level(logging.DEBUG)
    svc = PlaywrightBrowserService(profile_dir=tmp_path / "browser-profile", resolve_dns=False)
    try:
        with svc.acquire_lease("audit-browser", headless=True) as lease:
            # 1. Page with private-network subresource <img ...> and fetch()
            nav = lease.navigate(f"http://127.0.0.1:{pub_port}/page")
            assert nav.status_code == 200
            time.sleep(0.5)
            assert private_hits == []

            # 2. Redirect chain from allowed page -> 127.0.0.1:priv_port must be aborted
            with pytest.raises((ValueError, RuntimeError, Exception)):
                lease.navigate(f"http://127.0.0.1:{pub_port}/redir")
            assert private_hits == []
    finally:
        svc.close_browser()
        priv_srv.shutdown()
        priv_srv.server_close()
        pub_srv.shutdown()
        pub_srv.server_close()

    assert "SUPER_SECRET_COOKIE_123" not in caplog.text


def test_audit_6_concurrent_workers_atomic_claim_and_cancellation(tmp_path: Path) -> None:
    """Two concurrent worker processes cannot claim the same queued jobs."""
    db_path = tmp_path / "audit6.db"
    _init_db(db_path)

    engine = create_db_engine(db_path)
    init_database(engine, apply_wal=True)
    sf = create_session_factory(engine)
    store = SqliteBackgroundJobStore(sf)

    job_ids = [
        store.enqueue_job(
            user_request_summary=f"Concurrent job {i}",
            workflow_type="noop",
            steps=[("s1", True)],
        ).id
        for i in range(6)
    ]

    # Cancel one job while QUEUED
    cancelled = store.request_cancellation(job_ids[-1])
    assert cancelled.status == JobStatus.CANCELLED

    claimer_script = tmp_path / "claimer.py"
    claimer_script.write_text(
        f"""
import json, sys
from orion.infrastructure.jobs.repository import SqliteBackgroundJobStore
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory, init_database

engine = create_db_engine({str(db_path)!r})
init_database(engine, apply_wal=True)
store = SqliteBackgroundJobStore(create_session_factory(engine))
claimed = []
while True:
    j = store.claim_next_queued()
    if j is None:
        break
    claimed.append(j.id)
print(json.dumps(claimed))
engine.dispose()
""",
        encoding="utf-8",
    )

    p1 = subprocess.Popen([sys.executable, str(claimer_script)], stdout=subprocess.PIPE, text=True)
    p2 = subprocess.Popen([sys.executable, str(claimer_script)], stdout=subprocess.PIPE, text=True)
    out1, _ = p1.communicate(timeout=10)
    out2, _ = p2.communicate(timeout=10)
    assert p1.returncode == 0
    assert p2.returncode == 0

    import json

    c1 = json.loads(out1.strip())
    c2 = json.loads(out2.strip())
    # Disjoint sets whose union is exact 5 uncancelled jobs
    assert set(c1).isdisjoint(set(c2))
    assert sorted(c1 + c2) == sorted(job_ids[:-1])
    engine.dispose()
