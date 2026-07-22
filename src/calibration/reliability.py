"""Reliability diagrams + isotonic overlay (W2b).

Decile bins on p (fixed edges 0.0..1.0, left-closed/right-open except the
last bin which is closed both ends). After M3's price_clip to [0.01, 0.99]
the last-bin closed-at-1.0 branch is never actually hit by real data
(verified below, not assumed) -- kept for correctness/generality anyway,
since bin_reliability doesn't know about the panel's clip bounds and
shouldn't have to.

Isotonic overlay uses a hand-rolled pool-adjacent-violators (PAVA)
implementation rather than pulling in scikit-learn for one function.

IMPORTANT -- Wilson intervals are NOT the project's declared confidence
interval. They're the standard visual convention for reliability-diagram
error bars, and they assume independent observations within each bin.
That assumption is false here: rows from the same event can land in the
same bin or different bins, which is exactly why src/inference/bootstrap.py
exists. Any interval reported as inferential in W2c/W2d comes from
event_bootstrap, never from this module. The figures themselves carry a
caption saying so, so nobody reads the error bars as the real CIs.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

DECILE_EDGES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
DEFAULT_FIGURES_DIR = Path("reports/figures")

WILSON_CAPTION = (
    "Error bars: Wilson score interval per bin (assumes independent observations within-bin) -- "
    "a standard visual convention, NOT the project's declared CI. Inferential CIs come from the "
    "event-clustered bootstrap (src/inference/bootstrap.py), reported in W2c/W2d."
)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Visual convention
    only -- see module docstring."""
    if n == 0:
        return (float("nan"), float("nan"))
    p_hat = successes / n
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    half_width = z * ((p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) ** 0.5) / denom
    return (max(0.0, center - half_width), min(1.0, center + half_width))


def assign_bin_index(p: np.ndarray, edges: list[float] = DECILE_EDGES) -> np.ndarray:
    """Bin index per value of p: left-closed/right-open
    ([edges[i], edges[i+1])), except the last bin, closed on both ends
    ([edges[-2], edges[-1]]). Shared by bin_reliability (W2b) and the
    Murphy decomposition (W2c, src/calibration/murphy.py) -- both need
    the exact same binning convention, not just the same edge list."""
    interior = np.array(edges[1:-1])
    return np.searchsorted(interior, p, side="right")


def bin_reliability(df: pl.DataFrame, edges: list[float] = DECILE_EDGES) -> pl.DataFrame:
    """Per decile bin: n, mean_p, empirical_freq (share y==1), wilson_low,
    wilson_high. Bins are left-closed/right-open ([edges[i], edges[i+1])),
    except the last bin, which is closed on both ends
    ([edges[-2], edges[-1]])."""
    p = df["p"].to_numpy()
    y = df["y"].to_numpy().astype(float)
    n_bins = len(edges) - 1
    bin_idx = assign_bin_index(p, edges)

    rows = []
    for b in range(n_bins):
        mask = bin_idx == b
        n = int(mask.sum())
        row = {"bin": b, "bin_low": edges[b], "bin_high": edges[b + 1], "n": n}
        if n == 0:
            row.update(mean_p=None, empirical_freq=None, wilson_low=None, wilson_high=None)
        else:
            successes = int(y[mask].sum())
            lo, hi = wilson_interval(successes, n)
            row.update(mean_p=float(p[mask].mean()), empirical_freq=successes / n, wilson_low=lo, wilson_high=hi)
        rows.append(row)
    return pl.DataFrame(rows)


def pava_isotonic(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Hand-rolled pool-adjacent-violators isotonic regression. Returns
    the fitted monotonic-non-decreasing E[y|p] curve, aligned to `p`
    sorted ascending (pair with np.sort(p) for plotting, not the
    original-order p)."""
    order = np.argsort(p, kind="stable")
    y_sorted = y[order].astype(float)
    n = len(y_sorted)

    block_value: list[float] = []
    block_count: list[int] = []
    for i in range(n):
        v, c = float(y_sorted[i]), 1
        while block_value and block_value[-1] > v:
            pv, pc = block_value.pop(), block_count.pop()
            v = (v * c + pv * pc) / (c + pc)
            c += pc
        block_value.append(v)
        block_count.append(c)

    fitted = np.empty(n, dtype=float)
    idx = 0
    for v, c in zip(block_value, block_count):
        fitted[idx : idx + c] = v
        idx += c
    return fitted


def plot_reliability_diagram(
    binned: pl.DataFrame, p_sorted: np.ndarray, isotonic_fit: np.ndarray, title: str, out_path: Path
) -> None:
    """Renders and saves the figure. Overwrites out_path every call --
    filenames are stable (not timestamped/versioned) by design, so
    regenerating the report doesn't accumulate stale figures or add diff
    noise. See WILSON_CAPTION / module docstring re: what the error bars
    are and are not."""
    valid = binned.filter(pl.col("n") > 0)

    fig, ax = plt.subplots(figsize=(6, 6))
    if valid.height > 0:
        mean_p = valid["mean_p"].to_numpy()
        freq = valid["empirical_freq"].to_numpy()
        lo = valid["wilson_low"].to_numpy()
        hi = valid["wilson_high"].to_numpy()
        yerr = np.vstack([freq - lo, hi - freq])
        ax.errorbar(mean_p, freq, yerr=yerr, fmt="o", capsize=3, label="Binned empirical freq (Wilson, visual only)")

    ax.plot(p_sorted, isotonic_fit, "-", label="Isotonic fit")
    ax.plot([0, 1], [0, 1], "--", color="gray", label="Perfect calibration")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted probability (p)")
    ax.set_ylabel("Empirical frequency of y=1")
    ax.set_title(title)
    ax.legend(loc="upper left", fontsize=8)
    fig.text(0.5, 0.01, WILSON_CAPTION, ha="center", fontsize=6, wrap=True)
    fig.tight_layout(rect=(0, 0.06, 1, 1))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _slug(name: str) -> str:
    return name.lower().replace("/", "_").replace(" ", "_")


def build_reliability_report(df: pl.DataFrame, out_dir: Path = DEFAULT_FIGURES_DIR) -> dict:
    """Builds one reliability diagram per cell: pooled ex-Sports, Sports
    alone, and each individual category. Returns {cell_name: {n, out_path}}.
    Filenames are STABLE (f"{slug}_reliability.png"), overwritten on every
    run -- see plot_reliability_diagram."""
    cells = {
        "ex_Sports": df.filter(pl.col("category") != "Sports"),
        "Sports": df.filter(pl.col("category") == "Sports"),
    }
    for cat in sorted(df["category"].unique().to_list()):
        cells[cat] = df.filter(pl.col("category") == cat)

    results = {}
    for name, sub in cells.items():
        if sub.height == 0:
            continue
        binned = bin_reliability(sub)
        p = sub["p"].to_numpy()
        y = sub["y"].to_numpy().astype(float)
        fitted = pava_isotonic(p, y)
        p_sorted = np.sort(p)
        out_path = out_dir / f"{_slug(name)}_reliability.png"
        plot_reliability_diagram(binned, p_sorted, fitted, title=f"Reliability diagram — {name}", out_path=out_path)
        results[name] = {"n": sub.height, "out_path": str(out_path)}
    return results
