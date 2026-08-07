"""FRED DGS3MO (3-Month Treasury) pull -- W4a's carry-cost input.

Probed empirically before trusting it (2026-08-08, per rule 4): GET
https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO returns a plain
CSV, columns `observation_date,DGS3MO`, one row per US business day from
1981 to the present, no API key. Missing observations (491 of 11,723
rows checked, all holidays) are a BARE EMPTY FIELD ("1981-09-07,"), NOT
FRED's own API's "." convention -- that was an assumption going in and
turned out to be wrong for this specific CSV endpoint, corrected here
rather than left in. polars.read_csv treats an empty field as null by
default, so no special null_values handling is needed.

Cached to disk like every other pull in this project (skip-if-cached,
resumable) -- this is a small, one-shot, non-paginated pull, so no
pacing loop is needed, just the shared retry-wrapped GET.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.ingest.http import _get, make_session

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
DEFAULT_CACHE_PATH = Path("data/raw/fred/dgs3mo.csv")


def fetch_dgs3mo(cache_path: Path = DEFAULT_CACHE_PATH) -> pl.DataFrame:
    """Downloads (or reads from cache) the DGS3MO series. Returns a
    DataFrame with columns `date` (Date) and `dgs3mo` (Float64, percent;
    null on holidays/missing observations)."""
    if not cache_path.exists():
        session = make_session()
        resp = _get(session, FRED_URL, params={"id": "DGS3MO"})
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(resp.text)

    df = pl.read_csv(cache_path)
    return df.select(
        pl.col("observation_date").str.to_date().alias("date"),
        pl.col("DGS3MO").cast(pl.Float64).alias("dgs3mo"),
    ).sort("date")


def rate_on(rates: pl.DataFrame, date) -> float:
    """Forward-fills to the most recent NON-NULL published rate on or
    before `date` (Treasury rates aren't published on weekends/holidays,
    and a handful of business days are themselves missing -- both must
    be skipped, not just weekends). Raises ValueError if `date` predates
    every observation in `rates`."""
    eligible = rates.filter((pl.col("date") <= date) & pl.col("dgs3mo").is_not_null())
    if eligible.height == 0:
        raise ValueError(f"no DGS3MO observation on or before {date!r}")
    return float(eligible.sort("date")["dgs3mo"][-1])
