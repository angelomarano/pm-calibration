from pathlib import Path

import numpy as np
import polars as pl
import pytest

from src.calibration.data import load_calibration_frame
from src.calibration.reliability import (
    DECILE_EDGES,
    bin_reliability,
    build_reliability_report,
    pava_isotonic,
    wilson_interval,
)


def test_wilson_interval_matches_known_worked_example():
    """n=20, k=10 (p_hat=0.5) is a commonly-cited Wilson worked example;
    n=100, k=50 is hand-verifiable via the textbook formula (center
    collapses to exactly 0.5 by symmetry when p_hat=0.5)."""
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.40383, abs=1e-5)
    assert hi == pytest.approx(0.59617, abs=1e-5)

    lo2, hi2 = wilson_interval(10, 20)
    assert lo2 == pytest.approx(0.2993, abs=1e-4)
    assert hi2 == pytest.approx(0.7007, abs=1e-4)


def test_wilson_interval_edge_cases_stay_within_bounds():
    lo, hi = wilson_interval(0, 20)
    assert lo == pytest.approx(0.0)
    assert 0.0 <= hi <= 1.0

    lo2, hi2 = wilson_interval(20, 20)
    assert hi2 == pytest.approx(1.0)
    assert 0.0 <= lo2 <= 1.0


def test_bin_reliability_boundary_values_use_left_closed_right_open_convention():
    df = pl.DataFrame({"p": [0.05, 0.1, 0.2, 0.9, 1.0], "y": [1, 1, 0, 1, 1]})
    binned = bin_reliability(df)
    # 0.05 -> bin 0 [0.0,0.1); 0.1 -> bin 1 [0.1,0.2) (left-closed: edge belongs to the bin it opens);
    # 0.2 -> bin 2 [0.2,0.3); 0.9 and 1.0 -> bin 9 [0.9,1.0] (closed both ends, the one exception)
    counts = {row["bin"]: row["n"] for row in binned.iter_rows(named=True)}
    assert counts[0] == 1  # 0.05
    assert counts[1] == 1  # 0.1
    assert counts[2] == 1  # 0.2
    assert counts[9] == 2  # 0.9 and 1.0 both land in the last, closed-both-ends bin


def test_bin_reliability_empirical_frequency_and_n_correct():
    df = pl.DataFrame({"p": [0.15, 0.16, 0.17, 0.55], "y": [1, 0, 1, 1]})
    binned = bin_reliability(df)
    bin1 = binned.filter(pl.col("bin") == 1).row(0, named=True)  # [0.1, 0.2)
    assert bin1["n"] == 3
    assert bin1["empirical_freq"] == pytest.approx(2 / 3)
    assert bin1["mean_p"] == pytest.approx((0.15 + 0.16 + 0.17) / 3)


def test_bin_reliability_empty_bin_reports_null_not_error():
    df = pl.DataFrame({"p": [0.05], "y": [1]})
    binned = bin_reliability(df)
    empty_bin = binned.filter(pl.col("bin") == 5).row(0, named=True)
    assert empty_bin["n"] == 0
    assert empty_bin["mean_p"] is None


def test_pava_isotonic_classic_worked_example_with_violation():
    p = np.array([0.0, 1.0, 2.0, 3.0])
    y = np.array([1.0, 2.0, 1.0, 3.0])  # violation at index 2 (1 < 2) -> pool indices 1,2 -> 1.5
    fitted = pava_isotonic(p, y)
    assert fitted.tolist() == pytest.approx([1.0, 1.5, 1.5, 3.0])


def test_pava_isotonic_already_monotonic_unchanged():
    p = np.array([0.0, 1.0, 2.0, 3.0])
    y = np.array([1.0, 2.0, 3.0, 4.0])
    fitted = pava_isotonic(p, y)
    assert fitted.tolist() == pytest.approx([1.0, 2.0, 3.0, 4.0])


def test_pava_isotonic_sorts_by_p_first():
    """Input given out of p-order must still produce a fitted curve
    aligned to ascending p, not to original row order."""
    p = np.array([2.0, 0.0, 3.0, 1.0])
    y = np.array([1.0, 1.0, 3.0, 2.0])  # same data as the classic example, shuffled
    fitted = pava_isotonic(p, y)
    assert fitted.tolist() == pytest.approx([1.0, 1.5, 1.5, 3.0])


REAL_P1_PATH = Path("data/panel/p1.parquet")


@pytest.mark.skipif(not REAL_P1_PATH.exists(), reason="data/panel/p1.parquet not built in this environment")
def test_no_row_at_p_exactly_1_0_given_upstream_clip():
    """M3's price_clip is [0.01, 0.99] -- confirms the closed-both-ends
    convention for the last bin is currently a dead branch on real data,
    not a false assumption. If the clip bounds ever change, this is what
    catches the mismatch."""
    df, _ = load_calibration_frame(REAL_P1_PATH)
    assert (df["p"] == 1.0).sum() == 0
    assert (df["p"] == 0.0).sum() == 0
    assert df["p"].max() <= 0.99
    assert df["p"].min() >= 0.01


def test_build_reliability_report_smoke_produces_stable_filenames(tmp_path):
    rng = np.random.default_rng(0)
    n = 200
    df = pl.DataFrame(
        {
            "category": rng.choice(["Politics", "Sports", "Crypto"], size=n).tolist(),
            "p": rng.uniform(0.01, 0.99, size=n).tolist(),
            "y": rng.integers(0, 2, size=n).tolist(),
        }
    )
    out_dir = tmp_path / "figures"
    results = build_reliability_report(df, out_dir=out_dir)

    assert (out_dir / "ex_sports_reliability.png").exists()
    assert (out_dir / "sports_reliability.png").exists()
    assert (out_dir / "politics_reliability.png").exists()
    assert "ex_Sports" in results and results["ex_Sports"]["n"] > 0

    # regenerate -- must overwrite the same stable filename, not create a second file
    mtime_before = (out_dir / "sports_reliability.png").stat().st_mtime_ns
    build_reliability_report(df, out_dir=out_dir)
    files_after = list(out_dir.glob("sports_reliability*"))
    assert len(files_after) == 1  # still exactly one file, not a timestamped duplicate
