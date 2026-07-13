"""M3b — price attachment, staleness/clip, per-snapshot vol_tercile, and
final p1.parquet assembly.

attach_prices uses polars join_asof, which requires both frames sorted on
the "on" column (and, with a `by` group key, sorted within each group) —
unsorted input does not raise, it silently produces wrong matches. Both
frames are explicitly sorted here; upstream ordering is never trusted.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.panel.snapshots import build_candidate_pairs
from src.panel.spec_config import SpecConfig

DEFAULT_OUTPUT_PATH = Path("data/panel/p1.parquet")

P1_SCHEMA: dict[str, pl.PolarsDataType] = {
    "market_id": pl.Utf8,
    "event_id": pl.Utf8,
    "category": pl.Utf8,
    "design": pl.Utf8,
    "snapshot_date": pl.Datetime("us", "UTC"),
    "p": pl.Float64,
    "y": pl.Int8,
    "volume_num": pl.Float64,
    "vol_tercile": pl.Int8,
    "scheduled_life_hours": pl.Float64,
    "days_to_sched_end": pl.Float64,
    "days_to_resolution": pl.Float64,
    "fees_enabled": pl.Boolean,
    "taker_base_fee": pl.Float64,
    "leg_label": pl.Utf8,
    "restricted": pl.Boolean,
    "is_oos": pl.Boolean,
    "resolution_ambiguous": pl.Boolean,
}


def attach_prices(
    candidates: pl.DataFrame,
    prices: pl.DataFrame,
    staleness_max_hours: float,
    price_clip: tuple[float, float],
) -> tuple[pl.DataFrame, dict]:
    """As-of join: last prices.parquet point with ts <= snapshot_date, per
    clob_token_leg. Drops rows with no such point, or where
    snapshot_date - ts exceeds staleness_max_hours (both counted as
    missing_price). Clips surviving p to price_clip. Returns (df, stats)
    with stats = {candidates, missing_price, kept}."""
    cand_sorted = candidates.sort(["clob_token_leg", "snapshot_date"])
    prices_sorted = prices.sort(["clob_token_leg", "ts"])

    joined = cand_sorted.join_asof(
        prices_sorted, left_on="snapshot_date", right_on="ts", by="clob_token_leg", strategy="backward"
    )

    total = joined.height
    staleness_hours = (pl.col("snapshot_date") - pl.col("ts")).dt.total_seconds() / 3600
    fresh = pl.col("ts").is_not_null() & (staleness_hours <= staleness_max_hours)

    kept = joined.filter(fresh).with_columns(pl.col("p").clip(price_clip[0], price_clip[1]))
    missing = total - kept.height

    return kept, {"candidates": total, "missing_price": missing, "kept": kept.height}


def compute_vol_tercile(df: pl.DataFrame) -> pl.DataFrame:
    """Adds vol_tercile (1/2/3), computed by volume_num WITHIN each
    snapshot_date group, not globally."""
    return df.with_columns(
        pl.col("volume_num")
        .qcut(3, labels=["1", "2", "3"], allow_duplicates=True)
        .over("snapshot_date")
        .cast(pl.Utf8)  # qcut returns Categorical; casting straight to Int8 gives the 0-indexed
        .cast(pl.Int8)  # category code (0,1,2), not the "1"/"2"/"3" label text — go through Utf8 first
        .alias("vol_tercile")
    )


def build_p1_panel(markets: pl.DataFrame, prices: pl.DataFrame, config: SpecConfig) -> tuple[pl.DataFrame, dict]:
    """Orchestrates M3a (build_candidate_pairs) + M3b (attach_prices,
    compute_vol_tercile) into the final p1.parquet schema. Returns
    (p1_df, stats)."""
    candidates = build_candidate_pairs(markets, config.snapshot_dates, config.oos_boundary)
    with_prices, price_stats = attach_prices(candidates, prices, config.staleness_max_hours, config.price_clip)
    with_tercile = compute_vol_tercile(with_prices)

    p1 = with_tercile.with_columns(pl.lit("P1").alias("design")).drop("clob_token_leg")
    p1 = p1.select(list(P1_SCHEMA.keys())).cast(P1_SCHEMA)

    stats = {**price_stats, "final_rows": p1.height}
    return p1, stats
