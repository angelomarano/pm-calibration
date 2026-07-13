import datetime as dt

import pytest

from src.panel.spec_config import load_spec_config, monthly_snapshot_dates


def test_monthly_snapshot_dates_inclusive_both_ends():
    start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2024, 4, 1, tzinfo=dt.timezone.utc)
    dates = monthly_snapshot_dates(start, end)
    assert dates == [
        dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2024, 2, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2024, 4, 1, tzinfo=dt.timezone.utc),
    ]


def test_monthly_snapshot_dates_crosses_year_boundary():
    start = dt.datetime(2025, 11, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc)
    dates = monthly_snapshot_dates(start, end)
    assert [d.strftime("%Y-%m") for d in dates] == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_load_spec_config_real_file():
    config = load_spec_config()
    assert config.snapshot_dates[0] == dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    assert config.snapshot_dates[-1] == dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    assert len(config.snapshot_dates) == 30
    assert config.p2_horizons_days == [7, 30]
    assert config.staleness_max_hours == 72.0
    assert config.price_clip == (0.01, 0.99)
    assert config.oos_locked is True
    assert config.oos_boundary == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def test_load_spec_config_from_tmp_file(tmp_path):
    path = tmp_path / "spec.yaml"
    path.write_text(
        """
snapshot_dates:
  start: "2024-01-01T00:00:00Z"
  end: "2024-03-01T00:00:00Z"
  freq: "monthly_first"
p2_horizons_days: [7, 30]
staleness_max_hours: 48
price_clip: [0.02, 0.98]
oos_locked: false
oos_boundary: "2027-01-01T00:00:00Z"
"""
    )
    config = load_spec_config(path)
    assert len(config.snapshot_dates) == 3
    assert config.staleness_max_hours == 48.0
    assert config.price_clip == (0.02, 0.98)
    assert config.oos_locked is False


def test_load_spec_config_rejects_unsupported_freq(tmp_path):
    path = tmp_path / "spec.yaml"
    path.write_text(
        """
snapshot_dates:
  start: "2024-01-01T00:00:00Z"
  end: "2024-03-01T00:00:00Z"
  freq: "weekly"
p2_horizons_days: [7, 30]
staleness_max_hours: 48
price_clip: [0.02, 0.98]
oos_locked: false
oos_boundary: "2027-01-01T00:00:00Z"
"""
    )
    with pytest.raises(ValueError, match="unsupported"):
        load_spec_config(path)
