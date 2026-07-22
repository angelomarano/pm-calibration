"""Event-clustered bootstrap — the single inference engine every W2b-W2d
statistic gets its CI from. Built first, on purpose: retrofitting
inference after the statistics exist is how inconsistent CIs happen.

Clustering: rows sharing the same event_id are resampled together (an
event's markets share one realized outcome — see docs/W2_SPEC_ADDENDUM.md
§1). Orphan markets (event_id null) form their own singleton cluster keyed
by market_id, so a market with rows across multiple snapshots still moves
as one unit, just alone rather than with siblings.

stat_fn contract: stat_fn receives the resampled frame AS-IS — row-based,
exactly like the original panel — and must return a dict[str, float]. It
must NOT group by or deduplicate on event_id/market_id internally.
Resampled frames intentionally contain the same cluster multiple times
(that's what a cluster bootstrap draw is); a stat_fn that groups by
cluster id before computing its statistic would silently collapse those
replicas back to one row each and under-weight exactly the clusters the
draw meant to over-weight. None of the W2 stats (bin frequencies, Brier,
logistic fit) group by cluster, so this documented contract is enough for
now. If a future stat genuinely needs per-cluster grouping inside a draw,
extend the engine with a draw-unique replica-id column at that point —
don't work around it inside stat_fn.

NaN policy: the engine never catches exceptions from stat_fn — a raise
propagates and fails the whole call loudly. stat_fn MAY instead return
float('nan') for a specific key to signal a degenerate/failed fit on that
draw (e.g. a per-category logistic fit hitting an all-y-equal resampled
cell). CIs are computed over the non-nan draws only for that key, and
BootstrapResult.n_valid reports how many draws survived per key, so
degradation is visible rather than silently smoothed over. A key that is
nan on every single draw raises ValueError instead of returning a
meaningless empty-interval CI.

Determinism: one numpy Generator seeded once, advanced across all B
draws — same seed reproduces bit-identical results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import polars as pl

CI_PERCENTILES = (2.5, 97.5)


@dataclass(frozen=True)
class BootstrapResult:
    point: dict[str, float]
    ci_low: dict[str, float]
    ci_high: dict[str, float]
    n_valid: dict[str, int]
    B: int
    seed: int
    draws: list[dict[str, float]] | None = None


def event_bootstrap(
    df: pl.DataFrame,
    stat_fn: Callable[[pl.DataFrame], dict[str, float]],
    cluster_col: str = "event_id",
    id_col: str = "market_id",
    B: int = 2000,
    seed: int = 0,
    return_draws: bool = False,
) -> BootstrapResult:
    """Resamples clusters (coalesce(cluster_col, id_col)) with replacement,
    B times, refitting stat_fn once per draw. Percentile CIs (2.5/97.5)
    over non-nan draws per key. See module docstring for the stat_fn
    contract and the NaN policy.
    """
    keys = df[cluster_col].fill_null(df[id_col]).to_list()
    idx_by_cluster: dict = {}
    for i, k in enumerate(keys):
        idx_by_cluster.setdefault(k, []).append(i)
    cluster_arrays = [np.array(v, dtype=np.int64) for v in idx_by_cluster.values()]
    n_clusters = len(cluster_arrays)

    point = stat_fn(df)

    rng = np.random.default_rng(seed)
    cluster_positions = np.arange(n_clusters)
    draws: list[dict[str, float]] = []
    for _ in range(B):
        chosen = rng.choice(cluster_positions, size=n_clusters, replace=True)
        idx = np.concatenate([cluster_arrays[c] for c in chosen])
        resampled = df[idx]
        draws.append(stat_fn(resampled))

    ci_low: dict[str, float] = {}
    ci_high: dict[str, float] = {}
    n_valid: dict[str, int] = {}
    for k in point:
        vals = np.array([d[k] for d in draws], dtype=float)
        valid = vals[~np.isnan(vals)]
        n_valid[k] = int(valid.size)
        if valid.size == 0:
            raise ValueError(f"all {B} draws produced nan for stat key {k!r} — cannot compute a CI")
        lo, hi = np.percentile(valid, CI_PERCENTILES)
        ci_low[k] = float(lo)
        ci_high[k] = float(hi)

    return BootstrapResult(
        point=point,
        ci_low=ci_low,
        ci_high=ci_high,
        n_valid=n_valid,
        B=B,
        seed=seed,
        draws=draws if return_draws else None,
    )


def n_clusters_per_cell(
    df: pl.DataFrame,
    group_cols: list[str],
    cluster_col: str = "event_id",
    id_col: str = "market_id",
) -> pl.DataFrame:
    """Distinct cluster count (coalesce(cluster_col, id_col), same
    resampling unit event_bootstrap uses) per group_cols combination --
    the correct denominator for judging whether a cell has enough
    independent bootstrap units. Row count is not a reliable proxy for
    this: confirmed empirically in W2d's horizon-tercile split, where
    Sports has 18,793 rows total but its thinnest cell's cluster count is
    only 204 (barely clearing a 200 floor), and Other/Econ-Finance/
    Culture fail outright despite comfortable row counts -- a handful of
    very long-lived markets contribute many repeated snapshot rows per
    cluster, concentrated unevenly across whatever the cell's stratifying
    dimension is. Needed again for W3's reconciliation grid, which
    stratifies further still -- built here once, reused there."""
    return (
        df.with_columns(pl.col(cluster_col).fill_null(pl.col(id_col)).alias("_cluster_key"))
        .group_by(group_cols)
        .agg(pl.col("_cluster_key").n_unique().alias("n_clusters"))
    )
