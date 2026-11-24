"""Unit tests for ApprovalBroker, contracts, exact action binding, and audit events."""

from pydantic import BaseModel, Field

from orion.application.tools.approvals import (
    ApprovalBroker,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalStatus,
)
from orion.application.tools.contracts import (
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolInvocation,
)


class DummyInput(BaseModel):
    command: str = Field(..., min_length=1)
    timeout: int = Field(default=30, ge=1)


class DummyOutput(BaseModel):
    result: str


def _create_test_tool(tool_id: str = "test_tool", is_mutating: bool = True) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        name="Test Tool",
        description="A tool for testing approvals.",
        permission_level=PermissionLevel.WORKSPACE_WRITE
        if is_mutating
        else PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=is_mutating,
        input_model=DummyInput,
        output_model=DummyOutput,
    )


def _create_test_invocation(
    tool_id: str = "test_tool",
    command: str = "do_something",
    timeout: int = 30,
    client_request_id: str = "req_123",
    conversation_id: str = "conv_456",
) -> ToolInvocation:
    return ToolInvocation(
        tool_id=tool_id,
        arguments={"command": command, "timeout": timeout},
        client_request_id=client_request_id,
        conversation_id=conversation_id,
    )


def test_approval_creation():
    """ApprovalBroker creates a well-formed ApprovalRequest with secure nonce."""
    events: list[tuple[str, str, str | None, dict]] = []

    def record_evt(event_type: str, aggregate_type: str, aggregate_id: str | None, payload: dict):
        events.append((event_type, aggregate_type, aggregate_id, payload))

    broker = ApprovalBroker(ttl_seconds=60.0, event_recorder=record_evt)
    tool = _create_test_tool()
    inv = _create_test_invocation()

    req = broker.create_request(tool, inv)

    assert isinstance(req, ApprovalRequest)
    assert req.approval_id.startswith("appr_")
    assert len(req.nonce) == 32  # 16 bytes hex = 32 chars
    assert req.tool_id == "test_tool"
    assert req.tool_name == "Test Tool"
    assert req.arguments == {"command": "do_something", "timeout": 30}
    assert req.is_mutating is True
    assert req.status == ApprovalStatus.PENDING
    assert req.client_request_id == "req_123"
    assert req.conversation_id == "conv_456"

    # Verify audit event
    assert len(events) == 1
    evt_type, agg_type, agg_id, payload = events[0]
    assert evt_type == "tool.approval_requested"
    assert agg_type == "tool_approval"
    assert agg_id == req.approval_id
    assert payload["tool_id"] == "test_tool"
    assert payload["execution_id"] == inv.execution_id
    # SECURITY: Nonce must NEVER be stored in audit payload!
    assert "nonce" not in payload


def test_successful_approval():
    """User approval resolves request to APPROVED and burns nonce."""
    broker = ApprovalBroker(ttl_seconds=60.0)
    tool = _create_test_tool()
    inv = _create_test_invocation()
    req = broker.create_request(tool, inv)

    res = broker.resolve(req.approval_id, req.nonce, approved=True)

    assert isinstance(res, ApprovalResolution)
    assert res.status == ApprovalStatus.APPROVED
    assert res.invocation is not None
    assert res.invocation.arguments == inv.arguments
    assert res.invocation.execution_id == inv.execution_id

    # Pending list should now be empty
    assert len(broker.list_pending()) == 0


def test_user_rejection():
    """User rejection resolves request to REJECTED."""
    events = []
    broker = ApprovalBroker(event_recorder=lambda t, a, i, p: events.append((t, p)))
    tool = _create_test_tool()
    inv = _create_test_invocation()
    req = broker.create_request(tool, inv)

    res = broker.resolve(req.approval_id, req.nonce, approved=False)

    assert res.status == ApprovalStatus.REJECTED
    assert res.invocation is None
    assert "rejected" in res.reason.lower()

    # Verify audit event tool.denied
    assert any(evt[0] == "tool.denied" for evt in events)


def test_wrong_nonce_rejection():
    """Submitting incorrect nonce is rejected."""
    broker = ApprovalBroker()
    tool = _create_test_tool()
    inv = _create_test_invocation()
    req = broker.create_request(tool, inv)

    res = broker.resolve(req.approval_id, "wrong_nonce_123456789012345678", approved=True)

    assert res.status == ApprovalStatus.REJECTED
    assert "invalid nonce" in res.reason.lower()

    # Request remains pending since wrong nonce was rejected without burning the real request
    pending = broker.get_pending(req.approval_id)
    assert pending is not None
    assert pending.status == ApprovalStatus.PENDING


def test_replay_prevention():
    """A nonce cannot be reused after initial resolution."""
    broker = ApprovalBroker()
    tool = _create_test_tool()
    inv = _create_test_invocation()
    req = broker.create_request(tool, inv)

    # First resolution: APPROVED
    res1 = broker.resolve(req.approval_id, req.nonce, approved=True)
    assert res1.status == ApprovalStatus.APPROVED

    # Second attempt (replay)
    res2 = broker.resolve(req.approval_id, req.nonce, approved=True)
    assert res2.status == ApprovalStatus.REJECTED
    assert "replay" in res2.reason.lower()


