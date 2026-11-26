"""Milestone 4.6b.1 regression tests for tool-proposal reliability, sensitive-path enforcement,
response profiles, history bounding, and performance/keep-alive configuration."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config
from pydantic import ValidationError

from alembic import command
from orion.application.assistant_service import AssistantService
from orion.application.contracts import (
    AssistantServiceInterruptedError,
    CancellationToken,
    GenerationCompleted,
    MessageStatus,
    TextDelta,
    ThinkingDelta,
)
from orion.application.tools.approvals import ApprovalBroker
from orion.application.tools.contracts import (
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolProposal,
)
from orion.application.tools.permissions import (
    PermissionEngine,
    PermissionPolicy,
    SensitivePathArgumentValidator,
)
from orion.application.tools.registry import ToolRegistry
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.application.tools.workspace_contracts import (
    WorkspaceReadFileInput,
    WorkspaceReadFileOutput,
)
from orion.infrastructure.fake_model_provider import FakeModelProvider
from orion.infrastructure.ollama_model_provider import OllamaModelProvider
from orion.infrastructure.persistence.database import create_db_engine, create_session_factory
from orion.infrastructure.persistence.repositories import (
    SqliteConversationStore,
    SqliteSettingsStore,
)
from orion.infrastructure.tools.workspace_tools import register_workspace_tools

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_stores(tmp_path: Path):
    db_file = tmp_path / "m46b1.db"
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")
    engine = create_db_engine(db_file)
    factory = create_session_factory(engine)
    return SqliteConversationStore(factory), SqliteSettingsStore(factory), engine


class _FakeHTTPResp:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 1. Application-Layer Sensitive-Path Enforcement & Router Security
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sensitive_path",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".aws/credentials",
        "certs/server.pem",
        "keys/id_rsa",
        "secrets.json",
        "config/secrets.yaml",
        ".ssh/id_ed25519",
        "deploy/private.key",
    ],
)
def test_sensitive_file_proposals_rejected_at_application_layer(
    tmp_path: Path, sensitive_path: str
):
    """Sensitive paths are denied by ToolRouter/PermissionEngine before approval or dispatch."""
    reg = ToolRegistry()
    register_workspace_tools(reg)
    reg.seal()
    broker = ApprovalBroker()
    router = ToolRouter(
        registry=reg,
        permission_engine=PermissionEngine(PermissionPolicy(workspace_root=tmp_path)),
        executor=None,
        approval_broker=broker,
    )
    decision = router.route(
        ToolProposal(tool_id="workspace_read_file", arguments={"path": sensitive_path}),
        client_request_id="req_sens",
        conversation_id="conv_sens",
    )
    assert decision.disposition == RouterDisposition.DENIED
    assert decision.approval_request is None
    assert "sensitive" in (decision.error_message or "").lower()


def test_mutating_sensitive_path_denied_before_approval_prompt(tmp_path: Path):
    """Even a mutating tool targeting a sensitive path is DENIED before creating an ApprovalRequest."""
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            tool_id="workspace_write_file",
            name="Write File",
            description="Write file",
            permission_level=PermissionLevel.WORKSPACE_WRITE,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=True,
            model_exposable=False,
            input_model=WorkspaceReadFileInput,
            output_model=WorkspaceReadFileOutput,
        )
    )
    reg.seal()
    broker = ApprovalBroker()
    router = ToolRouter(
        registry=reg,
        permission_engine=PermissionEngine(PermissionPolicy(workspace_root=tmp_path)),
        approval_broker=broker,
    )
    decision = router.route(
        ToolProposal(tool_id="workspace_write_file", arguments={"path": ".env"}),
        client_request_id="req_mut",
        conversation_id="conv_mut",
    )
    assert decision.disposition == RouterDisposition.DENIED
    assert decision.approval_request is None
    assert len(broker.list_pending()) == 0


def test_out_of_workspace_and_traversal_paths_rejected(tmp_path: Path):
    reg = ToolRegistry()
    register_workspace_tools(reg)
    reg.seal()
    router = ToolRouter(reg, PermissionEngine(PermissionPolicy(workspace_root=tmp_path)))
    for bad_path in ("/etc/passwd", "/root", "../../etc/shadow", "\\Windows\\System32"):
        dec = router.route(
            ToolProposal(tool_id="workspace_read_file", arguments={"path": bad_path}),
            client_request_id="req_esc",
            conversation_id="conv_esc",
        )
        assert dec.disposition == RouterDisposition.DENIED


def test_relative_and_root_path_conventions(tmp_path: Path):
    reg = ToolRegistry()
    register_workspace_tools(reg)
    reg.seal()
    router = ToolRouter(reg, PermissionEngine(PermissionPolicy(workspace_root=tmp_path)))

    ok_file = router.route(
        ToolProposal(tool_id="workspace_read_file", arguments={"path": "orion/main.py"}),
        client_request_id="req_ok1",
        conversation_id="conv_ok",
    )
    assert ok_file.disposition == RouterDisposition.DISPATCHED

    ok_root = router.route(
        ToolProposal(tool_id="workspace_list_directory", arguments={"path": ""}),
        client_request_id="req_ok2",
        conversation_id="conv_ok",
    )
    assert ok_root.disposition == RouterDisposition.DISPATCHED

    bad_root = router.route(
        ToolProposal(tool_id="workspace_list_directory", arguments={"path": "/"}),
        client_request_id="req_bad_root",
        conversation_id="conv_ok",
    )
    assert bad_root.disposition == RouterDisposition.DENIED


def test_unknown_tool_forged_metadata_and_oversized_args_rejected(tmp_path: Path):
    reg = ToolRegistry()
    register_workspace_tools(reg)
    reg.seal()
    router = ToolRouter(reg, PermissionEngine(PermissionPolicy(workspace_root=tmp_path)))

    # Unknown tool ID
    unk = router.route(
        ToolProposal(tool_id="nonexistent_shell_tool", arguments={"cmd": "id"}),
        client_request_id="req_unk",
        conversation_id="conv_unk",
    )
    assert unk.disposition == RouterDisposition.VALIDATION_FAILED

    # Forged metadata fields rejected by ToolProposal extra="forbid"
    for extra_field in (
        {"approval_id": "appr_1"},
        {"nonce": "0000"},
        {"permission_level": "elevated"},
    ):
        with pytest.raises(ValidationError):
            ToolProposal(
                tool_id="workspace_read_file", arguments={"path": "README.md"}, **extra_field
            )

    # Oversized max_bytes / max_entries rejected by input_model
    oversized_read = router.route(
        ToolProposal(
            tool_id="workspace_read_file",
            arguments={"path": "README.md", "max_bytes": 999_999_999},
        ),
        client_request_id="req_big1",
        conversation_id="conv_big",
    )
    assert oversized_read.disposition == RouterDisposition.VALIDATION_FAILED

    oversized_list = router.route(
        ToolProposal(
            tool_id="workspace_list_directory",
            arguments={"path": "", "max_entries": 5000},
        ),
        client_request_id="req_big2",
        conversation_id="conv_big",
    )
    assert oversized_list.disposition == RouterDisposition.VALIDATION_FAILED


def test_duplicate_proposals_and_explicit_catalog_and_injection_invariance(tmp_path: Path):
    reg = ToolRegistry()
    register_workspace_tools(reg)
    reg.seal()
    eligible_ids = [t.tool_id for t in reg.list_model_eligible_tools()]
    assert eligible_ids == ["workspace_list_directory", "workspace_read_file"]
    schema_before = reg.build_model_decision_schema()

    router = ToolRouter(reg, PermissionEngine(PermissionPolicy(workspace_root=tmp_path)))
    for i in range(2):
        dec = router.route(
            ToolProposal(tool_id="workspace_read_file", arguments={"path": ".env"}),
            client_request_id=f"req_dup_{i}",
            conversation_id="conv_dup",
        )
        assert dec.disposition == RouterDisposition.DENIED

    assert reg.build_model_decision_schema() == schema_before
    assert (
        SensitivePathArgumentValidator().validate(
            reg.require("workspace_read_file"),
            router.route(
                ToolProposal(tool_id="workspace_read_file", arguments={"path": "README.md"}),
                "r1",
                "c1",
            ).invocation,
        )
        is None
    )


# ---------------------------------------------------------------------------
# 2. Ambiguous & Conversational Handling + Response Profiles (Fast vs Thinking)
# ---------------------------------------------------------------------------


def test_ambiguous_and_conversational_responses_without_tool_call(tmp_path: Path):
    conv_store, settings_store, engine = _make_stores(tmp_path)
    try:
        clarify = json.dumps(
            {
                "action": "respond",
                "tool": None,
                "arguments": None,
                "response": "Which exact file path would you like me to read?",
            }
        )
        provider = FakeModelProvider(fixed_response=clarify, simulated_delay=0.0)
        svc = AssistantService(provider, conv_store, settings_store)
        res = svc.send_message("Check that file for me.")
        parsed = json.loads(res.content)
        assert parsed["action"] == "respond"
        assert parsed["tool"] is None

        chat_resp = json.dumps(
            {"action": "respond", "tool": None, "arguments": None, "response": "4"}
        )
        provider2 = FakeModelProvider(fixed_response=chat_resp, simulated_delay=0.0)
        svc2 = AssistantService(provider2, conv_store, settings_store)
        res2 = svc2.send_message("What is 2 + 2?")
        assert json.loads(res2.content)["action"] == "respond"
    finally:
        engine.dispose()


def test_fast_and_thinking_profiles_budgets_keep_alive_and_metrics():
    captured_payloads: list[dict] = []

    def _fake_urlopen(req, timeout=30.0):
        payload = json.loads(req.data.decode("utf-8"))
        captured_payloads.append(payload)
        think_text = "reasoning step" if payload["think"] else ""
        lines = [
            json.dumps(
                {"message": {"thinking": think_text, "content": "Answer"}, "done": False}
            ).encode()
            + b"\n",
            json.dumps(
                {
                    "message": {"thinking": "", "content": "!"},
                    "done": True,
                    "done_reason": "stop",
                    "load_duration": 10_000_000,
                    "prompt_eval_count": 18,
                    "prompt_eval_duration": 50_000_000,
                    "eval_count": 4,
                    "eval_duration": 80_000_000,
                    "total_duration": 140_000_000,
                }
            ).encode()
            + b"\n",
        ]
        return _FakeHTTPResp(lines)

    provider = OllamaModelProvider(
        model_name="qwen3:1.7b",
        num_predict_fast=192,
        num_predict_thinking=512,
        keep_alive="5m",
        think=False,
    )
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        fast_events = list(provider.stream_events("2+2?", think=False))
        think_events = list(provider.stream_events("2+2?", think=True))

    # Fast profile: think=False, num_predict=192, keep_alive="5m", no ThinkingDelta leaks
    assert captured_payloads[0]["think"] is False
    assert captured_payloads[0]["options"]["num_predict"] == 192
    assert captured_payloads[0]["keep_alive"] == "5m"
    assert not any(isinstance(e, ThinkingDelta) for e in fast_events)
    assert any(isinstance(e, TextDelta) for e in fast_events)

    # Thinking profile: think=True, num_predict=512 (no unconditional 1024), separate channels
    assert captured_payloads[1]["think"] is True
    assert captured_payloads[1]["options"]["num_predict"] == 512
    assert any(isinstance(e, ThinkingDelta) for e in think_events)
    assert any(isinstance(e, TextDelta) for e in think_events)
    assert any(isinstance(e, GenerationCompleted) and e.stop_reason == "stop" for e in think_events)

    # Performance metrics parsed safely without prompt/secret fields
    metrics = provider.last_performance_metrics
    assert metrics is not None
    assert metrics["prompt_eval_count"] == 18
    assert metrics["eval_count"] == 4
    assert "messages" not in metrics
    assert "prompt" not in metrics


def test_budget_exhaustion_not_marked_completed_and_cancellation_during_thinking(tmp_path: Path):
    conv_store, settings_store, engine = _make_stores(tmp_path)
    try:
        # 1. Budget exhaustion (done_reason='length') must NOT persist as COMPLETED
        def _truncated_urlopen(req, timeout=30.0):
            lines = [
                json.dumps(
                    {"message": {"thinking": "long thought", "content": "Partial"}, "done": False}
                ).encode()
                + b"\n",
                json.dumps(
                    {
                        "message": {"thinking": "", "content": ""},
                        "done": True,
                        "done_reason": "length",
                    }
                ).encode()
                + b"\n",
            ]
            return _FakeHTTPResp(lines)

        provider = OllamaModelProvider(
            model_name="qwen3:1.7b", num_predict_thinking=256, think=True
        )
        provider._supports_thinking_cache = True
        svc = AssistantService(provider, conv_store, settings_store)
        svc.set_show_thinking(True)

        with (
            patch("urllib.request.urlopen", side_effect=_truncated_urlopen),
            pytest.raises(AssistantServiceInterruptedError),
        ):
            list(svc.stream_message("Explain everything", client_request_id="req_trunc"))

        hist = conv_store.load_history(svc.active_conversation_id, limit=10)
        asst_msg = next(
            m for m in hist if m.client_request_id == "req_trunc" and m.role == "assistant"
        )
        assert asst_msg.status == MessageStatus.INTERRUPTED
        assert asst_msg.status != MessageStatus.COMPLETED
        assert "long thought" not in (asst_msg.content or "")

        # 2. Cancellation during thinking
        token = CancellationToken()
        cancel_provider = FakeModelProvider(
            fixed_response="Final",
            thinking_text="Step 1 Step 2",
            thinking_supported=True,
            simulated_delay=0.05,
        )
        svc2 = AssistantService(cancel_provider, conv_store, settings_store)
        svc2.set_show_thinking(True)
        seen_thinking = []
        with pytest.raises(AssistantServiceInterruptedError):
            for _chunk in svc2.stream_message(
                "Think",
                client_request_id="req_cancel_think",
                on_thinking=lambda t: (
                    seen_thinking.append(t),
                    svc2.cancel_request("req_cancel_think"),
                ),
            ):
                pass
        assert seen_thinking

        # 3. Toggle snapshot mid-request: changing show_thinking during stream doesn't alter active request
        snap_provider = FakeModelProvider(
            fixed_response="Snapshot answer",
            thinking_text="Snapshot thought",
            thinking_supported=True,
            simulated_delay=0.0,
        )
        svc3 = AssistantService(snap_provider, conv_store, settings_store)
        svc3.set_show_thinking(False)
        captured_think = []

        def _flip_toggle(_c: str) -> None:
            svc3.set_show_thinking(True)

        out = "".join(
            svc3.stream_message(
                "Hi",
                client_request_id="req_snap",
                on_chunk=_flip_toggle,
                on_thinking=captured_think.append,
            )
        )
        assert out == "Snapshot answer"
        assert captured_think == []
        _ = token
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. Context / History Bounding (SQLite Full, Model Request Bounded)
# ---------------------------------------------------------------------------


def test_history_bounded_in_model_request_while_full_in_sqlite(tmp_path: Path):
    conv_store, settings_store, engine = _make_stores(tmp_path)
    try:
        recorded_histories: list[list[str]] = []

        class _RecordingProvider(FakeModelProvider):
            def stream_events(self, prompt, history=None, cancellation_token=None, think=None):
                recorded_histories.append([m.content for m in (history or [])])
                yield TextDelta(text=f"Reply to: {prompt}")
                yield GenerationCompleted(stop_reason="stop", final_text=f"Reply to: {prompt}")

        provider = _RecordingProvider(simulated_delay=0.0)
        svc = AssistantService(provider, conv_store, settings_store, max_history_messages=4)

        for idx in range(5):
            list(svc.stream_message(f"Message {idx}", client_request_id=f"req_h_{idx}"))

        # Full history (5 user + 5 assistant = 10 messages) remains persisted in SQLite
        full_db = svc.get_active_conversation_history(limit=100)
        assert len(full_db) == 10

        # Last model call received at most 4 bounded history messages, preserving the most recent turns
        last_sent = recorded_histories[-1]
        assert len(last_sent) == 4
        assert last_sent == [
            "Message 2",
            "Reply to: Message 2",
            "Message 3",
            "Reply to: Message 3",
        ]

        # Also verify char/token budget caps oversized historical messages while preserving recent turns
        svc_char = AssistantService(
            provider, conv_store, settings_store, max_history_messages=10, max_history_chars=60
        )
        recorded_histories.clear()
        list(svc_char.stream_message("Final", client_request_id="req_h_char"))
        char_sent = recorded_histories[-1]
        assert sum(len(m) for m in char_sent) <= 60
        assert len(char_sent) < 10
        assert len(svc_char.get_active_conversation_history(limit=100)) == 12
    finally:
        engine.dispose()
