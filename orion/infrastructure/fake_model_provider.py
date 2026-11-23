import re
import time
from collections.abc import Iterator, Sequence

from orion.application.contracts import CancellationToken, ChatMessage
from orion.infrastructure.ollama_model_provider import OllamaCancelledError


class FakeModelProvider:
    """Simulated model provider for local development and unit testing.

    Always marks responses explicitly as simulated.
    """

    def __init__(
        self,
        simulated_delay: float = 0.0,
        fixed_response: str | None = None,
        chunk_delay: float = 0.0,
    ) -> None:
        self.simulated_delay = simulated_delay
        self.fixed_response = fixed_response
        self.chunk_delay = chunk_delay

    def generate_response(
        self,
        prompt: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        if self.simulated_delay > 0:
            time.sleep(self.simulated_delay)

        if self.fixed_response is not None:
            return self.fixed_response

        return (
            f'[Simulated response] Echoing message: "{prompt}".\n'
            "(ORION simulated mode — local AI model not connected yet)"
        )

    def stream_response(
        self,
        prompt: str,
        history: Sequence[ChatMessage] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> Iterator[str]:
        if cancellation_token and cancellation_token.is_cancelled:
            raise OllamaCancelledError("Simulated generation cancelled before start.")

        if self.simulated_delay > 0:
            elapsed = 0.0
            step = 0.05
            while elapsed < self.simulated_delay:
                if cancellation_token and cancellation_token.is_cancelled:
                    raise OllamaCancelledError(
                        "Simulated generation cancelled during initial delay."
                    )
                sleep_time = min(step, self.simulated_delay - elapsed)
                time.sleep(sleep_time)
                elapsed += sleep_time

        full_text = (
            self.fixed_response
            if self.fixed_response is not None
            else (
                f'[Simulated response] Echoing message: "{prompt}".\n'
                "(ORION simulated mode — local AI model not connected yet)"
            )
        )

        tokens = re.findall(r"\S+|\s+", full_text)
        for token in tokens:
            if cancellation_token and cancellation_token.is_cancelled:
                raise OllamaCancelledError("Simulated generation cancelled.")
            if self.chunk_delay > 0:
                elapsed = 0.0
                step = 0.05
                while elapsed < self.chunk_delay:
                    if cancellation_token and cancellation_token.is_cancelled:
                        raise OllamaCancelledError(
                            "Simulated generation cancelled during chunk delay."
                        )
                    sleep_time = min(step, self.chunk_delay - elapsed)
                    time.sleep(sleep_time)
                    elapsed += sleep_time
            yield token
