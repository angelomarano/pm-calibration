import json

import pytest

import src.ingest.gamma_markets as gamma_markets
from src.ingest.gamma_markets import Window, fetch_window_pages, month_windows
from src.ingest.http import RetryableStatus


def test_month_windows_covers_full_range_plus_open_tail():
    windows = month_windows(start=(2024, 1), end=(2027, 12))
    assert len(windows) == 49  # 48 months + 1 open tail

    assert windows[0].id == "2024-01"
    assert windows[0].end_date_min == "2024-01-01T00:00:00Z"
    assert windows[0].end_date_max == "2024-02-01T00:00:00Z"

    assert windows[11].id == "2024-12"
    assert windows[11].end_date_max == "2025-01-01T00:00:00Z"

    assert windows[-2].id == "2027-12"
    assert windows[-2].end_date_max == "2028-01-01T00:00:00Z"

    assert windows[-1].id == "2028-open"
    assert windows[-1].end_date_min == "2028-01-01T00:00:00Z"
    assert windows[-1].end_date_max is None


def test_fetch_window_pages_follows_after_cursor_until_short_page(monkeypatch, tmp_path):
    seen_params = []
    responses = [
        {"markets": [{"id": str(i)} for i in range(100)], "next_cursor": "cursor-1"},
        {"markets": [{"id": str(i)} for i in range(100, 200)], "next_cursor": "cursor-2"},
        {"markets": [{"id": str(i)} for i in range(200, 242)], "next_cursor": None},
    ]

    def fake_get_json_cached(session, url, params, cache_path):
        seen_params.append(params)
        return responses.pop(0)

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    window = Window(id="test", end_date_min="a", end_date_max="b")
    result = fetch_window_pages(session=None, window=window, cache_dir=tmp_path)

    assert len(result) == 242
    assert "after_cursor" not in seen_params[0]
    assert seen_params[1]["after_cursor"] == "cursor-1"
    assert seen_params[2]["after_cursor"] == "cursor-2"


def test_fetch_window_pages_stops_on_short_page_even_if_cursor_present(monkeypatch, tmp_path):
    """A short page always terminates the loop, even if the server still
    hands back a (meaningless) next_cursor."""
    responses = [{"markets": [{"id": "1"}, {"id": "2"}], "next_cursor": "some-token"}]

    def fake_get_json_cached(session, url, params, cache_path):
        return responses.pop(0)

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    window = Window(id="test", end_date_min="a", end_date_max="b")
    result = fetch_window_pages(session=None, window=window, cache_dir=tmp_path, page_size=100)
    assert len(result) == 2


def test_fetch_window_pages_uses_deterministic_page_cache_paths(monkeypatch, tmp_path):
    seen_paths = []

    def fake_get_json_cached(session, url, params, cache_path):
        seen_paths.append(cache_path)
        return {"markets": [], "next_cursor": None}

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    window = Window(id="2024-01", end_date_min="a", end_date_max="b")
    fetch_window_pages(session=None, window=window, cache_dir=tmp_path)

    assert seen_paths == [tmp_path / "2024-01_page00000.json"]


def test_fetch_window_pages_open_ended_omits_end_date_max(monkeypatch, tmp_path):
    seen_params = []

    def fake_get_json_cached(session, url, params, cache_path):
        seen_params.append(params)
        return {"markets": [], "next_cursor": None}

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    window = Window(id="2028-open", end_date_min="2028-01-01T00:00:00Z", end_date_max=None)
    fetch_window_pages(session=None, window=window, cache_dir=tmp_path)

    assert "end_date_max" not in seen_params[0]
    assert seen_params[0]["end_date_min"] == "2028-01-01T00:00:00Z"


def test_fetch_window_pages_raises_on_stalled_pagination(monkeypatch, tmp_path):
    """Reproduces the real failure mode found by hand while debugging the
    full pull: an unrecognized/ignored after_cursor param makes the server
    return the same page again (HTTP 200, no error). fetch_window_pages
    must detect the repeated ids and raise rather than loop forever or
    silently duplicate data."""
    same_page = {"markets": [{"id": str(i)} for i in range(100)], "next_cursor": "some-token"}

    def fake_get_json_cached(session, url, params, cache_path):
        return same_page  # always identical, regardless of after_cursor

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    window = Window(id="test", end_date_min="a", end_date_max="b")
    with pytest.raises(RuntimeError, match="stalled"):
        fetch_window_pages(session=None, window=window, cache_dir=tmp_path)


