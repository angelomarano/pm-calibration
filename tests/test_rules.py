from datetime import date, datetime, timezone

import polars as pl
import pytest

from src.strategy.costs import fee_cost
from src.strategy.rules import (
    attach_costs_and_pnl,
    build_r1_positions,
    leg_direction_from_calibration_map,
)


def _row(**overrides) -> dict:
    base = {
        "market_id": "m1",
        "event_id": "e1",
        "category": "Politics",
        "p": 0.5,
        "y": 1,
        "snapshot_date": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "vol_tercile": 2,
        "taker_base_fee": 1000.0,
        "days_to_resolution": 30.0,
    }
    base.update(overrides)
    return base


def test_leg_direction_buy_no_when_price_too_high():
    df = pl.DataFrame([_row(market_id=f"m{i}", p=0.20, y=0) for i in range(10)])
    # mean_p=0.20, mean_y=0.0 -> price too high -> buy_no
    assert leg_direction_from_calibration_map(df, 0.0, 1.0) == "buy_no"


def test_leg_direction_buy_yes_when_price_too_low():
    df = pl.DataFrame([_row(market_id=f"m{i}", p=0.20, y=1) for i in range(10)])
    # mean_p=0.20, mean_y=1.0 -> price too low -> buy_yes
    assert leg_direction_from_calibration_map(df, 0.0, 1.0) == "buy_yes"


def test_leg_direction_none_when_exactly_calibrated():
    rows = [_row(market_id=f"m{i}", p=0.5, y=1) for i in range(5)] + [_row(market_id=f"m{i+5}", p=0.5, y=0) for i in range(5)]
    df = pl.DataFrame(rows)
    # mean_p=0.5, mean_y=0.5 -- exactly calibrated, no edge
    assert leg_direction_from_calibration_map(df, 0.0, 1.0) is None


def test_build_r1_positions_only_in_bucket_rows():
    df = pl.DataFrame(
        [
            _row(market_id="m1", p=0.05),  # longshot bucket
            _row(market_id="m2", p=0.95),  # favorite bucket
            _row(market_id="m3", p=0.50),  # neither
        ]
    )
    positions = build_r1_positions(df, longshot_direction="buy_no", favorite_direction="buy_yes")
    assert sorted(positions["market_id"].to_list()) == ["m1", "m2"]


def test_build_r1_positions_entry_price_inverted_for_buy_no():
    df = pl.DataFrame([_row(market_id="m1", p=0.05, y=0)])
    positions = build_r1_positions(df, longshot_direction="buy_no", favorite_direction=None)
    row = positions.row(0, named=True)
    assert row["entry_price"] == pytest.approx(0.95)
    assert row["won"] is True  # bought No, resolved y=0 -> No won


def test_build_r1_positions_entry_price_unchanged_for_buy_yes():
    df = pl.DataFrame([_row(market_id="m1", p=0.95, y=1)])
    positions = build_r1_positions(df, longshot_direction=None, favorite_direction="buy_yes")
    row = positions.row(0, named=True)
    assert row["entry_price"] == pytest.approx(0.95)
    assert row["won"] is True  # bought Yes, resolved y=1 -> Yes won


def test_build_r1_positions_none_direction_trades_nothing_for_that_leg():
    df = pl.DataFrame([_row(market_id="m1", p=0.05), _row(market_id="m2", p=0.95)])
    positions = build_r1_positions(df, longshot_direction=None, favorite_direction="buy_yes")
    assert positions["market_id"].to_list() == ["m2"]


def test_build_r1_positions_both_none_returns_empty_frame():
    df = pl.DataFrame([_row(market_id="m1", p=0.05), _row(market_id="m2", p=0.95)])
    positions = build_r1_positions(df, longshot_direction=None, favorite_direction=None)
    assert positions.height == 0


def test_build_r1_positions_not_deduplicated_per_market():
    df = pl.DataFrame(
        [
            _row(market_id="m1", p=0.05, snapshot_date=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            _row(market_id="m1", p=0.06, snapshot_date=datetime(2024, 2, 1, tzinfo=timezone.utc)),
        ]
    )
    positions = build_r1_positions(df, longshot_direction="buy_no", favorite_direction=None)
    assert positions.height == 2


def test_canary_entry_price_inversion_caught_by_asymmetric_footnote_formula():
    """The primary (documented) fee formula is symmetric in p vs 1-p, so
    it would NOT catch entry_price being left at the wrong side. The
    rejected footnote formula (min(p,1-p)/p) is asymmetric and catches it:
    a buy_no position at p=0.05 (entry_price=0.95) must match a buy_yes
    position at p=0.95 (entry_price=0.95) exactly, since both trade the
    same token price."""
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)

    df_longshot = pl.DataFrame([_row(market_id="m1", p=0.05, category="Sports")])
    positions_longshot = build_r1_positions(df_longshot, longshot_direction="buy_no", favorite_direction=None)
    entry_price_longshot = positions_longshot.row(0, named=True)["entry_price"]

    df_favorite = pl.DataFrame([_row(market_id="m2", p=0.95, category="Sports")])
    positions_favorite = build_r1_positions(df_favorite, longshot_direction=None, favorite_direction="buy_yes")
    entry_price_favorite = positions_favorite.row(0, named=True)["entry_price"]

    assert entry_price_longshot == pytest.approx(entry_price_favorite)

    fees_longshot = fee_cost("Sports", created, price=entry_price_longshot, taker_base_fee=1000.0)
    fees_favorite = fee_cost("Sports", created, price=entry_price_favorite, taker_base_fee=1000.0)
    assert fees_longshot["footnote_contract_formula_base"] == pytest.approx(
        fees_favorite["footnote_contract_formula_base"]
    )
    # sanity: if entry_price had been left at the WRONG (unflipped) price for the
    # longshot leg (0.05 instead of 0.95), this asymmetric formula would have differed
    wrong_fees = fee_cost("Sports", created, price=0.05, taker_base_fee=1000.0)
    assert wrong_fees["footnote_contract_formula_base"] != pytest.approx(
        fees_favorite["footnote_contract_formula_base"]
    )


