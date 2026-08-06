"""W3b — the reconciliation grid: Weighting x Sample x Period, 16 cells.

No Clock axis. The original spec had one (ex-ante A / ex-post B /
ran-to-term C), but it turned out to be a no-op: fit_calibration_regression
only ever reads p and y, never a horizon column -- the clock variable only
does anything when it stratifies rows into terciles (W3a), and this grid
has no tercile axis (one beta per cell, not three). A vs B would have
produced two identical numbers per cell regardless of which was named.
W3a also found A and B overlapping in 21/21 cells on both the full and
ran-to-term samples, so there was no divergence to reconcile even before
that. The clock is fixed at ex-ante (non-anticipative, this project's
default throughout); ran-to-term moves into the Sample axis, where it
actually changes something -- which rows are in the cell.

Sample levels are independent slices of the FULL panel, not nested inside
each other: "all" is unrestricted (Sports included), "ex_Sports" excludes
Sports, "top_liquidity_tercile" and "ran_to_term" are their own
restrictions and may include Sports too. Reuses existing machinery only:
vol_tercile (build_panel.py, computed within-snapshot) for liquidity,
clocks.add_days_early/ran_to_term_frame for the ran-to-term restriction --
no new computation invented for either.

Period overlap caveat: splitting by snapshot_date year is not splitting
into independent samples. A market open across the 2024/2025 boundary
contributes rows -- same market, same eventual outcome -- to both period
cells. period_overlap_stats reports, per sample level, how many clusters
(coalesce(event_id, market_id), the same resampling unit event_bootstrap
uses) appear in both period cells, so a 2024-vs-2025 comparison carries
that caveat with the number rather than leaving it to a docstring.

Weighted IRLS: weights = volume_num normalized to mean 1 within the cell,
added once before bootstrapping (not recomputed per resampled draw --
WLS is invariant to a uniform rescaling of weights, see
fit_calibration_regression's docstring, so this only affects how the
weight column reads, never beta).

Gate E's reconciliation check against W2d's headline (pooled ex-Sports,
equal weight, full period -> beta=1.165 exactly) is NOT a cell in this
grid -- there is no "full period" level here, and adding a third period
level just to cover one check would triple that axis's cost. Gate E does
that check as a standalone call to build_regression_report-equivalent
logic on the ex-Sports population, not by reading a grid row.
"""

from __future__ import annotations

import polars as pl

from src.calibration.clocks import add_days_early, ran_to_term_frame
from src.calibration.regression import calibration_stat_fn, fit_calibration_regression
from src.inference.bootstrap import event_bootstrap

CLUSTER_FLOOR = 200
WEIGHTING_LEVELS = ("equal", "volume_weighted")
SAMPLE_LEVELS = ("all", "ex_Sports", "top_liquidity_tercile", "ran_to_term")
PERIOD_LEVELS = ("2024", "2025")


def apply_sample_filter(df: pl.DataFrame, sample: str) -> pl.DataFrame:
    """Dispatches on a SAMPLE_LEVELS name. Each level filters the full
    panel independently -- see module docstring re: non-nesting."""
    if sample == "all":
        return df
    if sample == "ex_Sports":
        return df.filter(pl.col("category") != "Sports")
    if sample == "top_liquidity_tercile":
        return df.filter(pl.col("vol_tercile") == 3)
    if sample == "ran_to_term":
        restricted, _ = ran_to_term_frame(add_days_early(df))
        return restricted
    raise ValueError(f"unknown sample level: {sample!r}")


def apply_period_filter(df: pl.DataFrame, period: str) -> pl.DataFrame:
    """Dispatches on a PERIOD_LEVELS name ("2024"/"2025"), filtering on
    snapshot_date's calendar year."""
    return df.filter(pl.col("snapshot_date").dt.year() == int(period))


