import json
from datetime import datetime, timezone

import pytest

from src.panel.build_markets import SCHEMA, build_markets_table, parse_market


def _raw_market(**overrides) -> dict:
    base = {
        "id": "1",
        "conditionId": "0xabc",
        "question": "Will X happen?",
        "slug": "will-x-happen",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["tok-yes", "tok-no"]),
        "outcomePrices": json.dumps(["1", "0"]),
        "umaResolutionStatus": "resolved",
        "volumeNum": 50000.0,
        "volumeClob": 50000.0,
        "createdAt": "2024-01-01T00:00:00Z",
        "startDate": "2024-01-01T00:00:00Z",
        "endDate": "2024-06-01T00:00:00Z",
        "umaEndDate": "2024-05-20T00:00:00Z",
        "closedTime": "2024-05-20 00:00:05+00",
        "feesEnabled": False,
        "enableOrderBook": True,
        "orderPriceMinTickSize": 0.001,
        "archived": False,
        "restricted": False,
        "events": [{"id": "e1"}],
    }
    base.update(overrides)
    return base


def test_parse_market_happy_path():
    row = parse_market(_raw_market(), event_tags={"e1": ["Politics"]})
    assert row["market_id"] == "1"
    assert row["condition_id"] == "0xabc"
    assert row["leg_idx"] == 0
    assert row["leg_label"] == "Yes"
    assert row["clob_token_leg"] == "tok-yes"
    assert row["y"] == 1
    assert row["resolution_ambiguous"] is False
    assert row["category"] == "Politics"
    assert row["event_id"] == "e1"
    assert row["n_events"] == 1
    assert row["resolution_ts"] == datetime(2024, 5, 20, tzinfo=timezone.utc)
    assert row["scheduled_life_hours"] == pytest.approx(3648.0)  # 152 days, Jan 1 -> Jun 1
    assert row["panel_eligible"] is True


def test_parse_market_missing_clob_tokens_is_skipped():
    raw = _raw_market(clobTokenIds=json.dumps(["only-one"]))
    assert parse_market(raw, event_tags={}) is None


def test_parse_market_outcomes_clob_length_mismatch_is_skipped():
    raw = _raw_market(outcomes=json.dumps(["Yes", "No", "Maybe"]))
    assert parse_market(raw, event_tags={}) is None


def test_parse_market_missing_outcomes_is_skipped():
    raw = _raw_market()
    del raw["outcomes"]
    assert parse_market(raw, event_tags={}) is None


def test_parse_market_team_name_outcomes_use_index_zero():
    raw = _raw_market(outcomes=json.dumps(["Lakers", "Celtics"]), outcomePrices=json.dumps(["0", "1"]))
    row = parse_market(raw, event_tags={})
    assert row["leg_idx"] == 0
    assert row["leg_label"] == "Lakers"
    assert row["y"] == 0  # Celtics (other leg) won


def test_parse_market_non_degenerate_prices_are_ambiguous():
    raw = _raw_market(outcomePrices=json.dumps(["0.5", "0.5"]))
    row = parse_market(raw, event_tags={})
    assert row["y"] is None
    assert row["resolution_ambiguous"] is True


def test_parse_market_proposed_degenerate_old_resolution_is_not_ambiguous():
    """20/232 Gate-A markets sat in status='proposed' despite fully
    degenerate prices, all 853-913 days old — the dispute-window guard
    relaxes exactly this case."""
    raw = _raw_market(
        umaResolutionStatus="proposed",
        outcomePrices=json.dumps(["1", "0"]),
        umaEndDate="2024-01-01T00:00:00Z",
    )
    reference = datetime(2024, 2, 1, tzinfo=timezone.utc)  # 31 days later
    row = parse_market(raw, event_tags={}, reference_time=reference)
    assert row["y"] == 1
    assert row["resolution_ambiguous"] is False


def test_parse_market_proposed_degenerate_recent_resolution_stays_ambiguous():
    """Same degenerate prices, but resolution_ts is only 1h old — still
    inside a plausible dispute window, must NOT be relaxed."""
    raw = _raw_market(
        umaResolutionStatus="proposed",
        outcomePrices=json.dumps(["1", "0"]),
        umaEndDate="2024-01-01T00:00:00Z",
    )
    reference = datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)  # 1h later
    row = parse_market(raw, event_tags={}, reference_time=reference)
    assert row["y"] is None
    assert row["resolution_ambiguous"] is True


def test_parse_market_proposed_exactly_24h_stays_ambiguous_strict_boundary():
    raw = _raw_market(
        umaResolutionStatus="proposed",
        outcomePrices=json.dumps(["1", "0"]),
        umaEndDate="2024-01-01T00:00:00Z",
    )
    reference = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)  # exactly 24h later
    row = parse_market(raw, event_tags={}, reference_time=reference)
    assert row["resolution_ambiguous"] is True  # strictly > required, not >=


def test_parse_market_proposed_non_degenerate_stays_ambiguous_regardless_of_age():
    raw = _raw_market(
        umaResolutionStatus="proposed",
        outcomePrices=json.dumps(["0.5", "0.5"]),
        umaEndDate="2024-01-01T00:00:00Z",
    )
    reference = datetime(2025, 1, 1, tzinfo=timezone.utc)  # a year later
    row = parse_market(raw, event_tags={}, reference_time=reference)
    assert row["y"] is None
    assert row["resolution_ambiguous"] is True


def test_parse_market_proposed_missing_resolution_ts_stays_ambiguous():
    raw = _raw_market(umaResolutionStatus="proposed", outcomePrices=json.dumps(["1", "0"]))
    del raw["umaEndDate"]
    del raw["closedTime"]
    reference = datetime(2025, 1, 1, tzinfo=timezone.utc)
    row = parse_market(raw, event_tags={}, reference_time=reference)
    assert row["y"] is None
    assert row["resolution_ambiguous"] is True


