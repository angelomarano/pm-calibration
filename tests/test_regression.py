import numpy as np
import polars as pl
import pytest

from src.calibration.regression import (
    CLUSTER_FLOOR,
    benjamini_hochberg,
    build_horizon_stratified_report,
    build_regression_report,
    calibration_stat_fn,
    fit_calibration_regression,
    horizon_tercile,
    per_bin_binomial_tests,
    spiegelhalter_z,
)
from src.inference.bootstrap import event_bootstrap


def test_irls_matches_statsmodels_golden_reference():
    """statsmodels.GLM used as a one-time correctness reference during
    development (n=5000, seed=0, true alpha=0.1/beta=1.2) -- not a
    runtime dependency. Golden values: alpha=0.06454206, beta=1.18862949."""
    rng = np.random.default_rng(0)
    n = 5000
    true_p = rng.uniform(0.01, 0.99, n)
    logit_p = np.log(true_p / (1 - true_p))
    eta = 0.1 + 1.2 * logit_p
    pi = 1 / (1 + np.exp(-eta))
    y = rng.binomial(1, pi).astype(float)

    fit = fit_calibration_regression(true_p, y)
    assert fit["converged"] == 1.0
    assert fit["alpha"] == pytest.approx(0.06454206, abs=1e-6)
    assert fit["beta"] == pytest.approx(1.18862949, abs=1e-6)


def test_irls_recovers_known_model_not_just_matches_a_reference():
    """Sanity beyond the golden-value comparison: fit a different known
    (alpha, beta) and confirm recovery within a reasonable tolerance."""
    rng = np.random.default_rng(7)
    n = 8000
    p = rng.uniform(0.02, 0.98, n)
    x = np.log(p / (1 - p))
    alpha_true, beta_true = -0.3, 0.85
    pi = 1 / (1 + np.exp(-(alpha_true + beta_true * x)))
    y = rng.binomial(1, pi).astype(float)

    fit = fit_calibration_regression(p, y)
    assert fit["converged"] == 1.0
    assert fit["alpha"] == pytest.approx(alpha_true, abs=0.05)
    assert fit["beta"] == pytest.approx(beta_true, abs=0.05)


def test_quasi_separated_data_flagged_non_converged_not_spurious_large_beta():
    """Engineered quasi-separated small sample (all high-p rows y=1, all
    low-p rows y=0) -- must be flagged non-converged, not return a large,
    spuriously "clean" beta."""
    p = np.array([0.1, 0.1, 0.1, 0.9, 0.9, 0.9])
    y = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
    fit = fit_calibration_regression(p, y)
    assert fit["converged"] == 0.0


def test_calibration_stat_fn_returns_nan_when_not_converged():
    p = np.array([0.1, 0.1, 0.1, 0.9, 0.9, 0.9])
    y = np.array([0, 0, 0, 1, 1, 1])
    df = pl.DataFrame({"p": p, "y": y})
    result = calibration_stat_fn(df)
    assert np.isnan(result["alpha"])
    assert np.isnan(result["beta"])


def test_spiegelhalter_z_hand_computed_example():
    p = np.array([0.2, 0.2, 0.8, 0.8])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    assert spiegelhalter_z(p, y) == pytest.approx(-1.0, abs=1e-9)


def test_spiegelhalter_z_zero_under_perfect_calibration():
    p = np.array([0.3] * 10)
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=float)  # exactly 3/10 == p
    assert spiegelhalter_z(p, y) == pytest.approx(0.0, abs=1e-9)


def test_benjamini_hochberg_worked_example():
    p_values = [0.005, 0.01, 0.03, 0.04, 0.2]
    assert benjamini_hochberg(p_values, alpha=0.05) == [True, True, True, True, False]


def test_benjamini_hochberg_preserves_input_order():
    p_values = [0.2, 0.005]  # deliberately out of sorted order
    result = benjamini_hochberg(p_values, alpha=0.05)
    assert result == [False, True]  # 0.005 (index 1) rejects, 0.2 (index 0) doesn't


