"""Tests for Phase G: SSRF URL guard, public web search outcomes, and untrusted isolation."""

import pytest

from orion.application.web.url_guard import (
    WebSearchOutcome,
    WebSearchResultItem,
    validate_public_http_url,
)
from orion.application.web.web_search_service import PublicWebSearchService


def test_ssrf_url_guard_blocks_private_localhost_and_file():
    for bad in (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost:11434/api/tags",
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
    ):
        with pytest.raises(ValueError):
            validate_public_http_url(bad, resolve_dns=False)

    assert validate_public_http_url("https://example.com/docs", resolve_dns=False) == (
        "https://example.com/docs"
    )


def test_web_search_service_handles_empty_and_availability():
    svc = PublicWebSearchService()
    out = svc.search("   ")
    assert out.status == "empty"
    assert isinstance(svc.is_available(), bool)


def test_web_search_outcome_structure():
    out = WebSearchOutcome(
        status="ok",
        query="python 3.12",
        results=(
            WebSearchResultItem(
                title="Python 3.12 Release",
                url="https://www.python.org/downloads/release/python-3120/",
                snippet="Python 3.12",
                excerpt="Python 3.12.0",
            ),
        ),
    )
    assert out.results[0].url.startswith("https://")
