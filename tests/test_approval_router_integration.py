"""Integration tests for ToolRouter with ApprovalBroker."""

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
    utc_now,
)
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
from orion.application.tools.ports import ToolExecutorPort
from orion.application.tools.registry import ToolRegistry
from orion.application.tools.router import RouterDisposition, ToolRouter


class SearchInput(BaseModel):
    query: str = Field(..., min_length=1)


class WriteInput(BaseModel):
    filepath: str = Field(..., min_length=1)
    content: str = Field(default="")


class OutputModel(BaseModel):
    output: str


class RecordingExecutor(ToolExecutorPort):
    def __init__(self) -> None:
        self.invocations: list[ToolInvocation] = []

    def execute(self, tool: ToolDefinition, invocation: ToolInvocation) -> ToolResult:
        self.invocations.append(invocation)
        now = utc_now()
        return ToolResult(
            execution_id=invocation.execution_id,
            tool_id=tool.tool_id,
            status=ExecutionStatus.COMPLETED,
            output={"output": f"Executed {tool.tool_id}"},
            started_at=now,
            completed_at=now,
        )


def _setup_system(
    clock=None,
    policy: PermissionPolicy | None = None,
) -> tuple[ToolRouter, ToolRegistry, ApprovalBroker, RecordingExecutor]:
    registry = ToolRegistry()
    read_tool = ToolDefinition(
        tool_id="workspace_search",
        name="Workspace Search",
        description="Search workspace files",
        permission_level=PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        input_model=SearchInput,
        output_model=OutputModel,
    )
    write_tool = ToolDefinition(
        tool_id="workspace_write",
        name="Workspace Write",
        description="Write a file to workspace",
        permission_level=PermissionLevel.WORKSPACE_WRITE,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=True,
        input_model=WriteInput,
        output_model=OutputModel,
    )
    registry.register(read_tool)
    registry.register(write_tool)
    registry.seal()

    engine = PermissionEngine(policy)
    broker = ApprovalBroker(clock=clock) if clock else ApprovalBroker()
    executor = RecordingExecutor()

    router = ToolRouter(
        registry=registry,
        permission_engine=engine,
        executor=executor,
        approval_broker=broker,
    )
    return router, registry, broker, executor


def test_allow_bypasses_approval():
    """Read-only tool allowed by policy dispatches immediately without approval."""
    router, _, broker, executor = _setup_system()
    proposal = ToolProposal(tool_id="workspace_search", arguments={"query": "hello"})

    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.DISPATCHED
    assert decision.approval_request is None
    assert len(executor.invocations) == 1
    assert len(broker.list_pending()) == 0


def test_deny_produces_no_approval():
    """Tools strictly denied by policy produce DENIED and do not create approval requests."""
    # Disallow all tools
    policy = PermissionPolicy(allowed_tool_ids=frozenset(["some_other_tool"]))
    router, _, broker, executor = _setup_system(policy=policy)
    proposal = ToolProposal(tool_id="workspace_search", arguments={"query": "hello"})

    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.DENIED
    assert decision.approval_request is None
    assert len(executor.invocations) == 0
    assert len(broker.list_pending()) == 0


def test_require_approval_creates_pending_request():
    """Mutating tools return AWAITING_APPROVAL and do not call executor."""
    router, _, broker, executor = _setup_system()
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"filepath": "test.txt", "content": "data"},
    )

    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.AWAITING_APPROVAL
    assert decision.approval_request is not None
    assert decision.approval_request.tool_id == "workspace_write"
    assert decision.result is None
    # Crucial security guarantee: executor was NOT invoked
    assert len(executor.invocations) == 0
    assert len(broker.list_pending()) == 1


def test_dispatch_after_valid_approval():
    """Valid approval dispatch executes the tool and returns DISPATCHED with result."""
    router, _, _broker, executor = _setup_system()
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"filepath": "test.txt", "content": "data"},
    )

    route_decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    appr_req = route_decision.approval_request
    assert appr_req is not None
    assert len(executor.invocations) == 0

    # User approves via trusted UI
    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=route_decision.invocation,
    )

    assert dispatch_decision.disposition == RouterDisposition.DISPATCHED
    assert len(executor.invocations) == 1
    assert dispatch_decision.result is not None
    assert dispatch_decision.result.status == ExecutionStatus.COMPLETED


def test_rejection_prevents_dispatch():
    """User rejection prevents execution."""
    router, _, broker, executor = _setup_system()
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"filepath": "test.txt", "content": "data"},
    )

    route_decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    appr_req = route_decision.approval_request

    # User rejects via broker
    res = broker.resolve(appr_req.approval_id, appr_req.nonce, approved=False)
    assert res.status.value == "rejected"

    # Attempting to dispatch fails
    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=route_decision.invocation,
    )

    assert dispatch_decision.disposition == RouterDisposition.DENIED
    assert len(executor.invocations) == 0


def test_expiration_prevents_dispatch():
    """Expired requests cannot be dispatched."""
    current_time = 100.0
    router, _, _broker, executor = _setup_system(clock=lambda: current_time)
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"filepath": "test.txt", "content": "data"},
    )

    route_decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    appr_req = route_decision.approval_request

    # Advance clock past 60s expiration
    current_time = 175.0

    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=route_decision.invocation,
    )

    assert dispatch_decision.disposition == RouterDisposition.DENIED
    assert len(executor.invocations) == 0


def test_cancellation_prevents_dispatch():
    """Cancelled parent request prevents dispatch."""
    router, _, broker, executor = _setup_system()
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"filepath": "test.txt", "content": "data"},
    )

    route_decision = router.route(
        proposal, client_request_id="req_cancelling", conversation_id="conv_1"
    )
    appr_req = route_decision.approval_request

    # Cancel parent request
    broker.cancel_for_request("req_cancelling")

    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=route_decision.invocation,
    )

    assert dispatch_decision.disposition == RouterDisposition.DENIED
    assert len(executor.invocations) == 0


def test_altered_arguments_prevent_dispatch():
    """Altered arguments between approval request and dispatch are rejected."""
    router, _, _broker, executor = _setup_system()
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"filepath": "safe.txt", "content": "clean"},
    )

    route_decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    appr_req = route_decision.approval_request

    # Tampered invocation with different content
    tampered = ToolInvocation(
        tool_id=route_decision.invocation.tool_id,
        arguments={"filepath": "safe.txt", "content": "MALICIOUS PAYLOAD"},
        client_request_id=route_decision.invocation.client_request_id,
        conversation_id=route_decision.invocation.conversation_id,
        execution_id=route_decision.invocation.execution_id,
    )

    dispatch_decision = router.dispatch_approved(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        invocation=tampered,
    )

    assert dispatch_decision.disposition == RouterDisposition.DENIED
    assert len(executor.invocations) == 0
