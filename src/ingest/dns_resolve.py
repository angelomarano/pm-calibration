"""DNS pinning for hosts behind a poisoned local resolver.

This network's default resolver returns a wrong IP for gamma-api.polymarket.com
and clob.polymarket.com (an ISP-level block, confirmed by comparing against
1.1.1.1/8.8.8.8 and by the TLS cert mismatch when connecting through the
system resolver). This module resolves those hosts via DNS-over-HTTPS
against public resolvers instead, caches each IP with a TTL, and forces a
fresh lookup when a pinned IP stops working — Cloudflare's anycast IPs can
rotate, so a connection failure should not keep retrying the same dead
address through the whole backoff schedule.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

DOH_ENDPOINTS = (
    ("https://1.1.1.1/dns-query", {"accept": "application/dns-json"}),
    ("https://8.8.8.8/resolve", {"accept": "application/dns-json"}),
)
DEFAULT_TTL_SECONDS = 15 * 60


def doh_resolve(hostname: str) -> str:
    """Resolves `hostname` to an IPv4 address via DNS-over-HTTPS, trying
    each endpoint in DOH_ENDPOINTS in order. Raises RuntimeError if none
    answer with an A record."""
    last_exc: Exception | None = None
    for url, headers in DOH_ENDPOINTS:
        try:
            resp = requests.get(
                url, params={"name": hostname, "type": "A"}, headers=headers, timeout=10
            )
            resp.raise_for_status()
            answers = [a for a in resp.json().get("Answer", []) if a.get("type") == 1]
            if answers:
                return answers[0]["data"]
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
    raise RuntimeError(f"could not resolve {hostname!r} via any DoH endpoint") from last_exc


@dataclass
class _CacheEntry:
    ip: str
    resolved_at: float


class PinnedResolver:
    """Caches hostname -> IP lookups (via `resolve_fn`) with a TTL, and
    supports forced re-resolution after a connection failure."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, resolve_fn=doh_resolve):
        self._ttl = ttl_seconds
        self._resolve_fn = resolve_fn
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def resolve(self, hostname: str, force: bool = False) -> str:
        with self._lock:
            entry = self._cache.get(hostname)
            fresh = entry is not None and (time.monotonic() - entry.resolved_at) < self._ttl
            if not force and fresh:
                return entry.ip
        ip = self._resolve_fn(hostname)
        with self._lock:
            self._cache[hostname] = _CacheEntry(ip=ip, resolved_at=time.monotonic())
        return ip

    def invalidate(self, hostname: str) -> None:
        with self._lock:
            self._cache.pop(hostname, None)

    def is_cached(self, hostname: str) -> bool:
        with self._lock:
            return hostname in self._cache


@contextmanager
def _pin_getaddrinfo(hostname: str, ip: str):
    """Temporarily makes socket.getaddrinfo(hostname, ...) return `ip`; any
    other hostname still goes through the real (poisoned) resolver. Host
    header and TLS SNI are untouched — those come from the request/SSL
    context, not from getaddrinfo — so only the TCP destination changes.
    Not safe for concurrent requests to different hosts (global monkeypatch);
    fine for this project's sequential, rate-paced pulls."""
    original = socket.getaddrinfo

    def patched(host, *args, **kwargs):
        if host == hostname:
            host = ip
        return original(host, *args, **kwargs)

    socket.getaddrinfo = patched
    try:
        yield
    finally:
        socket.getaddrinfo = original


class DNSPinningAdapter(HTTPAdapter):
    """Transport adapter that resolves each request's host through a
    PinnedResolver instead of the system resolver, and invalidates the
    cached IP on a connection failure so the next retry re-resolves rather
    than hammering a stale address."""

    def __init__(self, resolver: PinnedResolver | None = None, *args, **kwargs):
        self.resolver = resolver or PinnedResolver()
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        host = urlparse(request.url).hostname
        ip = self.resolver.resolve(host)
        with _pin_getaddrinfo(host, ip):
            try:
                return super().send(request, **kwargs)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                self.resolver.invalidate(host)
                raise


def mount_dns_pinning(session: requests.Session, resolver: PinnedResolver | None = None) -> PinnedResolver:
    """Mounts a DNSPinningAdapter on `session` for all https:// requests.
    Returns the PinnedResolver instance backing it (for tests or a manual
    `.invalidate()`)."""
    adapter = DNSPinningAdapter(resolver=resolver)
    session.mount("https://", adapter)
    return adapter.resolver
