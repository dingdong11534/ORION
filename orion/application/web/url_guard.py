"""SSRF-safe URL validation and public web search contracts."""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class WebSearchResultItem:
    title: str
    url: str
    snippet: str
    excerpt: str = ""


@dataclass(frozen=True)
class WebSearchOutcome:
    status: str  # "ok" | "blocked" | "empty" | "error"
    query: str
    results: tuple[WebSearchResultItem, ...] = ()
    fallback_search_url: str = ""
    error_message: str = ""


def validate_public_http_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate that url is a public http/https URL (blocks localhost, private IPs, file://)."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("URL missing hostname.")
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise ValueError(f"Blocked local hostname: {host!r}")

    # Direct IP literal check
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"Blocked non-public IP address: {host!r}")
        return raw
    except ValueError as exc:
        if "Blocked non-public IP" in str(exc):
            raise

    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise ValueError(f"DNS resolution failed for {host!r}: {exc}") from exc
        for info in infos:
            addr = info[4][0]
            ip = ipaddress.ip_address(addr.split("%")[0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(f"Blocked non-public resolved IP {addr!r} for {host!r}")
    return raw
