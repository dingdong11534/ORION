import re
import time
from collections.abc import Iterator, Sequence

from orion.application.contracts import ChatMessage


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
    ) -> Iterator[str]:
        if self.simulated_delay > 0:
            time.sleep(self.simulated_delay)

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
            if self.chunk_delay > 0:
                time.sleep(self.chunk_delay)
            yield token
