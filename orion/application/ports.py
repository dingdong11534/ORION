from collections.abc import Sequence
from typing import Protocol

from orion.application.contracts import ChatMessage


class ModelProvider(Protocol):
    """Protocol defining the interface for generating assistant responses."""

    def generate_response(
        self,
        prompt: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Generate a text response for the given prompt and conversation history."""
        ...
