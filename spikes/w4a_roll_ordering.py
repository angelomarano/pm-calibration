#!/usr/bin/env python3
"""W4a report — Roll ordering check.

Per docs/W4_SPEC_ADDENDUM.md §1.4/§2 (W4a). Report-only, historical
in-sample data only (prices.parquet, markets.parquet) -- no OOS panel
touched.

Cross-sectional consistency check for the live /book sampling spike's
retroactive spread bands: computes Roll (1984) spread estimates per
market from prices.parquet's daily bars, and compares the ORDER of
(category, volume tercile) strata against w4a_book_sample.py's live-book
half-spreads. This is an ORDINAL check only -- the absolute Roll level on
daily bars is not a usable spread estimate (daily price moves are
dominated by genuine information, not bid-ask bounce), so only the
ranking is compared, never the magnitude. If Roll is undefined
(non-negative autocovariance) for more than ~50% of markets, the check
is too weak to support anything and this report says so plainly rather
than reporting whatever survived.

Volume tercile is computed fresh, within the whole set of markets that
have a usable Roll estimate (matching build_panel.py's vol_tercile
convention: within the cross-section, not within-category) -- NOT the
same as w4a_book_sample.py's live tercile (different market universe,
different volume scale), but the same STRATUM LABELS (category x
tercile) are what's being ordinally compared.

Usage: python spikes/w4a_roll_ordering.py
Output: spikes/w4a_roll_ordering_report.txt
Requires: spikes/w4a_book_sample_summary.csv (run w4a_book_sample.py first)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import polars as pl

from src.strategy.roll import roll_spread_estimate

REPORT_PATH = Path(__file__).resolve().parent / "w4a_roll_ordering_report.txt"
BOOK_SUMMARY_PATH = Path(__file__).resolve().parent / "w4a_book_sample_summary.csv"
UNDEFINED_SHARE_INCONCLUSIVE_BAR = 0.5


def main():
    markets = pl.read_parquet("data/panel/markets.parquet").filter(pl.col("panel_eligible"))
    prices = pl.read_parquet("data/panel/prices.parquet")

    print("computing per-market Roll estimates...")
    per_market = (
        prices.sort("ts")
        .group_by("clob_token_leg")
        .agg(pl.col("p").alias("price_series"))
    )
    estimates = [roll_spread_estimate(np.array(row["price_series"])) for row in per_market.iter_rows(named=True)]
    per_market = per_market.with_columns(pl.Series("roll_estimate", estimates))

    joined = per_market.join(
        markets.select(["clob_token_leg", "category", "volume_num"]).unique(subset=["clob_token_leg"]),
        on="clob_token_leg",
        how="inner",
    )

    n_total = joined.height
    n_undefined = joined["roll_estimate"].null_count()
    undefined_share = n_undefined / n_total if n_total else float("nan")

    L = ["=" * 20 + " W4a ROLL ORDERING CHECK " + "=" * 20]
    L.append(
        f"\nRoll undefined (non-negative first-order autocovariance of daily price changes): "
        f"{n_undefined}/{n_total} ({100*undefined_share:.1f}%)"
    )
    inconclusive = undefined_share > UNDEFINED_SHARE_INCONCLUSIVE_BAR
    if inconclusive:
        L.append(
            f"  >{100*UNDEFINED_SHARE_INCONCLUSIVE_BAR:.0f}% undefined -- per spec, this check is TOO WEAK "
            "to support anything. Reporting what follows for the record only, not as a finding."
        )

    usable = joined.filter(pl.col("roll_estimate").is_not_null())
    usable = usable.with_columns(
        pl.col("volume_num").qcut(3, labels=["1", "2", "3"], allow_duplicates=True).cast(pl.Utf8).cast(pl.Int8).alias("vol_tercile")
    )

    roll_summary = (
        usable.group_by(["category", "vol_tercile"])
        .agg(pl.col("roll_estimate").mean().alias("mean_roll"), pl.col("roll_estimate").median().alias("median_roll"), pl.len().alias("n"))
        .sort(["category", "vol_tercile"])
    )

    L.append("\nRoll estimate per (category, vol_tercile) stratum (in-sample markets, daily bars):")
    for r in roll_summary.iter_rows(named=True):
        L.append(f"  {r['category']:<14} t{r['vol_tercile']}  n={r['n']:<5} mean={r['mean_roll']:.4f}  median={r['median_roll']:.4f}")

    if not BOOK_SUMMARY_PATH.exists():
        L.append(f"\n{BOOK_SUMMARY_PATH} not found -- run spikes/w4a_book_sample.py first. Stopping here.")
        txt = "\n".join(L)
        print(txt)
        REPORT_PATH.write_text(txt)
        return

    book_summary = pl.read_csv(BOOK_SUMMARY_PATH)

    L.append("\n[ordinal comparison] ranking ALL (category, vol_tercile) strata present in both samples,")
    L.append("  by median Roll estimate vs. median live half-spread (1=tightest ... N=widest):")

    # Only strata with BOTH a Roll estimate and a live-book sample can be ranked against each other.
    roll_keyed = {(r["category"], r["vol_tercile"]): r["median_roll"] for r in roll_summary.iter_rows(named=True)}
    book_keyed = {(r["category"], r["vol_tercile"]): r["median_hs"] for r in book_summary.iter_rows(named=True)}
    shared = sorted(set(roll_keyed) & set(book_keyed))

    if len(shared) < 3:
        L.append(f"  only {len(shared)} strata present in both samples -- too few to rank meaningfully.")
    else:
        roll_rank = {k: i for i, k in enumerate(sorted(shared, key=lambda k: roll_keyed[k]))}
        book_rank = {k: i for i, k in enumerate(sorted(shared, key=lambda k: book_keyed[k]))}
        agreements = 0
        for k in shared:
            L.append(
                f"  {k[0]:<14} t{k[1]}  roll_rank={roll_rank[k]+1:<3} book_rank={book_rank[k]+1:<3}"
                f"  {'MATCH' if roll_rank[k] == book_rank[k] else 'diff'}"
            )
            if roll_rank[k] == book_rank[k]:
                agreements += 1
        # Spearman rank correlation (hand-computed: shared has no ties across ranks by construction)
        n = len(shared)
        d2 = sum((roll_rank[k] - book_rank[k]) ** 2 for k in shared)
        spearman = 1 - (6 * d2) / (n * (n**2 - 1)) if n > 1 else float("nan")
        L.append(f"\n  exact rank matches: {agreements}/{n}")
        L.append(f"  Spearman rank correlation (Roll vs. live-book ordering): {spearman:+.3f}")
        L.append(
            "  Positive and large -> liquid strata are tighter in both samples, the retroactive\n"
            "  spread-band application is reasonable. Near zero or negative -> ordering does not\n"
            "  hold, and the bands in costs.py should be widened, per spec."
        )

    if inconclusive:
        L.append(
            "\nRestating: the undefined-share bar above was already exceeded, so this ordinal\n"
            "comparison is presented for the record only, not as support for the retroactive\n"
            "spread assumption."
        )

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    print(f"\nwrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
