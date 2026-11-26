"""Deterministic permission evaluation engine for ORION tool requests."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from orion.application.tools.contracts import (
    PermissionDecision,
    PermissionLevel,
    PermissionOutcome,
    ToolDefinition,
    ToolInvocation,
)
from orion.application.tools.ports import PermissionEnginePort
from orion.application.tools.sensitive_paths import is_sensitive_relative_path

logger = logging.getLogger(__name__)


class ArgumentValidator(Protocol):
    """Protocol for deterministic application-layer tool argument validators."""

    def validate(self, tool: ToolDefinition, invocation: ToolInvocation) -> str | None:
        """Return a denial reason string if invalid/prohibited, or None if permitted."""
        ...


class SensitivePathArgumentValidator:
    """Rejects tool arguments referencing sensitive credential, key, or secret paths."""

    def validate(self, tool: ToolDefinition, invocation: ToolInvocation) -> str | None:
        """Return a denial reason if any string argument references a sensitive path."""
        for arg_key, arg_val in invocation.arguments.items():
            if isinstance(arg_val, str) and arg_val.strip() and is_sensitive_relative_path(arg_val):
                return f"Argument '{arg_key}' references a prohibited sensitive path: {arg_val!r}."
        return None


@dataclass(frozen=True)
class PermissionPolicy:
    """Security configuration parameters governing tool permission evaluation.

    Default settings embody strict defense-in-depth:
    - Default-deny for any unspecified capabilities.
    - System command execution strictly disabled.
    - Elevated / superuser access strictly forbidden.
    - Network access disabled.
    - Mutating workspace operations require human confirmation.
    - Read-only workspace inspection permitted only within explicitly bounded scope.
    """

    allow_read_only: bool = True
    """Whether read-only workspace inspection tools can execute without approval."""

    allow_system_execute: bool = False
    """Whether system subprocess execution is permitted (always requires approval if True)."""

    allow_code_execution: bool = False
    """Whether workspace-controlled code execution (e.g., pytest) is permitted (always requires approval if True)."""

    require_approval_mutating: bool = True
    """Whether mutating workspace actions require human approval (if False, denied)."""

    deny_elevated: bool = True
    """Whether elevated/superuser permissions are denied (must always be True)."""

    deny_network: bool = True
    """Whether network-accessing tools are denied."""

    allowed_tool_ids: frozenset[str] | None = None
    """Optional explicit allowlist of tool IDs. If specified, any tool not in this set is denied."""

    workspace_root: Path | None = None
    """Authorized base directory for filesystem tool scope validation."""

    prohibited_path_fragments: tuple[str, ...] = field(
        default_factory=lambda: (
            "/etc",
            "/proc",
            "/sys",
            "/dev",
            ".ssh",
            ".bashrc",
            ".profile",
            ".gnupg",
        )
    )


class PermissionEngine(PermissionEnginePort):
    """Deterministic security policy engine for tool execution.

    CRITICAL SECURITY PRINCIPLE:
    This engine relies strictly on deterministic code and configured policies.
    The LLM has ZERO influence over permission evaluation.
    Absence of an explicit permit rule results in immediate denial.
    """

    def __init__(
        self,
        policy: PermissionPolicy | None = None,
        argument_validators: Sequence[ArgumentValidator] | None = None,
    ) -> None:
        self._policy = policy if policy is not None else PermissionPolicy()
        self._argument_validators: tuple[ArgumentValidator, ...] = (
            tuple(argument_validators)
            if argument_validators is not None
            else (SensitivePathArgumentValidator(),)
        )

    @property
    def policy(self) -> PermissionPolicy:
        """Return the active immutable permission policy."""
        return self._policy

    def evaluate(self, tool: ToolDefinition, invocation: ToolInvocation) -> PermissionDecision:
        """Deterministically evaluate whether a tool invocation is allowed, denied, or requires approval."""
        tool_id = tool.tool_id

        # 1. Hard deny: Elevated / root permissions are never allowed
        if (
            tool.permission_level == PermissionLevel.ELEVATED
            or self._policy.deny_elevated
            and tool.permission_level == PermissionLevel.ELEVATED
        ):
            return PermissionDecision(
                outcome=PermissionOutcome.DENY,
                reason="Elevated (superuser) privileges are strictly prohibited.",
                tool_id=tool_id,
            )

        # 2. Explicit tool allowlist check (if configured)
        if (
            self._policy.allowed_tool_ids is not None
            and tool_id not in self._policy.allowed_tool_ids
        ):
            return PermissionDecision(
                outcome=PermissionOutcome.DENY,
                reason=f"Tool '{tool_id}' is not in the explicit allowlist.",
                tool_id=tool_id,
            )

        # 3. Path argument check for traversal, sensitive locations, and confinement
        for arg_key, arg_val in invocation.arguments.items():
            if isinstance(arg_val, str) and arg_val.strip():
                # 3a. Reject absolute paths and .. traversal syntax
                if arg_val.startswith(("/", "\\")) or ".." in Path(arg_val).parts:
                    return PermissionDecision(
                        outcome=PermissionOutcome.DENY,
                        reason=f"Argument '{arg_key}' contains invalid absolute or traversal path syntax: {arg_val!r}",
                        tool_id=tool_id,
                    )

                # 3b. Check prohibited sensitive path fragments
                if any(frag in arg_val for frag in self._policy.prohibited_path_fragments):
                    return PermissionDecision(
                        outcome=PermissionOutcome.DENY,
                        reason=f"Argument '{arg_key}' references prohibited sensitive system path fragment.",
                        tool_id=tool_id,
                    )

                # 3c. Run application-layer argument validators (e.g. sensitive filenames/suffixes)
        for validator in self._argument_validators:
            denial_reason = validator.validate(tool, invocation)
            if denial_reason is not None:
                return PermissionDecision(
                    outcome=PermissionOutcome.DENY,
                    reason=denial_reason,
                    tool_id=tool_id,
                )

        # 3d. If workspace_root is configured, enforce strict path confinement
        for arg_key, arg_val in invocation.arguments.items():
            if (
                isinstance(arg_val, str)
                and arg_val.strip()
                and self._policy.workspace_root is not None
                and arg_key in ("path", "target", "file")
            ):
                try:
                    resolved_root = self._policy.workspace_root.resolve()
                    candidate = (resolved_root / arg_val).resolve()
                    if candidate != resolved_root and resolved_root not in candidate.parents:
                        return PermissionDecision(
                            outcome=PermissionOutcome.DENY,
                            reason=f"Path '{arg_val}' escapes authorized workspace root.",
                            tool_id=tool_id,
                        )
                    raw_target = resolved_root / arg_val
                    if raw_target.is_symlink():
                        return PermissionDecision(
                            outcome=PermissionOutcome.DENY,
                            reason=f"Path '{arg_val}' is a symlink (symlinks are prohibited).",
                            tool_id=tool_id,
                        )
                except Exception as exc:  # noqa: BLE001
                    return PermissionDecision(
                        outcome=PermissionOutcome.DENY,
                        reason=f"Failed to validate path confinement for '{arg_val}': {exc}",
                        tool_id=tool_id,
                    )

        # 4. Code execution policy (distinct from READ_ONLY, WORKSPACE_WRITE, and SYSTEM_EXECUTE)
        if tool.permission_level == PermissionLevel.CODE_EXECUTION:
            if not self._policy.allow_code_execution:
                return PermissionDecision(
                    outcome=PermissionOutcome.DENY,
                    reason="Project-controlled code execution (CODE_EXECUTION) is disabled by policy.",
                    tool_id=tool_id,
                )
            return PermissionDecision(
                outcome=PermissionOutcome.REQUIRE_APPROVAL,
                reason="Project-controlled code execution requires explicit human approval.",
                tool_id=tool_id,
            )

        # 5. System execution policy
        if tool.permission_level == PermissionLevel.SYSTEM_EXECUTE:
            if not self._policy.allow_system_execute:
                return PermissionDecision(
                    outcome=PermissionOutcome.DENY,
                    reason="System command execution is disabled by policy in this environment.",
                    tool_id=tool_id,
                )
            return PermissionDecision(
                outcome=PermissionOutcome.REQUIRE_APPROVAL,
                reason="System command execution requires explicit user confirmation.",
                tool_id=tool_id,
            )

        # 5. Mutating operations (workspace_write or is_mutating flag)
        if tool.is_mutating or tool.permission_level == PermissionLevel.WORKSPACE_WRITE:
            if not self._policy.require_approval_mutating:
                return PermissionDecision(
                    outcome=PermissionOutcome.DENY,
                    reason="Mutating operations are disabled by policy.",
                    tool_id=tool_id,
                )
            return PermissionDecision(
                outcome=PermissionOutcome.REQUIRE_APPROVAL,
                reason="Mutating workspace operation requires explicit human approval.",
                tool_id=tool_id,
            )

        # 6. Read-only workspace inspection
        if tool.permission_level == PermissionLevel.READ_ONLY and not tool.is_mutating:
            if self._policy.allow_read_only:
                return PermissionDecision(
                    outcome=PermissionOutcome.ALLOW,
                    reason="Read-only workspace inspection permitted by policy.",
                    tool_id=tool_id,
                )
            return PermissionDecision(
                outcome=PermissionOutcome.REQUIRE_APPROVAL,
                reason="Read-only workspace inspection requires approval under current policy.",
                tool_id=tool_id,
            )

        # 7. Fallthrough: Default-deny for any unhandled combination
        return PermissionDecision(
            outcome=PermissionOutcome.DENY,
            reason="Action matches no permissive policy rule; default-deny enforced.",
            tool_id=tool_id,
        )
