"""Roll (1984) spread estimator -- W4a's cross-sectional consistency check
against the live-book sampling spike (docs/W4_SPEC_ADDENDUM.md §1.4).

Deliberately separate from costs.py: this is a historical-spread
ESTIMATOR used only to check whether the ORDER of strata (which
category/volume-tercile combinations are tighter or wider) agrees between
`prices.parquet`'s daily bars and a live `/book` sample -- it is never
part of the cost model actually applied to a trade, and its output is
never presented as an absolute historical spread level. Daily price
moves are dominated by genuine information, not bid-ask bounce, so an
absolute Roll estimate on daily bars would be a fiction; the ranking is
the only defensible use.

Roll's result: for an efficient-price random walk overlaid with an iid
+-1 bid/ask bounce of full spread s (trade at ask = fundamental + s/2, at
bid = fundamental - s/2, with equal probability, independent across
periods), Cov(dP_t, dP_{t-1}) = -s^2/4 exactly, so s = 2*sqrt(-Cov). The
estimator is undefined whenever the sample autocovariance is >= 0, which
is common on daily data (trending information dominates bounce at that
frequency) -- returns None rather than a nonsensical value under the
square root.
"""

from __future__ import annotations

import numpy as np

AUTOCOV_ZERO_EPS = 1e-10  # floating-point guard: a deterministic zero-autocov series
# (e.g. constant-step trending prices) can land a hair below 0 from summation-order
# noise, many orders of magnitude smaller than any economically meaningful spread


def roll_spread_estimate(prices: np.ndarray) -> float | None:
    """prices: a 1D array of consecutive price points for one market,
    already sorted by timestamp. Returns the Roll spread estimate, or
    None if undefined (fewer than 2 price changes, or non-negative
    first-order autocovariance of consecutive changes)."""
    prices = np.asarray(prices, dtype=float)
    if prices.size < 3:
        return None
    changes = np.diff(prices)
    if changes.size < 2:
        return None

    mean_change = changes.mean()
    centered = changes - mean_change
    autocov = float(np.mean(centered[:-1] * centered[1:]))
    if autocov >= -AUTOCOV_ZERO_EPS:
        return None
    return float(2 * np.sqrt(-autocov))