def test_per_bin_binomial_tests_matches_scipy_directly():
    from scipy import stats as scipy_stats

    df = pl.DataFrame({"p": [0.15] * 20, "y": [1] * 8 + [0] * 12})  # bin 1: n=20, successes=8, mean_p=0.15
    result = per_bin_binomial_tests(df)
    bin1 = result.filter(pl.col("bin") == 1).row(0, named=True)
    expected = scipy_stats.binomtest(8, 20, 0.15).pvalue
    assert bin1["p_value"] == pytest.approx(expected)
    assert bin1["n"] == 20
    assert bin1["successes"] == 8


def test_horizon_tercile_computed_independently_per_category():
    df = pl.DataFrame(
        {
            "category": ["A"] * 9 + ["B"] * 9,
            "days_to_sched_end": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
            + [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0, 7000.0, 8000.0, 9000.0],
        }
    )
    out = horizon_tercile(df)
    a_terciles = out.filter(pl.col("category") == "A").sort("days_to_sched_end")["horizon_tercile"].to_list()
    b_terciles = out.filter(pl.col("category") == "B").sort("days_to_sched_end")["horizon_tercile"].to_list()
    # both categories have the same 9-evenly-spaced-values shape -> same tercile pattern, independently
    assert a_terciles == b_terciles
    assert set(a_terciles) == {1, 2, 3}


