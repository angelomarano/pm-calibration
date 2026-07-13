"""The only sanctioned way to read a panel parquet (p1/p2) before W4.

Filters out is_oos=True rows by default. An explicit allow_oos=True still
cannot bypass the lock while config/spec.yaml's oos_locked is true — only
editing that config value can, which is a deliberate, visible, one-line
change any plan participant would notice in a diff (docs/M3_SPEC_ADDENDUM.md
§2).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.panel.spec_config import load_spec_config


def load_panel(path: Path, allow_oos: bool = False) -> pl.DataFrame:
    df = pl.read_parquet(path)
    if not allow_oos:
        return df.filter(~pl.col("is_oos"))

    config = load_spec_config()
    if config.oos_locked:
        raise RuntimeError(
            "OOS lock is active (config/spec.yaml oos_locked=true) — "
            "allow_oos=True cannot bypass it; flip oos_locked to false first."
        )
    return df
