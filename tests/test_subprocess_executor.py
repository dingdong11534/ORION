"""Integration tests for SubprocessExecutor with PermissionEngine, ApprovalBroker, and ToolRouter."""

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from orion.application.tools.approvals import ApprovalBroker
from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolInvocation,
    ToolProposal,
    ToolResult,
)
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
from orion.application.tools.ports import ToolError
from orion.application.tools.registry import ToolRegistry
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.infrastructure.tools.execution_manager import ExecutionManager
from orion.infrastructure.tools.subprocess_executor import SubprocessExecutor


class CommandInput(BaseModel):
    command: str = Field(default="printf")
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)


class CommandOutput(BaseModel):
    command: str
    stdout: str
    stderr: str
    exit_code: int | None


@pytest.fixture
def temp_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def execution_system(temp_workspace: Path):
    events: list[tuple[str, str, str | None, dict]] = []

    def event_recorder(event_type: str, agg_type: str, agg_id: str | None, payload: dict):
        events.append((event_type, agg_type, agg_id, payload))

    manager = ExecutionManager(workspace_root=temp_workspace, event_recorder=event_recorder)
    executor = SubprocessExecutor(execution_manager=manager, event_recorder=event_recorder)

    registry = ToolRegistry()
    subprocess_tool = ToolDefinition(
        tool_id="system_printf",
        name="System Printf",
        description="Print formatted text via controlled subprocess",
        permission_level=PermissionLevel.SYSTEM_EXECUTE,
        execution_type=ExecutionType.SUBPROCESS,
        is_mutating=False,
        input_model=CommandInput,
        output_model=CommandOutput,
    )
    registry.register(subprocess_tool)
    registry.seal()

    policy = PermissionPolicy(
        workspace_root=temp_workspace,
        allow_system_execute=True,  # Enables REQUIRE_APPROVAL for SYSTEM_EXECUTE
    )
    pe = PermissionEngine(policy)
    broker = ApprovalBroker(event_recorder=event_recorder)

    router = ToolRouter(
        registry=registry,
        permission_engine=pe,
        executor=executor,
        approval_broker=broker,
    )

    return {
        "manager": manager,
        "executor": executor,
        "registry": registry,
        "router": router,
        "broker": broker,
        "policy": policy,
        "events": events,
        "workspace": temp_workspace,
        "tool": subprocess_tool,
    }


def test_subprocess_executor_type_check(execution_system):
    """SubprocessExecutor raises ToolError if tool has ExecutionType.IN_PROCESS."""
    executor: SubprocessExecutor = execution_system["executor"]
    in_process_tool = ToolDefinition(
        tool_id="in_process_test",
        name="In Process",
        description="Not a subprocess tool",
        permission_level=PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        input_model=CommandInput,
        output_model=CommandOutput,
    )
    inv = ToolInvocation(
        tool_id="in_process_test",
        arguments={},
        client_request_id="req_1",
        conversation_id="conv_1",
    )
    with pytest.raises(ToolError, match="Expected ExecutionType.SUBPROCESS"):
        executor.execute(in_process_tool, inv)


def test_subprocess_executor_direct_execution(execution_system):
    """SubprocessExecutor executes allowlisted command and returns ToolResult."""
    executor: SubprocessExecutor = execution_system["executor"]
    tool = execution_system["tool"]

    inv = ToolInvocation(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Execution %s\n", "verified"]},
        client_request_id="req_sub_1",
        conversation_id="conv_sub_1",
    )

    result = executor.execute(tool, inv)
    assert isinstance(result, ToolResult)
    assert result.status == ExecutionStatus.COMPLETED
    assert result.exit_code == 0
    assert result.output["stdout"] == "Execution verified\n"
    assert result.error_message is None
    assert result.duration_ms > 0


