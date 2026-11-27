"""Browser-based public web search using Playwright + installed Google Chrome (no paid APIs)."""

import base64
import re
import shutil
from urllib.parse import parse_qs, quote_plus, urlparse

from orion.application.web.url_guard import (
    WebSearchOutcome,
    WebSearchResultItem,
    validate_public_http_url,
)

_CAPTCHA_MARKERS = (
    "verify you are human",
    "complete the following challenge",
    "captcha",
    "unusual traffic",
    "bot detection",
    "cloudflare",
)


def _decode_bing_url(url: str) -> str:
    parsed = urlparse(url)
    if "bing.com" in (parsed.hostname or "") and parsed.path.startswith("/ck/a"):
        u_vals = parse_qs(parsed.query).get("u", [])
        if u_vals and u_vals[0].startswith("a1"):
            b64 = u_vals[0][2:]
            pad = "=" * (-len(b64) % 4)
            try:
                return base64.urlsafe_b64decode(b64 + pad).decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                return url
    return url


def _clean_text(text: str, max_chars: int = 2500) -> str:
    collapsed = re.sub(r"\s+", " ", (text or "")).strip()
    return collapsed[:max_chars]


class PublicWebSearchService:
    """Isolated browser-based public search using Playwright + Chrome channel (zero API keys)."""

    def __init__(self, *, timeout_ms: int = 15000, max_results: int = 5) -> None:
        self._timeout_ms = timeout_ms
        self._max_results = max_results

    def is_available(self) -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return bool(
            shutil.which("google-chrome")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )

    def search(self, query: str, *, fetch_pages: int = 2) -> WebSearchOutcome:
        q = (query or "").strip()
        if not q:
            return WebSearchOutcome(status="empty", query=q, error_message="Empty search query.")

        search_url = f"https://www.bing.com/search?q={quote_plus(q)}"
        fallback_url = search_url
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            return WebSearchOutcome(
                status="error",
                query=q,
                fallback_search_url=fallback_url,
                error_message=f"Playwright is not installed: {exc}",
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    accept_downloads=False,
                    ignore_https_errors=False,
                )
                page = context.new_page()
                page.set_default_timeout(self._timeout_ms)
                page.goto(search_url, wait_until="domcontentloaded")
                body_low = (page.inner_text("body") or "").lower()
                if any(m in body_low for m in _CAPTCHA_MARKERS):
                    browser.close()
                    return WebSearchOutcome(
                        status="blocked",
                        query=q,
                        fallback_search_url=fallback_url,
                        error_message=(
                            "Public search page requested human verification. "
                            "Open the search URL in your browser instead."
                        ),
                    )

                raw_items = page.eval_on_selector_all(
                    "li.b_algo",
                    """els => els.slice(0, 8).map(el => {
                        const a = el.querySelector('h2 a');
                        const h2 = el.querySelector('h2');
                        const s = el.querySelector('.b_caption p') || el.querySelector('p');
                        return {
                            title: (a ? a.textContent : (h2 ? h2.textContent : '')).trim(),
                            url: a ? a.href : '',
                            snippet: s ? s.textContent.trim() : ''
                        };
                    })""",
                )

                results: list[WebSearchResultItem] = []
                for item in raw_items or []:
                    u = _decode_bing_url((item.get("url") or "").strip())
                    t = (item.get("title") or "").strip()
                    s = (item.get("snippet") or "").strip()
                    if not u or not t:
                        continue
                    try:
                        valid_u = validate_public_http_url(u, resolve_dns=True)
                    except ValueError:
                        continue

                    excerpt = ""
                    if len(results) < fetch_pages:
                        try:
                            p2 = context.new_page()
                            p2.set_default_timeout(10000)
                            resp = p2.goto(valid_u, wait_until="domcontentloaded")
                            if resp is not None:
                                validate_public_http_url(resp.url, resolve_dns=True)
                            excerpt = _clean_text(p2.inner_text("body") or "", max_chars=2500)
                            p2.close()
                        except Exception:  # noqa: BLE001
                            excerpt = ""

                    results.append(
                        WebSearchResultItem(title=t, url=valid_u, snippet=s, excerpt=excerpt)
                    )
                    if len(results) >= self._max_results:
                        break

                browser.close()

            if not results:
                return WebSearchOutcome(
                    status="empty",
                    query=q,
                    fallback_search_url=fallback_url,
                    error_message="No public search results found.",
                )
            return WebSearchOutcome(
                status="ok",
                query=q,
                results=tuple(results),
                fallback_search_url=fallback_url,
            )
        except Exception as exc:  # noqa: BLE001
            return WebSearchOutcome(
                status="error",
                query=q,
                fallback_search_url=fallback_url,
                error_message=f"Web search failed: {exc}",
            )
