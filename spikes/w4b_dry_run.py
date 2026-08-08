#!/usr/bin/env python3
"""W4b report — R1 in-sample dry run (mechanics smoke test).

Per docs/W4_SPEC_ADDENDUM.md §2 (W4b). IN-SAMPLE DATA ONLY -- the frozen
rule must never see 2026 before W4c's unlock commit. Nothing here is a
result.

NOT A RESULT: the leg directions below were read from the SAME in-sample
rows this report then trades -- that is circular by construction (the
rule was derived from the map fit on this data), which is exactly why
the addendum requires this dry run to be a mechanics check only. Every
number in this file exists to confirm the pipeline runs cleanly and
produces sane magnitudes, not to be cited as R1's performance.

Usage: python spikes/w4b_dry_run.py
Output: spikes/w4b_dry_run_report.txt
Requires: spikes/w4a_book_sample_summary.csv (from w4a_book_sample.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from src.calibration.data import load_calibration_frame
from src.inference.bootstrap import event_bootstrap
from src.ingest.fred import fetch_dgs3mo
from src.strategy.rules import (
    attach_costs_and_pnl,
    build_r1_positions,
    leg_direction_from_calibration_map,
)

REPORT_PATH = Path(__file__).resolve().parent / "w4b_dry_run_report.txt"
BOOK_SUMMARY_PATH = Path(__file__).resolve().parent / "w4a_book_sample_summary.csv"

CIRCULARITY_NOTE = (
    "NOT A RESULT -- circular by construction: the leg direction below was read from\n"
    "the very rows this report then trades, so any apparent edge partly reflects the\n"
    "map fit on this same data, not an out-of-sample test. That test is W4c."
)

BAND_MULTIPLIERS_ORDERED = [("0.5x (optimistic)", 0.5), ("1x", 1.0), ("2x", 2.0)]
FEE_SOURCES = [("base", "fee_base"), ("upper", "fee_upper")]


def _gross_edge_stat_fn(df: pl.DataFrame) -> dict[str, float]:
    return {"mean_gross_edge": df["gross_pnl"].mean()}


def _net_edge(df: pl.DataFrame, fee_col: str, band_multiplier: float) -> float:
    net = df["gross_pnl"] - df[fee_col] - df["spread_half"].fill_null(0.0) * band_multiplier - df["carry"]
    return float(net.mean())


def _report_breakdown(L: list[str], df: pl.DataFrame, group_col: str, title: str) -> None:
    L.append(f"\n{title} (descriptive breakdown of the single frozen pooled rule -- NOT a per-{group_col} rule):")
    for key in sorted(df[group_col].unique().to_list()):
        sub = df.filter(pl.col(group_col) == key)
        gross = sub["gross_pnl"].mean()
        L.append(f"  {key:<14} n={sub.height:<6} gross_edge={gross:+.4f}")
        for label, mult in BAND_MULTIPLIERS_ORDERED:
            net_base = _net_edge(sub, "fee_base", mult)
            net_upper = _net_edge(sub, "fee_upper", mult)
            L.append(f"      spread={label:<16} net(base fee)={net_base:+.4f}  net(upper fee)={net_upper:+.4f}")


def main():
    df, drop_stats = load_calibration_frame()

    longshot_direction = leg_direction_from_calibration_map(df.filter(pl.col("category") != "Sports"), 0.02, 0.10)
    favorite_direction = leg_direction_from_calibration_map(df.filter(pl.col("category") != "Sports"), 0.90, 0.98)

    ex_sports = df.filter(pl.col("category") != "Sports")
    longshot_bucket = ex_sports.filter((pl.col("p") >= 0.02) & (pl.col("p") <= 0.10))
    favorite_bucket = ex_sports.filter((pl.col("p") >= 0.90) & (pl.col("p") <= 0.98))

    L = ["=" * 20 + " W4b R1 DRY RUN (IN-SAMPLE MECHANICS CHECK) " + "=" * 20]
    L.append(f"\n{CIRCULARITY_NOTE}")

    L.append("\n[1] direction, read once from the pooled ex-Sports in-sample calibration map:")
    L.append(
        f"  longshot [0.02,0.10]: mean_p={longshot_bucket['p'].mean():.4f} mean_y={longshot_bucket['y'].mean():.4f}"
        f" -> direction={longshot_direction!r}"
    )
    L.append(
        f"  favorite [0.90,0.98]: mean_p={favorite_bucket['p'].mean():.4f} mean_y={favorite_bucket['y'].mean():.4f}"
        f" -> direction={favorite_direction!r}"
    )

    positions = build_r1_positions(df, longshot_direction, favorite_direction)
    L.append(f"\n[2] positions generated (ALL categories, including Sports -- the rule is pooled, not per-category): {positions.height}")

    markets = pl.read_parquet("data/panel/markets.parquet").select(["market_id", "created_at"]).unique(subset=["market_id"])
    rates = fetch_dgs3mo()
    if not BOOK_SUMMARY_PATH.exists():
        L.append(f"\n{BOOK_SUMMARY_PATH} not found -- run spikes/w4a_book_sample.py first. Stopping here.")
        txt = "\n".join(L)
        print(txt)
        REPORT_PATH.write_text(txt)
        return
    book_summary = pl.read_csv(BOOK_SUMMARY_PATH)
    spread_lookup = {(r["category"], r["vol_tercile"]): r["median_hs"] for r in book_summary.iter_rows(named=True)}

    result = attach_costs_and_pnl(positions, markets, rates, spread_lookup)

    L.append("\n[3] POOLED headline (all categories, both legs combined):")
    boot = event_bootstrap(result, _gross_edge_stat_fn, B=2000, seed=0)
    L.append(
        f"  mean gross edge = {boot.point['mean_gross_edge']:+.4f}  95% CI (mechanics check, NOT a result) "
        f"[{boot.ci_low['mean_gross_edge']:+.4f}, {boot.ci_high['mean_gross_edge']:+.4f}]  n_valid={boot.n_valid['mean_gross_edge']}"
    )
    for label, mult in BAND_MULTIPLIERS_ORDERED:
        net_base = _net_edge(result, "fee_base", mult)
        net_upper = _net_edge(result, "fee_upper", mult)
        L.append(f"  spread={label:<16} net(base fee)={net_base:+.4f}  net(upper fee)={net_upper:+.4f}")

    _report_breakdown(L, result, "category", "[4] per-category breakdown")
    _report_breakdown(L, result, "leg", "[5] per-leg breakdown (longshot vs favorite)")

    L.append(f"\n  dropped-row accounting (W2's exclusion, for reference): {drop_stats}")
    L.append(f"\n{CIRCULARITY_NOTE}")
    L.append("\n" + "=" * 68)

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
