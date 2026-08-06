#!/usr/bin/env python3
"""W3b report — the reconciliation grid.

Per docs/W3_SPEC_ADDENDUM.md §3. Report-only, same pattern as Gates A-D
and W3a. No Clock axis -- see src/calibration/grid.py's module docstring
for why: W3a found A/B overlapping in 21/21 cells, and without tercile
stratification the clock variable never enters the regression at all, so
the axis would have been a no-op. Fixed at ex-ante; ran-to-term lives in
the Sample axis instead.

Usage: python spikes/w3b_grid.py
Output: spikes/w3b_grid_report.txt, reports/figures/w3b_design_sensitivity.png
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import polars as pl

from src.calibration.data import load_calibration_frame
from src.calibration.grid import PERIOD_LEVELS, SAMPLE_LEVELS, WEIGHTING_LEVELS, build_reconciliation_grid, period_overlap_stats

REPORT_PATH = Path(__file__).resolve().parent / "w3b_grid_report.txt"
FIGURE_PATH = Path(__file__).resolve().parent.parent / "reports" / "figures" / "w3b_design_sensitivity.png"


def _plot_design_sensitivity(table: pl.DataFrame) -> None:
    order = [(s, p, w) for s in SAMPLE_LEVELS for p in PERIOD_LEVELS for w in WEIGHTING_LEVELS]
    rows = []
    for sample, period, weighting in order:
        r = table.filter(
            (pl.col("sample") == sample) & (pl.col("period") == period) & (pl.col("weighting") == weighting)
        )
        if r.height == 0:
            continue
        rows.append(r.row(0, named=True))

    fig, ax = plt.subplots(figsize=(8, 0.4 * len(rows) + 1.5))
    colors = {"equal": "tab:blue", "volume_weighted": "tab:orange"}
    markers = {"2024": "o", "2025": "s"}
    y_labels = []
    for i, r in enumerate(rows):
        y = len(rows) - i
        ax.errorbar(
            r["beta_point"],
            y,
            xerr=[[r["beta_point"] - r["beta_ci_low"]], [r["beta_ci_high"] - r["beta_point"]]],
            fmt=markers[r["period"]],
            color=colors[r["weighting"]],
            capsize=3,
            markersize=6,
        )
        flag = " (LOW_POWER)" if r["low_power"] else ""
        y_labels.append(f"{r['sample']} / {r['period']} / {r['weighting']}{flag}")

    ax.set_yticks(range(len(rows), 0, -1))
    ax.set_yticklabels(y_labels, fontsize=7)
    ax.axvline(1.0, color="gray", linestyle="--", linewidth=1)
    ax.set_xlabel("beta (point estimate, 95% CI)")
    ax.set_title("W3b design-sensitivity grid: which axis moves beta?")

    handles = [
        plt.Line2D([0], [0], color="tab:blue", marker="o", linestyle="", label="equal weight"),
        plt.Line2D([0], [0], color="tab:orange", marker="o", linestyle="", label="volume weighted"),
        plt.Line2D([0], [0], color="black", marker="o", linestyle="", label="2024"),
        plt.Line2D([0], [0], color="black", marker="s", linestyle="", label="2025"),
    ]
    ax.legend(handles=handles, loc="best", fontsize=7)
    fig.tight_layout()
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_PATH, dpi=150)
    plt.close(fig)


def main():
    df, drop_stats = load_calibration_frame()
    table = build_reconciliation_grid(df, B=2000, seed=0)
    overlap = period_overlap_stats(df)

    L = ["=" * 22 + " W3b RECONCILIATION GRID " + "=" * 22]
    L.append(
        "\nNo Clock axis (see src/calibration/grid.py docstring): W3a found A vs B overlapping\n"
        "in 21/21 cells on both samples, and without tercile stratification the clock variable\n"
        "never enters fit_calibration_regression at all -- it would have been a no-op here.\n"
        "Fixed at ex-ante; the ran-to-term restriction lives in the Sample axis instead."
    )

    L.append(
        "\nPERIOD OVERLAP CAVEAT: 2024 and 2025 are NOT independent samples -- a market open\n"
        "across the year boundary contributes rows (same market, same eventual outcome) to both\n"
        "period cells. Cluster overlap per sample level (coalesce(event_id, market_id)):"
    )
    for r in overlap.iter_rows(named=True):
        L.append(
            f"  {r['sample']:<22} 2024={r['n_clusters_2024']:<6} 2025={r['n_clusters_2025']:<6} "
            f"overlap={r['n_overlap']:<6} (={100*r['overlap_share_2024']:.1f}% of 2024, "
            f"{100*r['overlap_share_2025']:.1f}% of 2025)"
        )
    L.append(
        "  Any 2024-vs-2025 beta difference below should be read with this overlap in mind --\n"
        "  a large shared-cluster share means the two period cells are not resampling disjoint units."
    )

    L.append("\n[1] full grid (weighting x sample x period), 16 cells:")
    for r in table.sort(["sample", "period", "weighting"]).iter_rows(named=True):
        flag = " LOW_POWER" if r["low_power"] else ""
        L.append(
            f"  {r['sample']:<22} {r['period']:<5} {r['weighting']:<16} n={r['n']:<7} n_eff={r['n_eff']:<9.1f}"
            f" n_clusters={r['n_clusters']:<6} beta={r['beta_point']:.3f}"
            f" CI=[{r['beta_ci_low']:.3f}, {r['beta_ci_high']:.3f}] width={r['beta_ci_width']:.3f}{flag}"
        )

    L.append(
        "\n[2] equal-vs-weighted CI-width ratio, per (sample, period) -- descriptive, no editorializing:"
    )
    for sample in SAMPLE_LEVELS:
        for period in PERIOD_LEVELS:
            eq = table.filter(
                (pl.col("sample") == sample) & (pl.col("period") == period) & (pl.col("weighting") == "equal")
            )
            wt = table.filter(
                (pl.col("sample") == sample) & (pl.col("period") == period) & (pl.col("weighting") == "volume_weighted")
            )
            if eq.height == 0 or wt.height == 0:
                continue
            eq_w, wt_w = eq["beta_ci_width"][0], wt["beta_ci_width"][0]
            eq_neff, wt_neff = eq["n_eff"][0], wt["n_eff"][0]
            ratio = wt_w / eq_w if eq_w else float("nan")
            L.append(
                f"  {sample:<22} {period:<5} width: equal={eq_w:.3f} weighted={wt_w:.3f} ratio={ratio:.2f}x"
                f"   n_eff: equal={eq_neff:.0f} weighted={wt_neff:.1f}"
            )

    L.append(f"\n  dropped-row accounting (W2's exclusion, for reference): {drop_stats}")
    L.append(f"\n  figure written to {FIGURE_PATH}")
    L.append("\n" + "=" * 68)

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    _plot_design_sensitivity(table)
    print(f"\nwrote {REPORT_PATH}")
    print(f"wrote {FIGURE_PATH}")


if __name__ == "__main__":
    main()
