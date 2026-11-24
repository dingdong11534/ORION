"""Tests for graceful shutdown and lifecycle coordination (Milestone 3.6 Part C).

Updated in Milestone 3.6.2 with regression tests for the shutdown-timeout defect:
- Tests A–E verify that resources are preserved when workers do not drain,
  that shutdown resumes correctly, and that the main() finalization pattern
  performs a blocking drain.
"""

import time
from pathlib import Path

import pytest
from alembic.config import Config
from PySide6.QtGui import QGuiApplication

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import MessageStatus
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.lifecycle import ApplicationLifecycle
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.single_instance import SingleInstanceManager
from orion.ui.qt_bridge import OrionBridge


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def lifecycle_env(tmp_path: Path):
    """Provide a full application lifecycle environment with isolated DB and lock."""
    db_file = tmp_path / "lifecycle_test.db"
    db_url = f"sqlite:///{db_file}"
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    conv_store = SqliteConversationStore(factory)
    settings_store = SqliteSettingsStore(factory)
    provider = FakeModelProvider(simulated_delay=0.05)
    service = AssistantService(
        model_provider=provider,
        conversation_store=conv_store,
        settings_store=settings_store,
    )
    bridge = OrionBridge(assistant_service=service)

    lock_file = tmp_path / "test_lifecycle.lock"
    ipc_name = f"test_lifecycle_ipc_{tmp_path.name}"
    single_instance = SingleInstanceManager(lock_path=lock_file, ipc_name=ipc_name)
    assert single_instance.try_acquire() is True
    assert single_instance.start_ipc_server() is True

    lifecycle = ApplicationLifecycle(
        single_instance=single_instance,
        bridge=bridge,
        engine=engine,
    )

    return {
        "lifecycle": lifecycle,
        "single_instance": single_instance,
        "bridge": bridge,
        "service": service,
        "conv_store": conv_store,
        "engine": engine,
        "lock_file": lock_file,
    }


# ---------------------------------------------------------------------------
# Original Milestone 3.6 tests (updated for new return type)
# ---------------------------------------------------------------------------


def test_shutdown_while_idle(lifecycle_env, qapp):
    """Graceful shutdown while idle cleanly drains workers, closes IPC, disposes engine, and releases lock."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    single_instance: SingleInstanceManager = lifecycle_env["single_instance"]
    engine = lifecycle_env["engine"]

    assert single_instance.is_primary is True
    result = lifecycle.shutdown()

    assert result is True
    assert lifecycle.is_shut_down is True
    assert single_instance.is_primary is False
    # Engine is disposed
    assert engine.pool.status() is not None


def test_shutdown_is_idempotent(lifecycle_env, qapp):
    """Calling shutdown multiple times is safe and performs cleanup only once."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    single_instance: SingleInstanceManager = lifecycle_env["single_instance"]

    result1 = lifecycle.shutdown()
    assert result1 is True
    assert lifecycle.is_shut_down is True

    # Second shutdown call should be a no-op returning True
    result2 = lifecycle.shutdown()
    assert result2 is True
    assert lifecycle.is_shut_down is True
    assert single_instance.is_primary is False


def test_new_requests_rejected_once_shutdown_begins(lifecycle_env, qapp):
    """Bridge rejects new message and conversation submissions once shutting down."""
    bridge: OrionBridge = lifecycle_env["bridge"]
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]

    errors = []
    bridge.errorOccurred.connect(lambda err: errors.append(err))

    lifecycle.shutdown()

    # Attempting to send message after shutdown
    bridge.sendMessage("Hello after shutdown")
    qapp.processEvents()

    assert any("shutting down" in err.lower() for err in errors)


