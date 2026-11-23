import threading
import time
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from PySide6.QtCore import QCoreApplication, QThreadPool

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.workers import Worker
from orion.ui.qt_bridge import OrionBridge


def _apply_migrations(db_url: str) -> None:
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def _make_service(provider, tmp_path: Path) -> AssistantService:
    db_file = tmp_path / f"bridge_{uuid.uuid4().hex}.db"
    _apply_migrations(f"sqlite:///{db_file}")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    return AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_worker_success(qapp):
    results = []
    errors = []
    finished = []

    def task(x, y):
        return x + y

    worker = Worker(task, 10, 20)
    worker.signals.result.connect(lambda val: results.append(val))
    worker.signals.error.connect(lambda err: errors.append(err))
    worker.signals.finished.connect(lambda: finished.append(True))

    pool = QThreadPool()
    pool.start(worker)
    pool.waitForDone(2000)
    qapp.processEvents()

    assert results == [30]
    assert errors == []
    assert finished == [True]


def test_worker_error_handling(qapp):
    results = []
    errors = []
    finished = []

    def failing_task():
        raise ValueError("Simulated task failure")

    worker = Worker(failing_task)
    worker.signals.result.connect(lambda val: results.append(val))
    worker.signals.error.connect(lambda err: errors.append(err))
    worker.signals.finished.connect(lambda: finished.append(True))

    pool = QThreadPool()
    pool.start(worker)
    pool.waitForDone(2000)
    qapp.processEvents()

    assert results == []
    assert len(errors) == 1
    assert "Simulated task failure" in errors[0]
    assert finished == [True]


def test_bridge_send_and_busy_state(qapp, tmp_path):
    provider = FakeModelProvider(simulated_delay=0.05)
    service = _make_service(provider, tmp_path)
    pool = QThreadPool()
    bridge = OrionBridge(assistant_service=service, thread_pool=pool)

    responses = []
    errors = []
    busy_states = []

    bridge.responseReady.connect(lambda text: responses.append(text))
    bridge.errorOccurred.connect(lambda err: errors.append(err))
    bridge.busyChanged.connect(lambda busy: busy_states.append(busy))

    assert not bridge.isBusy

    bridge.sendMessage("Hello from Qt test")
    assert bridge.isBusy

    # Concurrent request while busy should be rejected
    bridge.sendMessage("Concurrent request")
    assert len(errors) == 1
    assert "already in progress" in errors[0]

    pool.waitForDone(3000)
    qapp.processEvents()

    assert not bridge.isBusy
    assert len(responses) == 1
    assert 'Echoing message: "Hello from Qt test"' in responses[0]
    assert True in busy_states
    assert False in busy_states


