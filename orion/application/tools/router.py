"""Tool router validating untrusted model proposals and evaluating dispatch decisions."""

import logging
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, ValidationError

from orion.application.tools.contracts import (
    PermissionDecision,
    PermissionOutcome,
    ToolInvocation,
    ToolProposal,
    ToolResult,
)
from orion.application.tools.ports import (
    PermissionEnginePort,
    ToolExecutorPort,
    ToolRegistryPort,
)

logger = logging.getLogger(__name__)


class RouterDisposition(StrEnum):
    """Categorical outcome of a tool routing operation."""

    DISPATCHED = "dispatched"
    """Tool invocation was authorized and passed to the executor."""

    AWAITING_APPROVAL = "awaiting_approval"
    """Tool invocation requires human confirmation before execution."""

    DENIED = "denied"
    """Tool invocation was rejected by deterministic security policy."""

    VALIDATION_FAILED = "validation_failed"
    """Tool proposal failed schema validation or referenced an unknown tool."""


class RouterDecision(BaseModel):
    """Structured, immutable result of a router dispatch evaluation."""

    model_config = ConfigDict(frozen=True)

    disposition: RouterDisposition
    proposal: ToolProposal
    invocation: ToolInvocation | None = None
    permission_decision: PermissionDecision | None = None
    result: ToolResult | None = None
    error_message: str | None = None


class ToolRouter:
    """Coordinates validation, permission evaluation, and dispatch for model-proposed tools.

    Boundaries:
    - Does NOT execute shell commands or direct OS operations itself.
    - Uses an injected ToolRegistry to resolve tool definitions.
    - Uses an injected PermissionEngine to deterministically enforce policy.
    - Optionally dispatches to a ToolExecutor when permitted.
    """

    def __init__(
        self,
        registry: ToolRegistryPort,
        permission_engine: PermissionEnginePort,
        executor: ToolExecutorPort | None = None,
    ) -> None:
        self._registry = registry
        self._permission_engine = permission_engine
        self._executor = executor

    def route(
        self,
        proposal: ToolProposal,
        client_request_id: str,
        conversation_id: str,
    ) -> RouterDecision:
        """Evaluate an untrusted ToolProposal and determine its routing disposition.

        Steps:
        1. Resolve tool definition from registry (reject unknown tools).
        2. Validate untrusted arguments against the tool's typed Pydantic input model.
        3. Create trusted ToolInvocation with application-assigned IDs and timestamps.
        4. Evaluate deterministic permissions via PermissionEngine.
        5. Return RouterDecision (dispatched, awaiting_approval, or denied).
        """
        # 1. Lookup in registry
        tool = self._registry.get(proposal.tool_id)
        if tool is None:
            return RouterDecision(
                disposition=RouterDisposition.VALIDATION_FAILED,
                proposal=proposal,
                error_message=f"Tool '{proposal.tool_id}' is not registered in the tool registry.",
            )

        # 2. Schema validation against tool's canonical Pydantic input model
        try:
            validated_input = tool.input_model.model_validate(proposal.arguments)
        except ValidationError as exc:
            return RouterDecision(
                disposition=RouterDisposition.VALIDATION_FAILED,
                proposal=proposal,
                error_message=f"Invalid arguments for tool '{proposal.tool_id}': {exc.errors()}",
            )
        except Exception as exc:  # noqa: BLE001
            return RouterDecision(
                disposition=RouterDisposition.VALIDATION_FAILED,
                proposal=proposal,
                error_message=f"Argument validation failed for tool '{proposal.tool_id}': {exc}",
            )

        # 3. Create trusted invocation
        invocation = ToolInvocation(
            tool_id=tool.tool_id,
            arguments=validated_input.model_dump(),
            client_request_id=client_request_id,
            conversation_id=conversation_id,
        )

        # 4. Evaluate permissions
        decision = self._permission_engine.evaluate(tool, invocation)

        # 5. Determine disposition
        if decision.outcome == PermissionOutcome.DENY:
            return RouterDecision(
                disposition=RouterDisposition.DENIED,
                proposal=proposal,
                invocation=invocation,
                permission_decision=decision,
                error_message=decision.reason,
            )

        if decision.outcome == PermissionOutcome.REQUIRE_APPROVAL:
            return RouterDecision(
                disposition=RouterDisposition.AWAITING_APPROVAL,
                proposal=proposal,
                invocation=invocation,
                permission_decision=decision,
            )

        # Decision is ALLOW
        result = None
        if self._executor is not None:
            try:
                result = self._executor.execute(tool, invocation)
            except Exception as exc:  # noqa: BLE001
                logger.error("Executor raised unexpected error: %s", exc)
                return RouterDecision(
                    disposition=RouterDisposition.DISPATCHED,
                    proposal=proposal,
                    invocation=invocation,
                    permission_decision=decision,
                    error_message=f"Tool execution failed: {exc}",
                )

        return RouterDecision(
            disposition=RouterDisposition.DISPATCHED,
            proposal=proposal,
            invocation=invocation,
            permission_decision=decision,
            result=result,
        )
