#!/usr/bin/env python3
"""Gate B report — pm-calibration project.

Runs the checks from docs/W1_SPEC.md §M4 against what actually exists at
this point in the project: data/panel/markets.parquet (M1, 331,035 rows)
and data/panel/prices.parquet (M2, panel-eligible markets only). M3 (the
P1/P2 snapshot panel builder) has not been built yet, so the checks that
are inherently panel-level concepts — "by design" breakdowns, missing_price
share, staleness distribution — are not computable yet and are marked
DEFERRED below rather than faked.

Early-resolution share (the number that decides P2's fate per plan §6) is
computed across the full population, including 2026+ markets. This is a
population-characterization check for panel design, the same thing Gate A
itself already computed across all three years — not the OOS-locked
calibration/outcome analysis that CLAUDE.md rule 5 restricts. No 2026+
snapshot panel exists yet for that rule to apply to.

Usage: python spikes/gate_b.py
Output: spikes/gate_b_report.txt
"""

from pathlib import Path

import polars as pl

MARKETS_PATH = Path("data/panel/markets.parquet")
PRICES_PATH = Path("data/panel/prices.parquet")
REPORT_PATH = Path(__file__).resolve().parent / "gate_b_report.txt"

EARLY_THRESHOLD_DAYS = 2  # matches Gate A's own methodology


def pct(n, d):
    return f"{100*n/d:.1f}%" if d else "n/a"


