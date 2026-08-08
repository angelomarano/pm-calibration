from datetime import datetime, timezone

import numpy as np
import polars as pl
import pytest

from src.strategy.evaluation import (
    annualized_return,
    break_even_multiplier,
    censor_positions,
    chronological_pnl,
    max_drawdown,
)


def _row(**overrides) -> dict:
    base = {
        "market_id": "m1",
        "category": "Politics",
        "vol_tercile": 2,
        "days_to_resolution": 30.0,
        "resolution_ts": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "snapshot_date": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "gross_pnl": 0.1,
    }
    base.update(overrides)
    return base


def test_censor_positions_boundary_is_inclusive():
    cutoff = datetime(2026, 7, 12, tzinfo=timezone.utc)
    df = pl.DataFrame(
        [
            _row(market_id="m1", resolution_ts=cutoff),  # exactly at cutoff -> kept
            _row(market_id="m2", resolution_ts=datetime(2026, 7, 13, tzinfo=timezone.utc)),  # after -> excluded
            _row(market_id="m3", resolution_ts=datetime(2026, 7, 11, tzinfo=timezone.utc)),  # before -> kept
        ]
    )
    kept, stats = censor_positions(df, cutoff)
    assert sorted(kept["market_id"].to_list()) == ["m1", "m3"]
    assert stats["n_total"] == 3
    assert stats["n_kept"] == 2
    assert stats["n_excluded"] == 1
    assert stats["share_excluded"] == pytest.approx(1 / 3)


def test_censor_positions_per_category_share_and_profile():
    cutoff = datetime(2026, 7, 12, tzinfo=timezone.utc)
    after = datetime(2026, 7, 13, tzinfo=timezone.utc)
    before = datetime(2026, 7, 1, tzinfo=timezone.utc)
    df = pl.DataFrame(
        [
            _row(market_id="m1", category="Sports", resolution_ts=after, days_to_resolution=5.0),
            _row(market_id="m2", category="Sports", resolution_ts=before, days_to_resolution=10.0),
            _row(market_id="m3", category="Politics", resolution_ts=before, days_to_resolution=50.0),
        ]
    )
    kept, stats = censor_positions(df, cutoff)
    assert stats["excluded_share_by_category"]["Sports"] == pytest.approx(0.5)
    assert stats["excluded_share_by_category"]["Politics"] == pytest.approx(0.0)
    # excluded profile: only m1 (Sports, days_to_resolution=5.0)
    assert stats["excluded_profile"]["n"] == 1
    assert stats["excluded_profile"]["mean_days_to_resolution"] == pytest.approx(5.0)
    assert stats["excluded_profile"]["category_mix"] == {"Sports": 1.0}
    # kept profile: m2 (10.0) and m3 (50.0) -> mean 30.0
    assert stats["kept_profile"]["n"] == 2
    assert stats["kept_profile"]["mean_days_to_resolution"] == pytest.approx(30.0)


def test_censor_positions_empty_excluded_gives_empty_profile_not_crash():
    cutoff = datetime(2026, 7, 12, tzinfo=timezone.utc)
    df = pl.DataFrame([_row(market_id="m1", resolution_ts=datetime(2026, 1, 1, tzinfo=timezone.utc))])
    kept, stats = censor_positions(df, cutoff)
    assert stats["n_excluded"] == 0
    assert stats["excluded_profile"]["n"] == 0
    assert stats["excluded_profile"]["category_mix"] == {}
    assert np.isnan(stats["excluded_profile"]["mean_days_to_resolution"])


def test_chronological_pnl_orders_by_date_not_input_row_order():
    df = pl.DataFrame(
        [
            _row(snapshot_date=datetime(2026, 3, 1, tzinfo=timezone.utc), gross_pnl=0.2),
            _row(snapshot_date=datetime(2026, 1, 1, tzinfo=timezone.utc), gross_pnl=0.1),
            _row(snapshot_date=datetime(2026, 2, 1, tzinfo=timezone.utc), gross_pnl=-0.05),
        ]
    )
    result = chronological_pnl(df, "gross_pnl")
    assert result["snapshot_date"].to_list() == [
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    ]
    assert result["cumulative_pnl"].to_list() == pytest.approx([0.1, 0.05, 0.25])


def test_max_drawdown_hand_computed():
    # cumulative = [0,1,2,1,0,3] -> running_peak=[0,1,2,2,2,3] -> drawdown=[0,0,0,1,2,0] -> max=2
    cumulative = np.array([0, 1, 2, 1, 0, 3])
    assert max_drawdown(cumulative) == pytest.approx(2.0)


def test_max_drawdown_non_decreasing_series_is_zero():
    assert max_drawdown(np.array([0, 1, 2, 3, 4])) == 0.0


def test_max_drawdown_empty_is_zero():
    assert max_drawdown(np.array([])) == 0.0


def test_annualized_return_hand_computed():
    # total_net_pnl=10, capital_deployed=100, period_days=182.5 (half year) -> 0.10 * 2 = 0.20
    result = annualized_return(total_net_pnl=10.0, capital_deployed=100.0, period_days=182.5)
    assert result == pytest.approx(0.20)


def test_annualized_return_zero_capital_or_period_is_nan():
    assert np.isnan(annualized_return(10.0, 0.0, 100.0))
    assert np.isnan(annualized_return(10.0, 100.0, 0.0))


def test_break_even_multiplier_hand_computed():
    # edge_net=0.02, mean_half_spread=0.01 -> m*=2.0 (at 2x the observed band, edge hits zero)
    assert break_even_multiplier(0.02, 0.01) == pytest.approx(2.0)


def test_break_even_multiplier_zero_spread_edge_cases():
    assert break_even_multiplier(0.02, 0.0) == float("inf")
    assert break_even_multiplier(-0.02, 0.0) == float("-inf")
