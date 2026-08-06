#!/usr/bin/env python3
"""W3a report — the clock comparison.

Per docs/W3_SPEC_ADDENDUM.md §2. Report-only, same pattern as Gates A-D.

Four clocks, not three -- see src/calibration/clocks.py's module
docstring for why "Clock C" alone would confound the stopping-time
mechanism with the ran-to-term restriction's category-varying selection
intensity (22%-78% kept).

What the first run of this report actually found: clock choice (A vs B)
barely moves the estimate here -- both on the full sample and on the
ran-to-term subset, A and B are statistically indistinguishable
everywhere (no non-overlapping cells either way). What moves the
estimate is which markets are in the sample: full vs ran-to-term, on the
SAME clock. Whether those full-vs-term movements are real or small-sample
noise (A_term's CIs are much wider) is what sections [6]/[7] check --
don't read [4] (A_term~=B_term) as a confirmation on its own; there was
no sharp A/B divergence to explain in the first place.

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


def _format_compare_section(title: str, guard: str, cmp: pl.DataFrame, label_x: str, label_y: str) -> list[str]:
    lines = [f"\n{title}", f"  {guard}"]
    n_overlap = int(cmp["cis_overlap"].sum())
    lines.append(f"\n  {n_overlap}/{cmp.height} (category, tercile) cells have overlapping CIs ({label_x} ~= {label_y})")
    for r in cmp.sort(["category", "tercile"]).iter_rows(named=True):
        match = "MATCH" if r["cis_overlap"] else "DIFFERS"
        lines.append(
            f"    {r['category']:<14} t{r['tercile']}  {label_x}_beta={r['beta_point']:.3f}  "
            f"{label_y}_beta={r['beta_point_y']:.3f}  diff={r['beta_diff']:+.3f}  [{match}]"
        )
    return lines


def main():
    df, drop_stats = load_calibration_frame()
    table, stats = build_clock_comparison(df, B=2000, seed=0)

    cmp_term = compare_clocks(table, "A_term", "B_term")
    cmp_full = compare_clocks(table, "A_full", "B_full")
    cmp_a = compare_clocks(table, "A_full", "A_term")
    cmp_b = compare_clocks(table, "B_full", "B_term")

    n_overlap_term = int(cmp_term["cis_overlap"].sum())
    n_overlap_full = int(cmp_full["cis_overlap"].sum())
    n_overlap_a = int(cmp_a["cis_overlap"].sum())
    n_overlap_b = int(cmp_b["cis_overlap"].sum())

    L = ["=" * 21 + " W3a CLOCK COMPARISON " + "=" * 21]
    L.append(
        f"\nSUMMARY: clock choice barely moves the estimate on this panel.\n"
        f"  A_full vs B_full:  {n_overlap_full}/{cmp_full.height} cells overlap (ex-post clock ~= ex-ante clock, full sample)\n"
        f"  A_term vs B_term:  {n_overlap_term}/{cmp_term.height} cells overlap (ex-post clock ~= ex-ante clock, ran-to-term sample)\n"
        f"  There was no sharp A/B divergence to explain in the first place -- so A_term ~= B_term\n"
        f"  is NOT on its own a confirmation of the stopping-time story.\n\n"
        f"  What DOES move: which markets are in the sample, holding the clock fixed.\n"
        f"  A_full vs A_term:  {n_overlap_a}/{cmp_a.height} cells overlap\n"
        f"  B_full vs B_term:  {n_overlap_b}/{cmp_b.height} cells overlap\n"
        f"  See [6]/[7] for whether that movement survives the (much wider) ran-to-term CIs\n"
        f"  or is small-sample noise, before citing it as sample-composition selection."
    )
    L.append(
        "\nDescriptive only throughout. Clock B/B_full/B_term are reproduced to show what the\n"
        "literature's ex-post clock does on this panel -- NOT endorsed as a valid design here."
    )

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
    L += _format_compare_section(
        "[4] prediction check: does A_term ~= B_term per (category, tercile)?",
        "If the A_full/B_full gap were stopping-time selection, A_term and B_term should nearly\n"
        "  coincide here. But see the SUMMARY above: there was little A/B gap to begin with, so\n"
        "  this match is not on its own a confirmation.",
        cmp_term,
        "A_term",
        "B_term",
    )

    # --- context: A_full vs B_full ---
    L += _format_compare_section(
        "[5] context: A_full vs B_full (the raw literature-clock gap this whole comparison was built to explain):",
        "Also overlapping everywhere -- the premise that this project's ex-ante clock and the\n"
        "  literature's ex-post clock diverge sharply on this panel does not hold up either.",
        cmp_full,
        "A_full",
        "B_full",
    )

    # --- NEW: A_full vs A_term ---
    L += _format_compare_section(
        "[6] A_full vs A_term: does the ran-to-term restriction itself move the ex-ante-clock estimate?",
        "This is where the real movement is (e.g. Politics: non-monotonic on A_full, cleanly rising\n"
        "  on A_term; Sports t3 collapses to 0.505). DIFFERS cells below are early-resolver selection\n"
        "  showing up as sample composition, not clock contamination -- IF they clear this overlap\n"
        "  check, given A_term's much wider CIs from the smaller restricted sample.",
        cmp_a,
        "A_full",
        "A_term",
    )

    # --- NEW: B_full vs B_term ---
    L += _format_compare_section(
        "[7] B_full vs B_term: same restriction, ex-post clock -- same mechanism should show up here too:",
        "If [6]'s movement is genuinely about which markets survive the restriction (not the clock),\n"
        "  the same DIFFERS pattern should appear here, on a different clock, for the same categories.",
        cmp_b,
        "B_full",
        "B_term",
    )

    L.append(f"\n  dropped-row accounting (W2's exclusion, for reference): {drop_stats}")

    L.append("\n" + "=" * 65)
    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