def test_expiration_monotonic_clock():
    """Requests expire after TTL based on monotonic clock."""
    current_mono = 1000.0

    def mock_clock():
        return current_mono

    events = []
    broker = ApprovalBroker(
        clock=mock_clock,
        ttl_seconds=60.0,
        event_recorder=lambda t, a, i, p: events.append((t, p)),
    )
    tool = _create_test_tool()
    inv = _create_test_invocation()
    req = broker.create_request(tool, inv)

    # At 30s: still valid
    current_mono = 1030.0
    pending = broker.list_pending()
    assert len(pending) == 1

    # Advance clock past 60s
    current_mono = 1061.0

    # Attempting to resolve returns EXPIRED
    res = broker.resolve(req.approval_id, req.nonce, approved=True)
    assert res.status == ApprovalStatus.EXPIRED
    assert "expired" in res.reason.lower()

    # Audit event emitted
    assert any(evt[0] == "tool.approval_expired" for evt in events)


def test_expiration_sweep():
    """check_expirations transitions timed-out requests to EXPIRED."""
    current_mono = 500.0

    broker = ApprovalBroker(clock=lambda: current_mono, ttl_seconds=60.0)
    tool = _create_test_tool()
    req = broker.create_request(tool, _create_test_invocation())

    assert len(broker.list_pending()) == 1

    # Advance clock past deadline
    current_mono = 565.0
    expired_ids = broker.check_expirations()

    assert expired_ids == [req.approval_id]
    assert len(broker.list_pending()) == 0
    updated_req = broker.get_pending(req.approval_id)
    assert updated_req.status == ApprovalStatus.EXPIRED


def test_altered_invocation_tampering_rejected():
    """Submitting altered arguments or context for an approved request is rejected."""
    events = []
    broker = ApprovalBroker(event_recorder=lambda t, a, i, p: events.append((t, p)))
    tool = _create_test_tool()
    inv = _create_test_invocation(command="safe_command")
    req = broker.create_request(tool, inv)

    # Attacker tries to approve with modified command
    tampered_inv = ToolInvocation(
        tool_id=inv.tool_id,
        arguments={"command": "malicious_command; rm -rf /", "timeout": 30},
        client_request_id=inv.client_request_id,
        conversation_id=inv.conversation_id,
        execution_id=inv.execution_id,
    )

    res = broker.resolve(req.approval_id, req.nonce, approved=True, invocation=tampered_inv)

    assert res.status == ApprovalStatus.REJECTED
    assert "tampering" in res.reason.lower() or "mismatch" in res.reason.lower()
    assert any(evt[0] == "tool.denied" for evt in events)


def test_cancel_for_request():
    """Cancelling a parent client request invalidates associated approvals."""
    events = []
    broker = ApprovalBroker(event_recorder=lambda t, a, i, p: events.append((t, p)))
    tool = _create_test_tool()

    req1 = broker.create_request(tool, _create_test_invocation(client_request_id="req_A"))
    req2 = broker.create_request(tool, _create_test_invocation(client_request_id="req_A"))
    req3 = broker.create_request(tool, _create_test_invocation(client_request_id="req_B"))

    cancelled = broker.cancel_for_request("req_A")
    assert set(cancelled) == {req1.approval_id, req2.approval_id}

    # req1 and req2 are cancelled
    assert broker.get_pending(req1.approval_id).status == ApprovalStatus.CANCELLED
    assert broker.get_pending(req2.approval_id).status == ApprovalStatus.CANCELLED

    # req3 is still pending
    assert broker.get_pending(req3.approval_id).status == ApprovalStatus.PENDING
    assert len(broker.list_pending()) == 1

    # Attempting to resolve cancelled request fails
    res = broker.resolve(req1.approval_id, req1.nonce, approved=True)
    assert res.status == ApprovalStatus.CANCELLED


def test_cancel_all_shutdown():
    """cancel_all invalidates all pending requests (used during shutdown)."""
    broker = ApprovalBroker()
    tool = _create_test_tool()

    req1 = broker.create_request(tool, _create_test_invocation(client_request_id="req_1"))
    req2 = broker.create_request(tool, _create_test_invocation(client_request_id="req_2"))

    cancelled = broker.cancel_all()
    assert set(cancelled) == {req1.approval_id, req2.approval_id}
    assert len(broker.list_pending()) == 0


def test_ui_dict_format():
    """to_ui_dict exposes safe, serializable metadata for QML."""
    broker = ApprovalBroker()
    tool = _create_test_tool()
    inv = _create_test_invocation()
    req = broker.create_request(tool, inv)

    ui_data = req.to_ui_dict()
    assert ui_data["approval_id"] == req.approval_id
    assert ui_data["nonce"] == req.nonce
    assert ui_data["tool_name"] == "Test Tool"
    assert ui_data["arguments"] == inv.arguments
    assert ui_data["is_mutating"] is True
    assert isinstance(ui_data["created_at"], str)
    assert isinstance(ui_data["expires_at"], str)
