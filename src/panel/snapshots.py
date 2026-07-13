"""M3a — candidate (market, snapshot) pairs, openness applied.

Openness at snapshot t: created_at <= t < resolution_ts. Never
end_date_sched — decided in docs/M3_SPEC_ADDENDUM.md §1, not open for
re-litigation here: resolution_ts is look-ahead-safe (only reflects what
had already happened by t) and materially different at scale (every
category shows >=21% of the panel-eligible population resolving >2 days
before end_date_sched — see the 2026-07-13 Gate B entry in DECISIONS.md).

Null resolution_ts (0.006% of panel-eligible markets as of the real
331,035-market pull — a market with neither uma_end_date nor closed_time,
never confirmed closed) makes "t < resolution_ts" unsatisfiable for any t
under a null-propagating comparison; this module makes that exclusion
explicit rather than accidental: a market never confirmed closed is
treated as never open at any snapshot, not as open-forever. It is not
silently dropped — count_null_resolution_ts() reports how many real
markets this affects.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

CANDIDATE_INPUT_COLUMNS = [
    "market_id",
    "event_id",
    "category",
    "clob_token_leg",
    "y",
    "resolution_ambiguous",
    "volume_num",
    "scheduled_life_hours",
    "fees_enabled",
    "taker_base_fee",
    "leg_label",
    "restricted",
    "created_at",
    "end_date_sched",
    "resolution_ts",
]


def count_null_resolution_ts(markets: pl.DataFrame) -> int:
    """Panel-eligible markets with resolution_ts is null — every one of
    them is structurally excluded from every snapshot by
    build_candidate_pairs; reported here so that exclusion is counted,
    not assumed."""
    return markets.filter(pl.col("panel_eligible") & pl.col("resolution_ts").is_null()).height


def build_candidate_pairs(
    markets: pl.DataFrame, snapshot_dates: list[datetime], oos_boundary: datetime
) -> pl.DataFrame:
    """One row per (market_id, snapshot_date) where the market is
    panel_eligible and open at t (see module docstring). Ambiguous-y
    markets are kept, not filtered — excluded at the calibration step,
    per docs/M3_SPEC_ADDENDUM.md §3, not here.

    Output columns: market_id, event_id, category, clob_token_leg
    (intermediate — dropped before the final p1.parquet write),
    snapshot_date, y, resolution_ambiguous, volume_num,
    scheduled_life_hours, days_to_sched_end, days_to_resolution,
    fees_enabled, taker_base_fee, leg_label, restricted, is_oos."""
    eligible = markets.filter(pl.col("panel_eligible")).select(CANDIDATE_INPUT_COLUMNS)

    snap_df = pl.DataFrame({"snapshot_date": snapshot_dates}).with_columns(
        pl.col("snapshot_date").cast(pl.Datetime("us", "UTC"))
    )

    candidates = eligible.join(snap_df, how="cross")

    open_mask = (
        (pl.col("created_at") <= pl.col("snapshot_date"))
        & pl.col("resolution_ts").is_not_null()
        & (pl.col("snapshot_date") < pl.col("resolution_ts"))
    )

    return (
        candidates.filter(open_mask)
        .with_columns(
            (pl.col("snapshot_date") >= pl.lit(oos_boundary)).alias("is_oos"),
            ((pl.col("end_date_sched") - pl.col("snapshot_date")).dt.total_seconds() / 86400).alias(
                "days_to_sched_end"
            ),
            ((pl.col("resolution_ts") - pl.col("snapshot_date")).dt.total_seconds() / 86400).alias(
                "days_to_resolution"
            ),
        )
        .drop(["created_at", "end_date_sched"])
    )
