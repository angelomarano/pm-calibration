import datetime as dt
import random

import polars as pl

from src.panel.build_panel import P1_SCHEMA, attach_prices, build_p1_panel, compute_vol_tercile
from src.panel.spec_config import SpecConfig

UTC = dt.timezone.utc


def _candidates_df(*rows) -> pl.DataFrame:
    base_cols = {
        "market_id": "m1",
        "event_id": "e1",
        "category": "Politics",
        "clob_token_leg": "tok1",
        "y": 1,
        "resolution_ambiguous": False,
        "volume_num": 50000.0,
        "scheduled_life_hours": 1000.0,
        "fees_enabled": False,
        "taker_base_fee": 0.0,
        "leg_label": "Yes",
        "restricted": False,
        "snapshot_date": dt.datetime(2024, 1, 1, tzinfo=UTC),
        "is_oos": False,
        "days_to_sched_end": 60.0,
        "days_to_resolution": 45.0,
    }
    out = []
    for r in rows:
        merged = dict(base_cols)
        merged.update(r)
        out.append(merged)
    return pl.DataFrame(out)


def test_attach_prices_finds_last_point_within_staleness():
    candidates = _candidates_df({"snapshot_date": dt.datetime(2024, 1, 10, tzinfo=UTC)})
    prices = pl.DataFrame(
        {
            "clob_token_leg": ["tok1", "tok1"],
            "ts": [dt.datetime(2024, 1, 5, tzinfo=UTC), dt.datetime(2024, 1, 9, tzinfo=UTC)],
            "p": [0.4, 0.5],
        }
    )
    kept, stats = attach_prices(candidates, prices, staleness_max_hours=72, price_clip=(0.01, 0.99))
    assert kept.height == 1
    assert kept["p"].to_list() == [0.5]  # the later (Jan 9) point, not Jan 5
    assert stats == {"candidates": 1, "missing_price": 0, "kept": 1}


def test_attach_prices_71h_kept_73h_dropped():
    """The exact edge case from docs/W1_SPEC.md §3."""
    snapshot = dt.datetime(2024, 1, 10, 0, 0, 0, tzinfo=UTC)
    candidates_71 = _candidates_df({"market_id": "m71", "snapshot_date": snapshot})
    candidates_73 = _candidates_df({"market_id": "m73", "snapshot_date": snapshot})

    prices_71 = pl.DataFrame(
        {"clob_token_leg": ["tok1"], "ts": [snapshot - dt.timedelta(hours=71)], "p": [0.5]}
    )
    prices_73 = pl.DataFrame(
        {"clob_token_leg": ["tok1"], "ts": [snapshot - dt.timedelta(hours=73)], "p": [0.5]}
    )

    kept_71, stats_71 = attach_prices(candidates_71, prices_71, staleness_max_hours=72, price_clip=(0.01, 0.99))
    kept_73, stats_73 = attach_prices(candidates_73, prices_73, staleness_max_hours=72, price_clip=(0.01, 0.99))

    assert kept_71.height == 1
    assert stats_71["missing_price"] == 0
    assert kept_73.height == 0
    assert stats_73["missing_price"] == 1


def test_attach_prices_missing_when_no_price_before_snapshot():
    candidates = _candidates_df({"snapshot_date": dt.datetime(2024, 1, 1, tzinfo=UTC)})
    prices = pl.DataFrame(
        {"clob_token_leg": ["tok1"], "ts": [dt.datetime(2024, 2, 1, tzinfo=UTC)], "p": [0.5]}
    )
    kept, stats = attach_prices(candidates, prices, staleness_max_hours=72, price_clip=(0.01, 0.99))
    assert kept.height == 0
    assert stats["missing_price"] == 1


def test_attach_prices_clips_to_bounds():
    candidates = _candidates_df({"snapshot_date": dt.datetime(2024, 1, 10, tzinfo=UTC)})
    prices = pl.DataFrame(
        {"clob_token_leg": ["tok1"], "ts": [dt.datetime(2024, 1, 9, tzinfo=UTC)], "p": [0.995]}
    )
    kept, _ = attach_prices(candidates, prices, staleness_max_hours=72, price_clip=(0.01, 0.99))
    assert kept["p"].to_list() == [0.99]


