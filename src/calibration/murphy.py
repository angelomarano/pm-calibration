"""Brier score, Murphy decomposition, Brier skill score, log-loss (W2c).

Murphy (1973) decomposition, computed on the same fixed decile bins as
W2b's reliability diagrams (src/calibration/reliability.py, via the
shared assign_bin_index):

    Brier = (1/N) sum (p_i - y_i)^2
    REL   = (1/N) sum_k n_k (p_bar_k - o_bar_k)^2   -- calibration term
    RES   = (1/N) sum_k n_k (o_bar_k - o_bar)^2      -- resolution term
    UNC   = o_bar * (1 - o_bar)                       -- uncertainty term
    Identity: Brier = REL - RES + UNC

where p_bar_k / o_bar_k are the mean forecast / outcome in bin k, and
o_bar is the overall base rate. Verified by hand on a trivial 4-point
example before trusting it in code (see test_murphy.py) -- this module
does not re-assert the identity at runtime (redundant cost on every
bootstrap draw); the hand-computed test is the guardrail against a
refactor silently breaking it.

BSS = 1 - Brier/UNC. UNC is algebraically exactly the "always predict the
base rate" reference Brier score, so no separate reference computation is
needed. nan (not an exception) when UNC == 0, per event_bootstrap's NaN
policy -- confirmed empirically that a nan in one stat_fn key does not
invalidate other keys from the same draw (tests/test_bootstrap.py::
test_nan_in_one_key_does_not_invalidate_other_keys_on_the_same_draw).

Log-loss is reported alongside as an unbinned robustness column, not part
of the decomposition, clipped at LOG_LOSS_EPS to avoid log(0) (moot given
the panel's [0.01, 0.99] price_clip, but this function doesn't assume its
caller respects that).

murphy_stat_fn returns all six values in one dict so a single
event_bootstrap call gives mutually consistent CIs for all of them
together, per W2a's design (multi-parameter stats refit once per draw).
"""

from __future__ import annotations

import numpy as np
import polars as pl

from src.calibration.reliability import DECILE_EDGES, assign_bin_index
from src.inference.bootstrap import event_bootstrap

LOG_LOSS_EPS = 1e-12


def murphy_decomposition(p: np.ndarray, y: np.ndarray, edges: list[float] = DECILE_EDGES) -> dict[str, float]:
    """Returns {brier, rel, res, unc} for one (p, y) sample."""
    bin_idx = assign_bin_index(p, edges)
    n = len(p)
    o_bar = float(y.mean())
    unc = o_bar * (1 - o_bar)

    rel = 0.0
    res = 0.0
    for b in np.unique(bin_idx):
        mask = bin_idx == b
        n_k = int(mask.sum())
        p_bar_k = float(p[mask].mean())
        o_bar_k = float(y[mask].mean())
        rel += n_k * (p_bar_k - o_bar_k) ** 2
        res += n_k * (o_bar_k - o_bar) ** 2
    rel /= n
    res /= n

    brier = float(np.mean((p - y) ** 2))
    return {"brier": brier, "rel": rel, "res": res, "unc": unc}


def brier_skill_score(brier: float, unc: float) -> float:
    """1 - brier/unc. nan (not an exception) if unc == 0 -- a degenerate
    all-same-outcome cell, signaling a failed/undefined stat for that
    draw rather than a crash."""
    if unc == 0:
        return float("nan")
    return 1 - brier / unc


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = LOG_LOSS_EPS) -> float:
    p_clipped = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p_clipped) + (1 - y) * np.log(1 - p_clipped)))


def murphy_stat_fn(df: pl.DataFrame, edges: list[float] = DECILE_EDGES) -> dict[str, float]:
    """The stat_fn passed to event_bootstrap: brier/rel/res/unc/bss/
    log_loss computed together from one resampled frame."""
    p = df["p"].to_numpy()
    y = df["y"].to_numpy().astype(float)
    decomp = murphy_decomposition(p, y, edges)
    return {
        **decomp,
        "bss": brier_skill_score(decomp["brier"], decomp["unc"]),
        "log_loss": log_loss(p, y),
    }


def build_murphy_report(df: pl.DataFrame, B: int = 2000, seed: int = 0) -> pl.DataFrame:
    """One row per cell (ex_Sports pooled, Sports alone, each category):
    n, and point/ci_low/ci_high for every murphy_stat_fn key. Mirrors
    W2b's build_reliability_report cell structure."""
    cells = {
        "ex_Sports": df.filter(pl.col("category") != "Sports"),
        "Sports": df.filter(pl.col("category") == "Sports"),
    }
    for cat in sorted(df["category"].unique().to_list()):
        cells[cat] = df.filter(pl.col("category") == cat)

    rows = []
    for name, sub in cells.items():
        if sub.height == 0:
            continue
        result = event_bootstrap(sub, murphy_stat_fn, B=B, seed=seed)
        row = {"cell": name, "n": sub.height}
        for key in result.point:
            row[f"{key}_point"] = result.point[key]
            row[f"{key}_ci_low"] = result.ci_low[key]
            row[f"{key}_ci_high"] = result.ci_high[key]
            row[f"{key}_n_valid"] = result.n_valid[key]
        rows.append(row)
    return pl.DataFrame(rows)
