"""Milestone 4.6b — Explicit Model-Exposure Allowlist & Pydantic Decision Schema Tests."""

from pydantic import BaseModel, Field

from orion.application.tools.contracts import (
    MODEL_EXPOSABLE_CAPABILITIES,
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
)
from orion.application.tools.registry import ToolRegistry
from orion.infrastructure.tools.workspace_tools import register_workspace_tools


class _DummyIn(BaseModel):
    path: str = Field(..., min_length=1)


class _DummyOut(BaseModel):
    ok: bool = True


def test_explicit_capability_allowlist_and_default_opt_out():
    """Only READ_ONLY + model_exposable=True + non-mutating tools are model-eligible."""
    assert MODEL_EXPOSABLE_CAPABILITIES == frozenset({PermissionLevel.READ_ONLY})

    reg = ToolRegistry()
    register_workspace_tools(reg)

    # Register a READ_ONLY tool WITHOUT model_exposable=True (default False)
    reg.register(
        ToolDefinition(
            tool_id="internal_readonly_unexposed",
            name="Internal Readonly",
            description="Not opted in for model exposure",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            input_model=_DummyIn,
            output_model=_DummyOut,
        )
    )
    # Register a mutating tool even with model_exposable=True -> must be excluded
    reg.register(
        ToolDefinition(
            tool_id="mutating_tool_opted_in",
            name="Mutating Tool",
            description="Mutating tool",
            permission_level=PermissionLevel.WORKSPACE_WRITE,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=True,
            model_exposable=True,
            input_model=_DummyIn,
            output_model=_DummyOut,
        )
    )
    # Register CODE_EXECUTION and SYSTEM_EXECUTE tools even with model_exposable=True -> excluded
    reg.register(
        ToolDefinition(
            tool_id="system_pytest",
            name="Pytest",
            description="Pytest runner",
            permission_level=PermissionLevel.CODE_EXECUTION,
            execution_type=ExecutionType.SUBPROCESS,
            is_mutating=False,
            model_exposable=True,
            input_model=_DummyIn,
            output_model=_DummyOut,
        )
    )
    reg.register(
        ToolDefinition(
            tool_id="system_git",
            name="Git",
            description="Git CLI",
            permission_level=PermissionLevel.SYSTEM_EXECUTE,
            execution_type=ExecutionType.SUBPROCESS,
            is_mutating=False,
            model_exposable=True,
            input_model=_DummyIn,
            output_model=_DummyOut,
        )
    )
    reg.seal()

    eligible_ids = [t.tool_id for t in reg.list_model_eligible_tools()]
    assert eligible_ids == ["workspace_list_directory", "workspace_read_file"]


def test_future_unknown_capability_excluded_by_default():
    """Future or unknown PermissionLevel values cannot accidentally become model-eligible."""
    reg = ToolRegistry()
    # Construct a ToolDefinition with a simulated future capability via model_construct
    future_tool = ToolDefinition.model_construct(
        tool_id="future_personal_data",
        name="Future Personal Data",
        description="Simulated future capability",
        version="1.0.0",
        permission_level="read_personal_data",  # type: ignore[arg-type]
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        model_exposable=True,
        timeout_seconds=10.0,
        input_model=_DummyIn,
        output_model=_DummyOut,
    )
    reg.register(future_tool)
    reg.seal()

    assert reg.list_model_eligible_tools() == []


def test_generated_decision_schema_strict_nested_additional_properties():
    """Generated JSON Schema must enforce additionalProperties: false at outer and nested levels."""
    reg = ToolRegistry()
    register_workspace_tools(reg)
    reg.seal()

    schema = reg.build_model_decision_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["action", "tool", "arguments", "response"]
    assert schema["properties"]["tool"]["enum"] == [
        "workspace_list_directory",
        "workspace_read_file",
        None,
    ]

    arg_variants = schema["properties"]["arguments"]["anyOf"]
    obj_variants = [v for v in arg_variants if v.get("type") == "object"]
    assert len(obj_variants) == 2
    for obj_schema in obj_variants:
        assert obj_schema["additionalProperties"] is False
        assert "approval_id" not in obj_schema["properties"]
        assert "nonce" not in obj_schema["properties"]
        assert "permission_level" not in obj_schema["properties"]
