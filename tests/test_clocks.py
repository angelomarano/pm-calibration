from pathlib import Path

import polars as pl
import pytest

from src.calibration.clocks import (
    CLOCKS,
    add_days_early,
    build_clock_comparison,
    classify_tercile_sequence,
    compare_clocks,
    ran_to_term_frame,
)
from src.calibration.data import load_calibration_frame


def _panel_row(**overrides) -> dict:
    base = {
        "market_id": "m1",
        "event_id": "e1",
        "category": "Politics",
        "p": 0.5,
        "y": 1,
        "days_to_sched_end": 30.0,
        "days_to_resolution": 30.0,
    }
    base.update(overrides)
    return base


def test_add_days_early_hand_computed():
    df = pl.DataFrame([_panel_row(days_to_sched_end=10.0, days_to_resolution=3.0)])
    out = add_days_early(df)
    assert out["days_early"].to_list() == [7.0]


def test_add_days_early_invariant_across_snapshots_of_same_market():
    """A market resolved 5 days early: at every snapshot, regardless of
    how far that snapshot is from either date, days_to_sched_end -
    days_to_resolution must be exactly 5 (both terms share the same
    snapshot_date, which cancels out algebraically)."""
    rows = [
        _panel_row(market_id="m1", days_to_sched_end=100.0, days_to_resolution=95.0),
        _panel_row(market_id="m1", days_to_sched_end=40.0, days_to_resolution=35.0),
        _panel_row(market_id="m1", days_to_sched_end=2.0, days_to_resolution=-3.0),
    ]
    out = add_days_early(pl.DataFrame(rows))
    assert out["days_early"].to_list() == [5.0, 5.0, 5.0]


REAL_P1_PATH = Path("data/panel/p1.parquet")


@pytest.mark.skipif(not REAL_P1_PATH.exists(), reason="data/panel/p1.parquet not built in this environment")
def test_add_days_early_invariant_on_real_data_within_float_tolerance():
    """Real-data spot check: exact equality fails on real data due to
    float noise in the day-count arithmetic (~1e-5 days max, confirmed by
    checking the magnitude, not assumed) -- must use an approximate
    check, not exact equality."""
    df, _ = load_calibration_frame(REAL_P1_PATH)
    out = add_days_early(df)
    spread = out.group_by("market_id").agg(
        (pl.col("days_early").max() - pl.col("days_early").min()).alias("spread"), pl.len().alias("n")
    ).filter(pl.col("n") > 1)
    assert spread.height > 0  # sanity: there really are multi-snapshot markets in the real data
    assert spread["spread"].max() < 0.01  # well under float noise for a day-scale quantity


def test_ran_to_term_frame_boundary_inclusive():
    rows = [
        _panel_row(market_id="m1", days_to_sched_end=10.0, days_to_resolution=8.0),  # days_early=2.0, exactly at tolerance
        _panel_row(market_id="m2", days_to_sched_end=10.0, days_to_resolution=7.9),  # days_early=2.1, just outside
    ]
    df = add_days_early(pl.DataFrame(rows))
    restricted, _ = ran_to_term_frame(df, tolerance_days=2.0)
    assert restricted["market_id"].to_list() == ["m1"]


def test_ran_to_term_frame_stats_per_category():
    rows = [
        _panel_row(market_id="m1", category="Politics", days_to_sched_end=10.0, days_to_resolution=10.0),  # kept
        _panel_row(market_id="m2", category="Politics", days_to_sched_end=10.0, days_to_resolution=0.0),  # dropped
        _panel_row(market_id="m3", category="Sports", days_to_sched_end=5.0, days_to_resolution=5.0),  # kept
    ]
    df = add_days_early(pl.DataFrame(rows))
    _, stats = ran_to_term_frame(df, tolerance_days=2.0)

    assert stats["Politics"]["n_total"] == 2
    assert stats["Politics"]["n_kept"] == 1
    assert stats["Politics"]["share_dropped"] == pytest.approx(0.5)
    assert stats["Sports"]["n_kept"] == 1
    assert stats["ALL"]["n_total"] == 3
    assert stats["ALL"]["n_kept"] == 2


