"""Protocol interfaces and domain exceptions for ORION's tool execution subsystem."""

from collections.abc import Sequence
from typing import Protocol

from orion.application.tools.contracts import (
    PermissionDecision,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Base exception for all tool subsystem errors."""


class ToolNotFoundError(ToolError):
    """Raised when an operation references a tool ID not registered in the system."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(f"Tool '{tool_id}' is not registered.")
        self.tool_id = tool_id


class ToolRegistrationError(ToolError):
    """Raised when an attempt to register a tool fails (e.g. duplicate tool ID)."""


class ToolRegistrySealedError(ToolRegistrationError):
    """Raised when an attempt to register a tool occurs after the registry has been sealed."""


class ToolValidationError(ToolError):
    """Raised when tool arguments fail validation against the tool's input schema."""


class ToolPermissionDeniedError(ToolError):
    """Raised when policy evaluation strictly denies execution of a tool."""

    def __init__(self, tool_id: str, reason: str) -> None:
        super().__init__(f"Execution of tool '{tool_id}' denied: {reason}")
        self.tool_id = tool_id
        self.reason = reason


# ---------------------------------------------------------------------------
# Protocols (Ports)
# ---------------------------------------------------------------------------


class ToolRegistryPort(Protocol):
    """Protocol for the tool registry storing declared tool capabilities."""

    def register(self, definition: ToolDefinition) -> None:
        """Register a tool definition into the registry.

        Raises:
            ToolRegistrySealedError: If registry has already been sealed.
            ToolRegistrationError: If tool ID is already registered or invalid.
        """
        ...

    def get(self, tool_id: str) -> ToolDefinition | None:
        """Return the registered definition for tool_id, or None if absent."""
        ...

    def require(self, tool_id: str) -> ToolDefinition:
        """Return the registered definition for tool_id, or raise ToolNotFoundError."""
        ...

    def list_tools(self) -> Sequence[ToolDefinition]:
        """Return all registered tool definitions in deterministic order."""
        ...

    def seal(self) -> None:
        """Permanently seal the registry against further registrations."""
        ...

    @property
    def is_sealed(self) -> bool:
        """Return True if registry is sealed."""
        ...


class PermissionEnginePort(Protocol):
    """Protocol for deterministic policy evaluation."""

    def evaluate(self, tool: ToolDefinition, invocation: ToolInvocation) -> PermissionDecision:
        """Evaluate security policies for the tool invocation and return a PermissionDecision."""
        ...


class ToolExecutorPort(Protocol):
    """Protocol for executing an authorized tool invocation."""

    def execute(self, tool: ToolDefinition, invocation: ToolInvocation) -> ToolResult:
        """Execute the tool invocation and return the immutable ToolResult."""
        ...
