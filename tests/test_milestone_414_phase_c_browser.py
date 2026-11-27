"""Tests for Milestone 4.14 Phase C: native Playwright BrowserService."""

import http.server
import stat
import threading

import pytest

from orion.application.browser import BrowserLeaseBusyError
from orion.application.web.url_guard import validate_public_http_url
from orion.infrastructure.browser import PlaywrightBrowserService


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = (
            b"<html><head><title>ORION Local Fixture</title></head>"
            b"<body><main><h1>Browser Test</h1>"
            b"<button aria-label='Run Job'>Run Job</button></main></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_browser_service_profile_permissions_ssrf_and_lease_lock(tmp_path) -> None:
    profile = tmp_path / "browser-profile"
    svc = PlaywrightBrowserService(profile_dir=profile, resolve_dns=False)

    assert stat.S_IMODE(svc.profile_dir.stat().st_mode) == 0o700
    assert svc.active_lease_owner() is None

    with pytest.raises(ValueError, match="Blocked non-public IP"):
        validate_public_http_url("http://127.0.0.1:8080/admin", resolve_dns=False)
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        validate_public_http_url("file:///etc/passwd", resolve_dns=False)


@pytest.mark.skipif(
    not PlaywrightBrowserService(profile_dir="/tmp/orion-probe-avail").is_available(),
    reason="Playwright or Google Chrome not available",
)
def test_browser_service_single_lease_and_real_chrome_teardown(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "browser-profile"
    svc = PlaywrightBrowserService(profile_dir=profile, timeout_ms=10000, resolve_dns=False)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Allow localhost fixture ONLY inside this test by bypassing validate_public_http_url
        monkeypatch.setattr(
            "orion.infrastructure.browser.playwright_browser_service.validate_public_http_url",
            lambda url, *, resolve_dns=True: url,
        )
        with svc.acquire_lease("job-1", headless=True) as lease:
            assert svc.active_lease_owner() == "job-1"
            with pytest.raises(BrowserLeaseBusyError), svc.acquire_lease("job-2", headless=True):
                pass

            nav = lease.navigate(f"http://127.0.0.1:{port}/")
            assert nav.status_code == 200
            assert nav.title == "ORION Local Fixture"

            snap = lease.get_accessibility_snapshot()
            assert "Browser Test" in snap.text
            assert snap.ax_tree_summary
            assert "Run Job" in snap.ax_tree_summary

        assert svc.active_lease_owner() is None
    finally:
        svc.close_browser()
        server.shutdown()
        server.server_close()
