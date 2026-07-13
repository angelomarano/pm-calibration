#!/usr/bin/env python3
"""Gate C report — pm-calibration project.

Panel sanity report for data/panel/p1.parquet, per
docs/M3_SPEC_ADDENDUM.md §4 (M3e): row uniqueness, y-per-market-constant,
missing_price/staleness distributions, is_oos split, and a load_panel()
integration check. Reads markets.parquet/prices.parquet/p1.parquet raw for
population-level stats (the addendum's carve-out: inspecting existence and
shape is not the "analysis" the OOS lock cares about), plus one explicit
call through the sanctioned load_panel() loader.

Usage: python spikes/gate_c.py
Output: spikes/gate_c_report.txt
"""

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from src.panel.build_panel import attach_prices
from src.panel.io import load_panel
from src.panel.snapshots import build_candidate_pairs
from src.panel.spec_config import load_spec_config

MARKETS_PATH = Path("data/panel/markets.parquet")
PRICES_PATH = Path("data/panel/prices.parquet")
P1_PATH = Path("data/panel/p1.parquet")
PRICES_CACHE_DIR = Path("data/raw/prices")
REPORT_PATH = Path(__file__).resolve().parent / "gate_c_report.txt"

CALIBRATION_CELL_MIN = 200


def m2_unexplained_tokens() -> set[str]:
    """Recomputes M2's "no history at either fidelity" token set directly
    from the cache (same method as the original 2026-07-13 investigation),
    rather than trusting a stale count."""
    tokens = set()
    for f720 in glob.glob(str(PRICES_CACHE_DIR / "*_720.json")):
        token = Path(f720).name[: -len("_720.json")]
        history = (json.loads(Path(f720).read_text()).get("history")) or []
        if not history:
            tokens.add(token)
    return tokens


