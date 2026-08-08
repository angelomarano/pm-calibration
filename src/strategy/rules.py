"""W4b — R1 strategy mechanics (docs/W4_SPEC_ADDENDUM.md §1.1/§2).

Built and exercised on IN-SAMPLE data only (the dry run) -- the frozen
rule must never see 2026 before W4c's unlock commit.

Price flow (the one thing that's silent and dangerous to get wrong):
both fee_cost and the spread lookup must receive the price of the token
ACTUALLY BOUGHT, never the panel's raw p. The favorite leg buys Yes at p
directly. The longshot leg buys No, which trades at (1-p) -- betting
against the longshot means buying the complement, priced high, not the
longshot's own low p. build_r1_positions computes this once, into
entry_price, and everything downstream (attach_costs_and_pnl) uses
entry_price exclusively -- never p_favorite/p_longshot's raw p again.

Getting this backwards would NOT be caught by the primary fee formula
(p*(1-p) is symmetric, so p=0.05 and p=0.95 give the identical fee) --
that's exactly why test_rules.py's canary test uses the REJECTED
footnote formula (min(p,1-p)/p, asymmetric) as the detector instead.

Direction is read ONCE from the pooled ex-Sports in-sample calibration
map (matching this project's primary population throughout W2d/Gate E)
via leg_direction_from_calibration_map, then passed into
build_r1_positions as a fixed argument -- the position-builder itself
never re-derives direction, so a later call on OOS data structurally
cannot "peek" and flip the rule.
"""

from __future__ import annotations

import polars as pl

from src.strategy.costs import carry_cost, fee_cost

DEFAULT_P_LONGSHOT = (0.02, 0.10)
DEFAULT_P_FAVORITE = (0.90, 0.98)


def leg_direction_from_calibration_map(df: pl.DataFrame, p_lo: float, p_hi: float) -> str | None:
    """Reads the frozen in-sample map ONCE: compares mean_p (stated
    price) against mean_y (realized frequency) among rows with p in
    [p_lo, p_hi]. "buy_no" if mean_p > mean_y (price too high --
    betting against is favored), "buy_yes" if mean_p < mean_y (price too
    low -- betting for is favored), None if mean_p == mean_y exactly (no
    edge, that leg does not trade). This is a single generic rule, not
    two different rules for "longshot" vs "favorite" -- the addendum's
    per-leg language is this rule applied at two different buckets."""
    bucket = df.filter((pl.col("p") >= p_lo) & (pl.col("p") <= p_hi))
    mean_p = bucket["p"].mean()
    mean_y = bucket["y"].mean()
    if mean_p > mean_y:
        return "buy_no"
    if mean_p < mean_y:
        return "buy_yes"
    return None


def build_r1_positions(
    df: pl.DataFrame,
    longshot_direction: str | None,
    favorite_direction: str | None,
    p_longshot: tuple[float, float] = DEFAULT_P_LONGSHOT,
    p_favorite: tuple[float, float] = DEFAULT_P_FAVORITE,
) -> pl.DataFrame:
    """One row per (market, snapshot) row in df whose p falls in the
    longshot or favorite bucket AND that leg's direction is not None (a
    None direction trades nothing for that leg). Adds: leg
    ("longshot"/"favorite"), side (the passed-in direction for that
    leg), entry_price (1-p for buy_no, p for buy_yes -- the bought
    token's price), won (y==0 for buy_no, y==1 for buy_yes). Not
    deduplicated per market -- one row in, one position out, per spec
    §1.1: a market with several in-bucket snapshots generates several
    positions."""
    legs = []
    if longshot_direction is not None:
        lo, hi = p_longshot
        sub = df.filter((pl.col("p") >= lo) & (pl.col("p") <= hi)).with_columns(
            pl.lit("longshot").alias("leg"), pl.lit(longshot_direction).alias("side")
        )
        legs.append(sub)
    if favorite_direction is not None:
        lo, hi = p_favorite
        sub = df.filter((pl.col("p") >= lo) & (pl.col("p") <= hi)).with_columns(
            pl.lit("favorite").alias("leg"), pl.lit(favorite_direction).alias("side")
        )
        legs.append(sub)

    if not legs:
        return df.head(0).with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("leg"),
            pl.lit(None, dtype=pl.Utf8).alias("side"),
            pl.lit(None, dtype=pl.Float64).alias("entry_price"),
            pl.lit(None, dtype=pl.Boolean).alias("won"),
        )

    positions = pl.concat(legs)
    positions = positions.with_columns(
        pl.when(pl.col("side") == "buy_no")
        .then(1 - pl.col("p"))
        .otherwise(pl.col("p"))
        .alias("entry_price"),
        pl.when(pl.col("side") == "buy_no")
        .then(pl.col("y") == 0)
        .otherwise(pl.col("y") == 1)
        .alias("won"),
    )
    return positions


