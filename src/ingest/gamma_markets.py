"""Windowed pull of Gamma /markets.

All closed markets from 2024-01 through 2027-12, plus one open-ended tail
window (end_date_min=2028-01-01) for long-dated markets that resolved early
but were scheduled to end far beyond the study window. Deduped by market id
across windows (edge case: overlapping windows can return the same market
twice).

Paginates via /markets/keyset + after_cursor, not plain /markets + offset.
Found empirically while running the real full pull: /markets' offset
pagination hard-ceilings around offset=2000 (offset=2010+ -> HTTP 422
"offset too large, use /markets/keyset for deeper pagination"), and
month-over-month market counts climb well past that within the study
window. /markets/keyset returns {"markets": [...], "next_cursor": ...}
instead of a bare list; the continuation param is "after_cursor" —
verified by brute force, since "cursor", "next_cursor", "starting_after",
"page_cursor", and "after" are all silently ignored (HTTP 200, page 1
repeated, no error). fetch_window_pages guards against exactly that failure
mode: if a new page's ids overlap the previous page's, pagination has
stalled and we raise rather than loop forever or silently duplicate data.

Two distinct failure modes were found running this for real, and they're
handled differently on purpose:
- Transient 500s: http.py's tenacity retry (5 attempts, backoff) already
  absorbs these; observed once in production, resolved on its own.
- A persistent, reproducible 500 at one exact cursor (confirmed 5/5 on
  manual retry against the live API — not transient): retrying harder
  doesn't help. fetch_window_pages retries MAX_PAGE_RETRY_ROUNDS times
  (each already internally 5x-retried by http.py), then gives up on this
  cursor, logs a gap record to {cache_dir}/_gaps.jsonl, and returns
  whatever was fetched so far rather than raising or hanging the whole
  pull. It deliberately does NOT try to skip past the bad cursor to
  recover the rest of the window — with an opaque cursor we don't know
  what markets that would silently drop, and an honest logged gap beats a
  silent partial one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

from src.ingest.http import RetryableStatus, get_json_cached, make_session

GAMMA_MARKETS_KEYSET_URL = "https://gamma-api.polymarket.com/markets/keyset"
PAGE_SIZE = 100
VOLUME_NUM_MIN = 10_000
DEFAULT_CACHE_DIR = Path("data/raw/gamma")
MAX_PAGE_RETRY_ROUNDS = 3  # each round already retries 5x internally (http.py); this bounds
                            # how many rounds before giving up on a stuck cursor and logging a gap


@dataclass(frozen=True)
class Window:
    id: str
    end_date_min: str
    end_date_max: str | None  # None for the open-ended tail window


def month_windows(start: tuple[int, int] = (2024, 1), end: tuple[int, int] = (2027, 12)) -> list[Window]:
    """Monthly end_date_min/max windows from `start` through `end`
    (inclusive), plus one open-ended tail window starting the month after
    `end` (end_date_max=None)."""
    windows = []
    y, m = start
    ey, em = end
    while (y, m) <= (ey, em):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        dmin = f"{y:04d}-{m:02d}-01T00:00:00Z"
        dmax = f"{ny:04d}-{nm:02d}-01T00:00:00Z"
        windows.append(Window(id=f"{y:04d}-{m:02d}", end_date_min=dmin, end_date_max=dmax))
        y, m = ny, nm

    tail_y, tail_m = (y, m)  # one past `end`, already advanced by the loop
    tail_dmin = f"{tail_y:04d}-{tail_m:02d}-01T00:00:00Z"
    windows.append(Window(id=f"{tail_y:04d}-open", end_date_min=tail_dmin, end_date_max=None))
    return windows


def _error_body(exc: Exception) -> str:
    if isinstance(exc, RetryableStatus):
        return exc.body
    return str(exc)


def _log_gap(cache_dir: Path, window: Window, cursor: str | None, markets_fetched: int, error: Exception) -> None:
    record = {
        "window_id": window.id,
        "last_successful_cursor": cursor,
        "markets_fetched_before_gap": markets_fetched,
        "error": _error_body(error),
    }
    with (cache_dir / "_gaps.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


def fetch_window_pages(
    session: requests.Session,
    window: Window,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    volume_num_min: int = VOLUME_NUM_MIN,
    page_size: int = PAGE_SIZE,
) -> list[dict]:
    """Pages a single window via /markets/keyset + after_cursor until the
    server signals the end (next_cursor is null/absent, or a short page).
    Each page is cached at {cache_dir}/{window.id}_page{n}.json via
    get_json_cached, so a re-run of an already-complete window makes no HTTP
    calls at all.

    Raises RuntimeError if a new page's ids overlap the previous page's (see
    module docstring: a wrong after_cursor param is silently ignored by the
    server rather than erroring) — this one stays loud on purpose.

    If the same cursor fails MAX_PAGE_RETRY_ROUNDS times in a row (the
    persistent-500 failure mode, distinct from the above), gives up on this
    window: logs a gap record to {cache_dir}/_gaps.jsonl and returns
    whatever was fetched so far, rather than raising or hanging the pull."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    cursor: str | None = None
    previous_ids: set[str] = set()
    page_num = 0

    while True:
        params = {
            "closed": "true",
            "limit": page_size,
            "end_date_min": window.end_date_min,
            "volume_num_min": volume_num_min,
        }
        if window.end_date_max is not None:
            params["end_date_max"] = window.end_date_max
        if cursor is not None:
            params["after_cursor"] = cursor

        cache_path = cache_dir / f"{window.id}_page{page_num:05d}.json"

        data = None
        last_exc: Exception | None = None
        for _ in range(MAX_PAGE_RETRY_ROUNDS):
            try:
                data = get_json_cached(session, GAMMA_MARKETS_KEYSET_URL, params, cache_path)
                break
            except (requests.exceptions.RequestException, RetryableStatus) as exc:
                last_exc = exc

        if data is None:
            print(
                f"  [GAP] {window.id}: giving up after {MAX_PAGE_RETRY_ROUNDS} rounds at "
                f"cursor={cursor!r} ({len(out)} markets fetched before the gap): {last_exc}"
            )
            _log_gap(cache_dir, window, cursor, len(out), last_exc)
            break

        page = data.get("markets", [])
        page_ids = {m.get("id") for m in page}
        if page_ids and page_ids & previous_ids:
            raise RuntimeError(
                f"keyset pagination stalled for window {window.id!r} at page {page_num}: "
                "new page's ids overlap the previous page's — after_cursor is likely "
                "being ignored by the server (check the param name)."
            )

        out.extend(page)
        previous_ids = page_ids

        next_cursor = data.get("next_cursor")
        if not next_cursor or len(page) < page_size:
            break
        cursor = next_cursor
        page_num += 1

    return out


def pull_gamma_universe(
    cache_dir: Path = DEFAULT_CACHE_DIR, session: requests.Session | None = None
) -> list[dict]:
    """Pulls all windows, dedupes by market id across windows, and prints
    per-window counts plus the duplicate count removed."""
    if session is None:
        session = make_session()

    seen: dict[str, dict] = {}
    duplicates = 0
    for window in month_windows():
        markets = fetch_window_pages(session, window, cache_dir=cache_dir)
        print(f"  {window.id}: pulled {len(markets)} markets")
        for m in markets:
            mid = m.get("id")
            if mid in seen:
                duplicates += 1
            else:
                seen[mid] = m

    print(f"total distinct markets: {len(seen)}  (duplicates removed: {duplicates})")
    return list(seen.values())
