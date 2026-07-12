"""Leg/outcome/resolution parsing. Pure functions, no I/O."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(raw: str | None) -> datetime | None:
    """Defensive ISO parser: handles clean ISO (umaEndDate/endDate/startDate/
    createdAt) and the closedTime quirk ("2024-06-01 06:40:11+00" — space
    separator, +00 offset missing the minutes component)."""
    if not raw:
        return None
    s = raw.strip().replace(" ", "T").replace("Z", "+00:00")
    if len(s) > 3 and s[-3] in "+-" and ":" not in s[-3:]:
        s += ":00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def leg_index(outcomes: list[str]) -> tuple[int, str]:
    """Index of "Yes" in outcomes if present, else 0 (deliberate,
    outcome-blind convention). Returns (leg_idx, leg_label)."""
    idx = outcomes.index("Yes") if "Yes" in outcomes else 0
    return idx, outcomes[idx]


def resolve_y(outcome_prices: list[float], leg_idx: int, uma_status: str | None) -> tuple[int | None, bool]:
    """(y, resolution_ambiguous). y=1 if the leg's price >=0.99, 0 if the
    other leg's price >=0.99, else None + ambiguous=True. Also ambiguous if
    uma_status != "resolved" or there aren't exactly 2 prices (binary-market
    assumption, same guard as the outcomes/clobTokenIds length check)."""
    if uma_status != "resolved" or len(outcome_prices) != 2:
        return None, True
    other_idx = 1 - leg_idx
    if outcome_prices[leg_idx] >= 0.99:
        return 1, False
    if outcome_prices[other_idx] >= 0.99:
        return 0, False
    return None, True


def resolution_timestamp(uma_end_date: str | None, closed_time: str | None) -> datetime | None:
    """Parsed uma_end_date if present, else parsed closed_time."""
    return parse_ts(uma_end_date) or parse_ts(closed_time)
