"""Application-layer deterministic coordinator for user-initiated workspace inspection."""

import logging
from collections.abc import Callable
from typing import Any

from orion.application.tools.contracts import ToolProposal
from orion.application.tools.router import RouterDecision, ToolRouter

logger = logging.getLogger(__name__)

USER_INITIATED_WORKSPACE_TOOLS: frozenset[str] = frozenset(
    {"workspace_list_directory", "workspace_read_file"}
)


class WorkspaceBrowserService:
    """Deterministically constructs and routes user-initiated read-only workspace proposals.

    Security boundaries:
    - Only permits ``workspace_list_directory`` and ``workspace_read_file``.
    - Never invokes an LLM to select tools or construct arguments.
    - Routes every proposal through ``ToolRouter.route()`` and ``PermissionEngine``.
    - Records metadata-only audit events when an event recorder is provided.
    """

    def __init__(
        self,
        router: ToolRouter,
        event_recorder: Callable[[str, str, str | None, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._router = router
        self._event_recorder = event_recorder

    def list_directory(
        self,
        relative_path: str,
        client_request_id: str,
        conversation_id: str = "workspace_browser",
        max_entries: int = 200,
    ) -> RouterDecision:
        """Construct and route a user-initiated ``workspace_list_directory`` proposal."""
        return self._dispatch(
            tool_id="workspace_list_directory",
            arguments={"path": relative_path, "max_entries": max_entries},
            client_request_id=client_request_id,
            conversation_id=conversation_id,
        )

    def read_file(
        self,
        relative_path: str,
        client_request_id: str,
        conversation_id: str = "workspace_browser",
    ) -> RouterDecision:
        """Construct and route a user-initiated ``workspace_read_file`` proposal."""
        return self._dispatch(
            tool_id="workspace_read_file",
            arguments={"path": relative_path},
            client_request_id=client_request_id,
            conversation_id=conversation_id,
        )

    def _dispatch(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        client_request_id: str,
        conversation_id: str,
    ) -> RouterDecision:
        if tool_id not in USER_INITIATED_WORKSPACE_TOOLS:
            raise ValueError(f"Tool {tool_id!r} is not permitted for user-initiated browsing.")

        proposal = ToolProposal(tool_id=tool_id, arguments=arguments)
        decision = self._router.route(
            proposal,
            client_request_id=client_request_id,
            conversation_id=conversation_id,
        )
        self._record_audit_event(decision, client_request_id)
        return decision

    def _record_audit_event(self, decision: RouterDecision, client_request_id: str) -> None:
        if self._event_recorder is None:
            return
        payload: dict[str, Any] = {
            "tool_id": decision.proposal.tool_id,
            "path": str(decision.proposal.arguments.get("path", "")),
            "disposition": decision.disposition.value,
            "client_request_id": client_request_id,
            "execution_id": decision.invocation.execution_id if decision.invocation else None,
            "status": decision.result.status.value if decision.result else None,
        }
        try:
            self._event_recorder(
                f"tool.user_initiated.{decision.disposition.value}",
                "tool_invocation",
                decision.invocation.execution_id if decision.invocation else client_request_id,
                payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record workspace tool audit event: %s", exc)
