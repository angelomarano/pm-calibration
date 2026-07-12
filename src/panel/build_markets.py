"""Orchestration: raw Gamma market JSON -> data/panel/markets.parquet.

Field-presence notes (checked against the 232-market Gate A cache, not
assumed — see docs/W1_SPEC.md §API facts for the rest):
- liquidityNum: present in only 33% of markets -> nullable, null when absent.
- makerBaseFee/takerBaseFee: present in only 35%, but present 9/9 times when
  feesEnabled=True and only sometimes when feesEnabled=False -> default 0.0
  when absent (the value is moot whenever fees are disabled). parse_market
  stores the raw value faithfully even when feesEnabled=False (see
  test_build_markets.py's fee-gating-invariant test) — any actual fee
  computation must gate on fees_enabled first, fee value second; that gating
  belongs to W4's cost code, not to this ingestion layer.
- volumeAmm: present in only 22%; missing usually means "no AMM volume ever"
  (91% of the missing cases have volumeClob == volumeNum exactly) but not
  always (9% have volumeClob != volumeNum with volumeAmm still absent) ->
  kept nullable rather than zero-filled; the discrepancy count is reported
  by build_markets_table.
- startDate: missing in 3/232 (all early-2024 UK election markets) -> falls
  back to createdAt, same pattern as resolution_ts.

resolve_y relaxed: status=="proposed" with degenerate prices and
resolution_ts >24h old is now treated as resolved (not ambiguous), matching
status=="resolved". Empirical driver: 20/232 Gate-A markets (8.6%, above the
<3% acceptance bar) were long-settled 2024 events (Oscars, primaries, Super
Bowl LVIII) permanently stuck in "proposed" — likely because nobody called
on-chain settlement on small/old markets, not because the outcome was
unclear (all 20 checked: 853-913 days old, fully degenerate prices). The 24h
buffer (vs the ~2h UMA dispute window) guards against trusting a market
still inside its actual dispute window if this pipeline is ever rerun
near-live. resolve_y itself stays a pure "status string -> is this
degenerate-price outcome trustworthy" function with no time dependency; the
guard lives here since parse_market already has resolution_ts computed.

panel_eligible (new column): both P1 (monthly calendar snapshots) and P2
(T-7/T-30 from scheduled deadline) require a market to be open days away
from its own creation. A market with scheduled_life_hours < 168 (7 days)
cannot structurally satisfy P2's T-7 leg, and can only hit a P1 monthly
snapshot by near-zero-probability coincidence — this is a resource-
optimization flag for M2 (only panel_eligible markets need a CLOB price-
history call), not a sample restriction: every volume-qualifying market
still gets a full row here regardless of eligibility, for population-size
honesty. Empirically (pooled 2025-09/2026-03/2026-06, 64,559 real markets):
73% of ALL markets and 99% of Crypto fall below 168h — Crypto's category
share collapses from 47.8% of all markets to 2.2% of the panel-eligible
population, while Sports rises to 70.6% of it. This resolves the "does
Crypto need its own ex-Crypto headline treatment" question from the same
investigation: it doesn't — the panel design already structurally excludes
almost all of it, no separate category treatment needed.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import requests

from src.ingest.gamma_events import fetch_event_tags
from src.ingest.gamma_markets import pull_gamma_universe
from src.ingest.http import make_session
from src.panel.categories import map_category
from src.panel.resolution import leg_index, parse_ts, resolve_y

DEFAULT_OUTPUT_PATH = Path("data/panel/markets.parquet")
DISPUTE_WINDOW_HOURS = 24  # UMA's actual dispute window is ~2h; wide margin
PANEL_ELIGIBLE_MIN_HOURS = 168.0  # 7 days — structural minimum for P2's T-7 leg (see module docstring)

SCHEMA: dict[str, pl.PolarsDataType] = {
    "market_id": pl.Utf8,
    "condition_id": pl.Utf8,
    "question": pl.Utf8,
    "slug": pl.Utf8,
    "event_id": pl.Utf8,
    "n_events": pl.Int32,
    "category": pl.Utf8,
    "tags_raw": pl.List(pl.Utf8),
    "outcomes": pl.List(pl.Utf8),
    "leg_idx": pl.Int8,
    "leg_label": pl.Utf8,
    "clob_token_leg": pl.Utf8,
    "outcome_prices": pl.List(pl.Float64),
    "y": pl.Int8,
    "resolution_ambiguous": pl.Boolean,
    "uma_resolution_status": pl.Utf8,
    "volume_num": pl.Float64,
    "liquidity_num": pl.Float64,
    "volume_clob": pl.Float64,
    "volume_amm": pl.Float64,
    "created_at": pl.Datetime("us", "UTC"),
    "start_date": pl.Datetime("us", "UTC"),
    "end_date_sched": pl.Datetime("us", "UTC"),
    "scheduled_life_hours": pl.Float64,
    "panel_eligible": pl.Boolean,
    "uma_end_date": pl.Datetime("us", "UTC"),
    "closed_time": pl.Datetime("us", "UTC"),
    "resolution_ts": pl.Datetime("us", "UTC"),
    "fees_enabled": pl.Boolean,
    "maker_base_fee": pl.Float64,
    "taker_base_fee": pl.Float64,
    "enable_order_book": pl.Boolean,
    "tick_size": pl.Float64,
    "archived": pl.Boolean,
    "restricted": pl.Boolean,
    "neg_risk": pl.Boolean,
}


def _pjson(x):
    """Gamma often returns lists encoded as JSON strings."""
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return None
    return x


def parse_market(
    raw: dict, event_tags: dict[str, list[str]], reference_time: datetime | None = None
) -> dict | None:
    """Parses one raw Gamma market dict into one output row dict. Returns
    None (caller counts a skip) if outcomes/clobTokenIds are missing, empty,
    length-mismatched, or not exactly length 2 (binary-market assumption).

    `reference_time` is the "now" used for the proposed-status dispute-window
    guard (see module docstring); defaults to the real clock, override with a
    fixed value for deterministic tests."""
    outcomes = _pjson(raw.get("outcomes"))
    tokens = _pjson(raw.get("clobTokenIds"))
    if not outcomes or not tokens or len(outcomes) != 2 or len(tokens) != 2:
        return None

    prices_raw = _pjson(raw.get("outcomePrices")) or []
    try:
        outcome_prices = [float(p) for p in prices_raw]
    except (TypeError, ValueError):
        return None

    leg_idx, leg_label = leg_index(outcomes)
    clob_token_leg = tokens[leg_idx]
    uma_status = raw.get("umaResolutionStatus")

    created_at = parse_ts(raw.get("createdAt"))
    start_date = parse_ts(raw.get("startDate")) or created_at
    end_date_sched = parse_ts(raw.get("endDate"))
    uma_end_date = parse_ts(raw.get("umaEndDate"))
    closed_time = parse_ts(raw.get("closedTime"))
    resolution_ts = uma_end_date or closed_time

    scheduled_life_hours = (
        (end_date_sched - start_date).total_seconds() / 3600
        if end_date_sched is not None and start_date is not None
        else None
    )
    panel_eligible = scheduled_life_hours is not None and scheduled_life_hours >= PANEL_ELIGIBLE_MIN_HOURS

    y, resolution_ambiguous = resolve_y(outcome_prices, leg_idx, uma_status)
    if resolution_ambiguous and uma_status == "proposed" and resolution_ts is not None:
        now = reference_time or datetime.now(timezone.utc)
        if now - resolution_ts > timedelta(hours=DISPUTE_WINDOW_HOURS):
            relaxed_y, relaxed_ambiguous = resolve_y(outcome_prices, leg_idx, "resolved")
            if not relaxed_ambiguous:
                y, resolution_ambiguous = relaxed_y, relaxed_ambiguous

    events = raw.get("events") or []
    event_id = events[0]["id"] if events else None
    tags = event_tags.get(event_id, []) if event_id else []
    category, tags_raw = map_category(tags)

    return {
        "market_id": raw.get("id"),
        "condition_id": raw.get("conditionId"),
        "question": raw.get("question"),
        "slug": raw.get("slug"),
        "event_id": event_id,
        "n_events": len(events),
        "category": category,
        "tags_raw": tags_raw,
        "outcomes": outcomes,
        "leg_idx": leg_idx,
        "leg_label": leg_label,
        "clob_token_leg": clob_token_leg,
        "outcome_prices": outcome_prices,
        "y": y,
        "resolution_ambiguous": resolution_ambiguous,
        "uma_resolution_status": uma_status,
        "volume_num": float(raw.get("volumeNum") or 0.0),
        "liquidity_num": float(raw["liquidityNum"]) if raw.get("liquidityNum") is not None else None,
        "volume_clob": float(raw.get("volumeClob") or 0.0),
        "volume_amm": float(raw["volumeAmm"]) if raw.get("volumeAmm") is not None else None,
        "created_at": created_at,
        "start_date": start_date,
        "end_date_sched": end_date_sched,
        "scheduled_life_hours": scheduled_life_hours,
        "panel_eligible": panel_eligible,
        "uma_end_date": uma_end_date,
        "closed_time": closed_time,
        "resolution_ts": resolution_ts,
        "fees_enabled": bool(raw.get("feesEnabled", False)),
        "maker_base_fee": float(raw["makerBaseFee"]) if raw.get("makerBaseFee") is not None else 0.0,
        "taker_base_fee": float(raw["takerBaseFee"]) if raw.get("takerBaseFee") is not None else 0.0,
        "enable_order_book": bool(raw.get("enableOrderBook", False)),
        "tick_size": float(raw.get("orderPriceMinTickSize") or 0.0),
        "archived": bool(raw.get("archived", False)),
        "restricted": bool(raw.get("restricted", False)),
        "neg_risk": raw.get("negRisk"),
    }


def build_markets_table(
    raw_markets: list[dict],
    event_tags: dict[str, list[str]],
    reference_time: datetime | None = None,
) -> pl.DataFrame:
    """Parses all markets, raises if a duplicate market_id survives (the
    ingest layer should already guarantee zero — a duplicate here means an
    upstream bug), and prints acceptance stats."""
    rows = []
    skipped = 0
    for raw in raw_markets:
        row = parse_market(raw, event_tags, reference_time=reference_time)
        if row is None:
            skipped += 1
        else:
            rows.append(row)

    id_counts = Counter(r["market_id"] for r in rows)
    dupes = sorted(mid for mid, count in id_counts.items() if count > 1)
    if dupes:
        raise ValueError(f"duplicate market_id at parse time (ingest-layer bug): {dupes}")

    df = pl.DataFrame(rows, schema=SCHEMA)

    print("rows by end_date_sched year:")
    by_year = (
        df.with_columns(pl.col("end_date_sched").dt.year().alias("_year"))
        .group_by("_year")
        .len()
        .sort("_year")
    )
    for row in by_year.iter_rows(named=True):
        print(f"  {row['_year']}: {row['len']}")

    print(f"parse failures (skipped): {skipped}")
    print(f"ambiguous-resolution share: {df['resolution_ambiguous'].mean():.1%}")
    print(f"unmapped-category share (category == 'Other'): {(df['category'] == 'Other').mean():.1%}")
    print(f"panel_eligible share (scheduled_life_hours >= {PANEL_ELIGIBLE_MIN_HOURS:.0f}h): {df['panel_eligible'].mean():.1%}")

    amm_discrepancy = df.filter(
        pl.col("volume_amm").is_null() & (pl.col("volume_clob") != pl.col("volume_num"))
    ).height
    print(f"volume_amm missing but volume_clob != volume_num: {amm_discrepancy}")

    return df


def main() -> None:
    session = make_session()
    raw_markets = pull_gamma_universe(session=session)

    event_ids = sorted({m["events"][0]["id"] for m in raw_markets if m.get("events")})
    event_tags = fetch_event_tags(session, event_ids)

    df = build_markets_table(raw_markets, event_tags)

    DEFAULT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(DEFAULT_OUTPUT_PATH)
    print(f"wrote {df.height} rows to {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