def test_system_execute_default_denied_when_policy_false(temp_workspace):
    """When allow_system_execute is False (default), SYSTEM_EXECUTE is strictly DENIED."""
    manager = ExecutionManager(workspace_root=temp_workspace)
    executor = SubprocessExecutor(execution_manager=manager)
    registry = ToolRegistry()
    tool = ToolDefinition(
        tool_id="system_printf",
        name="System Printf",
        description="Print text",
        permission_level=PermissionLevel.SYSTEM_EXECUTE,
        execution_type=ExecutionType.SUBPROCESS,
        is_mutating=False,
        input_model=CommandInput,
        output_model=CommandOutput,
    )
    registry.register(tool)
    registry.seal()

    default_policy = PermissionPolicy()  # allow_system_execute is False by default
    pe = PermissionEngine(default_policy)
    broker = ApprovalBroker()
    router = ToolRouter(
        registry=registry, permission_engine=pe, executor=executor, approval_broker=broker
    )

    proposal = ToolProposal(
        tool_id="system_printf", arguments={"command": "printf", "args": ["hi"]}
    )
    decision = router.route(proposal, "req_1", "conv_1")

    assert decision.disposition == RouterDisposition.DENIED
    assert decision.approval_request is None
    assert "disabled by policy" in (decision.error_message or "").lower()


def test_system_execute_full_approval_lifecycle(execution_system):
    """Full lifecycle: route -> AWAITING_APPROVAL -> no execution -> approve -> dispatch -> complete."""
    router: ToolRouter = execution_system["router"]
    events = execution_system["events"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Subprocess %d\n", "42"]},
    )

    # 1. Route requires approval
    route_decision = router.route(
        proposal, client_request_id="req_flow_1", conversation_id="conv_flow_1"
    )
    assert route_decision.disposition == RouterDisposition.AWAITING_APPROVAL
    assert route_decision.approval_request is not None
    appr_req = route_decision.approval_request

    # Confirm executor was NOT called yet
    assert not any(evt[0] == "tool.execution.started" for evt in events)

    # 2. Dispatch with approval
    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=route_decision.invocation,
    )

    assert dispatch_decision.disposition == RouterDisposition.DISPATCHED
    assert dispatch_decision.result is not None
    assert dispatch_decision.result.status == ExecutionStatus.COMPLETED
    assert dispatch_decision.result.output["stdout"] == "Subprocess 42\n"

    # Confirm audit events
    recorded_event_types = [e[0] for e in events]
    assert "tool.approval_requested" in recorded_event_types
    assert "tool.approved" in recorded_event_types
    assert "tool.execution.requested" in recorded_event_types
    assert "tool.execution.started" in recorded_event_types
    assert "tool.execution.completed" in recorded_event_types


def test_system_execute_rejection_prevents_execution(execution_system):
    """Rejection prevents subprocess execution entirely."""
    router: ToolRouter = execution_system["router"]
    broker: ApprovalBroker = execution_system["broker"]
    events = execution_system["events"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Never runs"]},
    )

    route_decision = router.route(proposal, client_request_id="req_rej", conversation_id="conv_rej")
    appr_req = route_decision.approval_request

    # User rejects
    res = broker.resolve(appr_req.approval_id, appr_req.nonce, approved=False)
    assert res.status.value == "rejected"

    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=route_decision.invocation,
    )

    assert dispatch_decision.disposition == RouterDisposition.DENIED
    assert not any(evt[0] == "tool.execution.started" for evt in events)


def test_system_execute_tampered_args_prevents_execution(execution_system):
    """Tampering with arguments between approval and dispatch is rejected."""
    router: ToolRouter = execution_system["router"]
    events = execution_system["events"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["clean"]},
    )

    route_decision = router.route(
        proposal, client_request_id="req_tamp", conversation_id="conv_tamp"
    )
    appr_req = route_decision.approval_request

    tampered_inv = ToolInvocation(
        tool_id=route_decision.invocation.tool_id,
        arguments={"command": "printf", "args": ["tampered!"]},
        client_request_id=route_decision.invocation.client_request_id,
        conversation_id=route_decision.invocation.conversation_id,
        execution_id=route_decision.invocation.execution_id,
    )

    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=tampered_inv,
    )

    assert dispatch_decision.disposition == RouterDisposition.DENIED
    assert not any(evt[0] == "tool.execution.started" for evt in events)