def test_shutdown_during_active_generation_cancels_and_persists_interrupted(lifecycle_env, qapp):
    """Shutdown during active streaming cancels generation, marks message interrupted, and drains."""
    bridge: OrionBridge = lifecycle_env["bridge"]
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    conv_store: SqliteConversationStore = lifecycle_env["conv_store"]
    service: AssistantService = lifecycle_env["service"]

    # Use a slow provider
    class SlowStreamProvider:
        def stream_response(self, prompt, history=None, cancellation_token=None):
            for i in range(50):
                if cancellation_token and cancellation_token.is_cancelled:
                    from orion.infrastructure.ollama_model_provider import OllamaCancelledError

                    raise OllamaCancelledError("Cancelled by shutdown.")
                time.sleep(0.05)
                yield f"Chunk{i} "

        def generate_response(self, prompt, history=None):
            return "slow response"

    service._model_provider = SlowStreamProvider()

    # Start generation
    bridge.sendMessage("Generate something slow")
    assert bridge.isBusy is True

    # Allow worker to start
    time.sleep(0.1)
    qapp.processEvents()

    # Initiate shutdown during active streaming
    lifecycle.shutdown(timeout_ms=3000)

    assert lifecycle.is_shut_down is True
    assert bridge.isBusy is False

    # Check persistence: assistant message was persisted as INTERRUPTED, not pending
    cid = service.active_conversation_id
    messages = conv_store.load_history(cid)
    asst_messages = [m for m in messages if m.role == "assistant"]
    assert len(asst_messages) == 1
    assert asst_messages[0].status == MessageStatus.INTERRUPTED
    assert "cancelled" in (asst_messages[0].error_message or "").lower()


def test_active_workers_finish_before_engine_disposal(lifecycle_env, qapp):
    """Database engine disposal happens strictly after active workers finish."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    engine = lifecycle_env["engine"]

    disposal_called = []
    orig_dispose = engine.dispose

    def tracking_dispose():
        disposal_called.append(time.time())
        orig_dispose()

    engine.dispose = tracking_dispose

    lifecycle.shutdown()
    assert len(disposal_called) == 1


def test_shutdown_immediately_after_cancellation(lifecycle_env, qapp):
    """Closing the app immediately after the user clicked Stop is safe and idempotent."""
    bridge: OrionBridge = lifecycle_env["bridge"]
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]

    bridge.sendMessage("Prompt before stop")
    bridge.stopGeneration()
    # Immediate shutdown call right after stop
    lifecycle.shutdown()

    assert lifecycle.is_shut_down is True


# ---------------------------------------------------------------------------
# Milestone 3.6.2 regression tests A–E
# ---------------------------------------------------------------------------


class _UncooperativeProvider:
    """Streaming provider that blocks in a single long sleep, ignoring cancellation.

    The provider deliberately performs one long blocking sleep *before* yielding,
    ensuring that the ``stream_message`` inter-chunk cancellation check cannot
    interrupt it early.  This simulates a model that does not support or honour
    cooperative cancellation.
    """

    def __init__(self, sleep_seconds: float = 6.0):
        self._sleep = sleep_seconds

    def stream_response(self, prompt, history=None, cancellation_token=None):
        # Single blocking sleep — the caller cannot interrupt between yields
        time.sleep(self._sleep)
        yield "done"

    def generate_response(self, prompt, history=None):
        time.sleep(self._sleep)
        return "done"


def test_a_timeout_preserves_resources(lifecycle_env, qapp):
    """Test A: When workers don't drain, engine is NOT disposed and lock is NOT released."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    single_instance: SingleInstanceManager = lifecycle_env["single_instance"]
    engine = lifecycle_env["engine"]
    service: AssistantService = lifecycle_env["service"]
    bridge: OrionBridge = lifecycle_env["bridge"]

    # Install uncooperative provider (sleeps 4 s, ignores cancellation)
    service._model_provider = _UncooperativeProvider(sleep_seconds=4.0)

    # Track whether engine.dispose() is called
    disposal_called = []
    orig_dispose = engine.dispose

    def tracking_dispose():
        disposal_called.append(True)
        orig_dispose()

    engine.dispose = tracking_dispose

    # Start a generation
    bridge.sendMessage("Uncooperative prompt")
    time.sleep(0.15)
    qapp.processEvents()
    assert bridge.isBusy is True

    # Shutdown with a 500 ms timeout — worker will NOT finish in time
    result = lifecycle.shutdown(timeout_ms=500)

    assert result is False
    assert lifecycle.is_shut_down is False  # NOT marked complete
    assert len(disposal_called) == 0  # engine NOT disposed
    assert single_instance.is_primary is True  # lock NOT released

    # Cleanup: wait for the worker to actually finish so pytest teardown is clean
    lifecycle.shutdown(timeout_ms=-1)
    engine.dispose = orig_dispose


