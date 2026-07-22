import math

import numpy as np
import polars as pl
import pytest

from src.calibration.murphy import (
    brier_skill_score,
    build_murphy_report,
    log_loss,
    murphy_decomposition,
    murphy_stat_fn,
)
from src.inference.bootstrap import event_bootstrap


def test_murphy_decomposition_hand_computed_identity():
    """4-point example, hand-verified before trusting it in code:
    2 obs at p=0.2/y=0, 2 obs at p=0.8/y=1 -> brier=0.04, rel=0.04,
    res=0.25, unc=0.25. rel - res + unc == brier exactly (to float
    precision), not just approximately."""
    p = np.array([0.2, 0.2, 0.8, 0.8])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    d = murphy_decomposition(p, y)

    assert d["brier"] == pytest.approx(0.04)
    assert d["rel"] == pytest.approx(0.04)
    assert d["res"] == pytest.approx(0.25)
    assert d["unc"] == pytest.approx(0.25)
    assert (d["rel"] - d["res"] + d["unc"]) == pytest.approx(d["brier"], abs=1e-12)


def test_murphy_decomposition_perfectly_calibrated_zero_rel():
    """Every bin's mean forecast equals its mean outcome -> REL exactly 0."""
    p = np.array([0.2, 0.2, 0.8, 0.8])
    y = np.array([0.2, 0.2, 0.8, 0.8])  # mean outcome per bin == mean forecast per bin
    d = murphy_decomposition(p, y)
    assert d["rel"] == pytest.approx(0.0, abs=1e-12)


def test_murphy_decomposition_no_resolution_zero_res():
    """Every bin has the same mean outcome as the overall base rate ->
    RES exactly 0 (bins carry no discriminating information)."""
    p = np.array([0.1, 0.1, 0.9, 0.9])
    y = np.array([0.0, 1.0, 0.0, 1.0])  # each bin's o_bar_k = 0.5 = overall o_bar
    d = murphy_decomposition(p, y)
    assert d["res"] == pytest.approx(0.0, abs=1e-12)


def test_brier_skill_score_positive_when_beats_baseline():
    # perfect predictions (brier=0) against a non-degenerate base rate -> bss=1
    assert brier_skill_score(brier=0.0, unc=0.25) == pytest.approx(1.0)


def test_brier_skill_score_nan_when_unc_zero():
    result = brier_skill_score(brier=0.0, unc=0.0)
    assert math.isnan(result)


def test_log_loss_matches_hand_computed_value():
    p = np.array([0.2, 0.2, 0.8, 0.8])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    # every term is -log(0.8) by construction (see module docstring's worked example)
    assert log_loss(p, y) == pytest.approx(-math.log(0.8), abs=1e-9)


def test_log_loss_clips_extreme_probabilities_no_inf_or_nan():
    p = np.array([0.0, 1.0])
    y = np.array([0.0, 1.0])  # perfectly "correct" but p at the extremes -- must not blow up
    result = log_loss(p, y)
    assert math.isfinite(result)


def test_murphy_stat_fn_returns_all_six_keys():
    df = pl.DataFrame({"p": [0.2, 0.2, 0.8, 0.8], "y": [0, 0, 1, 1]})
    result = murphy_stat_fn(df)
    assert set(result.keys()) == {"brier", "rel", "res", "unc", "bss", "log_loss"}


def test_assign_bin_index_regression_matches_reliability_convention():
    """Regression guard for the reliability.py refactor: the same boundary
    cases already covered in test_reliability.py must still bin the same
    way through the now-shared assign_bin_index."""
    from src.calibration.reliability import DECILE_EDGES, assign_bin_index

    p = np.array([0.05, 0.1, 0.2, 0.9, 1.0])
    idx = assign_bin_index(p, DECILE_EDGES)
    assert idx.tolist() == [0, 1, 2, 9, 9]


def test_murphy_stat_fn_through_event_bootstrap():
    df = pl.DataFrame(
        {
            "event_id": [f"e{i}" for i in range(20)],
            "market_id": [f"m{i}" for i in range(20)],
            "p": np.linspace(0.05, 0.95, 20).tolist(),
            "y": ([0] * 10 + [1] * 10),
        }
    )
    result = event_bootstrap(df, murphy_stat_fn, B=100, seed=0)
    for key in ("brier", "rel", "res", "unc", "bss", "log_loss"):
        assert key in result.point
        assert result.n_valid[key] > 0
        assert result.ci_low[key] <= result.ci_high[key]


def test_build_murphy_report_smoke():
    rng = np.random.default_rng(0)
    n = 300
    df = pl.DataFrame(
        {
            "event_id": [f"e{i}" for i in range(n)],
            "market_id": [f"m{i}" for i in range(n)],
            "category": rng.choice(["Politics", "Sports", "Crypto"], size=n).tolist(),
            "p": rng.uniform(0.01, 0.99, size=n).tolist(),
            "y": rng.integers(0, 2, size=n).tolist(),
        }
    )
    report = build_murphy_report(df, B=100, seed=0)
    assert set(report["cell"].to_list()) >= {"ex_Sports", "Sports", "Politics", "Crypto"}
    assert "brier_point" in report.columns
    assert "brier_ci_low" in report.columns
    assert "bss_n_valid" in report.columns
