"""Ports and contracts for ORION's native Playwright BrowserService."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class BrowserLeaseBusyError(RuntimeError):
    """Raised when a job attempts to acquire the browser lease while another job holds it."""


@dataclass(frozen=True, slots=True)
class BrowserNavigationResult:
    """Result of navigating the managed browser to a validated public URL."""

    url: str
    title: str
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class BrowserSnapshot:
    """Bounded accessibility and text snapshot of the current browser page."""

    url: str
    title: str
    text: str
    ax_tree_summary: str


class BrowserLeasePort(Protocol):
    """Scoped lease on the single managed browser instance."""

    @property
    def job_id(self) -> str: ...

    def navigate(self, url: str) -> BrowserNavigationResult: ...

    def get_accessibility_snapshot(self, *, max_chars: int = 4000) -> BrowserSnapshot: ...


class BrowserServicePort(Protocol):
    """Port for ORION's dedicated, single-instance Playwright browser service."""

    @property
    def profile_dir(self) -> Path: ...

    def is_available(self) -> bool: ...

    def active_lease_owner(self) -> str | None: ...

    def acquire_lease(
        self, job_id: str, *, headless: bool = True
    ) -> AbstractContextManager[BrowserLeasePort]: ...

    def close_browser(self) -> None: ...
