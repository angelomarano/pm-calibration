"""Shared prep for every W2 calibration module (reliability, Brier/Murphy,
the calibration regression). Loads the panel through the sanctioned,
OOS-locked src.panel.io.load_panel, then drops the two row categories that
were deliberately kept in the panel (docs/M3_SPEC_ADDENDUM.md §3) but must
exit here: y is null, or resolution_ambiguous is True. Every W2 report
counts what was dropped, per docs/W2_SPEC_ADDENDUM.md §1.

The two drop conditions are counted independently against the loaded
frame, not sequentially, and dropped_total is the UNION of the two, not
their sum. In the real panel (2026-07-13 pull) they are exactly
coincident — all 78 y-null rows are the same 78 resolution_ambiguous=True
rows, and vice versa (M1's resolve_y always sets them together) — but
this module doesn't assume that stays true; counting independently means
a future change that breaks the coincidence still gets reported
correctly rather than silently under-counted.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.panel.io import load_panel

DEFAULT_P1_PATH = Path("data/panel/p1.parquet")


def load_calibration_frame(path: Path = DEFAULT_P1_PATH) -> tuple[pl.DataFrame, dict]:
    """Returns (frame, stats). stats = {loaded, dropped_null_y,
    dropped_ambiguous, dropped_total, kept}."""
    loaded = load_panel(path)

    dropped_null_y = loaded.filter(pl.col("y").is_null()).height
    dropped_ambiguous = loaded.filter(pl.col("resolution_ambiguous")).height

    kept = loaded.filter(pl.col("y").is_not_null() & ~pl.col("resolution_ambiguous"))

    stats = {
        "loaded": loaded.height,
        "dropped_null_y": dropped_null_y,
        "dropped_ambiguous": dropped_ambiguous,
        "dropped_total": loaded.height - kept.height,
        "kept": kept.height,
    }
    return kept, stats
