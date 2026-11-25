"""Static in-memory registry for declared ORION tools."""

import logging
import threading
from collections.abc import Sequence
from typing import Any

from orion.application.tools.contracts import MODEL_EXPOSABLE_CAPABILITIES, ToolDefinition
from orion.application.tools.ports import (
    ToolNotFoundError,
    ToolRegistrationError,
    ToolRegistryPort,
    ToolRegistrySealedError,
)

logger = logging.getLogger(__name__)


class ToolRegistry(ToolRegistryPort):
    """Thread-safe, sealed in-memory registry of declared application tools.

    Guarantees:
    1. Tool identifiers are unique.
    2. Registrations are rejected if the registry has been sealed.
    3. Tool definitions are immutable.
    4. Discovery order is deterministic (lexicographical by tool_id).
    5. The LLM cannot dynamically register tools.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._sealed: bool = False
        self._lock = threading.Lock()

    @property
    def is_sealed(self) -> bool:
        """Return True if the registry has been permanently sealed."""
        with self._lock:
            return self._sealed

    def seal(self) -> None:
        """Seal the registry against any subsequent tool registrations.

        Typically invoked at the conclusion of application startup before any
        user input or model interactions occur.
        """
        with self._lock:
            if not self._sealed:
                self._sealed = True
                logger.info("ToolRegistry sealed with %d registered tool(s).", len(self._tools))

    def register(self, definition: ToolDefinition) -> None:
        """Register a declared tool definition.

        Raises:
            ToolRegistrySealedError: If the registry is already sealed.
            ToolRegistrationError: If definition is invalid or ID already exists.
        """
        with self._lock:
            if self._sealed:
                raise ToolRegistrySealedError(
                    f"Cannot register tool '{definition.tool_id}': ToolRegistry is sealed."
                )

            tool_id = definition.tool_id
            if tool_id in self._tools:
                raise ToolRegistrationError(
                    f"Tool with identifier '{tool_id}' is already registered."
                )

            self._tools[tool_id] = definition
            logger.debug(
                "Registered tool '%s' (%s, v%s)", tool_id, definition.name, definition.version
            )

    def get(self, tool_id: str) -> ToolDefinition | None:
        """Return the tool definition for tool_id, or None if not registered."""
        with self._lock:
            return self._tools.get(tool_id)

    def require(self, tool_id: str) -> ToolDefinition:
        """Return the tool definition for tool_id, or raise ToolNotFoundError."""
        with self._lock:
            tool = self._tools.get(tool_id)
            if tool is None:
                raise ToolNotFoundError(tool_id)
            return tool

    def list_tools(self) -> Sequence[ToolDefinition]:
        """Return all registered tool definitions ordered lexicographically by tool_id."""
        with self._lock:
            return sorted(self._tools.values(), key=lambda t: t.tool_id)

    def list_model_eligible_tools(self) -> Sequence[ToolDefinition]:
        """Return tools eligible for model-exposed catalog via explicit capability allowlist."""
        with self._lock:
            return sorted(
                (
                    t
                    for t in self._tools.values()
                    if t.model_exposable
                    and not t.is_mutating
                    and t.permission_level in MODEL_EXPOSABLE_CAPABILITIES
                ),
                key=lambda t: t.tool_id,
            )

    def build_model_decision_schema(self) -> dict[str, Any]:
        """Generate strict JSON Schema for model tool-decision proposals from eligible Pydantic models."""
        eligible = self.list_model_eligible_tools()
        tool_ids: list[str | None] = [t.tool_id for t in eligible]
        tool_ids.append(None)

        arg_schemas: list[dict[str, Any]] = []
        for t in eligible:
            raw_schema = t.input_model.model_json_schema()
            # Ensure nested schema is strictly closed (additionalProperties: false)
            clean_props = {}
            for prop_name, prop_def in raw_schema.get("properties", {}).items():
                clean_props[prop_name] = {k: v for k, v in prop_def.items() if k != "default"}
            arg_schemas.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": clean_props,
                    "required": list(raw_schema.get("required", [])),
                }
            )
        arg_schemas.append({"type": "null"})

        return {
            "type": "object",
            "required": ["action", "tool", "arguments", "response"],
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": ["respond", "propose_tool"]},
                "tool": {"type": ["string", "null"], "enum": tool_ids},
                "arguments": {"anyOf": arg_schemas},
                "response": {"type": ["string", "null"]},
            },
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)
