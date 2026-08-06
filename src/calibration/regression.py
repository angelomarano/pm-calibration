"""Calibration regression (alpha, beta), Spiegelhalter's Z, per-bin
BH-corrected binomial tests (W2d).

Primary estimand, exactly as pre-registered: logit(P(y=1)) = alpha +
beta*logit(p), fit per category and pooled ex-Sports. H0: (alpha, beta) =
(0, 1). CIs come from event_bootstrap (W2a) refitting this regression once
per draw, via calibration_stat_fn.

Fit via a hand-rolled IRLS, not statsmodels: benchmarked both on synthetic
data (n=5000) -- statsmodels.GLM 2.85ms/fit (~5.7s/cell at B=2000) vs.
hand-rolled IRLS 0.843ms/fit (~1.7s/cell), both converging to identical
fitted values. statsmodels is used only as a one-time correctness
reference during development (not a runtime dependency) -- the golden
values it produced are hardcoded into tests/test_regression.py.

Quasi-separation safeguard: IRLS with clipped pi can in principle "settle"
under the tol check while pi has already plateaued at the clip boundary
-- a clipping artifact, not a real fit. fit_calibration_regression checks
this explicitly (|beta| > BETA_SANITY_BOUND, or all fitted pi within
PLATEAU_EPS of 0/1) and reports converged=0.0 regardless of what the
tol-based iteration loop concluded.

Spiegelhalter's Z (1986): Z = sum((y-p)(1-2p)) / sqrt(sum((1-2p)^2 p(1-p))),
verified against multiple independent published sources -- an earlier
recollection of this formula (omitting the (1-2p) weight in the
numerator) was wrong and was corrected before implementation, not after.

Benjamini-Hochberg is hand-rolled (~10 lines); scipy.stats.binomtest is
used for the per-bin exact test (unlike the IRLS fit, an "exact" test's
correctness at scale depends on solid combinatorial/CDF computation that
isn't worth hand-rolling -- scipy is pinned as a real dependency here,
not just a validation reference).

horizon_tercile: days_to_sched_end (ex-ante, known at snapshot time --
never days_to_resolution, which is ex-post and reserved for W3) split
into terciles WITHIN each category (same .over("category") pattern as
M3's vol_tercile). Row count is NOT a reliable proxy for whether a
category's horizon split has enough independent clusters to bootstrap
reliably -- confirmed empirically (2026-07-22 DECISIONS.md entry): Other,
Econ/Finance, and Culture all fail a 200-cluster floor in their thinnest
tercile despite comfortable row counts, and Sports barely clears it (204)
despite being the largest category by rows. build_horizon_stratified_report
checks n_clusters_per_cell (src/inference/bootstrap.py) per category and
automatically falls back to a single pooled row (role="SECONDARY_POOLED")
for any category whose thinnest tercile falls below the floor, rather
than reporting three unreliable sub-cells.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from scipy import stats

from src.calibration.reliability import DECILE_EDGES, assign_bin_index
from src.inference.bootstrap import event_bootstrap, n_clusters_per_cell

BETA_SANITY_BOUND = 15.0
PI_FIT_EPS = 1e-10  # clipping bound used during IRLS iteration
PLATEAU_EPS = 1e-6  # looser bound for detecting a saturated/plateaued fit after convergence
CLUSTER_FLOOR = 200


def _logit(p: np.ndarray) -> np.ndarray:
    return np.log(p / (1 - p))


def fit_calibration_regression(p: np.ndarray, y: np.ndarray, max_iter: int = 25, tol: float = 1e-8) -> dict[str, float]:
    """Hand-rolled IRLS fit of logit(P(y=1)) = alpha + beta*logit(p).
    Returns {alpha, beta, converged, n_iter}. alpha/beta are the as-fitted
    values regardless of converged (callers needing the NaN-signaling
    contract for event_bootstrap should use calibration_stat_fn, not this
    function directly)."""
    x = _logit(p)
    X = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    converged = False
    n_iter_used = 0

    for i in range(max_iter):
        n_iter_used = i + 1
        eta = X @ beta
        pi = 1 / (1 + np.exp(-eta))
        pi = np.clip(pi, PI_FIT_EPS, 1 - PI_FIT_EPS)
        w = pi * (1 - pi)
        z = eta + (y - pi) / w
        WX = X * w[:, None]
        try:
            beta_new = np.linalg.solve(X.T @ WX, X.T @ (w * z))
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            converged = True
            break
        beta = beta_new

    alpha_fit, beta_fit = float(beta[0]), float(beta[1])

    # Quasi-separation safeguard, checked regardless of the tol-based flag above.
    eta_final = X @ beta
    pi_final = 1 / (1 + np.exp(-eta_final))
    plateau = bool(np.all((pi_final <= PLATEAU_EPS) | (pi_final >= 1 - PLATEAU_EPS)))
    if abs(beta_fit) > BETA_SANITY_BOUND or plateau:
        converged = False

    return {
        "alpha": alpha_fit,
        "beta": beta_fit,
        "converged": 1.0 if converged else 0.0,
        "n_iter": float(n_iter_used),
    }


def calibration_stat_fn(df: pl.DataFrame) -> dict[str, float]:
    """stat_fn for event_bootstrap: fits the regression, returns
    {alpha, beta} (both nan if the fit didn't converge, per
    event_bootstrap's NaN policy)."""
    p = df["p"].to_numpy()
    y = df["y"].to_numpy().astype(float)
    fit = fit_calibration_regression(p, y)
    if fit["converged"] == 0.0:
        return {"alpha": float("nan"), "beta": float("nan")}
    return {"alpha": fit["alpha"], "beta": fit["beta"]}


def spiegelhalter_z(p: np.ndarray, y: np.ndarray) -> float:
    """Z = sum((y-p)(1-2p)) / sqrt(sum((1-2p)^2 p(1-p))). See module
    docstring re: verification against published sources."""
    weight = 1 - 2 * p
    numerator = np.sum((y - p) * weight)
    denominator = np.sqrt(np.sum(weight**2 * p * (1 - p)))
    return float(numerator / denominator)


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Hand-rolled BH step-up procedure. Returns per-hypothesis
    reject/not-reject, aligned to the input order (not sorted order)."""
    m = len(p_values)
    order = np.argsort(p_values)
    sorted_p = np.array(p_values)[order]
    thresholds = (np.arange(1, m + 1) / m) * alpha
    below = sorted_p <= thresholds

    reject_sorted = np.zeros(m, dtype=bool)
    if below.any():
        max_k = int(np.max(np.where(below)[0]))
        reject_sorted[: max_k + 1] = True

    reject = np.zeros(m, dtype=bool)
    reject[order] = reject_sorted
    return reject.tolist()


def per_bin_binomial_tests(df: pl.DataFrame, edges: list[float] = DECILE_EDGES, alpha: float = 0.05) -> pl.DataFrame:
    """Per bin: n, successes, mean_p, exact binomial p-value (H0: true
    rate == bin's mean_p, via scipy.stats.binomtest), BH-corrected across
    the non-empty bins."""
    p = df["p"].to_numpy()
    y = df["y"].to_numpy().astype(float)
    bin_idx = assign_bin_index(p, edges)
    n_bins = len(edges) - 1

    rows = []
    for b in range(n_bins):
        mask = bin_idx == b
        n = int(mask.sum())
        if n == 0:
            rows.append({"bin": b, "n": 0, "mean_p": None, "successes": None, "p_value": None})
            continue
        successes = int(y[mask].sum())
        mean_p = float(p[mask].mean())
        p_value = float(stats.binomtest(successes, n, mean_p).pvalue)
        rows.append({"bin": b, "n": n, "mean_p": mean_p, "successes": successes, "p_value": p_value})

    valid_idx = [i for i, r in enumerate(rows) if r["p_value"] is not None]
    bh_reject: list[bool | None] = [None] * len(rows)
    if valid_idx:
        pvals = [rows[i]["p_value"] for i in valid_idx]
        rejects = benjamini_hochberg(pvals, alpha)
        for i, rej in zip(valid_idx, rejects):
            bh_reject[i] = rej

    for row, rej in zip(rows, bh_reject):
        row["bh_reject"] = rej

    return pl.DataFrame(rows)


def tercile_within_category(df: pl.DataFrame, value_col: str, out_col: str = "tercile") -> pl.DataFrame:
    """Adds out_col (1/2/3) via qcut(3) on value_col, computed
    independently WITHIN each category (.over("category")). Generalizes
    the mechanism originally built for horizon_tercile so W3's clocks.py
    can reuse it on days_to_resolution too, not just days_to_sched_end."""
    return df.with_columns(
        pl.col(value_col)
        .qcut(3, labels=["1", "2", "3"], allow_duplicates=True)
        .over("category")
        .cast(pl.Utf8)
        .cast(pl.Int8)
        .alias(out_col)
    )


def horizon_tercile(df: pl.DataFrame) -> pl.DataFrame:
    """Adds horizon_tercile (1/2/3) via qcut on days_to_sched_end (ex-ante
    horizon; never days_to_resolution), computed WITHIN each category.
    Thin wrapper around tercile_within_category -- unchanged behavior,
    see test_regression.py's byte-identical regression check."""
    return tercile_within_category(df, "days_to_sched_end", out_col="horizon_tercile")


def build_regression_report(df: pl.DataFrame, B: int = 2000, seed: int = 0) -> pl.DataFrame:
    """One row per cell (pooled ex-Sports + each category): n, alpha/beta
    point+CI (event_bootstrap), spiegelhalter_z, and a `role` column
    marking the pooled ex-Sports row PRIMARY, every other row SECONDARY
    -- the multiplicity framing lives in the output itself."""
    cells = [("ex_Sports", df.filter(pl.col("category") != "Sports"), "PRIMARY")]
    cells.append(("Sports", df.filter(pl.col("category") == "Sports"), "SECONDARY"))
    for cat in sorted(df["category"].unique().to_list()):
        if cat == "Sports":
            continue  # already added explicitly above, right after ex_Sports
        cells.append((cat, df.filter(pl.col("category") == cat), "SECONDARY"))

    rows = []
    for name, sub, role in cells:
        if sub.height == 0:
            continue
        result = event_bootstrap(sub, calibration_stat_fn, B=B, seed=seed)
        z = spiegelhalter_z(sub["p"].to_numpy(), sub["y"].to_numpy().astype(float))
        rows.append(
            {
                "cell": name,
                "role": role,
                "n": sub.height,
                "alpha_point": result.point["alpha"],
                "alpha_ci_low": result.ci_low["alpha"],
                "alpha_ci_high": result.ci_high["alpha"],
                "beta_point": result.point["beta"],
                "beta_ci_low": result.ci_low["beta"],
                "beta_ci_high": result.ci_high["beta"],
                "spiegelhalter_z": z,
                "n_valid": result.n_valid["alpha"],
            }
        )
    return pl.DataFrame(rows)


def _horizon_report_row(cell, tercile, role, n, n_clusters, note, result) -> dict:
    return {
        "cell": cell,
        "horizon_tercile": tercile,
        "role": role,
        "n": n,
        "n_clusters": n_clusters,
        "note": note,
        "alpha_point": result.point["alpha"],
        "alpha_ci_low": result.ci_low["alpha"],
        "alpha_ci_high": result.ci_high["alpha"],
        "beta_point": result.point["beta"],
        "beta_ci_low": result.ci_low["beta"],
        "beta_ci_high": result.ci_high["beta"],
    }


def build_horizon_stratified_report(
    df: pl.DataFrame, B: int = 2000, seed: int = 0, cluster_floor: int = CLUSTER_FLOOR
) -> pl.DataFrame:
    """Secondary/robustness cut: category x horizon_tercile refit of
    alpha/beta. n_clusters is reported in every row (pooled or split) so
    the reader never has to trust an invisible threshold decision. Any
    category whose thinnest horizon-tercile cluster count falls below
    cluster_floor gets ONE pooled row (role="SECONDARY_POOLED") instead
    of three unreliable sub-cells -- decided automatically per category,
    not hardcoded to any specific one (see module docstring)."""
    df_h = horizon_tercile(df)
    tercile_counts = n_clusters_per_cell(df_h, group_cols=["category", "horizon_tercile"])
    total_counts = n_clusters_per_cell(df_h, group_cols=["category"])

    rows = []
    for cat in sorted(df["category"].unique().to_list()):
        sub_cat = df_h.filter(pl.col("category") == cat)
        cat_tercile_counts = tercile_counts.filter(pl.col("category") == cat)
        min_clusters = int(cat_tercile_counts["n_clusters"].min())
        total_clusters = int(total_counts.filter(pl.col("category") == cat)["n_clusters"][0])

        if min_clusters < cluster_floor:
            result = event_bootstrap(sub_cat, calibration_stat_fn, B=B, seed=seed)
            note = f"pooled -- thinnest horizon-tercile n_clusters={min_clusters} < {cluster_floor}"
            rows.append(_horizon_report_row(cat, None, "SECONDARY_POOLED", sub_cat.height, total_clusters, note, result))
        else:
            for tercile in (1, 2, 3):
                sub_t = sub_cat.filter(pl.col("horizon_tercile") == tercile)
                if sub_t.height == 0:
                    continue
                n_clust_t = int(cat_tercile_counts.filter(pl.col("horizon_tercile") == tercile)["n_clusters"][0])
                result = event_bootstrap(sub_t, calibration_stat_fn, B=B, seed=seed)
                rows.append(_horizon_report_row(cat, tercile, "SECONDARY", sub_t.height, n_clust_t, None, result))

    return pl.DataFrame(rows)
