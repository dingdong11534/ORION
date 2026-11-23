from collections.abc import Sequence

from orion.application.contracts import ChatMessage, ChatRequest, MessageRole
from orion.application.ports import ModelProvider


class AssistantServiceError(Exception):
    """Raised when an error occurs during assistant service processing."""


class AssistantService:
    """Orchestrates assistant conversations and workflow.

    Does not depend on any GUI or operating system execution layers.
    """

    def __init__(self, model_provider: ModelProvider) -> None:
        self._model_provider = model_provider
        self._history: list[ChatMessage] = []

    def get_history(self) -> Sequence[ChatMessage]:
        """Return the current conversation history."""
        return list(self._history)

    def send_message(self, content: str) -> ChatMessage:
        """Process an incoming user message and return the assistant response.

        Validates input, records user message in history, queries the model provider,
        and records the assistant reply.
        """
        # Validate request contract (raises ValidationError if blank or invalid)
        request = ChatRequest(content=content)

        user_message = ChatMessage(
            role=MessageRole.USER.value,
            content=request.content,
        )
        self._history.append(user_message)

        try:
            response_text = self._model_provider.generate_response(
                prompt=user_message.content,
                history=self._history,
            )
        except Exception as exc:
            # Re-raise as domain-level AssistantServiceError without masking details
            raise AssistantServiceError(f"Model provider generation failed: {exc}") from exc

        assistant_message = ChatMessage(
            role=MessageRole.ASSISTANT.value,
            content=response_text,
        )
        self._history.append(assistant_message)
        return assistant_message