def test_parse_market_resolved_status_unaffected_by_reference_time():
    ancient_reference = datetime(1999, 1, 1, tzinfo=timezone.utc)
    row = parse_market(_raw_market(), event_tags={}, reference_time=ancient_reference)
    assert row["y"] == 1
    assert row["resolution_ambiguous"] is False


def test_parse_market_fallback_start_date_to_created_at():
    raw = _raw_market(createdAt="2024-03-01T00:00:00Z")
    del raw["startDate"]
    row = parse_market(raw, event_tags={})
    assert row["start_date"] == datetime(2024, 3, 1, tzinfo=timezone.utc)


def test_parse_market_liquidity_num_null_when_absent():
    row = parse_market(_raw_market(), event_tags={})
    assert row["liquidity_num"] is None


def test_parse_market_liquidity_num_present_when_given():
    row = parse_market(_raw_market(liquidityNum=1234.5), event_tags={})
    assert row["liquidity_num"] == 1234.5


def test_parse_market_fees_default_to_zero_when_absent():
    row = parse_market(_raw_market(), event_tags={})
    assert row["maker_base_fee"] == 0.0
    assert row["taker_base_fee"] == 0.0


def test_parse_market_fee_value_stored_independently_of_fees_enabled_gate():
    """A non-zero cached takerBaseFee on a feesEnabled=False market must
    still be stored faithfully (not zeroed by parse_market) — an actual fee
    computation must gate on fees_enabled first, fee value second. If
    parse_market collapsed this at ingest time, W4's cost code could never
    tell "disabled, would have been 999" apart from "disabled, was 0"."""
    raw = _raw_market(feesEnabled=False, makerBaseFee=888, takerBaseFee=999)
    row = parse_market(raw, event_tags={})
    assert row["fees_enabled"] is False
    assert row["maker_base_fee"] == 888.0
    assert row["taker_base_fee"] == 999.0


def test_parse_market_volume_amm_null_when_absent():
    row = parse_market(_raw_market(), event_tags={})
    assert row["volume_amm"] is None


def test_parse_market_volume_amm_present_when_given():
    row = parse_market(_raw_market(volumeAmm=42.0), event_tags={})
    assert row["volume_amm"] == 42.0


def test_parse_market_panel_eligible_false_below_168h():
    """A ~24h-lived market (the Crypto 'Up or Down' pattern found in
    production) cannot structurally appear in P2's T-7 leg, and can only
    hit a P1 monthly snapshot by near-zero-probability coincidence."""
    raw = _raw_market(startDate="2024-01-01T00:00:00Z", endDate="2024-01-02T00:00:00Z")
    row = parse_market(raw, event_tags={})
    assert row["scheduled_life_hours"] == pytest.approx(24.0)
    assert row["panel_eligible"] is False


def test_parse_market_panel_eligible_boundary_exactly_168h_is_true():
    raw = _raw_market(startDate="2024-01-01T00:00:00Z", endDate="2024-01-08T00:00:00Z")  # exactly 7 days
    row = parse_market(raw, event_tags={})
    assert row["scheduled_life_hours"] == pytest.approx(168.0)
    assert row["panel_eligible"] is True


def test_parse_market_panel_eligible_just_under_168h_is_false():
    raw = _raw_market(startDate="2024-01-01T00:00:00Z", endDate="2024-01-07T23:00:00Z")  # 167h
    row = parse_market(raw, event_tags={})
    assert row["scheduled_life_hours"] == pytest.approx(167.0)
    assert row["panel_eligible"] is False


def test_parse_market_panel_eligible_false_when_end_date_missing():
    raw = _raw_market()
    del raw["endDate"]
    row = parse_market(raw, event_tags={})
    assert row["scheduled_life_hours"] is None
    assert row["panel_eligible"] is False


def test_build_markets_table_schema_matches_spec():
    df = build_markets_table([_raw_market()], event_tags={"e1": ["Politics"]})
    assert df.schema == SCHEMA


def test_build_markets_table_skips_unparseable_rows():
    raw = [_raw_market(id="1"), _raw_market(id="2", clobTokenIds=json.dumps(["only-one"]))]
    df = build_markets_table(raw, event_tags={})
    assert df.height == 1
    assert df["market_id"].to_list() == ["1"]


def test_build_markets_table_raises_on_duplicate_market_id_collects_all_first():
    raw = [
        _raw_market(id="1"),
        _raw_market(id="1"),
        _raw_market(id="2"),
        _raw_market(id="2"),
        _raw_market(id="3"),
    ]
    with pytest.raises(ValueError) as exc_info:
        build_markets_table(raw, event_tags={})
    message = str(exc_info.value)
    assert "1" in message
    assert "2" in message
    assert "3" not in message  # only the actual duplicates are reported


def test_build_markets_table_multi_market_event_shares_event_id():
    raw = [
        _raw_market(id="1", question="A", events=[{"id": "e1"}]),
        _raw_market(id="2", question="B", events=[{"id": "e1"}]),
    ]
    df = build_markets_table(raw, event_tags={"e1": ["Sports"]})
    assert df["event_id"].to_list() == ["e1", "e1"]
    assert df["category"].to_list() == ["Sports", "Sports"]


def test_build_markets_table_reports_volume_amm_discrepancy_without_crashing():
    raw = [
        _raw_market(id="1", volumeNum=100.0, volumeClob=100.0),  # amm absent, clob==num: no discrepancy
        _raw_market(id="2", volumeNum=100.0, volumeClob=80.0),  # amm absent, clob!=num: discrepancy
    ]
    df = build_markets_table(raw, event_tags={})
    assert df.height == 2
    assert df["volume_amm"].is_null().all()