def _fixture_rates() -> pl.DataFrame:
    return pl.DataFrame({"date": [date(2024, 1, 1), date(2024, 6, 1), date(2026, 4, 1)], "dgs3mo": [5.0, 5.2, 4.0]})


def test_attach_costs_and_pnl_hand_computed_won_and_lost():
    created_at_by_market = pl.DataFrame(
        {"market_id": ["m1", "m2"], "created_at": [datetime(2024, 1, 1, tzinfo=timezone.utc)] * 2}
    )
    df = pl.DataFrame(
        [
            _row(market_id="m1", p=0.05, y=0, days_to_resolution=73.0, snapshot_date=datetime(2024, 6, 1, tzinfo=timezone.utc)),
            _row(market_id="m2", p=0.05, y=1, days_to_resolution=73.0, snapshot_date=datetime(2024, 6, 1, tzinfo=timezone.utc)),
        ]
    )
    positions = build_r1_positions(df, longshot_direction="buy_no", favorite_direction=None)
    result = attach_costs_and_pnl(
        positions, created_at_by_market, _fixture_rates(), spread_lookup={("Politics", 2): 0.01}
    )

    won = result.filter(pl.col("market_id") == "m1").row(0, named=True)
    lost = result.filter(pl.col("market_id") == "m2").row(0, named=True)

    # won: bought No at 0.95, resolved y=0 -> No won. payout = 1/0.95, gross_pnl = 1/0.95 - 1
    assert won["gross_pnl"] == pytest.approx(1 / 0.95 - 1)
    # lost: bought No at 0.95, resolved y=1 -> No lost. payout=0, gross_pnl=-1
    assert lost["gross_pnl"] == pytest.approx(-1.0)

    # in-sample (2024) -> fee is unconditionally zero regardless of category/price
    assert won["fee_base"] == 0.0
    assert won["fee_upper"] == 0.0

    # carry: 73 days at 5.2% (rate on 2024-06-01, exact match) annual, ACT/365
    assert won["carry"] == pytest.approx(1.0 * (5.2 / 100.0) * (73 / 365))

    # spread_half looked up from the passed-in dict by (category, vol_tercile)
    assert won["spread_half"] == pytest.approx(0.01)


def test_attach_costs_and_pnl_skips_null_rate_observation_not_crash():
    """A snapshot_date landing exactly on a null (holiday) FRED row must
    forward-fill past it to the nearest earlier NON-null observation, not
    pass None into carry_cost."""
    created_at_by_market = pl.DataFrame({"market_id": ["m1"], "created_at": [datetime(2024, 1, 1, tzinfo=timezone.utc)]})
    rates_with_holiday = pl.DataFrame(
        {"date": [date(2024, 5, 30), date(2024, 6, 1), date(2024, 6, 3)], "dgs3mo": [5.1, None, 5.3]}
    )
    df = pl.DataFrame([_row(market_id="m1", p=0.05, snapshot_date=datetime(2024, 6, 1, tzinfo=timezone.utc))])
    positions = build_r1_positions(df, longshot_direction="buy_no", favorite_direction=None)
    result = attach_costs_and_pnl(positions, created_at_by_market, rates_with_holiday, spread_lookup={})
    row = result.row(0, named=True)
    assert row["carry"] == pytest.approx(1.0 * (5.1 / 100.0) * (30 / 365))


def test_attach_costs_and_pnl_missing_spread_stratum_is_null_not_crash():
    created_at_by_market = pl.DataFrame({"market_id": ["m1"], "created_at": [datetime(2024, 1, 1, tzinfo=timezone.utc)]})
    df = pl.DataFrame([_row(market_id="m1", p=0.05, category="Geopolitics", vol_tercile=3)])
    positions = build_r1_positions(df, longshot_direction="buy_no", favorite_direction=None)
    result = attach_costs_and_pnl(positions, created_at_by_market, _fixture_rates(), spread_lookup={})
    assert result.row(0, named=True)["spread_half"] is None
