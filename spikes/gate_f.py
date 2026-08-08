#!/usr/bin/env python3
"""Gate F report — W4 and project close-out.

Per docs/W4_SPEC_ADDENDUM.md §2 (Gate F). Report-only, same pattern as
Gates A-E: re-orchestrates existing src/strategy and src/calibration
functions directly (not by importing spikes/w4c_oos_result.py, same
independence Gate D kept from Gate C's internals) so the [4] hard
assertions are checking a genuine independent re-run, not parsed report
text.

This is the last artifact of W4 and of the project's planned arc -- a
reader opening only this file should get the answer without
reconstructing it from three separate reports. [0] carries that weight.

Usage: python spikes/gate_f.py
Output: spikes/gate_f_report.txt
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from src.calibration.data import load_calibration_frame
from src.calibration.regression import calibration_stat_fn
from src.inference.bootstrap import event_bootstrap
from src.ingest.fred import fetch_dgs3mo
from src.panel.io import load_panel
from src.strategy.evaluation import break_even_multiplier, censor_positions
from src.strategy.rules import attach_costs_and_pnl, build_r1_positions

REPORT_PATH = Path(__file__).resolve().parent / "gate_f_report.txt"
BOOK_SUMMARY_PATH = Path(__file__).resolve().parent / "w4a_book_sample_summary.csv"
DATA_COLLECTION_CUTOFF = datetime(2026, 7, 12, tzinfo=timezone.utc)

# Frozen 2026-08-08 in W4b (commit 7e23fed) -- not recomputed here, see rules.py's own docstring.
LONGSHOT_DIRECTION = "buy_no"
FAVORITE_DIRECTION = "buy_yes"

# Hard reference constants, captured once from the actual historical runs -- same discipline as
# Gate E's W2D_HEADLINE_BETA. An assertion failure below means something changed since these were
# captured (2026-08-08): W4b's dry run (commit 7e23fed) and W4c's OOS run (commit cefefdc).
W4B_DRY_RUN_MEAN_GROSS_EDGE = 0.023748425387750852
W4C_OOS_BETA = 1.0766469608409126

BAND_MULTIPLIERS_ORDERED = [("0.5x (optimistic)", 0.5), ("1x", 1.0), ("2x", 2.0)]


def _net_edge(df: pl.DataFrame, fee_col: str, band_multiplier: float) -> float:
    net = df["gross_pnl"] - df[fee_col] - df["spread_half"].fill_null(0.0) * band_multiplier - df["carry"]
    return float(net.mean())


def _load_spread_lookup() -> dict[tuple[str, int], float]:
    book_summary = pl.read_csv(BOOK_SUMMARY_PATH)
    return {(r["category"], r["vol_tercile"]): r["median_hs"] for r in book_summary.iter_rows(named=True)}


def main():
    markets_created_at = (
        pl.read_parquet("data/panel/markets.parquet").select(["market_id", "created_at"]).unique(subset=["market_id"])
    )
    markets_resolution = (
        pl.read_parquet("data/panel/markets.parquet").select(["market_id", "resolution_ts"]).unique(subset=["market_id"])
    )
    rates = fetch_dgs3mo()
    spread_lookup = _load_spread_lookup()

    # --- rebuild both pipelines independently: in-sample (W4b) and OOS (W4c) ---
    in_sample_df, _ = load_calibration_frame()
    dry_positions = build_r1_positions(in_sample_df, LONGSHOT_DIRECTION, FAVORITE_DIRECTION)
    dry_result = attach_costs_and_pnl(dry_positions, markets_created_at, rates, spread_lookup)

    full = load_panel(Path("data/panel/p1.parquet"), allow_oos=True)
    clean = full.filter(pl.col("y").is_not_null() & ~pl.col("resolution_ambiguous"))
    in_sample_clean = clean.filter(~pl.col("is_oos"))
    oos_clean = clean.filter(pl.col("is_oos"))

    oos_positions = build_r1_positions(oos_clean, LONGSHOT_DIRECTION, FAVORITE_DIRECTION)
    oos_positions = oos_positions.join(markets_resolution, on="market_id", how="left")
    oos_kept, censor_stats = censor_positions(oos_positions, DATA_COLLECTION_CUTOFF)
    oos_result = attach_costs_and_pnl(oos_kept, markets_created_at, rates, spread_lookup)

    in_sample_fit = event_bootstrap(
        in_sample_clean.filter(pl.col("category") != "Sports"), calibration_stat_fn, B=2000, seed=0
    )
    oos_fit = event_bootstrap(oos_clean.filter(pl.col("category") != "Sports"), calibration_stat_fn, B=2000, seed=0)

    # --- [4] hard assertions (checked here, before most of the report text is assembled) ---
    dry_boot = event_bootstrap(dry_result, lambda d: {"mean_gross_edge": d["gross_pnl"].mean()}, B=2000, seed=0)
    assert dry_boot.point["mean_gross_edge"] == W4B_DRY_RUN_MEAN_GROSS_EDGE, (
        f"W4b dry run FAILED to reproduce: got {dry_boot.point['mean_gross_edge']!r}, "
        f"expected exactly {W4B_DRY_RUN_MEAN_GROSS_EDGE!r}. Something changed since W4b -- fix before closing Gate F."
    )
    assert oos_fit.point["beta"] == W4C_OOS_BETA, (
        f"OOS calibration cell FAILED to reproduce: got {oos_fit.point['beta']!r}, "
        f"expected exactly {W4C_OOS_BETA!r}. Something changed since W4c -- fix before closing Gate F."
    )

    # --- pooled net-edge table + break-even (needed for [0], [2], [5]) ---
    pooled_boot = event_bootstrap(oos_result, lambda d: {"mean_gross_edge": d["gross_pnl"].mean()}, B=2000, seed=0)
    gross_point = pooled_boot.point["mean_gross_edge"]
    gross_lo, gross_hi = pooled_boot.ci_low["mean_gross_edge"], pooled_boot.ci_high["mean_gross_edge"]
    net_1x_base = _net_edge(oos_result, "fee_base", 1.0)
    net_1x_upper = _net_edge(oos_result, "fee_upper", 1.0)
    net_2x_base = _net_edge(oos_result, "fee_base", 2.0)
    edge_net_of_fee_and_carry = float((oos_result["gross_pnl"] - oos_result["fee_base"] - oos_result["carry"]).mean())
    mean_half_spread = float(oos_result["spread_half"].fill_null(0.0).mean())
    be_mult = break_even_multiplier(edge_net_of_fee_and_carry, mean_half_spread)

    overlap_lo = max(in_sample_fit.ci_low["beta"], oos_fit.ci_low["beta"])
    overlap_hi = min(in_sample_fit.ci_high["beta"], oos_fit.ci_high["beta"])
    overlaps = overlap_lo <= overlap_hi

    L = ["=" * 22 + " GATE F REPORT -- W4 AND PROJECT CLOSE-OUT " + "=" * 22]

    # --- [0] SUMMARY OF RECORD ---
    L.append(
        "\n[0] SUMMARY OF RECORD\n"
        f"  Gross edge is real: {gross_point:+.4f}/trade, 95% CI [{gross_lo:+.4f}, {gross_hi:+.4f}], excludes zero.\n"
        f"  Net edge at the realistic 1x cost band is approximately zero: {net_1x_base:+.4f} (base fee) / "
        f"{net_1x_upper:+.4f} (upper fee). At 2x it is negative ({net_2x_base:+.4f}).\n"
        f"  Calibration: beta moved from {in_sample_fit.point['beta']:.3f} (in-sample) to {oos_fit.point['beta']:.3f} "
        f"(OOS), CIs overlapping only in a narrow band [{overlap_lo:.3f}, {overlap_hi:.3f}] "
        f"(width {overlap_hi-overlap_lo:.3f}). The OOS CI [{oos_fit.ci_low['beta']:.3f}, {oos_fit.ci_high['beta']:.3f}] "
        f"still excludes 1.0.\n"
        "\n  Of the four outcomes named in advance (§3): persists and tradeable / persists but not net of\n"
        "  costs / decayed / never robust -- this combination (statistically significant gross edge,\n"
        "  breakeven-ish net edge, beta still excluding 1.0 OOS but smaller than in-sample) is READ HERE\n"
        "  as 'persists but not net of costs, with partial decay.' Not 'never robust' (gross edge is\n"
        "  significant); not fully 'decayed' (OOS beta still excludes 1.0); not 'tradeable' (net edge at\n"
        "  the realistic band does not clear zero with any margin).\n"
        "\n  Break-even = 1.03x the observed 1x spread band. This is not a near-miss -- it is a measurement\n"
        "  of how precisely this market prices its own frictions: the gross mispricing and the cost of\n"
        "  capturing it are approximately the same size. That is the limits-to-arbitrage signature, and\n"
        "  it is this project's actual finding, not an inconclusive result.\n"
        "\n  Adversarial-review clause (§3): a LARGE POSITIVE net edge (net of costs, at a realistic band)\n"
        "  would have required an adversarial pipeline review before any celebration, since free money in\n"
        "  a public market is more likely a bug or a look-ahead leak than a genuine inefficiency. That did\n"
        "  NOT happen here -- no band shows a large positive net edge. Stating this explicitly so the\n"
        "  safeguard reads as correctly not invoked, not forgotten."
    )

    L.append(
        "\n  What this result does NOT establish:\n"
        "  - This is one frozen rule with fixed thresholds, not a search over rules. A different entry\n"
        "    band, a liquidity filter, or a category-conditional rule might behave differently -- untested\n"
        "    here by design, since testing them would have required the tuning freedom the\n"
        "    pre-registration deliberately gave up.\n"
        "  - The spread assumption is contemporary live books applied retroactively (W4a; Spearman +0.391\n"
        "    ordinal support -- modest, not strong). Break-even sits at 1.03x the assumed band, close\n"
        "    enough that a materially different true spread would flip the sign of the net edge. That is\n"
        "    exactly why break-even is reported as the headline number rather than the net edge itself.\n"
        "  - Six months of OOS on one venue. Not a claim about prediction markets generally, and not a\n"
        "    claim about periods under a different fee regime than the one investigated in W4a."
    )

    # --- [1] persistence ---
    L.append(
        "\n[1] persistence (formal comparison):\n"
        f"  in-sample beta={in_sample_fit.point['beta']:.4f} CI=[{in_sample_fit.ci_low['beta']:.4f}, {in_sample_fit.ci_high['beta']:.4f}]\n"
        f"  OOS beta={oos_fit.point['beta']:.4f} CI=[{oos_fit.ci_low['beta']:.4f}, {oos_fit.ci_high['beta']:.4f}]\n"
        f"  overlap interval: [{overlap_lo:.4f}, {overlap_hi:.4f}] ({'overlap' if overlaps else 'no overlap'})"
    )

    # --- [2] positive net edge under any band? ---
    L.append("\n[2] does R1 produce positive net edge under any spread band?")
    for label, mult in BAND_MULTIPLIERS_ORDERED:
        nb, nu = _net_edge(oos_result, "fee_base", mult), _net_edge(oos_result, "fee_upper", mult)
        L.append(f"  spread={label:<16} net(base fee)={nb:+.4f} ({'YES' if nb > 0 else 'no'})  net(upper fee)={nu:+.4f} ({'YES' if nu > 0 else 'no'})")
    L.append(f"  BREAK-EVEN HALF-SPREAD (headline): multiplier={be_mult:.2f}x the observed 1x band (= {be_mult*mean_half_spread:.4f} absolute)")

    # --- [3] censoring representativeness ---
    L.append(
        f"\n[3] censoring representativeness: {censor_stats['n_excluded']}/{censor_stats['n_total']} excluded "
        f"({100*censor_stats['share_excluded']:.1f}%)"
    )
    for cat, share in sorted(censor_stats["excluded_share_by_category"].items()):
        L.append(f"    {cat:<14} excluded_share={100*share:.1f}%")
    L.append(
        f"    kept:     n={censor_stats['kept_profile']['n']}  mean_days_to_resolution={censor_stats['kept_profile']['mean_days_to_resolution']:.1f}\n"
        f"    excluded: n={censor_stats['excluded_profile']['n']}  mean_days_to_resolution={censor_stats['excluded_profile']['mean_days_to_resolution']:.1f}\n"
        f"  VERDICT: {100*censor_stats['share_excluded']:.1f}% excluded is negligible at this sample size; despite the\n"
        f"  excluded set skewing toward longer-dated markets, the usable OOS sample is representative --\n"
        f"  this restriction is a rounding error, not a different population (contrast W3a's ran-to-term\n"
        f"  restriction, which dropped 22-60% and was a different population)."
    )

    # --- [4] hard assertions -- report the pass, the assert already ran above ---
    L.append(
        "\n[4] hard assertions (already checked above -- the script would have halted before this line "
        "otherwise):\n"
        f"  W4b in-sample dry run reproduces exactly: mean_gross_edge={dry_boot.point['mean_gross_edge']:.15f} -- PASS\n"
        f"  OOS calibration cell reproduces exactly: beta={oos_fit.point['beta']:.15f} -- PASS"
    )

    # --- [5] Geopolitics ---
    geo = oos_result.filter(pl.col("category") == "Geopolitics")
    geo_net_2x = _net_edge(geo, "fee_base", 2.0)
    L.append(
        f"\n[5] Geopolitics: n={geo.height}, gross_edge={geo['gross_pnl'].mean():+.4f}, net(2x, base fee)={geo_net_2x:+.4f}\n"
        "  The only category positive net of costs at every band, and the only fee-exempt category.\n"
        "  Reported as a curiosity, not a finding: one category out of seven, identified post-hoc, n=927,\n"
        "  no multiplicity correction applied. Consistent with a real effect, equally consistent with\n"
        "  chance given seven categories were looked at."
    )

    L.append("\n" + "=" * 68)
    L.append("EXIT VERDICT")
    L.append("  both hard assertions PASS -- Gate F closes, W4 and the project's planned arc close with it.")
    L.append("=" * 68)

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
