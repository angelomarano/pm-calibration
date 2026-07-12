from datetime import datetime, timezone

from src.panel.resolution import leg_index, parse_ts, resolution_timestamp, resolve_y


def test_parse_ts_handles_closed_time_quirk():
    assert parse_ts("2024-06-01 06:40:11+00") == datetime(2024, 6, 1, 6, 40, 11, tzinfo=timezone.utc)


def test_parse_ts_handles_clean_iso():
    assert parse_ts("2024-11-05T12:00:00Z") == datetime(2024, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_none_or_empty_returns_none():
    assert parse_ts(None) is None
    assert parse_ts("") is None


def test_leg_index_yes_present():
    assert leg_index(["Yes", "No"]) == (0, "Yes")
    assert leg_index(["No", "Yes"]) == (1, "Yes")


def test_leg_index_no_yes_team_names():
    assert leg_index(["Lakers", "Celtics"]) == (0, "Lakers")


def test_resolve_y_leg_wins():
    assert resolve_y([1.0, 0.0], leg_idx=0, uma_status="resolved") == (1, False)


def test_resolve_y_other_leg_wins():
    assert resolve_y([0.0, 1.0], leg_idx=0, uma_status="resolved") == (0, False)


def test_resolve_y_non_degenerate_is_ambiguous():
    assert resolve_y([0.5, 0.5], leg_idx=0, uma_status="resolved") == (None, True)


def test_resolve_y_unresolved_status_is_ambiguous_even_if_degenerate():
    assert resolve_y([1.0, 0.0], leg_idx=0, uma_status="disputed") == (None, True)


def test_resolve_y_wrong_price_count_is_ambiguous():
    assert resolve_y([1.0, 0.0, 0.0], leg_idx=0, uma_status="resolved") == (None, True)


def test_resolution_timestamp_prefers_uma_end_date():
    ts = resolution_timestamp("2024-11-05T12:00:00Z", "2024-11-06 00:00:00+00")
    assert ts == datetime(2024, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


def test_resolution_timestamp_falls_back_to_closed_time_when_uma_end_date_missing():
    ts = resolution_timestamp(None, "2024-06-01 06:40:11+00")
    assert ts == datetime(2024, 6, 1, 6, 40, 11, tzinfo=timezone.utc)