def test_pull_gamma_universe_dedupes_across_windows(monkeypatch, tmp_path):
    fake_windows = [
        Window(id="w1", end_date_min="a", end_date_max="b"),
        Window(id="w2", end_date_min="c", end_date_max="d"),
    ]
    fake_data = {
        "w1": [{"id": "1"}, {"id": "2"}],
        "w2": [{"id": "2"}, {"id": "3"}],  # id "2" overlaps across windows
    }

    def fake_fetch_window_pages(session, window, cache_dir=None, **kwargs):
        return fake_data[window.id]

    monkeypatch.setattr(gamma_markets, "month_windows", lambda: fake_windows)
    monkeypatch.setattr(gamma_markets, "fetch_window_pages", fake_fetch_window_pages)

    result = gamma_markets.pull_gamma_universe(cache_dir=tmp_path, session=object())

    ids = sorted(m["id"] for m in result)
    assert ids == ["1", "2", "3"]


def test_fetch_window_pages_logs_gap_and_returns_partial_after_persistent_failure(monkeypatch, tmp_path):
    """Reproduces the real persistent-500 failure found in production
    (2025-12, page 240): a cursor that fails identically every time, not a
    transient blip. After MAX_PAGE_RETRY_ROUNDS consecutive failures at that
    exact cursor, fetch_window_pages must stop retrying, log a gap record,
    and return whatever it collected — not raise, not hang, not skip past
    the bad cursor to guess at the rest of the window."""
    good_page = {"markets": [{"id": str(i)} for i in range(100)], "next_cursor": "stuck-cursor"}
    call_count = {"n": 0}

    def fake_get_json_cached(session, url, params, cache_path):
        call_count["n"] += 1
        if params.get("after_cursor") == "stuck-cursor":
            raise RetryableStatus(500, url, '{"type":"internal error","error":"internal server error"}')
        return good_page

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    window = Window(id="test-gap", end_date_min="a", end_date_max="b")
    result = fetch_window_pages(session=None, window=window, cache_dir=tmp_path)

    assert len(result) == 100  # first page succeeded; the stuck second page gave up
    assert call_count["n"] == 1 + gamma_markets.MAX_PAGE_RETRY_ROUNDS

    gap_path = tmp_path / "_gaps.jsonl"
    assert gap_path.exists()
    record = json.loads(gap_path.read_text().splitlines()[0])
    assert record["window_id"] == "test-gap"
    assert record["last_successful_cursor"] == "stuck-cursor"
    assert record["markets_fetched_before_gap"] == 100
    assert "internal server error" in record["error"]


def test_pull_gamma_universe_continues_past_a_gapped_window(monkeypatch, tmp_path):
    """End-to-end: window 1 hits the persistent-500 failure mode for real
    (not mocked away at the fetch_window_pages level); pull_gamma_universe
    must not raise or hang, and must still fully fetch window 2."""
    fake_windows = [
        Window(id="w1-stuck", end_date_min="a", end_date_max="b"),
        Window(id="w2-ok", end_date_min="c", end_date_max="d"),
    ]
    monkeypatch.setattr(gamma_markets, "month_windows", lambda: fake_windows)

    def fake_get_json_cached(session, url, params, cache_path):
        if params["end_date_min"] == "a":
            if params.get("after_cursor") == "stuck-cursor":
                raise RetryableStatus(500, url, '{"error":"internal server error"}')
            return {"markets": [{"id": f"w1-{i}"} for i in range(100)], "next_cursor": "stuck-cursor"}
        return {"markets": [{"id": "w2-m1"}], "next_cursor": None}

    monkeypatch.setattr(gamma_markets, "get_json_cached", fake_get_json_cached)

    result = gamma_markets.pull_gamma_universe(cache_dir=tmp_path, session=object())

    ids = sorted(m["id"] for m in result)
    assert ids == sorted([f"w1-{i}" for i in range(100)] + ["w2-m1"])

    gap_path = tmp_path / "_gaps.jsonl"
    record = json.loads(gap_path.read_text().splitlines()[0])
    assert record["window_id"] == "w1-stuck"