def attach_costs_and_pnl(
    positions: pl.DataFrame,
    created_at_by_market: pl.DataFrame,
    rates: pl.DataFrame,
    spread_lookup: dict[tuple[str, int], float],
    notional: float = 1.0,
) -> pl.DataFrame:
    """Per position: gross_pnl = payout/entry_price - 1 (equal notional,
    taker entry, hold to resolution -- payout = 1/entry_price if won
    else 0). Raw cost components are kept SEPARATE columns (fee_base,
    fee_upper, fee_footnote_contract_base, fee_footnote_contract_upper,
    spread_half, carry) rather than pre-combined into net PnL -- the
    band_multiplier x rate-source grid is a report-level aggregation,
    not baked in here (same separation W3b used between per-cell fits
    and the grid). days_held is days_to_resolution, already in
    p1.parquet -- no resolution_ts needed since R1 holds to resolution
    by construction.

    The FRED rate is attached via join_asof (forward-fill on snapshot
    date), not a per-row rate_on() call -- rate_on re-filters and
    re-sorts the whole rate series on every call, the wrong tool at
    position-table scale. join_asof is this project's own established
    idiom for exactly this forward-fill (see build_panel.py's price
    attachment), including the lesson already paid for once: both sides
    must be explicitly sorted first, since join_asof on unsorted input
    gives silently wrong matches, not an error."""
    joined = positions.join(created_at_by_market, on="market_id", how="left")
    joined = joined.with_columns(pl.col("snapshot_date").dt.date().alias("_snapshot_date_only"))
    # drop null observations (holidays) BEFORE the asof join, same as rate_on -- otherwise
    # backward-search can land exactly on a null-valued row instead of skipping past it
    rates_non_null = rates.filter(pl.col("dgs3mo").is_not_null())
    joined = joined.sort("_snapshot_date_only").join_asof(
        rates_non_null.sort("date").rename({"date": "_rate_date"}),
        left_on="_snapshot_date_only",
        right_on="_rate_date",
        strategy="backward",
    )

    rows = []
    for row in joined.iter_rows(named=True):
        entry_price = row["entry_price"]
        payout = (1.0 / entry_price) if row["won"] else 0.0
        gross_pnl = payout - 1.0

        fees = fee_cost(
            category=row["category"],
            created_at=row["created_at"],
            price=entry_price,
            taker_base_fee=row["taker_base_fee"],
            notional=notional,
        )
        half_spread = spread_lookup.get((row["category"], row["vol_tercile"]))
        carry = carry_cost(
            days_held=row["days_to_resolution"],
            annual_rate_pct=row["dgs3mo"],
            notional=notional,
        )

        rows.append(
            {
                **row,
                "gross_pnl": gross_pnl,
                "fee_base": fees["base"],
                "fee_upper": fees["upper"],
                "fee_footnote_contract_base": fees["footnote_contract_formula_base"],
                "fee_footnote_contract_upper": fees["footnote_contract_formula_upper"],
                "spread_half": half_spread,
                "carry": carry,
            }
        )

    return pl.DataFrame(rows)
