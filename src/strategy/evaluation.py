"""W4c evaluation utilities: censoring, chronological PnL, drawdown,
annualized return, break-even half-spread.

Built and tested entirely on synthetic data BEFORE the OOS unlock commit
(per the 2026-08-08 DECISIONS.md ordering) -- this module's logic is
frozen in git history before any OOS row is readable, the same
evidentiary argument as the pre-registered spec, applied to the
evaluation code itself. A skeptical reader can check the commit hash
predates the unlock commit.

Capital convention (annualized_return): capital_deployed is the sum of
notional across all trades, WITHOUT netting for time-overlap -- a
position open at the same time as another does not share capital in
this model. This OVERSTATES capital actually deployed whenever positions
overlap in time, which UNDERSTATES the resulting annualized return.
Treat this function's output as a conservative LOWER BOUND, not a point
estimate, and say so in any report that calls it. No concurrent-capital
model is built (a stated, deliberate scope decision, not an oversight).

censor_positions expects `resolution_ts` (absolute timestamp) already
joined onto df -- p1.parquet only carries days_to_resolution (a relative
distance), not the absolute timestamp censoring needs, so the caller
joins it in from markets.parquet, same pattern rules.py already uses for
created_at.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import polars as pl


def _share_dict(df: pl.DataFrame, col: str) -> dict:
    total = df.height
    if total == 0:
        return {}
    counts = df.group_by(col).len()
    return {row[col]: row["len"] / total for row in counts.iter_rows(named=True)}


def _profile(sub: pl.DataFrame) -> dict:
    if sub.height == 0:
        return {"n": 0, "category_mix": {}, "vol_tercile_mix": {}, "mean_days_to_resolution": float("nan")}
    return {
        "n": sub.height,
        "category_mix": _share_dict(sub, "category"),
        "vol_tercile_mix": _share_dict(sub, "vol_tercile"),
        "mean_days_to_resolution": float(sub["days_to_resolution"].mean()),
    }


def censor_positions(df: pl.DataFrame, cutoff: datetime) -> tuple[pl.DataFrame, dict]:
    """Keeps positions whose market resolution_ts is on or before
    cutoff (the data collection cutoff -- 2026-07-12 for this project,
    confirmed empirically against markets.parquet's max created_at/
    resolution_ts/closed_time, not assumed). Everything else is
    right-censored and excluded, per docs/W4_SPEC_ADDENDUM.md §1.3.
    Returns (kept, stats): stats reports the excluded share overall and
    per category, plus the kept-vs-excluded PROFILE (category mix,
    vol_tercile mix, mean days-to-resolution) -- the same "a restriction
    that looks like fewer rows might be a different population" check
    W3a's ran-to-term restriction required."""
    total = df.height
    is_excluded = pl.col("resolution_ts") > cutoff
    kept = df.filter(~is_excluded)
    excluded = df.filter(is_excluded)

    by_cat = (
        df.with_columns(is_excluded.alias("_excluded"))
        .group_by("category")
        .agg(pl.col("_excluded").mean().alias("share_excluded"))
        .sort("category")
    )
    excluded_share_by_category = {r["category"]: r["share_excluded"] for r in by_cat.iter_rows(named=True)}

    stats = {
        "n_total": total,
        "n_kept": kept.height,
        "n_excluded": excluded.height,
        "share_excluded": (excluded.height / total) if total else float("nan"),
        "excluded_share_by_category": excluded_share_by_category,
        "kept_profile": _profile(kept),
        "excluded_profile": _profile(excluded),
    }
    return kept, stats


def chronological_pnl(df: pl.DataFrame, pnl_col: str, date_col: str = "snapshot_date") -> pl.DataFrame:
    """Cumulative sum of pnl_col ordered by date_col ascending -- NOT
    input row order. Returns [date_col, pnl_col, cumulative_pnl]."""
    ordered = df.sort(date_col)
    return ordered.select([date_col, pnl_col]).with_columns(
        pl.col(pnl_col).cum_sum().alias("cumulative_pnl")
    )


def max_drawdown(cumulative: np.ndarray) -> float:
    """Standard running-peak-to-trough max decline. Non-negative;
    0.0 if the series never declines (or is empty)."""
    cumulative = np.asarray(cumulative, dtype=float)
    if cumulative.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(cumulative)
    drawdown = running_peak - cumulative
    return float(np.max(drawdown))


def annualized_return(total_net_pnl: float, capital_deployed: float, period_days: float) -> float:
    """(total_net_pnl / capital_deployed) * (365 / period_days). See
    module docstring's capital convention note -- this is a conservative
    LOWER BOUND on the true annualized return, since capital_deployed
    does not net time-overlapping positions against each other."""
    if capital_deployed == 0 or period_days == 0:
        return float("nan")
    return (total_net_pnl / capital_deployed) * (365.0 / period_days)


def break_even_multiplier(edge_net_of_fee_and_carry: float, mean_half_spread: float) -> float:
    """The spread-band multiplier m at which mean net edge hits exactly
    zero: m* = edge_net_of_fee_and_carry / mean_half_spread. The
    single most useful number in the report (per spec) -- report
    alongside the absolute half-spread it implies (m* times the
    observed 1x median half-spread), converting an assumption into a
    testable threshold a reader can check against their own execution."""
    if mean_half_spread == 0:
        return float("inf") if edge_net_of_fee_and_carry > 0 else float("-inf")
    return edge_net_of_fee_and_carry / mean_half_spread
