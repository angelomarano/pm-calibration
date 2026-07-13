import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import src.panel.io as io_module
from src.panel.io import load_panel
from src.panel.spec_config import SpecConfig

UTC = dt.timezone.utc


def _fake_config(oos_locked: bool) -> SpecConfig:
    return SpecConfig(
        snapshot_dates=[dt.datetime(2024, 1, 1, tzinfo=UTC)],
        p2_horizons_days=[7, 30],
        staleness_max_hours=72.0,
        price_clip=(0.01, 0.99),
        oos_locked=oos_locked,
        oos_boundary=dt.datetime(2026, 1, 1, tzinfo=UTC),
    )


def _write_panel(tmp_path) -> Path:
    df = pl.DataFrame(
        {
            "market_id": ["m1", "m2", "m3"],
            "is_oos": [False, False, True],
        }
    )
    path = tmp_path / "panel.parquet"
    df.write_parquet(path)
    return path


def test_load_panel_default_filters_out_oos_rows(tmp_path):
    path = _write_panel(tmp_path)
    df = load_panel(path)
    assert df["market_id"].to_list() == ["m1", "m2"]
    assert df["is_oos"].to_list() == [False, False]


def test_load_panel_allow_oos_raises_while_locked(tmp_path, monkeypatch):
    path = _write_panel(tmp_path)
    monkeypatch.setattr(io_module, "load_spec_config", lambda: _fake_config(oos_locked=True))
    with pytest.raises(RuntimeError, match="oos_locked"):
        load_panel(path, allow_oos=True)


def test_load_panel_allow_oos_returns_all_rows_when_unlocked(tmp_path, monkeypatch):
    path = _write_panel(tmp_path)
    monkeypatch.setattr(io_module, "load_spec_config", lambda: _fake_config(oos_locked=False))
    df = load_panel(path, allow_oos=True)
    assert df["market_id"].to_list() == ["m1", "m2", "m3"]


REAL_P1_PATH = Path("data/panel/p1.parquet")


@pytest.mark.skipif(not REAL_P1_PATH.exists(), reason="data/panel/p1.parquet not built in this environment")
def test_load_panel_real_p1_parquet_integration_check():
    """The real-data check: confirms load_panel's default matches today's
    is_oos=False count exactly, and that allow_oos=True still raises since
    the real config/spec.yaml has oos_locked=true."""
    df = load_panel(REAL_P1_PATH)
    assert df.height == 52388
    assert df["is_oos"].any() == False  # noqa: E712 (explicit False check reads clearer here)

    with pytest.raises(RuntimeError, match="oos_locked"):
        load_panel(REAL_P1_PATH, allow_oos=True)
