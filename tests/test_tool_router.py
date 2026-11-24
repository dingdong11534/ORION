"""Unit tests for the ToolRouter component."""

from pydantic import BaseModel, Field

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
    pattern: str = Field(..., min_length=1)
    max_results: int = Field(default=10, ge=1, le=50)


class SearchOutput(BaseModel):
    matches: list[str]


class MutatingInput(BaseModel):
    target: str = Field(..., min_length=1)


class DummyExecutor(ToolExecutorPort):
    """Test double recording execution invocations without real OS actions."""

    def __init__(self) -> None:
        self.invocations: list[ToolInvocation] = []

    def execute(self, tool: ToolDefinition, invocation: ToolInvocation) -> ToolResult:
        self.invocations.append(invocation)
        now = utc_now()
        return ToolResult(
            execution_id=invocation.execution_id,
            tool_id=tool.tool_id,
            status=ExecutionStatus.COMPLETED,
            output={"matches": ["match1"]},
            started_at=now,
            completed_at=now,
        )


def _setup_router(
    policy: PermissionPolicy | None = None,
) -> tuple[ToolRouter, ToolRegistry, DummyExecutor]:
    registry = ToolRegistry()
    search_tool = ToolDefinition(
        tool_id="workspace_search",
        name="Workspace Search",
        description="Search files in workspace.",
        permission_level=PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        input_model=SearchInput,
        output_model=SearchOutput,
    )
    write_tool = ToolDefinition(
        tool_id="workspace_write",
        name="Workspace Write",
        description="Write files to workspace.",
        permission_level=PermissionLevel.WORKSPACE_WRITE,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=True,
        input_model=MutatingInput,
        output_model=SearchOutput,
    )
    registry.register(search_tool)
    registry.register(write_tool)
    registry.seal()

    engine = PermissionEngine(policy)
    executor = DummyExecutor()
    router = ToolRouter(registry=registry, permission_engine=engine, executor=executor)
    return router, registry, executor


def test_route_unknown_tool_fails_validation():
    """Router rejects proposals with unregistered tool IDs."""
    router, _, _ = _setup_router()
    proposal = ToolProposal(tool_id="unknown_command", arguments={})

    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    assert decision.disposition == RouterDisposition.VALIDATION_FAILED
    assert "not registered" in (decision.error_message or "")
    assert decision.invocation is None


def test_route_invalid_arguments_fails_validation():
    """Router rejects proposals with missing or malformed arguments."""
    router, _, _ = _setup_router()

    # Missing required argument 'pattern'
    proposal = ToolProposal(tool_id="workspace_search", arguments={"max_results": 5})
    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    assert decision.disposition == RouterDisposition.VALIDATION_FAILED
    assert "pattern" in (decision.error_message or "")

    # Out-of-bounds argument (max_results > 50)
    p2 = ToolProposal(tool_id="workspace_search", arguments={"pattern": "test", "max_results": 999})
    d2 = router.route(p2, client_request_id="req_1", conversation_id="conv_1")
    assert d2.disposition == RouterDisposition.VALIDATION_FAILED


def test_route_allowed_tool_dispatches():
    """Valid read-only tool proposal is approved and dispatched to executor."""
    router, _, executor = _setup_router()
    proposal = ToolProposal(
        tool_id="workspace_search",
        arguments={"pattern": "def main", "max_results": 5},
    )

    decision = router.route(proposal, client_request_id="req_42", conversation_id="conv_99")
    assert decision.disposition == RouterDisposition.DISPATCHED
    assert decision.invocation is not None
    assert decision.invocation.tool_id == "workspace_search"
    assert decision.invocation.client_request_id == "req_42"
    assert decision.invocation.conversation_id == "conv_99"
    assert decision.invocation.arguments == {"pattern": "def main", "max_results": 5}

    # Verify executor received invocation
    assert len(executor.invocations) == 1
    assert decision.result is not None
    assert decision.result.status == ExecutionStatus.COMPLETED


def test_route_mutating_tool_awaits_approval():
    """Mutating tool proposals return AWAITING_APPROVAL and are not executed."""
    router, _, executor = _setup_router()
    proposal = ToolProposal(
        tool_id="workspace_write",
        arguments={"target": "output.txt"},
    )

    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    assert decision.disposition == RouterDisposition.AWAITING_APPROVAL
    assert decision.invocation is not None
    assert len(executor.invocations) == 0  # Executor was NOT called
    assert decision.result is None


def test_route_denied_tool_returns_denied():
    """Proposals violating policy return DENIED."""
    # Policy disallowing read-only and explicitly denying
    policy = PermissionPolicy(allowed_tool_ids=frozenset(["workspace_write"]))
    router, _, executor = _setup_router(policy)

    proposal = ToolProposal(
        tool_id="workspace_search",
        arguments={"pattern": "hello"},
    )

    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")
    assert decision.disposition == RouterDisposition.DENIED
    assert decision.permission_decision is not None
    assert len(executor.invocations) == 0
