#!/usr/bin/env python3
"""W3a report — the clock comparison.

Per docs/W3_SPEC_ADDENDUM.md §2. Report-only, same pattern as Gates A-D.

Four clocks, not three -- see src/calibration/clocks.py's module
docstring for why "Clock C" alone would confound the stopping-time
mechanism with the ran-to-term restriction's category-varying selection
intensity (22%-78% kept). B_term is the control: does A_term ~= B_term
per (category, tercile), as the stopping-time story predicts?

Usage: python spikes/w3a_clocks.py
Output: spikes/w3a_clocks_report.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from src.calibration.clocks import build_clock_comparison, classify_tercile_sequence, compare_clocks
from src.calibration.data import load_calibration_frame

REPORT_PATH = Path(__file__).resolve().parent / "w3a_clocks_report.txt"


def main():
    L = ["=" * 21 + " W3a CLOCK COMPARISON " + "=" * 21]
    L.append(
        "\nDescriptive only. Clock B/B_full/B_term are reproduced to show what the\n"
        "literature's ex-post clock does on this panel -- NOT endorsed as a valid\n"
        "design here. Any pattern below is a checkpoint for the stopping-time\n"
        "hypothesis, not this project's estimate."
    )

    df, drop_stats = load_calibration_frame()
    table, stats = build_clock_comparison(df, B=2000, seed=0)

    # --- ran-to-term drop share (Clock C's / A_term's required report) ---
    L.append("\n[1] ran-to-term restriction: share of rows dropped (used by A_term/B_term):")
    for cat, s in sorted(stats["ran_to_term"].items()):
        if cat == "ALL":
            continue
        L.append(f"  {cat:<14} kept={s['n_kept']:<6}/{s['n_total']:<6} (dropped {100*s['share_dropped']:.1f}%)")
    all_s = stats["ran_to_term"]["ALL"]
    L.append(f"  {'ALL':<14} kept={all_s['n_kept']:<6}/{all_s['n_total']:<6} (dropped {100*all_s['share_dropped']:.1f}%)")

    # --- full table ---
    L.append("\n[2] full table (category x clock x tercile):")
    for r in table.sort(["category", "clock", "tercile"]).iter_rows(named=True):
        flag = " LOW_POWER" if r["low_power"] else ""
        L.append(
            f"  {r['category']:<14} {r['clock']:<7} t{r['tercile']}  n={r['n']:<6} n_clusters={r['n_clusters']:<5}"
            f"  beta={r['beta_point']:.3f} CI=[{r['beta_ci_low']:.3f}, {r['beta_ci_high']:.3f}]{flag}"
        )

    # --- per (category, clock) rising-sequence classification ---
    L.append("\n[3] rising-with-horizon classification, per category x clock:")
    for cat in sorted(table["category"].unique().to_list()):
        for clock in sorted(table["clock"].unique().to_list()):
            sub = table.filter((pl.col("category") == cat) & (pl.col("clock") == clock)).sort("tercile")
            if sub.height < 3:
                continue
            betas = sub["beta_point"].to_list()
            cis = list(zip(sub["beta_ci_low"].to_list(), sub["beta_ci_high"].to_list()))
            verdict = classify_tercile_sequence(betas, cis)
            L.append(f"  {cat:<14} {clock:<7} {verdict}")

    # --- the falsifiable prediction: A_term ~= B_term ---
    L.append("\n[4] prediction check: does A_term ~= B_term per (category, tercile)?")
    L.append(
        "  If the A_full/B_full gap is stopping-time selection, A_term and B_term should\n"
        "  nearly coincide here (the two clocks' underlying values are ~equal on the\n"
        "  ran-to-term subset by construction). Divergence means another explanation is needed."
    )
    cmp_term = compare_clocks(table, "A_term", "B_term").sort(["category", "tercile"])
    n_overlap = int(cmp_term["cis_overlap"].sum())
    L.append(f"\n  {n_overlap}/{cmp_term.height} (category, tercile) cells have overlapping CIs (A_term ~= B_term holds)")
    for r in cmp_term.iter_rows(named=True):
        match = "MATCH" if r["cis_overlap"] else "DIFFERS"
        L.append(
            f"    {r['category']:<14} t{r['tercile']}  A_term_beta={r['beta_point']:.3f}  "
            f"B_term_beta={r['beta_point_y']:.3f}  diff={r['beta_diff']:+.3f}  [{match}]"
        )

    # --- context only: A_full vs B_full, not the falsifiable test itself ---
    L.append("\n[5] context: A_full vs B_full (the raw literature-clock gap this whole comparison explains):")
    cmp_full = compare_clocks(table, "A_full", "B_full").sort(["category", "tercile"])
    for r in cmp_full.iter_rows(named=True):
        match = "MATCH" if r["cis_overlap"] else "DIFFERS"
        L.append(
            f"    {r['category']:<14} t{r['tercile']}  A_full_beta={r['beta_point']:.3f}  "
            f"B_full_beta={r['beta_point_y']:.3f}  diff={r['beta_diff']:+.3f}  [{match}]"
        )

    L.append(f"\n  dropped-row accounting (W2's exclusion, for reference): {drop_stats}")

    L.append("\n" + "=" * 65)
    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
