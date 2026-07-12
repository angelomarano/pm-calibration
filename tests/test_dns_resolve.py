import socket
import time

import pytest
import requests

import src.ingest.dns_resolve as dns_resolve
from src.ingest.dns_resolve import (
    DNSPinningAdapter,
    PinnedResolver,
    _pin_getaddrinfo,
    mount_dns_pinning,
)


def test_resolver_caches_within_ttl():
    calls = []

    def fake_resolve(host):
        calls.append(host)
        return "1.2.3.4"

    r = PinnedResolver(ttl_seconds=900, resolve_fn=fake_resolve)
    assert r.resolve("example.com") == "1.2.3.4"
    assert r.resolve("example.com") == "1.2.3.4"
    assert calls == ["example.com"]


def test_resolver_refreshes_after_ttl_expires(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def fake_resolve(host):
        calls.append(host)
        return "1.2.3.4"

    r = PinnedResolver(ttl_seconds=10, resolve_fn=fake_resolve)
    r.resolve("example.com")
    clock[0] += 5
    r.resolve("example.com")
    assert calls == ["example.com"]  # still fresh at +5s (ttl=10)

    clock[0] += 10
    r.resolve("example.com")
    assert calls == ["example.com", "example.com"]  # expired -> re-resolved


def test_resolver_force_reresolve():
    calls = []
    r = PinnedResolver(resolve_fn=lambda h: (calls.append(h), "1.2.3.4")[1])
    r.resolve("example.com")
    r.resolve("example.com", force=True)
    assert calls == ["example.com", "example.com"]


def test_resolver_invalidate_then_resolve_refetches():
    calls = []
    r = PinnedResolver(resolve_fn=lambda h: (calls.append(h), "1.2.3.4")[1])
    r.resolve("example.com")
    assert r.is_cached("example.com")
    r.invalidate("example.com")
    assert not r.is_cached("example.com")
    r.resolve("example.com")
    assert calls == ["example.com", "example.com"]


def test_doh_resolve_falls_back_to_second_endpoint(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "1.1.1.1" in url:
            raise requests.exceptions.ConnectionError("blocked")

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"Answer": [{"type": 1, "data": "5.6.7.8"}]}

        return Resp()

    monkeypatch.setattr(dns_resolve.requests, "get", fake_get)
    assert dns_resolve.doh_resolve("gamma-api.polymarket.com") == "5.6.7.8"


def test_doh_resolve_raises_when_all_endpoints_fail(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("blocked")

    monkeypatch.setattr(dns_resolve.requests, "get", fake_get)
    with pytest.raises(RuntimeError):
        dns_resolve.doh_resolve("gamma-api.polymarket.com")


def test_pin_getaddrinfo_redirects_only_target_host():
    real = socket.getaddrinfo
    with _pin_getaddrinfo("pinned.example", "9.9.9.9"):
        result = socket.getaddrinfo("pinned.example", 443)
        assert result[0][4][0] == "9.9.9.9"
    assert socket.getaddrinfo is real


def test_adapter_invalidates_resolver_on_connection_error(monkeypatch):
    resolver = PinnedResolver(resolve_fn=lambda h: "1.2.3.4")
    resolver.resolve("gamma-api.polymarket.com")  # warm the cache
    adapter = DNSPinningAdapter(resolver=resolver)

    def boom(self, request, **kwargs):
        raise requests.exceptions.ConnectionError("stale IP")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", boom)

    prepared = requests.Request(
        method="GET", url="https://gamma-api.polymarket.com/events"
    ).prepare()

    with pytest.raises(requests.exceptions.ConnectionError):
        adapter.send(prepared)

    assert not resolver.is_cached("gamma-api.polymarket.com")


def test_mount_dns_pinning_returns_resolver_and_mounts_adapter():
    session = requests.Session()
    resolver = mount_dns_pinning(session)
    assert isinstance(resolver, PinnedResolver)
    adapter = session.get_adapter("https://gamma-api.polymarket.com/events")
    assert isinstance(adapter, DNSPinningAdapter)
    assert adapter.resolver is resolver
