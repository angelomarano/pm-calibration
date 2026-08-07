from datetime import datetime, timezone

import pytest

from src.strategy.costs import (
    BROAD_V2_ACTIVATION,
    CATEGORY_FEE_ACTIVATION,
    CATEGORY_FEE_RATE,
    carry_cost,
    fee_activation_date,
    fee_cost,
    is_fee_bearing,
    spread_haircut,
    total_cost,
)


def test_fee_activation_date_known_categories():
    assert fee_activation_date("Crypto") == datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert fee_activation_date("Sports") == datetime(2026, 2, 18, tzinfo=timezone.utc)


def test_fee_activation_date_defaults_to_broad_v2():
    for cat in ("Politics", "Econ/Finance", "Culture", "Geopolitics", "Other"):
        assert fee_activation_date(cat) == BROAD_V2_ACTIVATION


def test_is_fee_bearing_before_and_after_activation():
    before = datetime(2026, 1, 4, tzinfo=timezone.utc)
    after = datetime(2026, 1, 6, tzinfo=timezone.utc)
    assert not is_fee_bearing("Crypto", before)
    assert is_fee_bearing("Crypto", after)


def test_is_fee_bearing_in_sample_always_false():
    """Every in-sample (2024-2025) market predates every activation date
    -- no fee regime existed yet, for any category."""
    in_sample_date = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for cat in CATEGORY_FEE_RATE:
        assert not is_fee_bearing(cat, in_sample_date)


def test_fee_cost_in_sample_market_is_all_zero():
    in_sample_date = datetime(2024, 3, 1, tzinfo=timezone.utc)
    fees = fee_cost("Crypto", in_sample_date, price=0.05, taker_base_fee=1000.0, notional=100.0)
    assert fees == {"base_contract": 0.0, "base_documented": 0.0, "upper_contract": 0.0, "upper_documented": 0.0}


def test_fee_cost_hand_computed_at_p_05_sports():
    """Sports rate=0.05 (published V2), created after 2026-02-18. At
    p=0.05: contract mult = min(0.05,0.95)/0.05 = 1.0; documented mult =
    0.05*0.95 = 0.0475."""
    created = datetime(2026, 3, 1, tzinfo=timezone.utc)
    fees = fee_cost("Sports", created, price=0.05, taker_base_fee=1000.0, notional=100.0)
    assert fees["base_contract"] == pytest.approx(100.0 * 0.05 * 1.0)
    assert fees["base_documented"] == pytest.approx(100.0 * 0.05 * 0.0475)


def test_fee_cost_contract_over_documented_ratio_is_1_over_p_times_1_minus_p():
    """The contract/documented ratio cancels the rate entirely -- it's
    1/(p*(1-p)), independent of category. Spot-check at R1's two tail
    buckets: ~11x at p=0.10, ~51x at p=0.02."""
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    fees_p10 = fee_cost("Crypto", created, price=0.10, taker_base_fee=1000.0)
    fees_p02 = fee_cost("Crypto", created, price=0.02, taker_base_fee=1000.0)
    ratio_p10 = fees_p10["base_contract"] / fees_p10["base_documented"]
    ratio_p02 = fees_p02["base_contract"] / fees_p02["base_documented"]
    assert ratio_p10 == pytest.approx(1 / (0.10 * 0.90), rel=1e-6)
    assert ratio_p02 == pytest.approx(1 / (0.02 * 0.98), rel=1e-6)
    assert ratio_p02 > ratio_p10  # deeper tail -> wider dispute


def test_fee_cost_upper_band_uses_ingested_taker_base_fee():
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    fees = fee_cost("Geopolitics", created, price=0.05, taker_base_fee=1000.0, notional=100.0)
    # base rate for Geopolitics is 0 (published exemption), but the ingested-field
    # upper bound is NOT zero -- exactly the discrepancy this bound exists to surface
    assert fees["base_contract"] == 0.0
    assert fees["base_documented"] == 0.0
    assert fees["upper_contract"] == pytest.approx(100.0 * 0.10 * 1.0)


def test_geopolitics_base_rate_is_zero():
    assert CATEGORY_FEE_RATE["Geopolitics"] == 0.0


def test_spread_haircut_hand_computed():
    assert spread_haircut(half_spread=0.02, band_multiplier=1.0, notional=100.0) == pytest.approx(2.0)
    assert spread_haircut(half_spread=0.02, band_multiplier=0.5, notional=100.0) == pytest.approx(1.0)
    assert spread_haircut(half_spread=0.02, band_multiplier=2.0, notional=100.0) == pytest.approx(4.0)


def test_carry_cost_hand_computed():
    # 5% annual rate, 73 days held, notional 100 -> 100 * 0.05 * (73/365) = 1.0
    assert carry_cost(days_held=73, annual_rate_pct=5.0, notional=100.0) == pytest.approx(1.0)


def test_carry_cost_zero_days_is_zero():
    assert carry_cost(days_held=0, annual_rate_pct=5.0, notional=100.0) == 0.0


def test_total_cost_components_sum_to_total():
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    result = total_cost(
        category="Sports",
        created_at=created,
        price=0.05,
        taker_base_fee=1000.0,
        half_spread=0.02,
        band_multiplier=1.0,
        days_held=30,
        annual_rate_pct=5.0,
        notional=100.0,
    )
    for key in ("base_contract", "base_documented", "upper_contract", "upper_documented"):
        expected = result[f"fee_{key}"] + result["spread"] + result["carry"]
        assert result[f"total_{key}"] == pytest.approx(expected)


def test_total_cost_in_sample_has_zero_fee_but_nonzero_spread_and_carry():
    in_sample_date = datetime(2024, 6, 1, tzinfo=timezone.utc)
    result = total_cost(
        category="Sports",
        created_at=in_sample_date,
        price=0.05,
        taker_base_fee=1000.0,
        half_spread=0.02,
        band_multiplier=1.0,
        days_held=30,
        annual_rate_pct=5.0,
        notional=100.0,
    )
    assert result["fee_base_contract"] == 0.0
    assert result["spread"] > 0.0
    assert result["carry"] > 0.0
