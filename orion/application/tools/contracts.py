"""Typed Pydantic contracts for the ORION tool execution subsystem."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


def generate_execution_id() -> str:
    """Generate prefixed UUIDv4 for a trusted tool execution invocation.

    Result is 'exec_' + 32 hexadecimal characters = 37 characters total.
    """
    return f"exec_{uuid.uuid4().hex}"


def _require_timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("Timestamp must be timezone-aware.")
    return value


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PermissionLevel(StrEnum):
    """Categorical classification of the capability scope required by a tool."""

    READ_ONLY = "read_only"
    """Non-mutating data inspection within an authorized workspace."""

    WORKSPACE_WRITE = "workspace_write"
    """Modifies, creates, or deletes files strictly within the workspace root."""

    SYSTEM_EXECUTE = "system_execute"
    """Executes external binaries, shell utilities, or subprocesses."""

    ELEVATED = "elevated"
    """Requires superuser, administrative, or setuid access (strictly forbidden)."""


class ExecutionType(StrEnum):
    """Execution runtime environment for a tool."""

    IN_PROCESS = "in_process"
    """Executed as an in-process Python callable (e.g., read_file)."""

    SUBPROCESS = "subprocess"
    """Executed as an isolated OS process via ExecutionManager."""


class PermissionOutcome(StrEnum):
    """Deterministic policy outcome for a requested tool invocation."""

    ALLOW = "allow"
    """Permitted to execute immediately without human intervention."""

    DENY = "deny"
    """Action strictly rejected by policy. Cannot be overridden by user prompt."""

    REQUIRE_APPROVAL = "require_approval"
    """Action requires explicit, interactive human confirmation via trusted UI."""


class ExecutionStatus(StrEnum):
    """Lifecycle states of a tool execution invocation."""

    CREATED = "created"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PermissionDecision(BaseModel):
    """Immutable record of a deterministic policy evaluation."""

    model_config = ConfigDict(frozen=True)

    outcome: PermissionOutcome
    reason: str
    tool_id: str
    evaluated_at: datetime = Field(default_factory=utc_now)

    @field_validator("evaluated_at")
    @classmethod
    def validate_evaluated_at(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class ToolDefinition(BaseModel):
    """Immutable specification and schema declaration for a registered tool.

    Uses typed Pydantic models for input and output validation as the canonical
    contract source, and dynamically generates JSON Schema for model consumption.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    tool_id: str = Field(..., pattern=r"^[a-z0-9_]{3,48}$")
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    permission_level: PermissionLevel
    execution_type: ExecutionType
    is_mutating: bool
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    @field_validator("name", "description")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Field cannot be blank or contain only whitespace.")
        return trimmed

    def input_schema(self) -> dict[str, Any]:
        """Return canonical JSON Schema for tool input arguments."""
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        """Return canonical JSON Schema for tool output results."""
        return self.output_model.model_json_schema()


class ToolProposal(BaseModel):
    """Untrusted model output proposing a tool execution.

    STRICT SECURITY BOUNDARY:
    The model is only permitted to propose a tool_id and input arguments.
    It CANNOT supply execution IDs, timestamps, client request IDs,
    conversation IDs, or claim approval status. Extra fields are rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("tool_id cannot be blank.")
        return trimmed


class ToolInvocation(BaseModel):
    """Trusted, validated invocation package created exclusively by ORION application logic.

    Binds a validated proposal to application-controlled execution context.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(default_factory=generate_execution_id)
    tool_id: str
    arguments: dict[str, Any]
    client_request_id: str
    conversation_id: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)

    @field_validator("client_request_id", "conversation_id")
    @classmethod
    def validate_context_ids(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Context ID cannot be blank.")
        return trimmed


class ToolResult(BaseModel):
    """Immutable result contract produced after tool execution completes, fails, or cancels."""

    model_config = ConfigDict(frozen=True)

    execution_id: str
    tool_id: str
    status: ExecutionStatus
    output: Any | None = None
    error_message: str | None = None
    truncated: bool = False
    exit_code: int | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
    started_at: datetime
    completed_at: datetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)