def test_bridge_rejects_blank_message(qapp, tmp_path):
    service = _make_service(FakeModelProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    errors = []
    bridge.errorOccurred.connect(lambda err: errors.append(err))

    bridge.sendMessage("   ")
    assert len(errors) == 1
    assert "cannot be blank" in errors[0]
    assert not bridge.isBusy


def test_bridge_dedicated_thread_pool(qapp, tmp_path):
    service = _make_service(FakeModelProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    assert bridge._thread_pool is not None
    assert bridge._thread_pool != QThreadPool.globalInstance()
    assert bridge._thread_pool.maxThreadCount() == 1
    assert bridge._owns_thread_pool is True


def test_worker_references_tracked_and_cleaned_up(qapp, tmp_path):
    started_event = threading.Event()
    proceed_event = threading.Event()

    class SyncModelProvider:
        def generate_response(self, prompt: str, history=None) -> str:
            started_event.set()
            assert proceed_event.wait(timeout=5)
            return f"Done: {prompt}"

    service = _make_service(SyncModelProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    assert len(bridge._active_workers) == 0

    bridge.sendMessage("Tracked request")
    assert started_event.wait(timeout=2)

    # Worker is in flight: reference is tracked in active_workers
    assert len(bridge._active_workers) == 1
    req_id = next(iter(bridge._active_workers.keys()))
    assert isinstance(bridge._active_workers[req_id], Worker)
    assert req_id.startswith("req_")
    assert bridge.isBusy

    # Release worker to complete
    proceed_event.set()
    bridge._thread_pool.waitForDone(3000)
    qapp.processEvents()

    assert not bridge.isBusy
    assert len(bridge._active_workers) == 0


def test_shutdown_prevents_new_submissions(qapp, tmp_path):
    service = _make_service(FakeModelProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    errors = []
    bridge.errorOccurred.connect(lambda err: errors.append(err))

    drained = bridge.shutdown()
    assert drained is True
    assert bridge._is_shutting_down is True

    bridge.sendMessage("Submitting after shutdown")
    assert len(errors) == 1
    assert "shutting down" in errors[0]
    assert not bridge.isBusy
    assert len(bridge._active_workers) == 0


def test_shutdown_waits_for_active_work_to_complete(qapp, tmp_path):
    started_event = threading.Event()

    class DelayedProvider:
        def generate_response(self, prompt: str, history=None) -> str:
            started_event.set()
            time.sleep(0.05)
            return f"Done: {prompt}"

    service = _make_service(DelayedProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    bridge.sendMessage("Shutdown wait test")
    assert started_event.wait(timeout=2)
    assert len(bridge._active_workers) == 1

    drained = bridge.shutdown(timeout_ms=3000)
    assert drained is True
    assert len(bridge._active_workers) == 0
    assert not bridge.isBusy


def test_failure_resets_busy_state_and_cleans_up(qapp, tmp_path):
    class CrashingProvider:
        def generate_response(self, prompt: str, history=None) -> str:
            raise RuntimeError("Fatal inference failure")

    service = _make_service(CrashingProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    errors = []
    bridge.errorOccurred.connect(lambda err: errors.append(err))

    bridge.sendMessage("Crashing request")
    assert bridge.isBusy
    assert len(bridge._active_workers) == 1

    bridge._thread_pool.waitForDone(3000)
    qapp.processEvents()

    assert not bridge.isBusy
    assert len(bridge._active_workers) == 0
    assert len(errors) == 1
    assert "Fatal inference failure" in errors[0]


def test_shutdown_timeout_preserves_running_workers_and_cleans_up_on_completion(qapp, tmp_path):
    started_event = threading.Event()
    block_event = threading.Event()

    class BlockedProvider:
        def generate_response(self, prompt: str, history=None) -> str:
            started_event.set()
            assert block_event.wait(timeout=5)
            return f"Done: {prompt}"

    service = _make_service(BlockedProvider(), tmp_path)
    bridge = OrionBridge(assistant_service=service)

    bridge.sendMessage("Blocking request")
    assert started_event.wait(timeout=2)

    assert len(bridge._active_workers) == 1
    req_id = next(iter(bridge._active_workers.keys()))
    worker = bridge._active_workers[req_id]
    signals = worker.signals

    # Shutdown with a short timeout that expires while worker is still blocked
    drained = bridge.shutdown(timeout_ms=50)
    assert drained is False
    assert bridge._is_shutting_down is True

    # Crucial assertion: references and signals MUST remain alive while worker is executing
    assert len(bridge._active_workers) == 1
    assert bridge._active_workers[req_id] is worker
    assert worker.signals is signals
    assert bridge.isBusy

    # Verify no new requests can start after shutdown has been initiated
    errors = []
    bridge.errorOccurred.connect(lambda err: errors.append(err))
    bridge.sendMessage("Another request after shutdown")
    assert len(errors) == 1
    assert "shutting down" in errors[0]

    # Unblock the worker so it completes naturally
    block_event.set()

    # Wait for pool to finish
    assert bridge._thread_pool.waitForDone(3000) is True
    qapp.processEvents()

    # Cleanup must occur only after worker completion
    assert len(bridge._active_workers) == 0
    assert not bridge.isBusy


def test_bridge_passes_request_id_to_service(qapp, tmp_path):
    """Bridge generates prefixed UUIDv4 and passes it to AssistantService."""
    captured_ids = []

    class MockProvider:
        def generate_response(self, prompt: str, history=None) -> str:
            return "OK"

    service = _make_service(MockProvider(), tmp_path)
    orig_send = service.send_message

    def wrap_send(*args, **kwargs):
        req_id = kwargs.get("client_request_id")
        captured_ids.append(req_id)
        return orig_send(*args, **kwargs)

    service.send_message = wrap_send  # type: ignore[method-assign]

    bridge = OrionBridge(assistant_service=service)
    bridge.sendMessage("Test request ID passing")
    bridge._thread_pool.waitForDone(3000)
    qapp.processEvents()

    assert len(captured_ids) == 1
    rid = captured_ids[0]
    assert rid is not None
    assert rid.startswith("req_")
    assert len(rid) == 36


def test_bridge_response_reaches_signal_after_persistence(qapp, tmp_path):
    """When responseReady is emitted, message is already committed in SQLite."""
    db_file = tmp_path / "persistence_check.db"
    _apply_migrations(f"sqlite:///{db_file}")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    provider = FakeModelProvider()
    service = AssistantService(provider, conv_store, settings_store)

    bridge = OrionBridge(assistant_service=service)
    responses = []
    verified_in_db = []

    def on_response(text):
        responses.append(text)
        # Check SQLite immediately when signal arrives on GUI thread
        cid = service.active_conversation_id
        hist = conv_store.load_history(cid)
        # Both user and assistant must be completed in DB
        if len(hist) == 2 and hist[1].content == text:
            verified_in_db.append(True)

    bridge.responseReady.connect(on_response)
    bridge.sendMessage("Check persistence timing")
    bridge._thread_pool.waitForDone(3000)
    qapp.processEvents()

    assert len(responses) == 1
    assert len(verified_in_db) == 1
