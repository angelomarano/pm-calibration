import datetime as dt
import json

import pytest

import src.panel.build_prices as build_prices
from src.ingest.http import RetryableStatus
from src.panel.build_prices import PRICE_SCHEMA, build_prices_table, fetch_prices_for_markets


def _market(**overrides):
    base = {
        "market_id": "m1",
        "clob_token_leg": "tok1",
        "enable_order_book": True,
        "volume_clob": 50000.0,
        "volume_num": 50000.0,
    }
    base.update(overrides)
    return base


def test_fetch_prices_for_markets_collects_history_rows(monkeypatch, tmp_path):
    def fake_fetch_price_history(session, token_id, cache_dir):
        return [{"t": 1700000000, "p": 0.5}, {"t": 1700086400, "p": 0.6}], 1440

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    rows, stats = fetch_prices_for_markets([_market()], session=None, cache_dir=tmp_path)

    assert stats["total"] == 1
    assert stats["has_history"] == 1
    assert stats["amm_era"] == 0
    assert stats["unexplained"] == 0
    assert len(rows) == 2
    assert rows[0]["clob_token_leg"] == "tok1"
    assert rows[0]["ts"] == dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt.timezone.utc)
    assert rows[0]["p"] == 0.5


def test_fetch_prices_for_markets_normalizes_ms_timestamps(monkeypatch, tmp_path):
    def fake_fetch_price_history(session, token_id, cache_dir):
        return [{"t": 1700000000000, "p": 0.5}], 1440  # milliseconds

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    rows, _ = fetch_prices_for_markets([_market()], session=None, cache_dir=tmp_path)
    assert rows[0]["ts"] == dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt.timezone.utc)


def test_fetch_prices_for_markets_classifies_empty_as_amm_era(monkeypatch, tmp_path):
    def fake_fetch_price_history(session, token_id, cache_dir):
        return [], None

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    markets = [_market(enable_order_book=False)]
    rows, stats = fetch_prices_for_markets(markets, session=None, cache_dir=tmp_path)

    assert rows == []
    assert stats["amm_era"] == 1
    assert stats["unexplained"] == 0
    assert stats["has_history"] == 0


def test_fetch_prices_for_markets_classifies_empty_as_unexplained(monkeypatch, tmp_path):
    def fake_fetch_price_history(session, token_id, cache_dir):
        return [], None

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    markets = [_market(enable_order_book=True, volume_clob=50000.0, volume_num=50000.0)]
    rows, stats = fetch_prices_for_markets(markets, session=None, cache_dir=tmp_path)

    assert stats["unexplained"] == 1
    assert stats["amm_era"] == 0


def test_fetch_prices_for_markets_stats_totals_correct_across_mixed_batch(monkeypatch, tmp_path):
    call_log = []

    def fake_fetch_price_history(session, token_id, cache_dir):
        call_log.append(token_id)
        if token_id == "has-history":
            return [{"t": 1700000000, "p": 0.5}], 1440
        return [], None

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    markets = [
        _market(clob_token_leg="has-history"),
        _market(clob_token_leg="amm", enable_order_book=False),
        _market(clob_token_leg="unexplained", enable_order_book=True, volume_clob=50000.0, volume_num=50000.0),
    ]
    rows, stats = fetch_prices_for_markets(markets, session=None, cache_dir=tmp_path)

    assert stats["total"] == 3
    assert stats["has_history"] == 1
    assert stats["amm_era"] == 1
    assert stats["unexplained"] == 1
    assert len(call_log) == 3


def test_build_prices_table_schema():
    rows = [{"clob_token_leg": "tok1", "ts": dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), "p": 0.5}]
    df = build_prices_table(rows)
    assert df.schema == PRICE_SCHEMA
    assert df.height == 1


def test_build_prices_table_empty_rows_returns_empty_schema_df():
    df = build_prices_table([])
    assert df.schema == PRICE_SCHEMA
    assert df.height == 0


def test_fetch_prices_for_markets_logs_gap_and_continues_after_persistent_failure(monkeypatch, tmp_path):
    """A token that fails identically every time (persistent, not
    transient — same failure mode found in the real M1 pull) must not
    block the rest of the run: after MAX_TOKEN_RETRY_ROUNDS, log a gap
    and move to the next market."""
    call_count = {"n": 0}

    def fake_fetch_price_history(session, token_id, cache_dir):
        call_count["n"] += 1
        if token_id == "bad-token":
            raise RetryableStatus(500, "url", '{"error":"internal server error"}')
        return [{"t": 1700000000, "p": 0.5}], 1440

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    markets = [
        _market(market_id="m-bad", clob_token_leg="bad-token"),
        _market(market_id="m-good", clob_token_leg="good-token"),
    ]
    rows, stats = fetch_prices_for_markets(markets, session=None, cache_dir=tmp_path)

    assert stats["gaps"] == 1
    assert stats["has_history"] == 1  # the second market still got fetched
    assert call_count["n"] == build_prices.MAX_TOKEN_RETRY_ROUNDS + 1  # N failures + 1 success

    gap_path = tmp_path / "_gaps.jsonl"
    assert gap_path.exists()
    record = json.loads(gap_path.read_text().splitlines()[0])
    assert record["market_id"] == "m-bad"
    assert record["clob_token_leg"] == "bad-token"
    assert "internal server error" in record["error"]


def test_fetch_prices_for_markets_gap_does_not_count_as_amm_era_or_unexplained(monkeypatch, tmp_path):
    def fake_fetch_price_history(session, token_id, cache_dir):
        raise RetryableStatus(500, "url", "boom")

    monkeypatch.setattr(build_prices, "fetch_price_history", fake_fetch_price_history)

    rows, stats = fetch_prices_for_markets([_market()], session=None, cache_dir=tmp_path)

    assert stats["gaps"] == 1
    assert stats["amm_era"] == 0
    assert stats["unexplained"] == 0
    assert stats["has_history"] == 0
    assert rows == []
