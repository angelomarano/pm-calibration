import json
import time

import pytest
import requests

from src.ingest.dns_resolve import DNSPinningAdapter
from src.ingest.http import get_json_cached, make_session


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


def test_get_json_cached_skips_http_when_cache_exists(tmp_path):
    cache_path = tmp_path / "x.json"
    cache_path.write_text(json.dumps({"hello": "world"}))

    class ExplodingSession:
        def get(self, *a, **k):
            raise AssertionError("should not hit the network when cached")

    result = get_json_cached(ExplodingSession(), "http://x", {}, cache_path)
    assert result == {"hello": "world"}


def test_get_json_cached_fetches_and_writes_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    cache_path = tmp_path / "sub" / "x.json"
    session = FakeSession([FakeResponse(200, {"a": 1})])

    result = get_json_cached(session, "http://x", {}, cache_path)

    assert result == {"a": 1}
    assert json.loads(cache_path.read_text()) == {"a": 1}
    assert session.calls == 1


def test_get_json_cached_retries_on_429_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    cache_path = tmp_path / "x.json"
    session = FakeSession([FakeResponse(429), FakeResponse(200, {"ok": True})])

    result = get_json_cached(session, "http://x", {}, cache_path)

    assert result == {"ok": True}
    assert session.calls == 2


def test_get_json_cached_gives_up_after_persistent_5xx(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    cache_path = tmp_path / "x.json"
    session = FakeSession([FakeResponse(500)] * 5)

    with pytest.raises(Exception):
        get_json_cached(session, "http://x", {}, cache_path)

    assert not cache_path.exists()


def test_get_json_cached_does_not_retry_plain_404(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    cache_path = tmp_path / "x.json"
    session = FakeSession([FakeResponse(404)])

    with pytest.raises(requests.exceptions.HTTPError):
        get_json_cached(session, "http://x", {}, cache_path)

    assert session.calls == 1


def test_make_session_mounts_dns_pinning_by_default():
    """Every make_session() caller (pull_gamma_universe, fetch_event_tags,
    main()) must get DNS pinning automatically — this must not depend on
    anyone remembering to wire it up by hand."""
    session = make_session()
    adapter = session.get_adapter("https://gamma-api.polymarket.com/events")
    assert isinstance(adapter, DNSPinningAdapter)

    adapter2 = session.get_adapter("https://clob.polymarket.com/prices-history")
    assert isinstance(adapter2, DNSPinningAdapter)
