import numpy as np
import pytest

from src.strategy.roll import roll_spread_estimate


def test_roll_spread_estimate_too_short_returns_none():
    assert roll_spread_estimate(np.array([100.0])) is None
    assert roll_spread_estimate(np.array([100.0, 100.5])) is None


def test_roll_spread_estimate_recovers_known_spread_from_bid_ask_bounce():
    """Textbook Roll (1984) setup: fundamental price is a driftless
    random walk; observed price = fundamental + (s/2)*q_t, q_t iid +-1
    with equal probability, independent across periods. Population
    result: Cov(dP_t, dP_{t-1}) = -s^2/4, so the estimator should recover
    s within a reasonable tolerance on a large sample."""
    rng = np.random.default_rng(0)
    n = 200_000
    true_spread = 0.04
    fundamental_increments = rng.normal(0, 0.01, n)
    fundamental = 0.50 + np.cumsum(fundamental_increments)
    q = rng.choice([-1.0, 1.0], size=n)
    observed = fundamental + (true_spread / 2) * q

    estimate = roll_spread_estimate(observed)
    assert estimate is not None
    assert estimate == pytest.approx(true_spread, rel=0.15)


def test_roll_spread_estimate_undefined_on_trending_series():
    """A monotonically trending series (constant-step increments) has
    non-negative first-order autocovariance of changes -- Roll must
    return None, not a value computed from a negative number under the
    square root."""
    prices = np.cumsum(np.full(100, 0.01)) + 0.50
    assert roll_spread_estimate(prices) is None


def test_roll_spread_estimate_constant_price_returns_none():
    """No price changes at all -- autocovariance is exactly 0, undefined
    by the >= 0 rule, not a spread of zero."""
    prices = np.full(50, 0.50)
    assert roll_spread_estimate(prices) is None
