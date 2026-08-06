"""W3a — the clock comparison (centerpiece of W3's reconciliation grid).

Four clocks, not three -- "Clock C" alone would confound the stopping-
time mechanism with the fact that the ran-to-term restriction keeps a
different, category-varying share of rows (22%-78%, see DECISIONS.md).
The control is B_term: Clock B's estimator run on the SAME ran-to-term
subset A_term (née "Clock C") uses.

    A_full : days_to_sched_end terciles, all rows
    B_full : days_to_resolution terciles, all rows
    A_term : days_to_sched_end terciles, ran-to-term rows only
    B_term : days_to_resolution terciles, ran-to-term rows only

Prediction: on the ran-to-term subset, days_to_sched_end and
days_to_resolution are nearly equal on every row by construction (a
market that resolved within ~2 days of its scheduled end has
end_date_sched ~= resolution_ts, so days_to_sched_end ~= days_to_resolution
for every snapshot of that market, not just at one point in time). If the
A_full/B_full gap is driven by early-resolver selection, A_term and B_term
should nearly coincide per (category, tercile). If they don't, the
stopping-time story doesn't explain the gap and needs another explanation.
That's compare_clocks's job.

days_early = days_to_sched_end - days_to_resolution. Both terms share the
same snapshot_date (M3's snapshots.py), which cancels out algebraically to
end_date_sched - resolution_ts -- a market-level quantity, no re-ingestion
needed. Verified on real data to be invariant across a market's snapshot
rows to within ~1e-5 days (float noise from the day-count arithmetic, not
a logic bug -- confirmed by checking the actual spread, not just assuming
exact equality would hold).

W3's LOW_POWER policy (per docs/W3_SPEC_ADDENDUM.md §1) is deliberately
the OPPOSITE of W2d's pooling fallback: every (category, clock, tercile)
cell is computed regardless of n_clusters, flagged low_power=True when
thin, never pooled away or dropped.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from src.calibration.regression import calibration_stat_fn, tercile_within_category
from src.inference.bootstrap import event_bootstrap, n_clusters_per_cell

RAN_TO_TERM_TOLERANCE_DAYS = 2.0  # matches Gate B's early/late convention
CLUSTER_FLOOR = 200

CLOCKS = ("A_full", "B_full", "A_term", "B_term")
CLOCK_SPECS: dict[str, tuple[str, bool]] = {
    "A_full": ("days_to_sched_end", False),
    "B_full": ("days_to_resolution", False),
    "A_term": ("days_to_sched_end", True),
    "B_term": ("days_to_resolution", True),
}


def add_days_early(df: pl.DataFrame) -> pl.DataFrame:
    """days_early = days_to_sched_end - days_to_resolution."""
    return df.with_columns((pl.col("days_to_sched_end") - pl.col("days_to_resolution")).alias("days_early"))


def ran_to_term_frame(
    df: pl.DataFrame, tolerance_days: float = RAN_TO_TERM_TOLERANCE_DAYS
) -> tuple[pl.DataFrame, dict]:
    """Restricts to rows with |days_early| <= tolerance_days (inclusive).
    `df` must already have a days_early column (see add_days_early).
    Returns (restricted_df, stats): per-category {n_total, n_kept,
    share_dropped}, plus an "ALL" aggregate key."""
    restricted = df.filter(pl.col("days_early").abs() <= tolerance_days)

    stats = {}
    for cat in sorted(df["category"].unique().to_list()):
        n_total = df.filter(pl.col("category") == cat).height
        n_kept = restricted.filter(pl.col("category") == cat).height
        stats[cat] = {
            "n_total": n_total,
            "n_kept": n_kept,
            "share_dropped": (n_total - n_kept) / n_total if n_total else float("nan"),
        }
    stats["ALL"] = {
        "n_total": df.height,
        "n_kept": restricted.height,
        "share_dropped": (df.height - restricted.height) / df.height if df.height else float("nan"),
    }
    return restricted, stats


def build_clock_comparison(
    df: pl.DataFrame,
    B: int = 2000,
    seed: int = 0,
    cluster_floor: int = CLUSTER_FLOOR,
    tolerance_days: float = RAN_TO_TERM_TOLERANCE_DAYS,
) -> tuple[pl.DataFrame, dict]:
    """One row per (category, clock, tercile), clock in CLOCKS. Every
    cell is computed regardless of n_clusters (low_power=True when
    below cluster_floor, never suppressed). Returns (table, stats) where
    stats["ran_to_term"] is ran_to_term_frame's drop-share report."""
    df_days_early = add_days_early(df)
    term_df, term_stats = ran_to_term_frame(df_days_early, tolerance_days)

    rows: list[dict] = []
    for clock in CLOCKS:
        value_col, restrict = CLOCK_SPECS[clock]
        base = term_df if restrict else df_days_early
        tercile_df = tercile_within_category(base, value_col, out_col="tercile")
        cluster_counts = n_clusters_per_cell(tercile_df, group_cols=["category", "tercile"])

        for cat in sorted(base["category"].unique().to_list()):
            for t in (1, 2, 3):
                sub = tercile_df.filter((pl.col("category") == cat) & (pl.col("tercile") == t))
                if sub.height == 0:
                    continue
                n_clust_row = cluster_counts.filter((pl.col("category") == cat) & (pl.col("tercile") == t))
                n_clusters_val = int(n_clust_row["n_clusters"][0]) if n_clust_row.height else 0
                result = event_bootstrap(sub, calibration_stat_fn, B=B, seed=seed)
                rows.append(
                    {
                        "category": cat,
                        "clock": clock,
                        "tercile": t,
                        "n": sub.height,
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

    table = pl.DataFrame(rows)
    return table, {"ran_to_term": term_stats}


def compare_clocks(table: pl.DataFrame, clock_x: str, clock_y: str) -> pl.DataFrame:
    """Joins two clocks' per-(category, tercile) beta results side by
    side: beta_x, beta_y, beta_diff, cis_overlap (whether the two CIs
    intersect at all). Used for the A_term vs B_term prediction check:
    if the stopping-time-selection story is right, they should nearly
    coincide per (category, tercile) since the two clocks' underlying
    values are nearly equal on the ran-to-term subset by construction."""
    cols = ["category", "tercile", "beta_point", "beta_ci_low", "beta_ci_high"]
    x = table.filter(pl.col("clock") == clock_x).select(cols)
    y = table.filter(pl.col("clock") == clock_y).select(cols)
    joined = x.join(y, on=["category", "tercile"], suffix="_y")
    return joined.with_columns(
        (pl.col("beta_point") - pl.col("beta_point_y")).alias("beta_diff"),
        (
            (pl.col("beta_ci_low") <= pl.col("beta_ci_high_y")) & (pl.col("beta_ci_low_y") <= pl.col("beta_ci_high"))
        ).alias("cis_overlap"),
    )


def classify_tercile_sequence(betas: list[float], cis: list[tuple[float, float]]) -> str:
    """Gate D's verdict logic, extracted and tested here: "rising with
    non-overlapping CIs" / "directionally rising but overlapping" /
    "not monotonically rising". gate_d.py's own already-shipped inline
    version is left as-is (not retrofitted)."""
    rising = all(b2 >= b1 for b1, b2 in zip(betas, betas[1:]))
    non_overlapping_rising = all(cis[i + 1][0] > cis[i][1] for i in range(len(cis) - 1))
    if non_overlapping_rising:
        return "rising with non-overlapping CIs"
    if rising:
        return "directionally rising but overlapping"
    return "not monotonically rising"
