"""Native Playwright Python BrowserService using a dedicated persistent Chrome profile."""

import json
import logging
import os
import shutil
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orion.application.browser.ports import (
    BrowserLeaseBusyError,
    BrowserLeasePort,
    BrowserNavigationResult,
    BrowserSnapshot,
)
from orion.application.web.url_guard import validate_public_http_url

logger = logging.getLogger(__name__)


def get_browser_profile_dir(custom_dir: Path | str | None = None) -> Path:
    """Resolve ORION's dedicated Chrome profile directory and enforce 0700 permissions."""
    if custom_dir is not None:
        target = Path(custom_dir).expanduser().resolve()
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home and xdg_data_home.strip():
            base = Path(xdg_data_home).expanduser().resolve()
        else:
            base = Path.home() / ".local" / "share"
        target = (base / "orion" / "browser-profile").resolve()

    target.mkdir(parents=True, exist_ok=True)
    current_mode = stat.S_IMODE(target.stat().st_mode)
    if current_mode != 0o700:
        target.chmod(0o700)
    return target


class _PlaywrightBrowserLease(BrowserLeasePort):
    """Active lease bound to a single job_id and Playwright persistent context."""

    def __init__(
        self,
        *,
        job_id: str,
        page: Any,
        resolve_dns: bool,
    ) -> None:
        self._job_id = job_id
        self._page = page
        self._resolve_dns = resolve_dns

    @property
    def job_id(self) -> str:
        return self._job_id

    def navigate(self, url: str) -> BrowserNavigationResult:
        safe_url = validate_public_http_url(url, resolve_dns=self._resolve_dns)
        response = self._page.goto(safe_url, wait_until="domcontentloaded")
        status_code = response.status if response is not None else None
        title = (self._page.title() or "").strip()
        final_url = self._page.url or safe_url
        validate_public_http_url(final_url, resolve_dns=self._resolve_dns)
        return BrowserNavigationResult(
            url=final_url,
            title=title,
            status_code=status_code,
        )

    def get_accessibility_snapshot(self, *, max_chars: int = 4000) -> BrowserSnapshot:
        title = (self._page.title() or "").strip()
        current_url = self._page.url or ""
        try:
            body_text = self._page.inner_text("body") or ""
        except Exception:
            logger.debug("Failed reading body inner_text", exc_info=True)
            body_text = ""

        ax_summary = ""
        try:
            if hasattr(self._page, "accessibility") and self._page.accessibility is not None:
                raw_ax = self._page.accessibility.snapshot()
                ax_summary = json.dumps(raw_ax, ensure_ascii=False) if raw_ax else ""
        except Exception:
            logger.debug(
                "page.accessibility.snapshot unavailable, trying aria_snapshot", exc_info=True
            )
            ax_summary = ""

        if not ax_summary:
            try:
                ax_summary = self._page.locator("body").aria_snapshot() or ""
            except Exception:
                logger.debug("aria_snapshot unavailable", exc_info=True)
                ax_summary = ""

        return BrowserSnapshot(
            url=current_url,
            title=title,
            text=body_text.strip()[:max_chars],
            ax_tree_summary=ax_summary.strip()[:max_chars],
        )


class PlaywrightBrowserService:
    """Single-instance, dedicated-profile Playwright Chrome service (no MCP, sandbox enabled)."""

    def __init__(
        self,
        *,
        profile_dir: Path | str | None = None,
        timeout_ms: int = 15000,
        resolve_dns: bool = True,
    ) -> None:
        self._profile_dir = get_browser_profile_dir(profile_dir)
        self._timeout_ms = timeout_ms
        self._resolve_dns = resolve_dns
        self._lock = threading.Lock()
        self._active_job_id: str | None = None
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._headless_mode: bool | None = None

    @property
    def profile_dir(self) -> Path:
        return self._profile_dir

    def is_available(self) -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return bool(
            shutil.which("google-chrome")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )

    def active_lease_owner(self) -> str | None:
        with self._lock:
            return self._active_job_id

    @contextmanager
    def acquire_lease(self, job_id: str, *, headless: bool = True) -> Iterator[BrowserLeasePort]:
        """Acquire exclusive lease on the managed browser instance for `job_id`."""
        with self._lock:
            if self._active_job_id is not None:
                raise BrowserLeaseBusyError(
                    f"Browser lease is currently held by job {self._active_job_id}"
                )
            self._active_job_id = job_id

        try:
            page = self._ensure_started(headless=headless)
            yield _PlaywrightBrowserLease(
                job_id=job_id,
                page=page,
                resolve_dns=self._resolve_dns,
            )
        finally:
            with self._lock:
                self._active_job_id = None

    def _ensure_started(self, *, headless: bool) -> Any:
        if self._context is not None and self._headless_mode == headless:
            if self._page is None or self._page.is_closed():
                self._page = self._context.new_page()
                self._page.set_default_timeout(self._timeout_ms)
            return self._page

        self.close_browser()

        from playwright.sync_api import sync_playwright

        self._profile_dir = get_browser_profile_dir(self._profile_dir)
        self._playwright = sync_playwright().start()
        try:
            # Sandbox remains enabled; downloads disabled by default; dedicated 0700 profile.
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._profile_dir),
                channel="chrome",
                headless=headless,
                accept_downloads=False,
                ignore_https_errors=False,
            )
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.set_default_timeout(self._timeout_ms)
            self._headless_mode = headless
            return self._page
        except Exception:
            self.close_browser()
            raise

    def close_browser(self) -> None:
        """Close the managed browser context and Playwright driver cleanly."""
        ctx = self._context
        pw = self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        self._headless_mode = None

        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                logger.debug("Error closing Playwright browser context", exc_info=True)
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                logger.debug("Error stopping Playwright driver", exc_info=True)
