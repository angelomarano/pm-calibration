from datetime import date

import polars as pl
import pytest

from src.ingest.fred import fetch_dgs3mo, rate_on

FIXTURE_CSV = """observation_date,DGS3MO
2026-01-02,4.50
2026-01-05,
2026-01-06,4.52
2026-01-09,4.51
"""


def test_fetch_dgs3mo_reads_cache_without_network_call(tmp_path):
    """Writing the fixture directly to cache_path means fetch_dgs3mo must
    never attempt a network call -- if it did, this test would fail on
    any machine without network access, which it doesn't."""
    cache_path = tmp_path / "dgs3mo.csv"
    cache_path.write_text(FIXTURE_CSV)

    df = fetch_dgs3mo(cache_path=cache_path)
    assert df.columns == ["date", "dgs3mo"]
    assert df.height == 4
    assert df["date"].to_list() == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 9)]


def test_fetch_dgs3mo_parses_empty_field_as_null_not_dot_marker(tmp_path):
    """The bare-empty-field convention confirmed empirically 2026-08-08
    -- NOT FRED API's separate "." convention."""
    cache_path = tmp_path / "dgs3mo.csv"
    cache_path.write_text(FIXTURE_CSV)

    df = fetch_dgs3mo(cache_path=cache_path)
    row = df.filter(pl.col("date") == date(2026, 1, 5)).row(0, named=True)
    assert row["dgs3mo"] is None


def _fixture_rates() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 9)],
            "dgs3mo": [4.50, None, 4.52, 4.51],
        }
    )


def test_rate_on_exact_match():
    assert rate_on(_fixture_rates(), date(2026, 1, 6)) == 4.52


def test_rate_on_forward_fills_over_weekend_gap():
    # 2026-01-08 (Thursday, not a business day in the fixture) -> most recent is 2026-01-06
    assert rate_on(_fixture_rates(), date(2026, 1, 8)) == 4.52


def test_rate_on_skips_a_null_observation_not_just_missing_dates():
    """2026-01-05 exists as a row but is null (a holiday) -- rate_on must
    skip past it to 2026-01-02, not return null or crash."""
    assert rate_on(_fixture_rates(), date(2026, 1, 5)) == 4.50


def test_rate_on_raises_before_first_observation():
    with pytest.raises(ValueError):
        rate_on(_fixture_rates(), date(2025, 12, 31))