def test_attach_prices_shuffled_input_still_produces_correct_asof_match():
    """join_asof requires sorted input; unsorted input doesn't raise, it
    silently gives wrong matches. Feed deliberately shuffled candidates
    and prices and confirm the match is still correct (not just that it
    doesn't crash)."""
    candidates = _candidates_df(
        {"market_id": "m1", "clob_token_leg": "tokA", "snapshot_date": dt.datetime(2024, 3, 1, tzinfo=UTC)},
        {"market_id": "m2", "clob_token_leg": "tokB", "snapshot_date": dt.datetime(2024, 3, 1, tzinfo=UTC)},
        {"market_id": "m3", "clob_token_leg": "tokA", "snapshot_date": dt.datetime(2024, 1, 1, tzinfo=UTC)},
    )
    # shuffle candidate row order
    shuffled_candidates = candidates[[2, 0, 1]]

    prices_rows = [
        {"clob_token_leg": "tokA", "ts": dt.datetime(2024, 2, 15, tzinfo=UTC), "p": 0.7},  # last-before-Mar1 for tokA
        {"clob_token_leg": "tokA", "ts": dt.datetime(2024, 1, 5, tzinfo=UTC), "p": 0.2},  # after m3's snapshot, must NOT match m3
        {"clob_token_leg": "tokB", "ts": dt.datetime(2024, 2, 20, tzinfo=UTC), "p": 0.9},  # last-before-Mar1 for tokB
        {"clob_token_leg": "tokA", "ts": dt.datetime(2023, 12, 20, tzinfo=UTC), "p": 0.1},  # last-before-Jan1 for tokA (m3)
    ]
    random.Random(0).shuffle(prices_rows)
    shuffled_prices = pl.DataFrame(prices_rows)

    kept, _ = attach_prices(shuffled_candidates, shuffled_prices, staleness_max_hours=24 * 90, price_clip=(0.0, 1.0))
    result = {r["market_id"]: r["p"] for r in kept.iter_rows(named=True)}

    assert result["m1"] == 0.7  # tokA as of 2024-03-01 -> the 2024-02-15 point, not 2024-01-05 or 2023-12-20
    assert result["m2"] == 0.9  # tokB as of 2024-03-01 -> the 2024-02-20 point
    assert result["m3"] == 0.1  # tokA as of 2024-01-01 -> the 2023-12-20 point, NOT the 2024-01-05 one (that's after)


def test_compute_vol_tercile_independent_per_snapshot():
    df = pl.DataFrame(
        {
            "snapshot_date": ["2024-01-01"] * 6 + ["2024-02-01"] * 6,
            "volume_num": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0],
        }
    )
    out = compute_vol_tercile(df)
    jan = out.filter(pl.col("snapshot_date") == "2024-01-01").sort("volume_num")
    feb = out.filter(pl.col("snapshot_date") == "2024-02-01").sort("volume_num")
    # both snapshots have the same shape (6 evenly-spaced values) -> same tercile pattern independently
    assert jan["vol_tercile"].to_list() == feb["vol_tercile"].to_list()
    assert set(jan["vol_tercile"].to_list()) == {1, 2, 3}


def test_build_p1_panel_final_schema_and_design_column():
    utc = UTC
    markets = pl.DataFrame(
        [
            {
                "market_id": "m1",
                "event_id": "e1",
                "category": "Politics",
                "clob_token_leg": "tok1",
                "y": 1,
                "resolution_ambiguous": False,
                "volume_num": 50000.0,
                "scheduled_life_hours": 1000.0,
                "fees_enabled": False,
                "taker_base_fee": 0.0,
                "leg_label": "Yes",
                "restricted": False,
                "created_at": dt.datetime(2024, 1, 1, tzinfo=utc),
                "end_date_sched": dt.datetime(2024, 3, 1, tzinfo=utc),
                "resolution_ts": dt.datetime(2024, 2, 15, tzinfo=utc),
                "panel_eligible": True,
            }
        ]
    )
    prices = pl.DataFrame(
        {"clob_token_leg": ["tok1"], "ts": [dt.datetime(2024, 1, 1, tzinfo=utc)], "p": [0.5]}
    )
    config = SpecConfig(
        snapshot_dates=[dt.datetime(2024, 1, 1, tzinfo=utc)],
        p2_horizons_days=[7, 30],
        staleness_max_hours=72.0,
        price_clip=(0.01, 0.99),
        oos_locked=True,
        oos_boundary=dt.datetime(2026, 1, 1, tzinfo=utc),
    )
    p1, stats = build_p1_panel(markets, prices, config)
    assert p1.schema == P1_SCHEMA
    assert p1["design"].to_list() == ["P1"]
    assert "clob_token_leg" not in p1.columns
    assert stats["final_rows"] == 1