def add_volume_weight(df: pl.DataFrame, volume_col: str = "volume_num") -> pl.DataFrame:
    """Adds a `weight` column = volume_col / mean(volume_col), computed
    on the passed-in df (i.e. call this AFTER filtering to the cell, so
    the normalization is "within the cell" per spec)."""
    mean_vol = df[volume_col].mean()
    return df.with_columns((pl.col(volume_col) / mean_vol).alias("weight"))


def weighted_calibration_stat_fn(df: pl.DataFrame) -> dict[str, float]:
    """Like calibration_stat_fn, but reads a pre-existing `weight` column
    (see add_volume_weight) and fits with case weights. Precondition:
    `weight` must already be a column of df -- this function does not
    compute it, so it survives event_bootstrap's cluster resampling
    attached to its row, same as any other column."""
    p = df["p"].to_numpy()
    y = df["y"].to_numpy().astype(float)
    w = df["weight"].to_numpy()
    fit = fit_calibration_regression(p, y, weights=w)
    if fit["converged"] == 0.0:
        return {"alpha": float("nan"), "beta": float("nan")}
    return {"alpha": fit["alpha"], "beta": fit["beta"]}


def build_reconciliation_grid(
    df: pl.DataFrame, B: int = 2000, seed: int = 0, cluster_floor: int = CLUSTER_FLOOR
) -> pl.DataFrame:
    """One row per (weighting, sample, period) -- 16 rows. Every cell is
    computed regardless of n_clusters (low_power=True when below
    cluster_floor, never suppressed, same policy as W3a)."""
    rows: list[dict] = []
    for sample in SAMPLE_LEVELS:
        sampled = apply_sample_filter(df, sample)
        for period in PERIOD_LEVELS:
            cell = apply_period_filter(sampled, period)
            if cell.height == 0:
                continue
            n_clusters_val = int(cell["event_id"].fill_null(cell["market_id"]).n_unique())
            for weighting in WEIGHTING_LEVELS:
                if weighting == "equal":
                    stat_fn = calibration_stat_fn
                    fit_input = cell
                else:
                    fit_input = add_volume_weight(cell)
                    stat_fn = weighted_calibration_stat_fn
                result = event_bootstrap(fit_input, stat_fn, B=B, seed=seed)
                rows.append(
                    {
                        "weighting": weighting,
                        "sample": sample,
                        "period": period,
                        "n": cell.height,
                        "n_clusters": n_clusters_val,
                        "low_power": n_clusters_val < cluster_floor,
                        "alpha_point": result.point["alpha"],
                        "alpha_ci_low": result.ci_low["alpha"],
                        "alpha_ci_high": result.ci_high["alpha"],
                        "beta_point": result.point["beta"],
                        "beta_ci_low": result.ci_low["beta"],
                        "beta_ci_high": result.ci_high["beta"],
                    }
                )
    return pl.DataFrame(rows)


def period_overlap_stats(df: pl.DataFrame) -> pl.DataFrame:
    """Per sample level: how many clusters (coalesce(event_id, market_id))
    appear in BOTH the 2024 and 2025 period cells. See module docstring --
    the period split is not a split into independent samples."""
    rows = []
    for sample in SAMPLE_LEVELS:
        sampled = apply_sample_filter(df, sample)

        def _cluster_keys(sub: pl.DataFrame) -> set:
            return set(sub["event_id"].fill_null(sub["market_id"]).to_list())

        keys_2024 = _cluster_keys(apply_period_filter(sampled, "2024"))
        keys_2025 = _cluster_keys(apply_period_filter(sampled, "2025"))
        overlap = keys_2024 & keys_2025
        rows.append(
            {
                "sample": sample,
                "n_clusters_2024": len(keys_2024),
                "n_clusters_2025": len(keys_2025),
                "n_overlap": len(overlap),
                "overlap_share_2024": (len(overlap) / len(keys_2024)) if keys_2024 else float("nan"),
                "overlap_share_2025": (len(overlap) / len(keys_2025)) if keys_2025 else float("nan"),
            }
        )
    return pl.DataFrame(rows)
