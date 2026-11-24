"""Subprocess tool executor implementing ToolExecutorPort for ORION."""

import logging
from collections.abc import Callable
from typing import Any

from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
    utc_now,
)
from orion.application.tools.ports import ToolError, ToolExecutorPort
from orion.infrastructure.tools.execution_manager import ExecutionManager, ExecutionOutput

logger = logging.getLogger(__name__)


class SubprocessExecutor(ToolExecutorPort):
    """Adapter bridging registered subprocess tools to ExecutionManager."""

    def __init__(
        self,
        execution_manager: ExecutionManager,
        event_recorder: Callable[[str, str, str | None, dict[str, Any]], None] | None = None,
    ) -> None:
        self._manager = execution_manager
        self._event_recorder = event_recorder

    @property
    def execution_manager(self) -> ExecutionManager:
        return self._manager

    def cancel(self, execution_id: str) -> bool:
        """Cancel the active execution matching execution_id. Delegates to ExecutionManager."""
        return self._manager.cancel(execution_id)

    def execute(self, tool: ToolDefinition, invocation: ToolInvocation) -> ToolResult:
        """Execute an authorized subprocess tool invocation."""
        if tool.execution_type != ExecutionType.SUBPROCESS:
            raise ToolError(
                f"SubprocessExecutor received tool '{tool.tool_id}' with execution type '{tool.execution_type}'. "
                "Expected ExecutionType.SUBPROCESS."
            )

        start_time = utc_now()

        # Determine binary command: from arguments["command"] or inferred from tool_id
        command = invocation.arguments.get("command")
        if not command:
            # Map tool IDs like "system_pytest" -> "pytest"
            if tool.tool_id.startswith("system_"):
                command = tool.tool_id[len("system_") :]
            elif tool.tool_id.startswith("workspace_"):
                command = tool.tool_id[len("workspace_") :]
            else:
                command = tool.tool_id

        raw_args = invocation.arguments.get("args", [])
        if isinstance(raw_args, tuple):
            args = list(raw_args)
        elif isinstance(raw_args, list):
            args = raw_args
        else:
            args = [str(raw_args)] if raw_args else []

        cwd = invocation.arguments.get("cwd")
        timeout = invocation.arguments.get("timeout_seconds", tool.timeout_seconds)

        try:
            exec_output: ExecutionOutput = self._manager.run_command(
                command=command,
                args=args,
                cwd=cwd,
                timeout_seconds=timeout,
                execution_id=invocation.execution_id,
                conversation_id=invocation.conversation_id,
                client_request_id=invocation.client_request_id,
            )

            # Map status
            status = exec_output.status
            if status == ExecutionStatus.COMPLETED and (
                exec_output.exit_code is not None and exec_output.exit_code != 0
            ):
                status = ExecutionStatus.FAILED

            return ToolResult(
                execution_id=invocation.execution_id,
                tool_id=tool.tool_id,
                status=status,
                output={
                    "command": exec_output.command,
                    "stdout": exec_output.stdout,
                    "stderr": exec_output.stderr,
                    "exit_code": exec_output.exit_code,
                },
                error_message=exec_output.error_message,
                truncated=exec_output.truncated,
                exit_code=exec_output.exit_code,
                duration_ms=exec_output.duration_ms,
                started_at=exec_output.started_at,
                completed_at=exec_output.completed_at,
            )

        except Exception as exc:  # noqa: BLE001
            end_time = utc_now()
            logger.error("Subprocess execution failed for %s: %s", invocation.execution_id, exc)
            return ToolResult(
                execution_id=invocation.execution_id,
                tool_id=tool.tool_id,
                status=ExecutionStatus.FAILED,
                output=None,
                error_message=str(exc),
                truncated=False,
                exit_code=-1,
                duration_ms=0.0,
                started_at=start_time,
                completed_at=end_time,
            )