def _synthetic_calibration_df(n_per_cat=300, categories=("Politics", "Sports", "Crypto"), seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for cat in categories:
        for i in range(n_per_cat):
            rows.append(
                {
                    "category": cat,
                    "event_id": f"{cat}_e{i}",
                    "market_id": f"{cat}_m{i}",
                    "p": float(rng.uniform(0.02, 0.98)),
                    "y": int(rng.integers(0, 2)),
                    "days_to_sched_end": float(rng.uniform(1, 300)),
                }
            )
    return pl.DataFrame(rows)


def test_build_regression_report_role_column_exactly_one_primary():
    df = _synthetic_calibration_df()
    report = build_regression_report(df, B=50, seed=0)
    primary_rows = report.filter(pl.col("role") == "PRIMARY")
    assert primary_rows.height == 1
    assert primary_rows["cell"].to_list() == ["ex_Sports"]
    assert (report.filter(pl.col("cell") != "ex_Sports")["role"] == "SECONDARY").all()


def test_build_regression_report_no_duplicate_cells():
    """Regression test: Sports previously appeared twice (once from the
    explicit pre-add, once again from the per-category loop that didn't
    exclude it) -- caught on the real data run, not by this test suite,
    since the role-only checks above still passed with the duplicate."""
    df = _synthetic_calibration_df(categories=("Politics", "Sports", "Crypto"))
    report = build_regression_report(df, B=50, seed=0)
    cells = report["cell"].to_list()
    assert len(cells) == len(set(cells))
    assert cells.count("Sports") == 1


def test_build_horizon_stratified_report_pools_thin_category(monkeypatch):
    """A category engineered to have very few distinct clusters (many
    rows, few events) must get one pooled row, not three thin sub-cells."""
    rng = np.random.default_rng(1)
    rows = []
    # "Thin" category: only 5 distinct events, each repeated ~60 times -> way under CLUSTER_FLOOR per tercile
    for e in range(5):
        for i in range(60):
            rows.append(
                {
                    "category": "Thin",
                    "event_id": f"e{e}",
                    "market_id": f"e{e}_m{i}",
                    "p": float(rng.uniform(0.02, 0.98)),
                    "y": int(rng.integers(0, 2)),
                    "days_to_sched_end": float(rng.uniform(1, 300)),
                }
            )
    # "Healthy" category: 900 distinct events, comfortably above the floor per tercile
    for e in range(900):
        rows.append(
            {
                "category": "Healthy",
                "event_id": f"h{e}",
                "market_id": f"h{e}_m0",
                "p": float(rng.uniform(0.02, 0.98)),
                "y": int(rng.integers(0, 2)),
                "days_to_sched_end": float(rng.uniform(1, 300)),
            }
        )
    df = pl.DataFrame(rows)

    report = build_horizon_stratified_report(df, B=50, seed=0)

    thin_rows = report.filter(pl.col("cell") == "Thin")
    assert thin_rows.height == 1
    assert thin_rows["role"].to_list() == ["SECONDARY_POOLED"]
    assert thin_rows["horizon_tercile"][0] is None
    assert "< 200" in thin_rows["note"][0]

    healthy_rows = report.filter(pl.col("cell") == "Healthy")
    assert healthy_rows.height == 3
    assert set(healthy_rows["role"].to_list()) == {"SECONDARY"}
    assert healthy_rows["note"].null_count() == 3


def test_fit_calibration_regression_weights_none_matches_omitted():
    """weights=None (explicit) must be byte-identical to omitting the
    argument entirely -- the default preserves current behavior exactly,
    per W3b's spec requirement."""
    rng = np.random.default_rng(3)
    n = 2000
    p = rng.uniform(0.02, 0.98, n)
    y = rng.binomial(1, p).astype(float)
    fit_omitted = fit_calibration_regression(p, y)
    fit_explicit_none = fit_calibration_regression(p, y, weights=None)
    assert fit_omitted == fit_explicit_none


def test_fit_calibration_regression_uniform_weights_matches_unweighted():
    """Uniform case weights (all 1.0) must give the same fit as no
    weights at all -- a uniform weighting is a no-op."""
    rng = np.random.default_rng(4)
    n = 2000
    p = rng.uniform(0.02, 0.98, n)
    y = rng.binomial(1, p).astype(float)
    fit_unweighted = fit_calibration_regression(p, y)
    fit_uniform = fit_calibration_regression(p, y, weights=np.ones(n))
    assert fit_uniform["alpha"] == pytest.approx(fit_unweighted["alpha"], abs=1e-8)
    assert fit_uniform["beta"] == pytest.approx(fit_unweighted["beta"], abs=1e-8)


def test_fit_calibration_regression_weight_scale_invariance():
    """WLS normal equations are invariant to a uniform rescaling of the
    weight vector ((X'WX)b = X'Wz scales both sides by the same constant)
    -- scaling all weights by a constant must not move beta. Guards
    against a normalization bug creeping into the IRLS loop itself."""
    rng = np.random.default_rng(5)
    n = 2000
    p = rng.uniform(0.02, 0.98, n)
    y = rng.binomial(1, p).astype(float)
    w = rng.uniform(0.5, 3.0, n)
    fit_w = fit_calibration_regression(p, y, weights=w)
    fit_5w = fit_calibration_regression(p, y, weights=5.0 * w)
    assert fit_5w["alpha"] == pytest.approx(fit_w["alpha"], abs=1e-8)
    assert fit_5w["beta"] == pytest.approx(fit_w["beta"], abs=1e-8)


def test_fit_calibration_regression_nonuniform_weights_change_fit():
    """Sanity that weighting actually does something: concentrating
    weight on a subset whose true relationship differs from the rest
    must pull beta toward that subset's pattern, not leave it unchanged."""
    rng = np.random.default_rng(6)
    n = 4000
    p = rng.uniform(0.02, 0.98, n)
    x = np.log(p / (1 - p))
    # first half: beta=1.0 (well-calibrated); second half: beta=2.0 (steeper)
    half = n // 2
    pi = np.empty(n)
    pi[:half] = 1 / (1 + np.exp(-(0.0 + 1.0 * x[:half])))
    pi[half:] = 1 / (1 + np.exp(-(0.0 + 2.0 * x[half:])))
    y = rng.binomial(1, pi).astype(float)

    w_first_half = np.concatenate([np.full(half, 10.0), np.full(n - half, 0.1)])
    w_second_half = np.concatenate([np.full(half, 0.1), np.full(n - half, 10.0)])
    fit_first = fit_calibration_regression(p, y, weights=w_first_half)
    fit_second = fit_calibration_regression(p, y, weights=w_second_half)
    assert fit_second["beta"] > fit_first["beta"] + 0.3


def test_calibration_stat_fn_through_event_bootstrap():
    df = _synthetic_calibration_df(n_per_cat=100, categories=("Politics",))
    result = event_bootstrap(df, calibration_stat_fn, B=100, seed=0)
    assert "alpha" in result.point
    assert "beta" in result.point
    assert result.n_valid["alpha"] > 0
