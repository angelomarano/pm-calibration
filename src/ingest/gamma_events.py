"""Batched pull of event tags from gamma-api.polymarket.com/events.

/markets' nested `events` list carries no `tags` field (verified against the
Gate A cache — event0.keys() has no "tags"); tags only show up when hitting
/events/{id} directly. This module fetches them separately per event_id,
batched up to the server's cap of 100 ids per request (verified empirically:
requesting 126 ids in one call returns HTTP 422 "expected array length <=
100"; `id=` is a repeatable query param and `limit` controls page size up to
that cap).
"""

from __future__ import annotations

from pathlib import Path

import requests

from src.ingest.http import get_json_cached

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
BATCH_SIZE = 100
DEFAULT_CACHE_DIR = Path("data/raw/gamma_events")


def _batches(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_event_tags(
    session: requests.Session,
    event_ids: list[str],
    cache_dir: Path = DEFAULT_CACHE_DIR,
    batch_size: int = BATCH_SIZE,
) -> dict[str, list[str]]:
    """Returns {event_id: [tag_label, ...]} for every id in `event_ids`.
    One cached, resumable call per batch of up to `batch_size` ids."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    ids = sorted(set(event_ids))
    tags_by_event: dict[str, list[str]] = {}

    for batch in _batches(ids, batch_size):
        cache_path = cache_dir / f"batch_{batch[0]}_{batch[-1]}_{len(batch)}.json"
        params = [("id", event_id) for event_id in batch] + [("limit", batch_size)]
        events = get_json_cached(session, GAMMA_EVENTS_URL, params, cache_path)
        for event in events:
            labels = [t.get("label") for t in (event.get("tags") or []) if t.get("label")]
            tags_by_event[event["id"]] = labels

    return tags_by_event
