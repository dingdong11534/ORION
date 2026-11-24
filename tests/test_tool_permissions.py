"""Unit tests for the deterministic PermissionEngine."""

from pydantic import BaseModel

from orion.application.tools.contracts import (
    ExecutionType,
    PermissionLevel,
    PermissionOutcome,
    ToolDefinition,
    ToolInvocation,
)
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy


class SimpleInput(BaseModel):
    path: str = ""


class SimpleOutput(BaseModel):
    ok: bool


def _build_tool(
    tool_id: str,
    level: PermissionLevel,
    is_mutating: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        name="Test Tool",
        description="A permission test tool.",
        permission_level=level,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=is_mutating,
        input_model=SimpleInput,
        output_model=SimpleOutput,
    )


def _build_invocation(tool_id: str, arguments: dict | None = None) -> ToolInvocation:
    return ToolInvocation(
        tool_id=tool_id,
        arguments=arguments or {},
        client_request_id="req_test",
        conversation_id="conv_test",
    )


def test_elevated_permissions_strictly_denied():
    """Any tool requesting ELEVATED permissions is unconditionally denied."""
    engine = PermissionEngine()
    tool = _build_tool("sudo_cmd", PermissionLevel.ELEVATED)
    inv = _build_invocation("sudo_cmd")

    decision = engine.evaluate(tool, inv)
    assert decision.outcome == PermissionOutcome.DENY
    assert "strictly prohibited" in decision.reason


def test_read_only_allowed_by_default():
    """Read-only non-mutating tool is allowed under default policy."""
    engine = PermissionEngine()
    tool = _build_tool("read_file", PermissionLevel.READ_ONLY, is_mutating=False)
    inv = _build_invocation("read_file", {"path": "src/main.py"})

    decision = engine.evaluate(tool, inv)
    assert decision.outcome == PermissionOutcome.ALLOW
    assert decision.tool_id == "read_file"


def test_read_only_requires_approval_when_policy_restricts():
    """When allow_read_only is False, read-only tools require approval."""
    policy = PermissionPolicy(allow_read_only=False)
    engine = PermissionEngine(policy)
    tool = _build_tool("read_file", PermissionLevel.READ_ONLY, is_mutating=False)
    inv = _build_invocation("read_file")

    decision = engine.evaluate(tool, inv)
    assert decision.outcome == PermissionOutcome.REQUIRE_APPROVAL


def test_mutating_workspace_operation_requires_approval():
    """Mutating tools or workspace_write permission level require approval."""
    engine = PermissionEngine()

    # Tool marked is_mutating
    t1 = _build_tool("delete_file", PermissionLevel.READ_ONLY, is_mutating=True)
    d1 = engine.evaluate(t1, _build_invocation("delete_file"))
    assert d1.outcome == PermissionOutcome.REQUIRE_APPROVAL

    # Tool with WORKSPACE_WRITE level
    t2 = _build_tool("write_file", PermissionLevel.WORKSPACE_WRITE, is_mutating=True)
    d2 = engine.evaluate(t2, _build_invocation("write_file"))
    assert d2.outcome == PermissionOutcome.REQUIRE_APPROVAL


def test_system_execute_denied_by_default():
    """System command execution is disabled by default in Milestone 4.1."""
    engine = PermissionEngine()
    tool = _build_tool("run_sh", PermissionLevel.SYSTEM_EXECUTE)
    inv = _build_invocation("run_sh")

    decision = engine.evaluate(tool, inv)
    assert decision.outcome == PermissionOutcome.DENY
    assert "disabled by policy" in decision.reason


def test_system_execute_requires_approval_when_allowed():
    """When allow_system_execute is True, system tools require approval (never auto-allow)."""
    policy = PermissionPolicy(allow_system_execute=True)
    engine = PermissionEngine(policy)
    tool = _build_tool("run_sh", PermissionLevel.SYSTEM_EXECUTE)
    inv = _build_invocation("run_sh")

    decision = engine.evaluate(tool, inv)
    assert decision.outcome == PermissionOutcome.REQUIRE_APPROVAL


def test_explicit_allowlist_denies_unlisted_tools():
    """If allowed_tool_ids is specified, any unlisted tool is denied."""
    policy = PermissionPolicy(allowed_tool_ids=frozenset(["allowed_tool"]))
    engine = PermissionEngine(policy)

    t_allowed = _build_tool("allowed_tool", PermissionLevel.READ_ONLY)
    t_other = _build_tool("other_tool", PermissionLevel.READ_ONLY)

    assert (
        engine.evaluate(t_allowed, _build_invocation("allowed_tool")).outcome
        == PermissionOutcome.ALLOW
    )
    assert (
        engine.evaluate(t_other, _build_invocation("other_tool")).outcome == PermissionOutcome.DENY
    )


def test_sensitive_path_fragments_strictly_denied():
    """Arguments pointing to sensitive paths (/etc, ~/.ssh, etc.) are denied."""
    engine = PermissionEngine()
    tool = _build_tool("read_file", PermissionLevel.READ_ONLY)

    inv_etc = _build_invocation("read_file", {"path": "/etc/shadow"})
    assert engine.evaluate(tool, inv_etc).outcome == PermissionOutcome.DENY

    inv_ssh = _build_invocation("read_file", {"path": "/home/user/.ssh/id_rsa"})
    assert engine.evaluate(tool, inv_ssh).outcome == PermissionOutcome.DENY