def main():
    L = ["=" * 21 + " GATE C REPORT " + "=" * 21]

    markets = pl.read_parquet(MARKETS_PATH)
    prices = pl.read_parquet(PRICES_PATH)
    config = load_spec_config()

    # --- 1. row uniqueness (independent re-check, not trusting build_p1_panel's own count) ---
    p1 = pl.read_parquet(P1_PATH)
    dupes = p1.height - p1.select(["market_id", "snapshot_date"]).n_unique()
    L.append(f"\n[1] row uniqueness: {p1.height} rows, {dupes} duplicate (market_id, snapshot_date) pairs")

    # --- 2. y-per-market-constant ---
    y_per_market = p1.filter(pl.col("y").is_not_null()).group_by("market_id").agg(pl.col("y").n_unique().alias("n_distinct_y"))
    inconsistent = y_per_market.filter(pl.col("n_distinct_y") > 1)
    L.append(f"\n[2] y-per-market-constant: {inconsistent.height} markets with inconsistent y across snapshots (expect 0)")

    # --- 3. missing_price / staleness, + M2-gap overlap quantification ---
    L.append("\n[3] missing_price / staleness distribution:")
    candidates = build_candidate_pairs(markets, config.snapshot_dates, config.oos_boundary)
    kept, price_stats = attach_prices(candidates, prices, config.staleness_max_hours, config.price_clip)
    L.append(f"  candidates={price_stats['candidates']}  kept={price_stats['kept']}  "
             f"missing_price={price_stats['missing_price']} ({100*price_stats['missing_price']/price_stats['candidates']:.2f}%)")

    kept_keys = set(zip(kept["market_id"].to_list(), kept["snapshot_date"].to_list()))
    cand_ids = list(zip(candidates["market_id"].to_list(), candidates["snapshot_date"].to_list()))
    is_missing = [k not in kept_keys for k in cand_ids]
    missing_df = candidates.filter(pl.Series(is_missing))

    L.append("\n  missing_price share by category:")
    by_cat_total = candidates.group_by("category").len().rename({"len": "total"})
    by_cat_missing = missing_df.group_by("category").len().rename({"len": "missing"})
    cat_tbl = (
        by_cat_total.join(by_cat_missing, on="category", how="left")
        .fill_null(0)
        .with_columns((pl.col("missing") / pl.col("total") * 100).alias("pct"))
        .sort("pct", descending=True)
    )
    for r in cat_tbl.iter_rows(named=True):
        L.append(f"    {r['category']:<14} {r['missing']:>6}/{r['total']:<7} ({r['pct']:.1f}%)")

    L.append("\n  missing_price share by snapshot year:")
    cy = candidates.with_columns(pl.col("snapshot_date").dt.year().alias("year")).group_by("year").len().rename({"len": "total"})
    my = missing_df.with_columns(pl.col("snapshot_date").dt.year().alias("year")).group_by("year").len().rename({"len": "missing"})
    year_tbl = cy.join(my, on="year", how="left").fill_null(0).with_columns((pl.col("missing") / pl.col("total") * 100).alias("pct")).sort("year")
    for r in year_tbl.iter_rows(named=True):
        L.append(f"    {r['year']}: {r['missing']:>6}/{r['total']:<7} ({r['pct']:.1f}%)")

    # M2-gap overlap: quantified, not assumed
    unexplained_tokens = m2_unexplained_tokens()
    explained = missing_df.filter(pl.col("clob_token_leg").is_in(unexplained_tokens))
    L.append(
        f"\n  M2-unexplained-gap overlap: {explained.height}/{missing_df.height} missing_price rows "
        f"({100*explained.height/missing_df.height:.1f}%) trace to a market already known (M2, 2026-07-13 "
        f"DECISIONS.md entry) to have zero CLOB history at any fidelity."
    )
    if explained.height / missing_df.height < 0.5:
        L.append(
            "  -> minority explained by the known M2 gap. The dominant cause is NOT the same gap "
            "propagating downstream — see the no-point-vs-stale split below for the real cause."
        )

    # no-price-point-at-all vs stale-point split, for the unexplained majority
    joined = candidates.sort(["clob_token_leg", "snapshot_date"]).join_asof(
        prices.sort(["clob_token_leg", "ts"]), left_on="snapshot_date", right_on="ts", by="clob_token_leg", strategy="backward"
    )
    no_point = joined.filter(pl.col("ts").is_null())
    stale = joined.filter(
        pl.col("ts").is_not_null()
        & (((pl.col("snapshot_date") - pl.col("ts")).dt.total_seconds() / 3600) > config.staleness_max_hours)
    )
    L.append(
        f"\n  of the {missing_df.height} missing_price rows: {no_point.height} ({100*no_point.height/missing_df.height:.1f}%) "
        f"have no price point before the snapshot at all; {stale.height} ({100*stale.height/missing_df.height:.1f}%) "
        f"have a point but it exceeds the {config.staleness_max_hours:.0f}h staleness bound."
    )
    if stale.height:
        hrs = sorted(((stale["snapshot_date"] - stale["ts"]).dt.total_seconds() / 3600).to_list())
        n = len(hrs)
        L.append(f"    stale-point age: median={hrs[n//2]:.0f}h  p90={hrs[int(n*0.9)]:.0f}h  max={hrs[-1]:.0f}h")

    # --- 4. is_oos split ---
    L.append("\n[4] is_oos split:")
    oos_tbl = p1.group_by("is_oos").len().sort("is_oos")
    for r in oos_tbl.iter_rows(named=True):
        L.append(f"  is_oos={r['is_oos']}: {r['len']} ({100*r['len']/p1.height:.1f}%)")
    n_oos_snapshots = sum(1 for d in config.snapshot_dates if d >= config.oos_boundary)
    L.append(
        f"  {n_oos_snapshots}/{len(config.snapshot_dates)} snapshot dates are OOS by calendar, but the "
        f"eligible population skews hard toward 2025-2026 (Gate B) -> OOS rows are a larger share of the "
        f"panel than of the snapshot grid."
    )

    L.append("\n  category x is_oos counts:")
    cat_oos = p1.group_by(["category", "is_oos"]).len().sort(["category", "is_oos"])
    for r in cat_oos.iter_rows(named=True):
        L.append(f"    {r['category']:<14} is_oos={r['is_oos']!s:<6} {r['len']}")

    L.append(f"\n  non-OOS (W2 input population) by category, vs the ~{CALIBRATION_CELL_MIN}/cell target:")
    non_oos_cat = p1.filter(~pl.col("is_oos")).group_by("category").len().sort("len")
    for r in non_oos_cat.iter_rows(named=True):
        flag = "  <-- below target" if r["len"] < CALIBRATION_CELL_MIN else ""
        L.append(f"    {r['category']:<14} {r['len']}{flag}")
    below = non_oos_cat.filter(pl.col("len") < CALIBRATION_CELL_MIN)
    if below.height == 0:
        L.append(
            f"  -> all categories comfortably above {CALIBRATION_CELL_MIN} on non-OOS rows. If W2 further "
            "splits by horizon/vol_tercile, the smaller categories (Other, Geopolitics, Crypto) have much "
            "less headroom than Politics/Sports — worth watching once that cell structure is decided."
        )

    # --- 5. load_panel() integration check ---
    L.append("\n[5] load_panel() integration check:")
    via_loader = load_panel(P1_PATH)
    L.append(f"  load_panel(p1.parquet) rows: {via_loader.height}  (matches is_oos=False count above: "
             f"{'YES' if via_loader.height == oos_tbl.filter(~pl.col('is_oos'))['len'][0] else 'NO -- MISMATCH'})")
    dupes_via_loader = via_loader.height - via_loader.select(["market_id", "snapshot_date"]).n_unique()
    L.append(f"  duplicates via loader: {dupes_via_loader}")
    try:
        load_panel(P1_PATH, allow_oos=True)
        L.append("  allow_oos=True: did NOT raise -- UNEXPECTED (oos_locked should still be true)")
    except RuntimeError as e:
        L.append(f"  allow_oos=True: raised RuntimeError as expected ({e})")

    L.append("\n" + "=" * 57)
    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