def _synthetic_panel(n_per_category: int = 400) -> pl.DataFrame:
    import numpy as np

    rng = np.random.default_rng(0)
    rows = []
    for cat in ("Politics", "Sports"):
        for i in range(n_per_category):
            days_sched = rng.uniform(10, 300)
            days_early_true = rng.normal(0, 5)
            days_res = days_sched - days_early_true
            # p correlated with y but with enough overlap that small resampled
            # cells don't come out perfectly separated (which would legitimately
            # make event_bootstrap raise -- that's correct behavior, not
            # something to work around here, just avoided in this smoke data)
            true_rate = rng.uniform(0.2, 0.8)
            y = rng.binomial(1, true_rate)
            p = np.clip(true_rate + rng.normal(0, 0.15), 0.02, 0.98)
            rows.append(
                _panel_row(
                    market_id=f"{cat}_m{i}",
                    event_id=f"{cat}_e{i}",
                    category=cat,
                    p=float(p),
                    y=int(y),
                    days_to_sched_end=days_sched,
                    days_to_resolution=days_res,
                )
            )
    return pl.DataFrame(rows)


def test_build_clock_comparison_low_power_flag():
    df = _synthetic_panel(n_per_category=400)
    table, stats = build_clock_comparison(df, B=50, seed=0, cluster_floor=1000)  # absurdly high floor -> everything low_power
    assert table["low_power"].all()

    table2, _ = build_clock_comparison(df, B=50, seed=0, cluster_floor=1)  # floor of 1 -> nothing low_power
    assert not table2["low_power"].any()


def test_build_clock_comparison_no_cells_suppressed():
    """Contrast with W2d's pooling: every (category, clock, tercile)
    combination must appear, even for thin cells -- nothing gets pooled
    away here."""
    df = _synthetic_panel(n_per_category=400)
    table, _ = build_clock_comparison(df, B=50, seed=0)
    expected_rows = len(CLOCKS) * 2 * 3  # 4 clocks x 2 categories x 3 terciles
    assert table.height == expected_rows
    for clock in CLOCKS:
        assert set(table.filter(pl.col("clock") == clock)["category"].unique().to_list()) == {"Politics", "Sports"}


def test_compare_clocks_beta_diff_and_overlap():
    table = pl.DataFrame(
        [
            {"category": "Politics", "clock": "A_term", "tercile": 1, "beta_point": 1.0, "beta_ci_low": 0.8, "beta_ci_high": 1.2},
            {"category": "Politics", "clock": "B_term", "tercile": 1, "beta_point": 1.05, "beta_ci_low": 0.9, "beta_ci_high": 1.3},
            {"category": "Politics", "clock": "A_term", "tercile": 2, "beta_point": 1.0, "beta_ci_low": 0.9, "beta_ci_high": 1.1},
            {"category": "Politics", "clock": "B_term", "tercile": 2, "beta_point": 2.0, "beta_ci_low": 1.8, "beta_ci_high": 2.2},
        ]
    )
    result = compare_clocks(table, "A_term", "B_term").sort("tercile")
    assert result["beta_diff"].to_list() == pytest.approx([1.0 - 1.05, 1.0 - 2.0])
    assert result["cis_overlap"].to_list() == [True, False]


def test_classify_tercile_sequence_matches_gate_d_reference_cases():
    # Geopolitics (real Gate D numbers): directionally rising, CIs overlap
    geo_betas = [1.120, 1.311, 1.584]
    geo_cis = [(0.985, 1.306), (1.122, 1.601), (1.268, 2.004)]
    assert classify_tercile_sequence(geo_betas, geo_cis) == "directionally rising but overlapping"

    # Sports (real Gate D numbers): not monotonic
    sports_betas = [0.989, 0.922, 0.919]
    sports_cis = [(0.917, 1.067), (0.830, 1.030), (0.790, 1.079)]
    assert classify_tercile_sequence(sports_betas, sports_cis) == "not monotonically rising"

    # constructed: strictly rising with non-overlapping CIs
    rising_betas = [1.0, 1.5, 2.0]
    rising_cis = [(0.9, 1.1), (1.3, 1.7), (1.9, 2.1)]
    assert classify_tercile_sequence(rising_betas, rising_cis) == "rising with non-overlapping CIs"


def test_build_clock_comparison_smoke_all_four_clocks():
    df = _synthetic_panel(n_per_category=400)
    table, stats = build_clock_comparison(df, B=30, seed=0)
    assert set(table["clock"].unique().to_list()) == set(CLOCKS)
    assert "ran_to_term" in stats
    assert "ALL" in stats["ran_to_term"]
