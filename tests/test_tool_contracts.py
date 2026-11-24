"""Unit tests for typed tool execution contracts and schema generation."""

import pytest
from pydantic import BaseModel, Field, ValidationError

from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    PermissionDecision,
    PermissionLevel,
    PermissionOutcome,
    ToolDefinition,
    ToolInvocation,
    ToolProposal,
    ToolResult,
    generate_execution_id,
    utc_now,
)


class DummyInput(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class DummyOutput(BaseModel):
    results: list[str]


def test_tool_definition_valid():
    """Valid tool definition exposes metadata and generates JSON schema."""
    defn = ToolDefinition(
        tool_id="workspace_search",
        name="Workspace Search",
        description="Search files in the workspace.",
        version="1.2.0",
        permission_level=PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        timeout_seconds=15.0,
        input_model=DummyInput,
        output_model=DummyOutput,
    )

    assert defn.tool_id == "workspace_search"
    assert defn.version == "1.2.0"
    assert defn.is_mutating is False
    assert defn.timeout_seconds == 15.0

    in_schema = defn.input_schema()
    assert in_schema["type"] == "object"
    assert "query" in in_schema["properties"]
    assert "limit" in in_schema["properties"]

    out_schema = defn.output_schema()
    assert "results" in out_schema["properties"]


def test_tool_definition_immutable():
    """ToolDefinition is frozen and cannot be mutated."""
    defn = ToolDefinition(
        tool_id="workspace_search",
        name="Workspace Search",
        description="Search files.",
        permission_level=PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        input_model=DummyInput,
        output_model=DummyOutput,
    )

    with pytest.raises(ValidationError):
        defn.name = "New Name"  # type: ignore[misc]


def test_tool_definition_invalid_identifiers():
    """Invalid tool IDs (uppercase, symbols, too short/long) are rejected."""
    with pytest.raises(ValidationError):
        ToolDefinition(
            tool_id="Bad-Name!",
            name="Bad",
            description="Bad",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            input_model=DummyInput,
            output_model=DummyOutput,
        )


def test_tool_definition_blank_fields_rejected():
    """Blank names or descriptions are rejected."""
    with pytest.raises(ValidationError):
        ToolDefinition(
            tool_id="valid_tool",
            name="   ",
            description="Valid description",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            input_model=DummyInput,
            output_model=DummyOutput,
        )


def test_tool_proposal_accepts_only_untrusted_fields():
    """ToolProposal only allows tool_id and arguments. Extra fields are rejected."""
    prop = ToolProposal(tool_id="search", arguments={"query": "hello"})
    assert prop.tool_id == "search"
    assert prop.arguments == {"query": "hello"}

    # Security check: Model attempting to supply execution metadata must be rejected
    with pytest.raises(ValidationError):
        ToolProposal(
            tool_id="search",
            arguments={"query": "hello"},
            execution_id="exec_123",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        ToolProposal(
            tool_id="search",
            arguments={"query": "hello"},
            approved=True,  # type: ignore[call-arg]
        )


def test_tool_invocation_trusted_context():
    """ToolInvocation binds validated arguments to application context."""
    inv = ToolInvocation(
        tool_id="search",
        arguments={"query": "foo", "limit": 5},
        client_request_id="req_123",
        conversation_id="conv_456",
    )
    assert inv.execution_id.startswith("exec_")
    assert inv.tool_id == "search"
    assert inv.client_request_id == "req_123"
    assert inv.conversation_id == "conv_456"
    assert inv.created_at.tzinfo is not None


def test_permission_decision_contract():
    """PermissionDecision correctly captures outcome, reason, and evaluation timestamp."""
    dec = PermissionDecision(
        outcome=PermissionOutcome.DENY,
        reason="Elevated permissions prohibited.",
        tool_id="system_sudo",
    )
    assert dec.outcome == PermissionOutcome.DENY
    assert dec.reason == "Elevated permissions prohibited."
    assert dec.tool_id == "system_sudo"
    assert dec.evaluated_at.tzinfo is not None


def test_tool_result_contract():
    """ToolResult captures execution lifecycle outcome."""
    now = utc_now()
    res = ToolResult(
        execution_id="exec_test",
        tool_id="search",
        status=ExecutionStatus.COMPLETED,
        output={"results": ["file1.py"]},
        duration_ms=42.5,
        started_at=now,
        completed_at=now,
    )
    assert res.status == ExecutionStatus.COMPLETED
    assert res.duration_ms == 42.5
    assert res.truncated is False


def test_execution_id_generation():
    """Execution IDs are unique with 'exec_' prefix."""
    id1 = generate_execution_id()
    id2 = generate_execution_id()
    assert id1.startswith("exec_")
    assert id2.startswith("exec_")
    assert id1 != id2