def test_b_shutdown_resumes_after_timeout(lifecycle_env, qapp):
    """Test B: After a timed-out shutdown, a second call resumes and completes."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    single_instance: SingleInstanceManager = lifecycle_env["single_instance"]
    service: AssistantService = lifecycle_env["service"]
    bridge: OrionBridge = lifecycle_env["bridge"]

    # Install uncooperative provider (sleeps 2 s)
    service._model_provider = _UncooperativeProvider(sleep_seconds=2.0)

    # Start generation
    bridge.sendMessage("Resume test prompt")
    time.sleep(0.15)
    qapp.processEvents()

    # First shutdown: too short to drain
    result1 = lifecycle.shutdown(timeout_ms=200)
    assert result1 is False
    assert lifecycle.is_shut_down is False

    # Second shutdown: long enough to finish
    result2 = lifecycle.shutdown(timeout_ms=5000)
    assert result2 is True
    assert lifecycle.is_shut_down is True
    assert single_instance.is_primary is False


def test_c_repeated_calls_after_completion(lifecycle_env, qapp):
    """Test C: After full shutdown, repeated calls return True without re-disposing."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    engine = lifecycle_env["engine"]

    disposal_count = []
    orig_dispose = engine.dispose

    def counting_dispose():
        disposal_count.append(True)
        orig_dispose()

    engine.dispose = counting_dispose

    # First complete shutdown (idle, no workers)
    r1 = lifecycle.shutdown()
    assert r1 is True
    assert lifecycle.is_shut_down is True
    assert len(disposal_count) == 1

    # Second and third calls should be no-ops
    r2 = lifecycle.shutdown()
    r3 = lifecycle.shutdown(timeout_ms=1000)
    assert r2 is True
    assert r3 is True
    assert len(disposal_count) == 1  # dispose called only once


def test_d_main_finalization_blocks_until_drain(lifecycle_env, qapp):
    """Test D: Simulates the main.py pattern — initial timeout, then final blocking drain."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    single_instance: SingleInstanceManager = lifecycle_env["single_instance"]
    service: AssistantService = lifecycle_env["service"]
    bridge: OrionBridge = lifecycle_env["bridge"]

    # Install uncooperative provider (sleeps 2 s)
    service._model_provider = _UncooperativeProvider(sleep_seconds=2.0)

    # Start generation
    bridge.sendMessage("Finalization test prompt")
    time.sleep(0.15)
    qapp.processEvents()

    # Simulate aboutToQuit handler: 300 ms timeout (too short)
    r1 = lifecycle.shutdown(timeout_ms=300)
    assert r1 is False
    assert lifecycle.is_shut_down is False

    # Simulate main() finally block: blocking drain
    r2 = lifecycle.shutdown(timeout_ms=-1)
    assert r2 is True
    assert lifecycle.is_shut_down is True
    assert single_instance.is_primary is False


def test_e_cancellation_immediately_before_shutdown(lifecycle_env, qapp):
    """Test E: Cancel active request then immediately shutdown — safe and idempotent."""
    lifecycle: ApplicationLifecycle = lifecycle_env["lifecycle"]
    service: AssistantService = lifecycle_env["service"]
    bridge: OrionBridge = lifecycle_env["bridge"]

    # Use a provider that respects cancellation
    class RespectfulProvider:
        def stream_response(self, prompt, history=None, cancellation_token=None):
            for i in range(100):
                if cancellation_token and cancellation_token.is_cancelled:
                    from orion.infrastructure.ollama_model_provider import OllamaCancelledError

                    raise OllamaCancelledError("Cancelled.")
                time.sleep(0.03)
                yield f"tok{i} "

        def generate_response(self, prompt, history=None):
            return "done"

    service._model_provider = RespectfulProvider()

    # Start generation
    bridge.sendMessage("Pre-cancel prompt")
    time.sleep(0.15)
    qapp.processEvents()

    # User clicks Stop
    bridge.stopGeneration()

    # Immediately followed by shutdown (e.g. closing the window)
    result = lifecycle.shutdown(timeout_ms=3000)
    assert result is True
    assert lifecycle.is_shut_down is True
