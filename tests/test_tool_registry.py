"""Unit tests for the static in-memory ToolRegistry."""

import pytest
from pydantic import BaseModel

from orion.application.tools.contracts import (
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
)
from orion.application.tools.ports import (
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRegistrySealedError,
)
from orion.application.tools.registry import ToolRegistry


class DummyInput(BaseModel):
    text: str


class DummyOutput(BaseModel):
    length: int


def _make_tool(tool_id: str, name: str = "Test Tool") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        name=name,
        description="A test tool definition.",
        permission_level=PermissionLevel.READ_ONLY,
        execution_type=ExecutionType.IN_PROCESS,
        is_mutating=False,
        input_model=DummyInput,
        output_model=DummyOutput,
    )


def test_register_and_retrieve():
    """Can register a tool and retrieve it by ID."""
    registry = ToolRegistry()
    t1 = _make_tool("tool_a", "Tool A")

    registry.register(t1)
    assert len(registry) == 1
    assert registry.get("tool_a") == t1
    assert registry.require("tool_a") == t1


def test_duplicate_registration_rejected():
    """Registering the same tool ID twice raises ToolRegistrationError."""
    registry = ToolRegistry()
    t1 = _make_tool("tool_a", "First")
    t2 = _make_tool("tool_a", "Second")

    registry.register(t1)
    with pytest.raises(ToolRegistrationError, match="already registered"):
        registry.register(t2)


def test_unknown_tool_lookup():
    """Looking up unknown tool returns None on get() and raises ToolNotFoundError on require()."""
    registry = ToolRegistry()
    assert registry.get("nonexistent") is None
    with pytest.raises(ToolNotFoundError, match="nonexistent"):
        registry.require("nonexistent")


def test_list_tools_ordered():
    """Listing tools returns them in deterministic lexicographical order by tool_id."""
    registry = ToolRegistry()
    registry.register(_make_tool("tool_z"))
    registry.register(_make_tool("tool_a"))
    registry.register(_make_tool("tool_m"))

    tools = registry.list_tools()
    ids = [t.tool_id for t in tools]
    assert ids == ["tool_a", "tool_m", "tool_z"]


def test_sealing_registry():
    """Once sealed, further tool registrations are rejected."""
    registry = ToolRegistry()
    registry.register(_make_tool("tool_a"))
    assert registry.is_sealed is False

    registry.seal()
    assert registry.is_sealed is True

    # Repeated seal() call is safe
    registry.seal()
    assert registry.is_sealed is True

    # Registration after sealing fails
    with pytest.raises(ToolRegistrySealedError, match="sealed"):
        registry.register(_make_tool("tool_b"))


def test_immutable_after_registration():
    """Tool definitions returned from registry cannot be mutated."""
    registry = ToolRegistry()
    registry.register(_make_tool("tool_a"))

    tool = registry.require("tool_a")
    with pytest.raises(Exception):  # noqa: B017
        tool.name = "Mutated"  # type: ignore[misc]
