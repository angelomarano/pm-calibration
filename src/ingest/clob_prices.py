"""CLOB /prices-history pull for panel-eligible markets.

/prices-history?market={token_id}&interval=max&fidelity={f} returns
{"history": [{"t": epoch_s, "p": float}, ...]}. Resolved markets only serve
coarse buckets: fidelity 1440 works, 720 as fallback (verified at Gate A;
sub-12h fidelities return empty). Empty responses are classified amm_era
(enable_order_book=False or volume_clob under 1% of volume_num) vs
unexplained, per docs/W1_SPEC.md §M2 — unexplained >= 1% is a stop-and-
investigate acceptance bar, not just a report line.
"""

from __future__ import annotations

from pathlib import Path

import requests

from src.ingest.http import get_json_cached

CLOB_PRICES_URL = "https://clob.polymarket.com/prices-history"
FIDELITIES = (1440, 720)
DEFAULT_CACHE_DIR = Path("data/raw/prices")
AMM_VOLUME_CLOB_SHARE_MIN = 0.01  # volume_clob below 1% of volume_num => AMM-era, not unexplained


def fetch_price_history(
    session: requests.Session, token_id: str, cache_dir: Path = DEFAULT_CACHE_DIR
) -> tuple[list[dict], int | None]:
    """Tries fidelity 1440, falls back to 720. Returns (history, fidelity_used);
    fidelity_used is None if both come back empty."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    for fidelity in FIDELITIES:
        cache_path = cache_dir / f"{token_id}_{fidelity}.json"
        params = {"market": token_id, "interval": "max", "fidelity": fidelity}
        data = get_json_cached(session, CLOB_PRICES_URL, params, cache_path)
        history = (data or {}).get("history") or []
        if history:
            return history, fidelity
    return [], None


def classify_empty(enable_order_book: bool, volume_clob: float, volume_num: float) -> str:
    """"amm_era" if enable_order_book is False or volume_clob is under 1% of
    volume_num; "unexplained" otherwise."""
    if not enable_order_book:
        return "amm_era"
    if volume_num and volume_clob < AMM_VOLUME_CLOB_SHARE_MIN * volume_num:
        return "amm_era"
    return "unexplained"
