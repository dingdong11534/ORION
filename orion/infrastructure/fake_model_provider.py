import time
from collections.abc import Sequence

from orion.application.contracts import ChatMessage


class FakeModelProvider:
    """Simulated model provider for local development and unit testing.

    Always marks responses explicitly as simulated.
    """

    def __init__(
        self,
        simulated_delay: float = 0.0,
        fixed_response: str | None = None,
    ) -> None:
        self.simulated_delay = simulated_delay
        self.fixed_response = fixed_response

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
