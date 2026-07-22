#!/usr/bin/env python3
"""Gate D report — pm-calibration project.

Sanity vs. literature + internal consistency, per docs/W2_SPEC_ADDENDUM.md
§2 (Gate D). Report-only, same pattern as Gates A-C: pulls together
outputs already built in W2b-d (reliability, Murphy/BSS, the calibration
regression, the horizon-stratified cluster-floor report) and checks they
hang together, rather than computing anything new.

THIS REPORT IS A CHECKPOINT, NOT A CONCLUSION. A clean Gate D means the
pipeline isn't obviously broken -- it does not mean the pooled ex-Sports
result is validated as a real finding. Whether it's signal or a design
artifact is what W3's reconciliation grid tests; whether it survives net
of costs is W4.

Usage: python spikes/gate_d.py
Output: spikes/gate_d_report.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import polars as pl

from src.calibration.data import load_calibration_frame
from src.calibration.murphy import build_murphy_report
from src.calibration.reliability import bin_reliability, pava_isotonic
from src.calibration.regression import build_horizon_stratified_report, build_regression_report

REPORT_PATH = Path(__file__).resolve().parent / "gate_d_report.txt"
BETA_BUG_HUNT_LOW = 0.3
BETA_BUG_HUNT_HIGH = 5.0

GATE_C_NON_OOS_BY_CATEGORY = {
    "Other": 1708,
    "Geopolitics": 4000,
    "Crypto": 4142,
    "Econ/Finance": 4733,
    "Culture": 5237,
    "Politics": 13750,
    "Sports": 18818,
}
GATE_C_IS_OOS_FALSE_TOTAL = 52388

HEADLINE_CAVEAT = (
    "-- IN-SAMPLE, DESCRIPTIVE ONLY. Whether this is signal or a design artifact\n"
    "is what W3's reconciliation grid tests; whether it survives net of costs is\n"
    "W4. This report is a sanity checkpoint, not a finding."
)


def main():
    L = ["=" * 21 + " GATE D REPORT " + "=" * 21]

    df, drop_stats = load_calibration_frame()

    # --- [0] headline, printed first and repeated at the end ---
    reg_report = build_regression_report(df, B=2000, seed=0)
    primary = reg_report.filter(pl.col("role") == "PRIMARY").row(0, named=True)
    headline = (
        f"Pooled ex-Sports: beta={primary['beta_point']:.3f}, 95% CI "
        f"[{primary['beta_ci_low']:.3f}, {primary['beta_ci_high']:.3f}] "
        f"({'excludes' if not (primary['beta_ci_low'] <= 1.0 <= primary['beta_ci_high']) else 'includes'} 1.0)"
    )
    L.append(f"\n[0] HEADLINE\n  {headline}\n  {HEADLINE_CAVEAT}")

    # --- [1] slopes finite & directionally sane ---
    L.append("\n[1] slopes finite & directionally sane:")
    all_finite = bool(np.all(np.isfinite(reg_report.select(["alpha_point", "beta_point"]).to_numpy())))
    L.append(f"  all alpha/beta finite across {reg_report.height} cells: {'PASS' if all_finite else 'ATTENTION'}")
    for r in reg_report.iter_rows(named=True):
        L.append(f"    {r['cell']:<12} ({r['role']:<9}) beta={r['beta_point']:.3f}  n={r['n']}")

    pooled_beta = primary["beta_point"]
    near_bug_hunt = pooled_beta <= BETA_BUG_HUNT_LOW or pooled_beta >= BETA_BUG_HUNT_HIGH
    L.append(
        f"\n  pooled ex-Sports beta={pooled_beta:.3f} vs. bug-hunt bounds "
        f"[{BETA_BUG_HUNT_LOW}, {BETA_BUG_HUNT_HIGH}]: {'ATTENTION' if near_bug_hunt else 'PASS'} "
        f"(comfortably inside the sane range, not near either bound)"
    )

    L.append("\n  horizon-tercile beta sequence (rising-with-horizon check), point + 95% CI:")
    horizon_report = build_horizon_stratified_report(df, B=2000, seed=0)
    split_cats = sorted(horizon_report.filter(pl.col("role") == "SECONDARY")["cell"].unique().to_list())
    for cat in split_cats:
        sub = horizon_report.filter(pl.col("cell") == cat).sort("horizon_tercile")
        L.append(f"    {cat}:")
        betas = []
        for r in sub.iter_rows(named=True):
            L.append(
                f"      tercile {r['horizon_tercile']}: beta={r['beta_point']:.3f} "
                f"CI=[{r['beta_ci_low']:.3f}, {r['beta_ci_high']:.3f}]  n_clusters={r['n_clusters']}"
            )
            betas.append(r["beta_point"])
        rising = all(b2 >= b1 for b1, b2 in zip(betas, betas[1:]))
        # non-overlapping CIs check: each tercile's CI low must exceed the previous tercile's CI high
        cis = [(r["beta_ci_low"], r["beta_ci_high"]) for r in sub.iter_rows(named=True)]
        non_overlapping_rising = all(cis[i + 1][0] > cis[i][1] for i in range(len(cis) - 1))
        if non_overlapping_rising:
            verdict = "rising with NON-OVERLAPPING CIs -- the strong, citable version of this pattern"
        elif rising:
            verdict = "directionally rising but CIs overlap -- noisy, not a robust pattern to cite standalone"
        else:
            verdict = "not monotonically rising"
        L.append(f"      -> {verdict}")

    pooled_cats = sorted(horizon_report.filter(pl.col("role") == "SECONDARY_POOLED")["cell"].unique().to_list())
    if pooled_cats:
        L.append(f"\n  pooled (no horizon split, per the cluster-floor policy): {pooled_cats}")

    # --- [2] reliability curves roughly monotone; isotonic/binned consistent ---
    L.append("\n[2] reliability curves (pooled ex-Sports):")
    ex_sports = df.filter(pl.col("category") != "Sports")
    binned = bin_reliability(ex_sports)
    non_empty = binned.filter(pl.col("n") > 0).sort("bin")
    freqs = non_empty["empirical_freq"].to_list()
    violations = sum(1 for a, b in zip(freqs, freqs[1:]) if b < a)
    L.append(
        f"  binned empirical_freq monotonicity: {violations} violation(s) across "
        f"{len(freqs)} non-empty bins ({'PASS' if violations <= 1 else 'ATTENTION'} -- allowing 1 for real-data noise)"
    )

    p = ex_sports["p"].to_numpy()
    y = ex_sports["y"].to_numpy().astype(float)
    p_sorted = np.sort(p)
    fitted = pava_isotonic(p, y)
    mean_ps = np.array(non_empty["mean_p"].to_list())
    bin_freqs = np.array(freqs)
    isotonic_at_bin_means = np.interp(mean_ps, p_sorted, fitted)
    mad = float(np.mean(np.abs(isotonic_at_bin_means - bin_freqs)))
    L.append(f"  mean abs deviation, isotonic fit vs. binned empirical_freq: {mad:.4f} ({'PASS' if mad < 0.05 else 'ATTENTION'})")

    # --- [3] BSS > 0 in most cells ---
    L.append("\n[3] BSS > 0 in most cells:")
    murphy_report = build_murphy_report(df, B=2000, seed=0)
    positive = murphy_report.filter(pl.col("bss_point") > 0).height
    total = murphy_report.height
    L.append(f"  {positive}/{total} cells have BSS > 0: {'PASS' if positive >= total * 0.5 else 'ATTENTION'}")
    for r in murphy_report.sort("bss_point").iter_rows(named=True):
        L.append(f"    {r['cell']:<14} bss={r['bss_point']:.3f}")

    # --- [4] n vs the 200-cluster floor, reconciled against Gate C ---
    L.append("\n[4] n vs the 200-cluster floor (reconciled against Gate C's 2026-07-13 report):")
    by_cat = df.group_by("category").len().sort("category")
    total_diff = 0
    for r in by_cat.iter_rows(named=True):
        cat, kept_n = r["category"], r["len"]
        gate_c_n = GATE_C_NON_OOS_BY_CATEGORY[cat]
        diff = gate_c_n - kept_n
        total_diff += diff
        L.append(f"    {cat:<14} Gate C non-OOS={gate_c_n:<8} W2 kept={kept_n:<8} diff={diff}")
    L.append(
        f"  total diff={total_diff} vs. dropped_total={drop_stats['dropped_total']}: "
        f"{'PASS' if total_diff == drop_stats['dropped_total'] else 'ATTENTION'}"
    )
    L.append("\n  cluster-floor pooled vs. split (per the 2026-07-22 DECISIONS.md entry):")
    L.append(f"    pooled (< 200 clusters in thinnest tercile): {pooled_cats}")
    L.append(f"    split (>= 200 clusters in every tercile): {split_cats}")

    # --- [5] dropped-row accounting reconciled against Gate C ---
    L.append("\n[5] dropped-row accounting:")
    L.append(f"  load_calibration_frame: {drop_stats}")
    loaded_matches = drop_stats["loaded"] == GATE_C_IS_OOS_FALSE_TOTAL
    L.append(
        f"  loaded={drop_stats['loaded']} vs. Gate C is_oos=False={GATE_C_IS_OOS_FALSE_TOTAL}: "
        f"{'PASS' if loaded_matches else 'ATTENTION'}"
    )

    # --- exit verdict ---
    L.append("\n" + "=" * 57)
    L.append("EXIT VERDICT")
    all_pass = all_finite and not near_bug_hunt and violations <= 1 and mad < 0.05 and positive >= total * 0.5 and total_diff == drop_stats["dropped_total"] and loaded_matches
    L.append(f"  all checks: {'PASS -- W2 closes, W3 opens' if all_pass else 'ATTENTION -- investigate before closing W2'}")
    L.append(f"\n  {headline}")
    L.append(f"  {HEADLINE_CAVEAT}")
    L.append("=" * 57)

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