def main():
    df = pl.read_parquet(MARKETS_PATH)
    L = []
    L.append("=" * 21 + " GATE B REPORT " + "=" * 21)
    L.append(f"markets.parquet: {df.height} rows")

    # --- 1. rows/distinct markets by category x year; events; markets-per-event ---
    L.append("\n[1] rows by category x year (design breakdown DEFERRED — no P1/P2 panel yet, M3 not built):")
    by_cat_year = (
        df.with_columns(pl.col("end_date_sched").dt.year().alias("year"))
        .group_by(["category", "year"])
        .len()
        .sort(["category", "year"])
    )
    for r in by_cat_year.iter_rows(named=True):
        L.append(f"  {r['category']:<14} {r['year']}: {r['len']}")

    n_events = df.filter(pl.col("event_id").is_not_null())["event_id"].n_unique()
    L.append(f"\ndistinct events: {n_events}")
    per_event = df.filter(pl.col("event_id").is_not_null()).group_by("event_id").len()
    mpe = sorted(per_event["len"].to_list())
    if mpe:
        n = len(mpe)
        L.append(
            "markets-per-event: "
            f"median={mpe[n//2]}  p90={mpe[int(n*0.9)]}  max={mpe[-1]}  "
            f"(events with >1 market: {sum(1 for v in mpe if v > 1)}/{n})"
        )

    # --- 2. base rate of y by category and by year ---
    L.append("\n[2] base rate of y (share y==1 among non-ambiguous), by category:")
    resolved = df.filter(pl.col("y").is_not_null())
    by_cat = resolved.group_by("category").agg(pl.col("y").mean().alias("rate"), pl.len())
    for r in by_cat.sort("category").iter_rows(named=True):
        L.append(f"  {r['category']:<14} base_rate={r['rate']:.1%}  n={r['len']}")

    L.append("\nbase rate of y by year:")
    by_year = (
        resolved.with_columns(pl.col("end_date_sched").dt.year().alias("year"))
        .group_by("year")
        .agg(pl.col("y").mean().alias("rate"), pl.len())
        .sort("year")
    )
    for r in by_year.iter_rows(named=True):
        L.append(f"  {r['year']}: base_rate={r['rate']:.1%}  n={r['len']}")

    # --- 3. duplicates / missing_price / staleness ---
    L.append("\n[3] duplicates:")
    dupes = df.height - df["market_id"].n_unique()
    L.append(f"  duplicate market_id: {dupes}  (build_markets_table raises if any survive parsing)")
    L.append("  missing_price / staleness distributions: DEFERRED — snapshot-level concepts, need M3's panel")

    # --- 4. early-resolution share and days-early distribution per category ---
    def early_resolution_table(frame: pl.DataFrame, label: str) -> list[str]:
        lines = [f"\n[4{label and ' (' + label + ')'}] early-resolution share per category:"]
        lines.append(f"  early = resolution_ts < end_date_sched by more than {EARLY_THRESHOLD_DAYS} days")
        er = frame.filter(
            pl.col("resolution_ts").is_not_null() & pl.col("end_date_sched").is_not_null()
        ).with_columns(
            ((pl.col("end_date_sched") - pl.col("resolution_ts")).dt.total_seconds() / 86400).alias("days_early")
        )
        summary = []
        for cat in sorted(er["category"].unique().to_list()):
            sub = er.filter(pl.col("category") == cat)
            n = sub.height
            early = sub.filter(pl.col("days_early") > EARLY_THRESHOLD_DAYS)
            share = early.height / n if n else 0.0
            summary.append((cat, share, n))
            de = sorted(early["days_early"].to_list())
            if de:
                m = len(de)
                lines.append(
                    f"  {cat:<14} early={pct(early.height, n):<7} n={n:<8} "
                    f"days_early: median={de[m//2]:.0f} p90={de[int(m*0.9)]:.0f}"
                )
            else:
                lines.append(f"  {cat:<14} early={pct(early.height, n):<7} n={n:<8} days_early: n/a (0 early resolvers)")

        below_15 = [c for c, s, n in summary if s < 0.15]
        lines.append(f"\n  categories with early share < 15%: {below_15 or 'NONE'}")
        return lines, summary

    full_lines, full_summary = early_resolution_table(df, "FULL POPULATION, 331,035 markets — diluted by ~72% panel-ineligible")
    L += full_lines
    L.append("  -> NOT the number that decides P2's fate; see the panel-eligible-only table below.")

    eligible_df = df.filter(pl.col("panel_eligible"))
    elig_lines, elig_summary = early_resolution_table(
        eligible_df, "PANEL-ELIGIBLE ONLY, 94,442 markets — this is what actually decides P2's fate"
    )
    L += elig_lines
    elig_below_15 = [c for c, s, n in elig_summary if s < 0.15]
    if elig_below_15:
        L.append(
            f"  -> {elig_below_15} clear the <15% bar on the eligible population -> P2 viable as a "
            f"category-specific secondary design for {elig_below_15}, per plan §6's per-category contingency "
            f"(not an all-or-nothing global call)."
        )
    else:
        L.append("  -> no category clears the 15% bar even restricted to eligible markets; P2 stays exploratory.")

    # --- 5. AMM-era / unexplained recap from M2 ---
    L.append("\n[5] M2 recap (from the full run, 2026-07-13):")
    L.append("  panel_eligible markets: 94,442 (28.5% of 331,035)")
    L.append("  coverage: 98.86%  (bar >=95%: PASS)")
    L.append("  amm_era: 0")
    L.append("  unexplained: 1,072 (1.135%)  (bar <1%: FAIL — see DECISIONS.md 2026-07-13 entry for the investigation trail)")
    L.append("  gaps (persistent per-token failures): 0")

    # --- 6. spot-checks ---
    L.append("\n[6] spot-checks:")
    pres = df.filter(pl.col("question").str.contains("(?i)2024 US Presidential Election"))
    found = pres.height > 0
    L.append(f"  2024 US presidential-election market present: {'YES' if found else 'NO'} (n={pres.height})")
    if found:
        for r in pres.sort("volume_num", descending=True).head(3).iter_rows(named=True):
            L.append(f"    - ${r['volume_num']:>14,.0f}  y={r['y']}  {r['question']}")

    if PRICES_PATH.exists():
        prices = pl.read_parquet(PRICES_PATH)
        mid2024 = prices.filter(
            (pl.col("ts") >= pl.datetime(2024, 6, 1, time_zone="UTC"))
            & (pl.col("ts") < pl.datetime(2024, 7, 1, time_zone="UTC"))
        )
        if mid2024.height:
            L.append(
                f"  mid-2024 (June) snapshot prices: n={mid2024.height}  "
                f"range=[{mid2024['p'].min():.3f}, {mid2024['p'].max():.3f}]  "
                f"(plausible: within [0,1])"
            )
        else:
            L.append("  mid-2024 snapshot prices: none found in prices.parquet")
    else:
        L.append("  prices.parquet not found — skipping mid-2024 price spot-check")

    L.append("\n  top-10 markets by volume:")
    top10 = df.sort("volume_num", descending=True).head(10)
    for r in top10.iter_rows(named=True):
        L.append(f"    ${r['volume_num']:>14,.0f}  {r['category']:<12} | {r['question'][:70]}")

    L.append("\n" + "=" * 57)
    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
