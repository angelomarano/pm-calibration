"""Orchestration: panel-eligible markets -> data/panel/prices.parquet.

Only markets with panel_eligible=True get a CLOB price-history call — P1
and P2 structurally cannot include a market with scheduled_life_hours < 168h
(see DECISIONS.md), so fetching prices for the rest would spend a CLOB call
per market the panel builder (M3) discards anyway.

Same stuck-cursor-style discipline as gamma_markets.py: if a token fails
MAX_TOKEN_RETRY_ROUNDS times in a row (each round already 5x-retried
internally by http.py), give up on that one market, log a gap record to
{cache_dir}/_gaps.jsonl, and move to the next market rather than blocking
the whole overnight run on one bad token.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import polars as pl
import requests

from src.ingest.clob_prices import classify_empty, fetch_price_history
from src.ingest.http import RetryableStatus

DEFAULT_OUTPUT_PATH = Path("data/panel/prices.parquet")
DEFAULT_CACHE_DIR = Path("data/raw/prices")
MAX_TOKEN_RETRY_ROUNDS = 3  # mirrors gamma_markets.py's MAX_PAGE_RETRY_ROUNDS
PRICE_SCHEMA: dict[str, pl.PolarsDataType] = {
    "clob_token_leg": pl.Utf8,
    "ts": pl.Datetime("us", "UTC"),
    "p": pl.Float64,
}


def _error_body(exc: Exception) -> str:
    if isinstance(exc, RetryableStatus):
        return exc.body
    return str(exc)


def _log_gap(cache_dir: Path, market_id, token_id: str, error: Exception) -> None:
    record = {"market_id": market_id, "clob_token_leg": token_id, "error": _error_body(error)}
    with (cache_dir / "_gaps.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


def fetch_prices_for_markets(
    markets: list[dict],
    session: requests.Session,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    progress_every: int = 500,
) -> tuple[list[dict], dict]:
    """`markets` is a list of dicts, each needing market_id, clob_token_leg,
    enable_order_book, volume_clob, volume_num. Returns (price_rows, stats):
    stats has total/has_history/amm_era/unexplained/gaps counts and
    elapsed_s. Prints progress every `progress_every` markets with an ETA."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    price_rows: list[dict] = []
    has_history = 0
    amm_era = 0
    unexplained = 0
    gaps = 0
    t0 = time.time()
    total = len(markets)

    for i, m in enumerate(markets, start=1):
        fetched_ok = False
        history: list[dict] = []
        last_exc: Exception | None = None
        for _ in range(MAX_TOKEN_RETRY_ROUNDS):
            try:
                history, _fidelity = fetch_price_history(session, m["clob_token_leg"], cache_dir=cache_dir)
                fetched_ok = True
                break
            except (requests.exceptions.RequestException, RetryableStatus) as exc:
                last_exc = exc

        if not fetched_ok:
            gaps += 1
            print(f"  [GAP] market_id={m.get('market_id')} token={m['clob_token_leg']}: {last_exc}")
            _log_gap(cache_dir, m.get("market_id"), m["clob_token_leg"], last_exc)
        elif history:
            has_history += 1
            for point in history:
                t = float(point["t"])
                if t > 1e12:  # normalize ms -> s
                    t /= 1000.0
                price_rows.append(
                    {
                        "clob_token_leg": m["clob_token_leg"],
                        "ts": dt.datetime.fromtimestamp(t, tz=dt.timezone.utc),
                        "p": float(point["p"]),
                    }
                )
        else:
            kind = classify_empty(m["enable_order_book"], m["volume_clob"], m["volume_num"])
            if kind == "amm_era":
                amm_era += 1
            else:
                unexplained += 1

        if i % progress_every == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed else 0
            eta = (total - i) / rate if rate else float("inf")
            print(
                f"  [{i}/{total}] has_history={has_history} amm_era={amm_era} "
                f"unexplained={unexplained} gaps={gaps}  elapsed={elapsed:.0f}s  ETA={eta:.0f}s"
            )

    stats = {
        "total": total,
        "has_history": has_history,
        "amm_era": amm_era,
        "unexplained": unexplained,
        "gaps": gaps,
        "elapsed_s": time.time() - t0,
    }
    return price_rows, stats


def build_prices_table(price_rows: list[dict]) -> pl.DataFrame:
    if not price_rows:
        return pl.DataFrame(schema=PRICE_SCHEMA)
    return pl.DataFrame(price_rows, schema=PRICE_SCHEMA)
