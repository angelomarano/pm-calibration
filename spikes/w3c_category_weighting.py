#!/usr/bin/env python3
"""W3c report — per-category weighting cut.

Per docs/W3_SPEC_ADDENDUM.md §4. Report-only, same pattern as Gates A-D
and W3a/W3b. 7 categories x 2 weightings = 14 cells, full in-sample
panel, no period/sample split, no Clock axis (see src/calibration/grid.py
module docstring for why).

Usage: python spikes/w3c_category_weighting.py
Output: spikes/w3c_category_weighting_report.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from src.calibration.data import load_calibration_frame
from src.calibration.grid import build_weighting_by_category_grid

REPORT_PATH = Path(__file__).resolve().parent / "w3c_category_weighting_report.txt"


def main():
    df, drop_stats = load_calibration_frame()
    table = build_weighting_by_category_grid(df, B=2000, seed=0)

    L = ["=" * 20 + " W3c PER-CATEGORY WEIGHTING CUT " + "=" * 20]
    L.append(
        "\nNo Clock axis, same reasoning as W3b. Full in-sample panel, no period/sample split --\n"
        "the direct analogue of the published trade-level vs market-level divergence, cut by\n"
        "category so the reader can see where volume weighting matters most."
    )

    L.append("\n[1] full table (category x weighting), 14 cells:")
    for r in table.sort(["category", "weighting"]).iter_rows(named=True):
        flag = " LOW_POWER" if r["low_power"] else ""
        L.append(
            f"  {r['category']:<14} {r['weighting']:<16} n={r['n']:<7} n_eff={r['n_eff']:<9.1f}"
            f" n_clusters={r['n_clusters']:<6} top1pct_vol_share={r['top1pct_volume_share']:.3f}"
            f" beta={r['beta_point']:.3f} CI=[{r['beta_ci_low']:.3f}, {r['beta_ci_high']:.3f}]"
            f" width={r['beta_ci_width']:.3f}{flag}"
        )

    L.append(
        "\n[2] does the n_eff collapse track volume concentration? Per category, equal-vs-weighted\n"
        "  ratio and top1pct_volume_share side by side -- descriptive, no editorializing beyond\n"
        "  stating whether the pattern holds:"
    )
    per_cat = []
    for cat in sorted(table["category"].unique().to_list()):
        eq = table.filter((pl.col("category") == cat) & (pl.col("weighting") == "equal"))
        wt = table.filter((pl.col("category") == cat) & (pl.col("weighting") == "volume_weighted"))
        if eq.height == 0 or wt.height == 0:
            continue
        eq_n, wt_neff = eq["n"][0], wt["n_eff"][0]
        eq_w, wt_w = eq["beta_ci_width"][0], wt["beta_ci_width"][0]
        concentration = eq["top1pct_volume_share"][0]
        n_eff_collapse = eq_n / wt_neff if wt_neff else float("nan")
        width_ratio = wt_w / eq_w if eq_w else float("nan")
        per_cat.append((cat, concentration, n_eff_collapse, width_ratio))
        L.append(
            f"  {cat:<14} top1pct_vol_share={concentration:.3f}  n_eff collapse={n_eff_collapse:6.1f}x"
            f"  CI-width ratio={width_ratio:5.2f}x"
        )

    by_concentration = sorted(per_cat, key=lambda t: t[1], reverse=True)
    by_collapse = sorted(per_cat, key=lambda t: t[2], reverse=True)
    concentration_rank = [c for c, _, _, _ in by_concentration]
    collapse_rank = [c for c, _, _, _ in by_collapse]
    holds = concentration_rank == collapse_rank
    L.append(
        f"\n  Ranking by top1pct_volume_share: {concentration_rank}\n"
        f"  Ranking by n_eff collapse:        {collapse_rank}\n"
        f"  Rankings {'match exactly' if holds else 'do NOT match exactly'} -- "
        f"{'the concentration story holds on this panel' if holds else 'concentration alone does not fully explain the collapse pattern here'}."
    )

    L.append(f"\n  dropped-row accounting (W2's exclusion, for reference): {drop_stats}")
    L.append("\n" + "=" * 68)

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
