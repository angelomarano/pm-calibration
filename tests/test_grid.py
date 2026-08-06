from datetime import datetime, timezone

import numpy as np
import polars as pl
import pytest

from src.calibration.clocks import add_days_early, ran_to_term_frame
from src.calibration.regression import calibration_stat_fn
from src.calibration.grid import (
    PERIOD_LEVELS,
    SAMPLE_LEVELS,
    WEIGHTING_LEVELS,
    add_volume_weight,
    apply_period_filter,
    apply_sample_filter,
    build_reconciliation_grid,
    kish_effective_sample_size,
    period_overlap_stats,
    weighted_calibration_stat_fn,
)


def _row(**overrides) -> dict:
    base = {
        "market_id": "m1",
        "event_id": "e1",
        "category": "Politics",
        "p": 0.5,
        "y": 1,
        "days_to_sched_end": 30.0,
        "days_to_resolution": 30.0,
        "volume_num": 100.0,
        "vol_tercile": 2,
        "snapshot_date": datetime(2024, 6, 1, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def test_apply_sample_filter_all_is_noop():
    df = pl.DataFrame([_row(category="Sports"), _row(category="Politics")])
    out = apply_sample_filter(df, "all")
    assert out.height == df.height


def test_apply_sample_filter_ex_sports_excludes_sports():
    df = pl.DataFrame([_row(market_id="m1", category="Sports"), _row(market_id="m2", category="Politics")])
    out = apply_sample_filter(df, "ex_Sports")
    assert out["market_id"].to_list() == ["m2"]


def test_apply_sample_filter_top_liquidity_tercile_keeps_only_tercile_3():
    df = pl.DataFrame(
        [
            _row(market_id="m1", vol_tercile=1),
            _row(market_id="m2", vol_tercile=2),
            _row(market_id="m3", vol_tercile=3),
        ]
    )
    out = apply_sample_filter(df, "top_liquidity_tercile")
    assert out["market_id"].to_list() == ["m3"]


def test_apply_sample_filter_ran_to_term_matches_ran_to_term_frame_directly():
    """Must reuse clocks.ran_to_term_frame's own restriction exactly, not
    reimplement it -- checked by comparing against a direct call on the
    same frame."""
    df = pl.DataFrame(
        [
            _row(market_id="m1", days_to_sched_end=10.0, days_to_resolution=8.0),  # kept, days_early=2.0
            _row(market_id="m2", days_to_sched_end=10.0, days_to_resolution=7.9),  # dropped, days_early=2.1
            _row(market_id="m3", days_to_sched_end=5.0, days_to_resolution=5.0),  # kept, days_early=0
        ]
    )
    via_grid = apply_sample_filter(df, "ran_to_term")
    expected, _ = ran_to_term_frame(add_days_early(df))
    assert sorted(via_grid["market_id"].to_list()) == sorted(expected["market_id"].to_list())
    assert via_grid["market_id"].to_list() != df["market_id"].to_list() or via_grid.height < df.height


def test_apply_sample_filter_unknown_level_raises():
    df = pl.DataFrame([_row()])
    with pytest.raises(ValueError):
        apply_sample_filter(df, "not_a_real_level")


def test_apply_period_filter_year_boundary():
    df = pl.DataFrame(
        [
            _row(market_id="m1", snapshot_date=datetime(2024, 12, 31, 23, 59, tzinfo=timezone.utc)),
            _row(market_id="m2", snapshot_date=datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)),
        ]
    )
    assert apply_period_filter(df, "2024")["market_id"].to_list() == ["m1"]
    assert apply_period_filter(df, "2025")["market_id"].to_list() == ["m2"]


def test_add_volume_weight_mean_is_one_within_the_passed_frame():
    df = pl.DataFrame([_row(market_id="m1", volume_num=10.0), _row(market_id="m2", volume_num=30.0)])
    out = add_volume_weight(df)
    assert out["weight"].mean() == pytest.approx(1.0)
    assert out["weight"].to_list() == pytest.approx([10.0 / 20.0, 30.0 / 20.0])


def test_weighted_calibration_stat_fn_requires_weight_column():
    df = pl.DataFrame([_row()])
    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        weighted_calibration_stat_fn(df)


def _synthetic_panel(n=2000, seed=0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    categories = ["Politics", "Sports", "Crypto"]
    for i in range(n):
        cat = categories[i % len(categories)]
        year = 2024 if i % 2 == 0 else 2025
        true_rate = rng.uniform(0.2, 0.8)
        y = rng.binomial(1, true_rate)
        p = float(np.clip(true_rate + rng.normal(0, 0.15), 0.02, 0.98))
        rows.append(
            _row(
                market_id=f"m{i}",
                event_id=f"e{i}",
                category=cat,
                p=p,
                y=int(y),
                volume_num=float(rng.uniform(1, 1000)),
                vol_tercile=int(rng.integers(1, 4)),
                snapshot_date=datetime(year, 6, 1, tzinfo=timezone.utc),
            )
        )
    return pl.DataFrame(rows)


def test_weighted_calibration_stat_fn_changes_beta_on_nonuniform_weights():
    df = _synthetic_panel(n=4000, seed=1)
    half = df.height // 2
    # steepen the p/y relationship in the second half only
    y = df["y"].to_numpy().copy()
    p = df["p"].to_numpy().copy()
    x = np.log(p / (1 - p))
    pi_steep = 1 / (1 + np.exp(-(0.0 + 2.5 * x[half:])))
    rng = np.random.default_rng(2)
    y[half:] = rng.binomial(1, pi_steep)
    df = df.with_columns(pl.Series("y", y))

    weight = np.concatenate([np.full(half, 0.05), np.full(df.height - half, 20.0)])
    weighted_df = df.with_columns(pl.Series("weight", weight))

    fit_equal = calibration_stat_fn(df)
    fit_weighted = weighted_calibration_stat_fn(weighted_df)
    assert fit_weighted["beta"] > fit_equal["beta"] + 0.3


def test_build_reconciliation_grid_has_all_16_cells():
    df = _synthetic_panel(n=3000, seed=3)
    table = build_reconciliation_grid(df, B=30, seed=0)
    assert table.height == len(WEIGHTING_LEVELS) * len(SAMPLE_LEVELS) * len(PERIOD_LEVELS)
    seen = {(r["weighting"], r["sample"], r["period"]) for r in table.iter_rows(named=True)}
    expected = {(w, s, p) for w in WEIGHTING_LEVELS for s in SAMPLE_LEVELS for p in PERIOD_LEVELS}
    assert seen == expected
    assert table["n_clusters"].min() > 0
    assert set(table["low_power"].to_list()) <= {True, False}


def test_build_reconciliation_grid_reports_n_eff_and_ci_width():
    df = _synthetic_panel(n=3000, seed=3)
    table = build_reconciliation_grid(df, B=30, seed=0)
    expected_width = (table["beta_ci_high"] - table["beta_ci_low"]).to_list()
    assert table["beta_ci_width"].to_list() == pytest.approx(expected_width)
    equal_rows = table.filter(pl.col("weighting") == "equal")
    assert (equal_rows["n_eff"] == equal_rows["n"]).all()  # equal weighting: n_eff == n exactly
    weighted_rows = table.filter(pl.col("weighting") == "volume_weighted")
    assert (weighted_rows["n_eff"] <= weighted_rows["n"]).all()  # Kish's n_eff never exceeds n


def test_kish_effective_sample_size_uniform_weights_equals_n():
    w = np.ones(50)
    assert kish_effective_sample_size(w) == pytest.approx(50.0)


def test_kish_effective_sample_size_scale_invariant():
    w = np.array([1.0, 2.0, 3.0, 10.0])
    assert kish_effective_sample_size(w) == pytest.approx(kish_effective_sample_size(3.0 * w))


def test_kish_effective_sample_size_skewed_weights_below_n():
    """One dominant weight among many tiny ones -- n_eff should collapse
    toward ~1, not stay near the nominal count."""
    w = np.concatenate([[100.0], np.full(99, 0.01)])
    n_eff = kish_effective_sample_size(w)
    assert n_eff < 5.0


def test_period_overlap_stats_detects_shared_cluster():
    df = pl.DataFrame(
        [
            _row(market_id="m1", event_id="e1", snapshot_date=datetime(2024, 6, 1, tzinfo=timezone.utc)),
            _row(market_id="m1", event_id="e1", snapshot_date=datetime(2025, 2, 1, tzinfo=timezone.utc)),
            _row(market_id="m2", event_id="e2", snapshot_date=datetime(2024, 6, 1, tzinfo=timezone.utc)),
        ]
    )
    out = period_overlap_stats(df)
    row = out.filter(pl.col("sample") == "all").row(0, named=True)
    assert row["n_clusters_2024"] == 2  # e1, e2
    assert row["n_clusters_2025"] == 1  # e1 only
    assert row["n_overlap"] == 1  # e1 appears in both
