import src.ingest.gamma_events as gamma_events
from src.ingest.gamma_events import _batches, fetch_event_tags


def test_batches_splits_into_chunks_of_size():
    ids = [str(i) for i in range(250)]
    chunks = list(_batches(ids, 100))
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_fetch_event_tags_batches_and_parses(monkeypatch, tmp_path):
    calls = []

    def fake_get_json_cached(session, url, params, cache_path):
        calls.append(params)
        ids = [v for k, v in params if k == "id"]
        return [{"id": i, "tags": [{"label": "Politics"}, {"label": "Trump"}]} for i in ids]

    monkeypatch.setattr(gamma_events, "get_json_cached", fake_get_json_cached)

    ids = [str(i) for i in range(150)]
    result = fetch_event_tags(session=None, event_ids=ids, cache_dir=tmp_path, batch_size=100)

    assert len(calls) == 2  # 150 ids -> 2 batches of <=100
    assert set(result.keys()) == set(ids)
    assert result[ids[0]] == ["Politics", "Trump"]


def test_fetch_event_tags_dedupes_and_sorts_ids(monkeypatch, tmp_path):
    seen_batches = []

    def fake_get_json_cached(session, url, params, cache_path):
        ids = [v for k, v in params if k == "id"]
        seen_batches.append(ids)
        return [{"id": i, "tags": []} for i in ids]

    monkeypatch.setattr(gamma_events, "get_json_cached", fake_get_json_cached)

    fetch_event_tags(session=None, event_ids=["3", "1", "2", "1"], cache_dir=tmp_path)

    assert seen_batches == [["1", "2", "3"]]


def test_fetch_event_tags_missing_tags_key_returns_empty_list(monkeypatch, tmp_path):
    def fake_get_json_cached(session, url, params, cache_path):
        return [{"id": "1"}]  # no "tags" key at all

    monkeypatch.setattr(gamma_events, "get_json_cached", fake_get_json_cached)

    result = fetch_event_tags(session=None, event_ids=["1"], cache_dir=tmp_path)
    assert result["1"] == []


def test_fetch_event_tags_drops_tags_with_no_label(monkeypatch, tmp_path):
    def fake_get_json_cached(session, url, params, cache_path):
        return [{"id": "1", "tags": [{"label": "Sports"}, {"id": "999"}]}]

    monkeypatch.setattr(gamma_events, "get_json_cached", fake_get_json_cached)

    result = fetch_event_tags(session=None, event_ids=["1"], cache_dir=tmp_path)
    assert result["1"] == ["Sports"]


def test_fetch_event_tags_cache_path_is_deterministic(monkeypatch, tmp_path):
    cache_paths = []

    def fake_get_json_cached(session, url, params, cache_path):
        cache_paths.append(cache_path)
        return []

    monkeypatch.setattr(gamma_events, "get_json_cached", fake_get_json_cached)

    fetch_event_tags(session=None, event_ids=["1", "2"], cache_dir=tmp_path)
    fetch_event_tags(session=None, event_ids=["1", "2"], cache_dir=tmp_path)

    assert cache_paths[0] == cache_paths[1]
