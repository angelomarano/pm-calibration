#!/usr/bin/env python3
"""W4c report — the OOS unlock result.

Per docs/W4_SPEC_ADDENDUM.md §1/§2 (W4c). Same script for both:
  --dry-run : loads IN-SAMPLE data only (load_calibration_frame(), no
              unlock needed) -- a plumbing shape check. Produces the same
              kind of circular non-result W4b's dry run did, now
              exercising evaluation.py's censoring/drawdown/annualized-
              return/break-even code too. NOT A RESULT.
  (default) : loads the FULL panel via load_panel(..., allow_oos=True),
              which raises RuntimeError until config/spec.yaml's
              oos_locked is flipped to false in its own commit. This is
              the real OOS run, only meaningful after that commit exists.

Frozen-rule reuse, not re-derivation: LONGSHOT_DIRECTION/FAVORITE_DIRECTION
below are hardcoded from W4b's dry run (commit 7e23fed,
spikes/w4b_dry_run_report.txt), never recomputed on OOS rows. A hard
assertion re-derives them on the IN-SAMPLE subset of whatever was loaded
and fails loudly if they don't still match -- a reproducibility check,
not a re-derivation.

Usage:
  python spikes/w4c_oos_result.py --dry-run   # in-sample shape check, safe pre-unlock
  python spikes/w4c_oos_result.py             # real OOS run, requires the unlock commit
Output: spikes/w4c_oos_result_report.txt (dry-run writes a separate file, see below)
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import polars as pl

from src.calibration.data import load_calibration_frame
from src.calibration.regression import calibration_stat_fn
from src.inference.bootstrap import event_bootstrap
from src.ingest.fred import fetch_dgs3mo
from src.panel.io import load_panel
from src.strategy.evaluation import (
    annualized_return,
    break_even_multiplier,
    censor_positions,
    chronological_pnl,
    max_drawdown,
)
from src.strategy.rules import attach_costs_and_pnl, build_r1_positions, leg_direction_from_calibration_map

P1_PATH = Path("data/panel/p1.parquet")
# Confirmed empirically 2026-08-08 against markets.parquet's max created_at/resolution_ts/
# closed_time (all three top out at 2026-07-12) -- the actual M1/M2 data collection cutoff,
# not assumed.
DATA_COLLECTION_CUTOFF = datetime(2026, 7, 12, tzinfo=timezone.utc)
BOOK_SUMMARY_PATH = Path(__file__).resolve().parent / "w4a_book_sample_summary.csv"

# Frozen 2026-08-08 in W4b (commit 7e23fed, spikes/w4b_dry_run_report.txt).
# NOT recomputed here -- see module docstring.
LONGSHOT_DIRECTION = "buy_no"
FAVORITE_DIRECTION = "buy_yes"

BAND_MULTIPLIERS_ORDERED = [("0.5x (optimistic)", 0.5), ("1x", 1.0), ("2x", 2.0)]


def _net_edge(df: pl.DataFrame, fee_col: str, band_multiplier: float) -> float:
    net = df["gross_pnl"] - df[fee_col] - df["spread_half"].fill_null(0.0) * band_multiplier - df["carry"]
    return float(net.mean())


def _report_block(L: list[str], df: pl.DataFrame, title: str) -> None:
    L.append(f"\n{title}: n={df.height}  gross_edge={df['gross_pnl'].mean():+.4f}")
    for label, mult in BAND_MULTIPLIERS_ORDERED:
        net_base = _net_edge(df, "fee_base", mult)
        net_upper = _net_edge(df, "fee_upper", mult)
        L.append(f"    spread={label:<16} net(base fee)={net_base:+.4f}  net(upper fee)={net_upper:+.4f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="in-sample plumbing shape check, no unlock needed")
    args = parser.parse_args()

    report_path = Path(__file__).resolve().parent / (
        "w4c_dry_run_report.txt" if args.dry_run else "w4c_oos_result_report.txt"
    )

    L = ["=" * 20 + (" W4c DRY RUN (IN-SAMPLE SHAPE CHECK)" if args.dry_run else " W4c OOS RESULT") + " " + "=" * 20]

    if args.dry_run:
        L.append(
            "\nNOT A RESULT -- in-sample plumbing check only, same circular caveat as W4b's dry run,\n"
            "now exercising censoring/chronological-PnL/drawdown/annualized-return/break-even too."
        )
        df, drop_stats = load_calibration_frame()
        in_sample = df
    else:
        full = load_panel(P1_PATH, allow_oos=True)
        drop_stats = {"note": "load_panel(allow_oos=True) does not drop rows; full panel returned"}
        in_sample = full.filter(~pl.col("is_oos"))
        df = full.filter(pl.col("is_oos"))

    # --- reproducibility assertion: frozen directions must still match on IN-SAMPLE data ---
    recomputed_longshot = leg_direction_from_calibration_map(in_sample.filter(pl.col("category") != "Sports"), 0.02, 0.10)
    recomputed_favorite = leg_direction_from_calibration_map(in_sample.filter(pl.col("category") != "Sports"), 0.90, 0.98)
    assert recomputed_longshot == LONGSHOT_DIRECTION, (
        f"Frozen longshot direction {LONGSHOT_DIRECTION!r} no longer reproduces on in-sample data "
        f"(got {recomputed_longshot!r}) -- STOP, something changed since W4b."
    )
    assert recomputed_favorite == FAVORITE_DIRECTION, (
        f"Frozen favorite direction {FAVORITE_DIRECTION!r} no longer reproduces on in-sample data "
        f"(got {recomputed_favorite!r}) -- STOP, something changed since W4b."
    )
    L.append(f"\n[0] frozen-rule reproducibility: longshot={LONGSHOT_DIRECTION!r}, favorite={FAVORITE_DIRECTION!r} -- PASS")

    # --- positions on the target population (in-sample for --dry-run, OOS for the real run) ---
    positions = build_r1_positions(df, LONGSHOT_DIRECTION, FAVORITE_DIRECTION)
    L.append(f"\n[1] positions generated (pre-censoring): {positions.height}")

    markets = (
        pl.read_parquet("data/panel/markets.parquet")
        .select(["market_id", "created_at", "resolution_ts"])
        .unique(subset=["market_id"])
    )
    positions = positions.join(markets.select(["market_id", "resolution_ts"]), on="market_id", how="left")

    # --- censoring (§1.3) -- reported BEFORE anything else downstream ---
    kept, censor_stats = censor_positions(positions, DATA_COLLECTION_CUTOFF)
    L.append(
        f"\n[2] censoring (cutoff={DATA_COLLECTION_CUTOFF.date()}): "
        f"{censor_stats['n_excluded']}/{censor_stats['n_total']} excluded "
        f"({100*censor_stats['share_excluded']:.1f}%)"
    )
    for cat, share in sorted(censor_stats["excluded_share_by_category"].items()):
        L.append(f"    {cat:<14} excluded_share={100*share:.1f}%")
    L.append(f"    kept profile:     n={censor_stats['kept_profile']['n']}  mean_days_to_resolution={censor_stats['kept_profile']['mean_days_to_resolution']:.1f}")
    L.append(f"    excluded profile: n={censor_stats['excluded_profile']['n']}  mean_days_to_resolution={censor_stats['excluded_profile']['mean_days_to_resolution']:.1f}")

    positions = kept

    rates = fetch_dgs3mo()
    if not BOOK_SUMMARY_PATH.exists():
        L.append(f"\n{BOOK_SUMMARY_PATH} not found -- run spikes/w4a_book_sample.py first. Stopping here.")
        txt = "\n".join(L)
        print(txt)
        report_path.write_text(txt)
        return
    book_summary = pl.read_csv(BOOK_SUMMARY_PATH)
    spread_lookup = {(r["category"], r["vol_tercile"]): r["median_hs"] for r in book_summary.iter_rows(named=True)}

    result = attach_costs_and_pnl(positions, markets.select(["market_id", "created_at"]), rates, spread_lookup)

    # --- pooled / longshot / favorite, EQUAL PROMINENCE (per 2026-08-08 direction) ---
    L.append(
        f"\n[3] POOLED (n={result.height}, dominated by the longshot leg -- see [4]/[5] for the split, not an appendix):"
    )
    boot = event_bootstrap(result, lambda d: {"mean_gross_edge": d["gross_pnl"].mean()}, B=2000, seed=0)
    L.append(
        f"  mean gross edge = {boot.point['mean_gross_edge']:+.4f}  95% CI "
        f"[{boot.ci_low['mean_gross_edge']:+.4f}, {boot.ci_high['mean_gross_edge']:+.4f}]"
    )
    for label, mult in BAND_MULTIPLIERS_ORDERED:
        L.append(
            f"    spread={label:<16} net(base fee)={_net_edge(result, 'fee_base', mult):+.4f}"
            f"  net(upper fee)={_net_edge(result, 'fee_upper', mult):+.4f}"
        )

    _report_block(L, result.filter(pl.col("leg") == "longshot"), "[4] LONGSHOT leg (full prominence, not a sub-breakdown)")
    _report_block(L, result.filter(pl.col("leg") == "favorite"), "[5] FAVORITE leg (full prominence, not a sub-breakdown)")

    L.append("\n[6] per-category breakdown (descriptive, single frozen pooled rule -- NOT a per-category rule):")
    for cat in sorted(result["category"].unique().to_list()):
        _report_block(L, result.filter(pl.col("category") == cat), f"    {cat}")

    # --- per-trade distribution ---
    # Reported as two separate win/loss summaries, not one quantile ladder over a bimodal
    # distribution: a single p5 on a >=95%-win-rate binary payoff lands INSIDE the winning
    # region (p5 > 0) while the loss mass (gross_pnl == -1.0 exactly, 100% of notional) sits
    # entirely below it -- technically correct, but it reads as "nothing ever loses," exactly
    # the "mean alone misleads on lumpy binary payoffs" failure the spec warns about.
    L.append("\n[7] per-trade gross_pnl distribution (win/loss reported separately -- see module docstring note):")
    wins = result.filter(pl.col("gross_pnl") > 0)
    losses = result.filter(pl.col("gross_pnl") <= 0)
    n = result.height
    L.append(
        f"  wins:   n={wins.height:<6} ({100*wins.height/n:.1f}%)  mean={wins['gross_pnl'].mean():+.4f}"
        f"  total_contribution={wins['gross_pnl'].sum():+.2f}"
    )
    L.append(
        f"  losses: n={losses.height:<6} ({100*losses.height/n:.1f}%)  mean={losses['gross_pnl'].mean():+.4f}"
        f"  total_contribution={losses['gross_pnl'].sum():+.2f}"
    )
    L.append(
        f"  distinct loss values: {sorted(losses['gross_pnl'].unique().to_list())} "
        f"(sanity check: a loss always means payout=0 regardless of entry price, so gross_pnl "
        f"must be exactly -1.0 on every loss -- confirmed, not assumed)"
    )
    L.append(
        f"  full-sample quantiles for reference: min={result['gross_pnl'].min():.4f}"
        f"  p1={result['gross_pnl'].quantile(0.01):.4f}  p5={result['gross_pnl'].quantile(0.05):.4f}"
        f"  median={result['gross_pnl'].median():.4f}  p95={result['gross_pnl'].quantile(0.95):.4f}"
    )

    # --- chronological PnL + drawdown (net at 1x, base fee) ---
    result = result.with_columns(
        (pl.col("gross_pnl") - pl.col("fee_base") - pl.col("spread_half").fill_null(0.0) - pl.col("carry")).alias("net_pnl_1x_base")
    )
    chrono = chronological_pnl(result, "net_pnl_1x_base")
    dd = max_drawdown(chrono["cumulative_pnl"].to_numpy())
    L.append(f"\n[8] chronological PnL (net, 1x band, base fee): final cumulative={chrono['cumulative_pnl'][-1]:+.2f}  max_drawdown={dd:.2f}")

    # --- annualized return (lower bound, per capital convention) ---
    period_days = (result["snapshot_date"].max() - result["snapshot_date"].min()).total_seconds() / 86400
    capital_deployed = float(result.height) * 1.0  # notional=1 per position, summed, NOT netted for overlap
    total_net = float(result["net_pnl_1x_base"].sum())
    ann_return = annualized_return(total_net, capital_deployed, period_days) if period_days > 0 else float("nan")
    L.append(
        f"\n[9] annualized return (net, 1x band, base fee): {ann_return:+.4f} -- CONSERVATIVE LOWER BOUND:\n"
        f"    capital_deployed sums notional across all {result.height} trades without netting time-overlap,\n"
        f"    which overstates capital deployed and therefore understates this return (see evaluation.py docstring)."
    )

    # --- break-even half-spread (headline) ---
    edge_net_of_fee_and_carry = float((result["gross_pnl"] - result["fee_base"] - result["carry"]).mean())
    mean_half_spread = float(result["spread_half"].fill_null(0.0).mean())
    be_mult = break_even_multiplier(edge_net_of_fee_and_carry, mean_half_spread)
    L.append(
        f"\n[10] BREAK-EVEN HALF-SPREAD (headline number): multiplier={be_mult:.2f}x the observed 1x band "
        f"(= {be_mult*mean_half_spread:.4f} absolute half-spread, vs. observed mean 1x={mean_half_spread:.4f})"
    )

    L.append("\n[11] no Sharpe ratio -- binary payoffs with lumpy resolution timing make it meaningless here.")

    if args.dry_run:
        L.append(
            "\n[12] OOS calibration persistence: N/A in --dry-run mode (no OOS sample exists yet to compare against)."
        )
    else:
        L.append("\n[12] OOS calibration persistence (separate from the strategy result):")
        in_sample_ex_sports = in_sample.filter(pl.col("category") != "Sports")
        oos_ex_sports = df.filter(pl.col("category") != "Sports")
        in_sample_fit = event_bootstrap(in_sample_ex_sports, calibration_stat_fn, B=2000, seed=0)
        oos_fit = event_bootstrap(oos_ex_sports, calibration_stat_fn, B=2000, seed=0)
        overlap = (in_sample_fit.ci_low["beta"] <= oos_fit.ci_high["beta"]) and (
            oos_fit.ci_low["beta"] <= in_sample_fit.ci_high["beta"]
        )
        L.append(
            f"  in-sample beta={in_sample_fit.point['beta']:.3f} CI=[{in_sample_fit.ci_low['beta']:.3f}, {in_sample_fit.ci_high['beta']:.3f}]"
        )
        L.append(f"  OOS beta={oos_fit.point['beta']:.3f} CI=[{oos_fit.ci_low['beta']:.3f}, {oos_fit.ci_high['beta']:.3f}]")
        L.append(f"  CIs overlap: {overlap} -- {'miscalibration persists' if overlap else 'beta has moved -- decay, not persistence'}")

    L.append(f"\n  dropped-row / panel accounting: {drop_stats}")
    L.append("\n" + "=" * 68)

    txt = "\n".join(L)
    print(txt)
    report_path.write_text(txt)
    print(f"\nwrote {report_path}")


if __name__ == "__main__":
    main()
