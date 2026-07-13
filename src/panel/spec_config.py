"""Loads config/spec.yaml — snapshot dates, staleness/clip bounds, and the
OOS lock. Snapshot definitions come from here, never hardcoded (CLAUDE.md
rule 7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config/spec.yaml")


@dataclass(frozen=True)
class SpecConfig:
    snapshot_dates: list[datetime]
    p2_horizons_days: list[int]
    staleness_max_hours: float
    price_clip: tuple[float, float]
    oos_locked: bool
    oos_boundary: datetime


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def monthly_snapshot_dates(start: datetime, end: datetime) -> list[datetime]:
    """1st-of-month 00:00 UTC dates, inclusive of both `start` and `end`
    (both must already be 1st-of-month 00:00 UTC values)."""
    dates = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        dates.append(datetime(y, m, 1, tzinfo=timezone.utc))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return dates


def load_spec_config(path: Path = DEFAULT_CONFIG_PATH) -> SpecConfig:
    raw = yaml.safe_load(path.read_text())

    sd = raw["snapshot_dates"]
    if sd["freq"] != "monthly_first":
        raise ValueError(f"unsupported snapshot_dates.freq: {sd['freq']!r}")
    snapshot_dates = monthly_snapshot_dates(_parse_iso(sd["start"]), _parse_iso(sd["end"]))

    return SpecConfig(
        snapshot_dates=snapshot_dates,
        p2_horizons_days=list(raw["p2_horizons_days"]),
        staleness_max_hours=float(raw["staleness_max_hours"]),
        price_clip=tuple(raw["price_clip"]),
        oos_locked=bool(raw["oos_locked"]),
        oos_boundary=_parse_iso(raw["oos_boundary"]),
    )
