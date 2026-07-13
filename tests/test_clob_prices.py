import src.ingest.clob_prices as clob_prices
from src.ingest.clob_prices import classify_empty, fetch_price_history


def test_fetch_price_history_uses_1440_first(monkeypatch, tmp_path):
    seen_fidelities = []

    def fake_get_json_cached(session, url, params, cache_path):
        seen_fidelities.append(params["fidelity"])
        return {"history": [{"t": 1700000000, "p": 0.5}]}

    monkeypatch.setattr(clob_prices, "get_json_cached", fake_get_json_cached)

    history, fidelity = fetch_price_history(session=None, token_id="tok1", cache_dir=tmp_path)
    assert fidelity == 1440
    assert seen_fidelities == [1440]
    assert history == [{"t": 1700000000, "p": 0.5}]


def test_fetch_price_history_falls_back_to_720(monkeypatch, tmp_path):
    responses = {1440: {"history": []}, 720: {"history": [{"t": 1700000000, "p": 0.3}]}}

    def fake_get_json_cached(session, url, params, cache_path):
        return responses[params["fidelity"]]

    monkeypatch.setattr(clob_prices, "get_json_cached", fake_get_json_cached)

    history, fidelity = fetch_price_history(session=None, token_id="tok2", cache_dir=tmp_path)
    assert fidelity == 720
    assert history == [{"t": 1700000000, "p": 0.3}]


def test_fetch_price_history_empty_when_both_fidelities_empty(monkeypatch, tmp_path):
    def fake_get_json_cached(session, url, params, cache_path):
        return {"history": []}

    monkeypatch.setattr(clob_prices, "get_json_cached", fake_get_json_cached)

    history, fidelity = fetch_price_history(session=None, token_id="tok3", cache_dir=tmp_path)
    assert history == []
    assert fidelity is None


def test_fetch_price_history_uses_deterministic_cache_paths(monkeypatch, tmp_path):
    seen_paths = []

    def fake_get_json_cached(session, url, params, cache_path):
        seen_paths.append(cache_path)
        return {"history": [{"t": 1, "p": 0.5}]}

    monkeypatch.setattr(clob_prices, "get_json_cached", fake_get_json_cached)
    fetch_price_history(session=None, token_id="tok4", cache_dir=tmp_path)
    assert seen_paths == [tmp_path / "tok4_1440.json"]


def test_classify_empty_amm_era_when_order_book_disabled():
    assert classify_empty(enable_order_book=False, volume_clob=50000, volume_num=50000) == "amm_era"


def test_classify_empty_amm_era_when_volume_clob_under_1pct():
    assert classify_empty(enable_order_book=True, volume_clob=50, volume_num=50000) == "amm_era"


def test_classify_empty_unexplained_when_order_book_enabled_and_clob_share_ok():
    assert classify_empty(enable_order_book=True, volume_clob=50000, volume_num=50000) == "unexplained"


def test_classify_empty_handles_zero_volume_num():
    assert classify_empty(enable_order_book=True, volume_clob=0, volume_num=0) == "unexplained"
