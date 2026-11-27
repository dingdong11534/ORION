"""HeavyWorkGate: serializes memory-heavy operations (LLM/indexing/voice) on 8 GB RAM."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager


class HeavyWorkGate:
    """Coordinates exclusive access to CPU/RAM-heavy work on constrained hardware.

    Uses a Condition lock only for short state transitions (never held across yield),
    so reading `active_kind` or cancelling never self-deadlocks.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._active_kind: str | None = None

    @property
    def active_kind(self) -> str | None:
        with self._cond:
            return self._active_kind

    @contextmanager
    def acquire(self, kind: str, *, timeout: float = 30.0) -> Iterator[None]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            while self._active_kind is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for HeavyWorkGate ({kind}); busy with {self._active_kind}."
                    )
                self._cond.wait(timeout=remaining)
            self._active_kind = kind
        try:
            yield
        finally:
            with self._cond:
                self._active_kind = None
                self._cond.notify_all()
