#!/usr/bin/env python3
"""W4a report — live /book sampling spike.

Per docs/W4_SPEC_ADDENDUM.md §1.4/§2 (W4a). Report-only, no OOS panel
touched: this pulls a FRESH, live cross-section of currently-open markets
directly from Gamma (this project's own historical markets.parquet has
only 6 panel-eligible markets still open as of this run -- almost
everything in a 2024-2026 panel has resolved by now, far too small a
pool to stratify). Provides the contemporary half-spread per (category,
volume tercile) stratum that costs.py's spread_haircut bands (0.5x/1x/2x)
apply retroactively to the historical period -- see
w4a_roll_ordering.py for the cross-sectional consistency check that makes
that retroactive application defensible.

/book's bids/asks are not guaranteed sorted in a fixed direction (probed
2026-08-08: this run returned bids ascending, asks descending -- best
price is whichever extreme is actually best, not "the first/last
element" by convention) -- half_spread_from_book takes max(bid prices)
and min(ask prices) directly rather than trusting array order.

Volume tercile is computed WITHIN this live sample as a whole (not
within-category), matching this project's own vol_tercile convention
(build_panel.py's compute_vol_tercile is `.over("snapshot_date")`, i.e.
within the whole cross-section, not within-category).

Leg choice: uses clobTokenIds[0] for every market (a simplifying choice
for this throwaway spike, not the panel's own leg_index heuristic --
spread magnitude is roughly symmetric between a binary market's two
complementary legs, so this doesn't bias the stratum-level comparison
this spike exists to support).

Usage: python spikes/w4a_book_sample.py
Output: spikes/w4a_book_sample_report.txt
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from src.ingest.gamma_events import fetch_event_tags
from src.ingest.http import make_session
from src.panel.categories import map_category

GAMMA_MARKETS_KEYSET_URL = "https://gamma-api.polymarket.com/markets/keyset"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "w4a_book_sample"
REPORT_PATH = Path(__file__).resolve().parent / "w4a_book_sample_report.txt"
SUMMARY_CSV_PATH = Path(__file__).resolve().parent / "w4a_book_sample_summary.csv"

VOLUME_NUM_MIN = 1000
TARGET_TOTAL_MARKETS = 4000  # server caps each page at 100 regardless of requested
# limit (probed 2026-08-08) -- category diversity (Crypto, Sports, etc. alongside
# the Politics-heavy early pages) only shows up after ~10+ pages, so this needs to
# page deep, not wide
MAX_PER_STRATUM = 20  # cap on /book calls per (category, tercile) cell
PACING_SECONDS = 0.25


def pull_open_markets(session, target_total: int = TARGET_TOTAL_MARKETS) -> list[dict]:
    """Live pull, closed=false -- NOT cached (this is a fresh cross-section
    by design, not a historical pull), paged until target_total markets or
    the server signals no more pages (next_cursor absent -- NOT page
    length, since the server silently caps each page at 100 regardless of
    the requested limit)."""
    out: list[dict] = []
    cursor = None
    page_num = 0
    while len(out) < target_total:
        params = {"closed": "false", "limit": 200, "volume_num_min": VOLUME_NUM_MIN}
        if cursor is not None:
            params["after_cursor"] = cursor
        resp = session.get(GAMMA_MARKETS_KEYSET_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        page = data.get("markets", [])
        out.extend(page)
        if page_num % 5 == 0:
            print(f"  page {page_num}: +{len(page)} markets (total {len(out)})")
        next_cursor = data.get("next_cursor")
        if not next_cursor or not page:
            break
        cursor = next_cursor
        page_num += 1
        time.sleep(PACING_SECONDS)
    return out


def half_spread_from_book(book: dict) -> float | None:
    """None if either side is empty. Does not trust array order -- see
    module docstring."""
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(float(b["price"]) for b in bids)
    best_ask = min(float(a["price"]) for a in asks)
    return (best_ask - best_bid) / 2


def main():
    session = make_session()

    print("pulling live open-markets cross-section...")
    raw_markets = pull_open_markets(session)
    print(f"total pulled: {len(raw_markets)}")

    rows = []
    for m in raw_markets:
        tokens = m.get("clobTokenIds")
        if isinstance(tokens, str):
            import json as _json

            tokens = _json.loads(tokens)
        if not tokens:
            continue
        events = m.get("events") or []
        event_id = events[0]["id"] if events else None
        vol = m.get("volumeNum")
        if vol is None:
            continue
        rows.append({"market_id": m["id"], "event_id": event_id, "token": tokens[0], "volume_num": float(vol)})

    df = pl.DataFrame(rows)
    print(f"markets with usable token+volume: {df.height}")

    event_ids = [e for e in df["event_id"].unique().to_list() if e is not None]
    tags_by_event = fetch_event_tags(session, event_ids, cache_dir=CACHE_DIR / "event_tags")
    categories = []
    for eid in df["event_id"].to_list():
        tags = tags_by_event.get(eid, []) if eid else []
        cat, _ = map_category(tags)
        categories.append(cat)
    df = df.with_columns(pl.Series("category", categories))

    df = df.with_columns(
        pl.col("volume_num").qcut(3, labels=["1", "2", "3"], allow_duplicates=True).cast(pl.Utf8).cast(pl.Int8).alias("vol_tercile")
    )

    L = ["=" * 20 + " W4a LIVE /book SAMPLING SPIKE " + "=" * 20]
    L.append(f"\nLive cross-section: {df.height} open markets, volume_num >= {VOLUME_NUM_MIN}, pulled just now.")
    L.append("vol_tercile computed within this whole sample (not within-category), matching build_panel.py's convention.")

    cat_counts = df.group_by("category").len().sort("category")
    L.append("\ncategory counts in the live sample:")
    for r in cat_counts.iter_rows(named=True):
        L.append(f"  {r['category']:<14} {r['len']}")

    print("sampling /book per stratum...")
    result_rows = []
    n_empty = 0
    n_ok = 0
    for cat in sorted(df["category"].unique().to_list()):
        for tercile in (1, 2, 3):
            cell = df.filter((pl.col("category") == cat) & (pl.col("vol_tercile") == tercile))
            sample = cell.sample(n=min(MAX_PER_STRATUM, cell.height), seed=0) if cell.height else cell
            for row in sample.iter_rows(named=True):
                resp = session.get(CLOB_BOOK_URL, params={"token_id": row["token"]}, timeout=15)
                time.sleep(PACING_SECONDS)
                if resp.status_code != 200:
                    n_empty += 1
                    continue
                hs = half_spread_from_book(resp.json())
                if hs is None:
                    n_empty += 1
                    continue
                n_ok += 1
                result_rows.append({"category": cat, "vol_tercile": tercile, "half_spread": hs})

    result = pl.DataFrame(result_rows)
    L.append(f"\n/book calls: {n_ok} usable, {n_empty} empty/missing/error (of {n_ok + n_empty} attempted)")

    L.append("\nhalf-spread per (category, vol_tercile) stratum:")
    summary = (
        result.group_by(["category", "vol_tercile"])
        .agg(pl.col("half_spread").mean().alias("mean_hs"), pl.col("half_spread").median().alias("median_hs"), pl.len().alias("n"))
        .sort(["category", "vol_tercile"])
    )
    for r in summary.iter_rows(named=True):
        L.append(f"  {r['category']:<14} t{r['vol_tercile']}  n={r['n']:<4} mean={r['mean_hs']:.4f}  median={r['median_hs']:.4f}")

    txt = "\n".join(L)
    print(txt)
    REPORT_PATH.write_text(txt)
    summary.write_csv(SUMMARY_CSV_PATH)  # consumed programmatically by w4a_roll_ordering.py
    print(f"\nwrote {REPORT_PATH}")
    print(f"wrote {SUMMARY_CSV_PATH}")


if __name__ == "__main__":
    main()
