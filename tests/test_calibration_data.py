import datetime as dt

import polars as pl

from src.calibration.data import load_calibration_frame

UTC = dt.timezone.utc


def _panel_row(**overrides) -> dict:
    base = {
        "market_id": "m1",
        "event_id": "e1",
        "category": "Politics",
        "design": "P1",
        "snapshot_date": dt.datetime(2024, 1, 1, tzinfo=UTC),
        "p": 0.5,
        "y": 1,
        "volume_num": 50000.0,
        "vol_tercile": 2,
        "scheduled_life_hours": 1000.0,
        "days_to_sched_end": 60.0,
        "days_to_resolution": 45.0,
        "fees_enabled": False,
        "taker_base_fee": 0.0,
        "leg_label": "Yes",
        "restricted": False,
        "is_oos": False,
        "resolution_ambiguous": False,
    }
    base.update(overrides)
    return base


def test_load_calibration_frame_drops_null_y_and_ambiguous_and_counts(tmp_path, monkeypatch):
    rows = [
        _panel_row(market_id="m1", y=1, resolution_ambiguous=False),  # kept
        _panel_row(market_id="m2", y=0, resolution_ambiguous=False),  # kept
        _panel_row(market_id="m3", y=None, resolution_ambiguous=True),  # dropped, both conditions
        _panel_row(market_id="m4", y=1, resolution_ambiguous=False),  # kept
    ]
    df = pl.DataFrame(rows)
    path = tmp_path / "p1.parquet"
    df.write_parquet(path)

    kept, stats = load_calibration_frame(path)

    assert kept.height == 3
    assert set(kept["market_id"].to_list()) == {"m1", "m2", "m4"}
    assert stats == {
        "loaded": 4,
        "dropped_null_y": 1,
        "dropped_ambiguous": 1,
        "dropped_total": 1,  # union, not sum -- these are the same row
        "kept": 3,
    }


def test_load_calibration_frame_counts_non_overlapping_drop_reasons_independently(tmp_path):
    """If y-null and resolution_ambiguous ever stop being perfectly
    coincident, both reasons must still be counted correctly (not
    silently zeroed out by sequential filtering)."""
    rows = [
        _panel_row(market_id="m1", y=1, resolution_ambiguous=False),  # kept
        _panel_row(market_id="m2", y=None, resolution_ambiguous=False),  # dropped: null y only
        _panel_row(market_id="m3", y=1, resolution_ambiguous=True),  # dropped: ambiguous only
    ]
    df = pl.DataFrame(rows)
    path = tmp_path / "p1.parquet"
    df.write_parquet(path)

    kept, stats = load_calibration_frame(path)

    assert kept.height == 1
    assert stats["dropped_null_y"] == 1
    assert stats["dropped_ambiguous"] == 1
    assert stats["dropped_total"] == 2  # two distinct rows this time, not one


def test_load_calibration_frame_never_returns_oos_rows(tmp_path):
    """Inherits load_panel's default OOS filter -- one-line confirmation,
    not re-testing load_panel itself."""
    rows = [
        _panel_row(market_id="m1", is_oos=False),
        _panel_row(market_id="m2", is_oos=True),
    ]
    df = pl.DataFrame(rows)
    path = tmp_path / "p1.parquet"
    df.write_parquet(path)

    kept, _ = load_calibration_frame(path)
    assert kept["is_oos"].to_list() == [False]
