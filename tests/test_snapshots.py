import datetime as dt

import polars as pl

from src.panel.snapshots import build_candidate_pairs, count_null_resolution_ts

UTC = dt.timezone.utc


def _market_row(**overrides) -> dict:
    base = {
        "market_id": "m1",
        "event_id": "e1",
        "category": "Politics",
        "clob_token_leg": "tok1",
        "y": 1,
        "resolution_ambiguous": False,
        "volume_num": 50000.0,
        "scheduled_life_hours": 1000.0,
        "fees_enabled": False,
        "taker_base_fee": 0.0,
        "leg_label": "Yes",
        "restricted": False,
        "created_at": dt.datetime(2024, 1, 1, tzinfo=UTC),
        "end_date_sched": dt.datetime(2024, 3, 1, tzinfo=UTC),
        "resolution_ts": dt.datetime(2024, 2, 15, tzinfo=UTC),
        "panel_eligible": True,
    }
    base.update(overrides)
    return base


DATETIME_COLUMNS = ["created_at", "end_date_sched", "resolution_ts"]


def _markets_df(*rows) -> pl.DataFrame:
    # schema_overrides so a column that's None in every row of a given test
    # (e.g. resolution_ts=None) still keeps its Datetime dtype rather than
    # degrading to polars' Null type, which the day-arithmetic can't subtract.
    return pl.DataFrame(
        list(rows), schema_overrides={c: pl.Datetime("us", "UTC") for c in DATETIME_COLUMNS}
    )


SNAPSHOTS = [dt.datetime(2024, 1, 1, tzinfo=UTC), dt.datetime(2024, 2, 1, tzinfo=UTC), dt.datetime(2024, 3, 1, tzinfo=UTC)]
OOS_BOUNDARY = dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_open_market_included_at_every_snapshot_within_its_life():
    markets = _markets_df(_market_row())
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    # resolution_ts=2024-02-15: open at 2024-01-01 and 2024-02-01, resolved by 2024-03-01
    assert sorted(pairs["snapshot_date"].to_list()) == [SNAPSHOTS[0], SNAPSHOTS[1]]


def test_market_created_after_snapshot_excluded():
    markets = _markets_df(_market_row(created_at=dt.datetime(2024, 1, 15, tzinfo=UTC)))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert SNAPSHOTS[0] not in pairs["snapshot_date"].to_list()


def test_market_resolved_before_snapshot_excluded_even_though_end_date_sched_is_later():
    """The regression test for the decided rule: end_date_sched is far in
    the future, but resolution_ts is before the snapshot — must be
    excluded. Using end_date_sched here would wrongly include it."""
    markets = _markets_df(
        _market_row(
            created_at=dt.datetime(2024, 1, 1, tzinfo=UTC),
            resolution_ts=dt.datetime(2024, 1, 20, tzinfo=UTC),  # resolved well before Feb snapshot
            end_date_sched=dt.datetime(2025, 1, 1, tzinfo=UTC),  # scheduled end is a year later
        )
    )
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert SNAPSHOTS[1] not in pairs["snapshot_date"].to_list()
    assert SNAPSHOTS[2] not in pairs["snapshot_date"].to_list()
    # only the Jan 1 snapshot (before creation... wait created_at==Jan1, so Jan1 IS open)
    assert pairs["snapshot_date"].to_list() == [SNAPSHOTS[0]]


def test_boundary_created_at_equal_to_snapshot_is_included():
    markets = _markets_df(_market_row(created_at=SNAPSHOTS[1], resolution_ts=dt.datetime(2024, 3, 15, tzinfo=UTC)))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert SNAPSHOTS[1] in pairs["snapshot_date"].to_list()


def test_boundary_resolution_ts_equal_to_snapshot_is_excluded():
    markets = _markets_df(_market_row(resolution_ts=SNAPSHOTS[1]))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert SNAPSHOTS[1] not in pairs["snapshot_date"].to_list()


def test_non_eligible_market_never_appears():
    markets = _markets_df(_market_row(panel_eligible=False))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert pairs.height == 0


def test_ambiguous_resolution_market_is_kept_not_dropped():
    markets = _markets_df(_market_row(y=None, resolution_ambiguous=True))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert pairs.height > 0
    assert pairs["resolution_ambiguous"].to_list() == [True] * pairs.height
    assert pairs["y"].to_list() == [None] * pairs.height


def test_null_resolution_ts_excluded_from_every_snapshot():
    """The decided-deliberately case: a market never confirmed closed is
    excluded from every snapshot, not treated as open-forever."""
    markets = _markets_df(_market_row(resolution_ts=None))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    assert pairs.height == 0


def test_count_null_resolution_ts_counts_only_eligible_markets():
    markets = _markets_df(
        _market_row(market_id="m1", resolution_ts=None, panel_eligible=True),
        _market_row(market_id="m2", resolution_ts=None, panel_eligible=False),  # not eligible, not counted
        _market_row(market_id="m3", panel_eligible=True),  # has resolution_ts, not counted
    )
    assert count_null_resolution_ts(markets) == 1


def test_is_oos_computed_at_boundary():
    markets = _markets_df(
        _market_row(created_at=dt.datetime(2025, 1, 1, tzinfo=UTC), resolution_ts=dt.datetime(2026, 6, 1, tzinfo=UTC))
    )
    snapshots = [dt.datetime(2025, 12, 1, tzinfo=UTC), dt.datetime(2026, 1, 1, tzinfo=UTC)]
    pairs = build_candidate_pairs(markets, snapshots, OOS_BOUNDARY).sort("snapshot_date")
    assert pairs["is_oos"].to_list() == [False, True]


def test_one_row_per_market_snapshot_pair_no_duplicates():
    markets = _markets_df(_market_row(market_id="m1"), _market_row(market_id="m2", clob_token_leg="tok2"))
    pairs = build_candidate_pairs(markets, SNAPSHOTS, OOS_BOUNDARY)
    keys = list(zip(pairs["market_id"].to_list(), pairs["snapshot_date"].to_list()))
    assert len(keys) == len(set(keys))


def test_days_to_sched_end_and_days_to_resolution_computed():
    markets = _markets_df(_market_row())  # end_date_sched=2024-03-01, resolution_ts=2024-02-15
    pairs = build_candidate_pairs(markets, [SNAPSHOTS[0]], OOS_BOUNDARY)  # snapshot 2024-01-01
    row = pairs.row(0, named=True)
    assert row["days_to_sched_end"] == 60.0  # Jan1 -> Mar1
    assert row["days_to_resolution"] == 45.0  # Jan1 -> Feb15
