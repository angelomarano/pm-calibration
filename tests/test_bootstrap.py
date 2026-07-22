import numpy as np
import polars as pl
import pytest

from src.inference.bootstrap import event_bootstrap


def test_seed_reproducibility():
    df = pl.DataFrame({"event_id": ["a", "a", "b", "b", "c"], "market_id": ["m1", "m1", "m2", "m2", "m3"], "y": [1.0, 1.0, 2.0, 2.0, 3.0]})
    stat_fn = lambda d: {"mean": d["y"].mean()}

    r1 = event_bootstrap(df, stat_fn, B=100, seed=42)
    r2 = event_bootstrap(df, stat_fn, B=100, seed=42)

    assert r1.point == r2.point
    assert r1.ci_low == r2.ci_low
    assert r1.ci_high == r2.ci_high


def test_cluster_integrity_paired_values_sum_to_zero_in_every_draw():
    """Rows within an event are engineered to sum to exactly 0 (event e_k:
    +k and -k). If cluster resampling ever split an event's rows apart,
    some draws would not sum to 0. Since every valid draw sums to exactly
    0 regardless of which clusters/how-often they're picked, the resulting
    CI must collapse to a single point at 0."""
    event_ids, values = [], []
    for k in range(1, 21):
        event_ids += [f"e{k}", f"e{k}"]
        values += [float(k), -float(k)]
    df = pl.DataFrame({"event_id": event_ids, "market_id": event_ids, "value": values})
    stat_fn = lambda d: {"sum": d["value"].sum()}

    result = event_bootstrap(df, stat_fn, B=500, seed=7)

    assert result.point["sum"] == 0.0
    assert result.ci_low["sum"] == 0.0
    assert result.ci_high["sum"] == 0.0


def test_singleton_orphan_cluster_no_crash():
    df = pl.DataFrame(
        {
            "event_id": ["e1", "e1", None, None],
            "market_id": ["m1", "m1", "m2", "m3"],
            "y": [1.0, 1.0, 5.0, 6.0],
        }
    )
    stat_fn = lambda d: {"mean": d["y"].mean()}
    result = event_bootstrap(df, stat_fn, B=50, seed=1)
    assert result.B == 50


def test_orphan_market_multiple_snapshot_rows_move_together():
    """An orphan market (event_id=None) with 3 snapshot rows sharing the
    same market_id must cluster on market_id — all 3 rows move together,
    not independently. Same paired-sum trick: the orphan market's 3 rows
    sum to a known non-zero constant per appearance; a normal event's rows
    sum to 0. Total sum in any draw must be an integer multiple of the
    orphan's per-appearance constant, verified via divisibility."""
    event_ids = ["e1", "e1", None, None, None]
    market_ids = ["m1", "m1", "orphan", "orphan", "orphan"]
    values = [1.0, -1.0, 10.0, 10.0, 10.0]  # orphan's 3 rows always contribute exactly 30 together
    df = pl.DataFrame({"event_id": event_ids, "market_id": market_ids, "value": values})

    def stat_fn(d):
        n_orphan_rows = (d["market_id"] == "orphan").sum()
        return {"sum": d["value"].sum(), "n_orphan_rows": float(n_orphan_rows)}

    result = event_bootstrap(df, stat_fn, B=300, seed=3, return_draws=True)
    for draw in result.draws:
        # orphan cluster always contributes rows in multiples of 3 (all-or-nothing per draw pick)
        assert draw["n_orphan_rows"] % 3 == 0
        # each appearance of the orphan cluster contributes exactly 30; each appearance
        # of e1 contributes exactly 0 -> total sum is exactly 30 * (n_orphan_rows / 3)
        assert draw["sum"] == 30.0 * (draw["n_orphan_rows"] / 3)


def test_stat_fn_called_once_per_draw_not_per_key():
    df = pl.DataFrame({"event_id": ["a", "b", "c"], "market_id": ["m1", "m2", "m3"], "y": [1.0, 2.0, 3.0]})
    call_count = {"n": 0}

    def stat_fn(d):
        call_count["n"] += 1
        return {"mean": d["y"].mean(), "sum": d["y"].sum(), "max": d["y"].max()}

    event_bootstrap(df, stat_fn, B=150, seed=0)
    assert call_count["n"] == 150 + 1  # B draws + the one point-estimate call on original data


def test_point_estimate_is_original_data_not_bootstrap_mean():
    df = pl.DataFrame(
        {"event_id": [f"e{i}" for i in range(10)], "market_id": [f"m{i}" for i in range(10)], "y": [0.0] * 9 + [900.0]}
    )
    stat_fn = lambda d: {"mean": d["y"].mean()}
    result = event_bootstrap(df, stat_fn, B=200, seed=5)
    assert result.point["mean"] == pytest.approx(90.0)  # exact original-sample mean
    # the bootstrap distribution's own mean will differ noticeably from the point estimate
    # given resampling variance on a 10-cluster, heavily-skewed sample
    draws = event_bootstrap(df, stat_fn, B=200, seed=5, return_draws=True).draws
    bootstrap_mean = sum(d["mean"] for d in draws) / len(draws)
    assert abs(bootstrap_mean - result.point["mean"]) > 1e-9


def test_ci_low_le_ci_high():
    df = pl.DataFrame({"event_id": [f"e{i}" for i in range(15)], "market_id": [f"m{i}" for i in range(15)], "y": list(range(15))})
    stat_fn = lambda d: {"mean": d["y"].mean()}
    result = event_bootstrap(df, stat_fn, B=300, seed=2)
    assert result.ci_low["mean"] <= result.ci_high["mean"]


def test_nan_draws_excluded_from_ci_and_counted_in_n_valid():
    df = pl.DataFrame({"event_id": [f"e{i}" for i in range(20)], "market_id": [f"m{i}" for i in range(20)], "y": list(range(20))})

    def stat_fn(d):
        # nan whenever cluster "e0" wasn't picked in this draw (~36.8% of draws,
        # the standard bootstrap "not selected" rate) -> a known, non-degenerate
        # mix of nan/non-nan draws that actually depends on which clusters got
        # picked, not just the resampled row count (singleton clusters here
        # means every draw has the same row count regardless of content).
        val = float("nan") if "m0" not in d["market_id"].to_list() else d["y"].mean()
        return {"maybe_nan": val, "always_ok": d["y"].mean()}

    result = event_bootstrap(df, stat_fn, B=400, seed=9)
    assert result.n_valid["always_ok"] == 400
    assert 0 < result.n_valid["maybe_nan"] < 400
    assert not np.isnan(result.ci_low["maybe_nan"])
    assert not np.isnan(result.ci_high["maybe_nan"])


def test_fully_nan_key_raises():
    df = pl.DataFrame({"event_id": ["e1", "e2"], "market_id": ["m1", "m2"], "y": [1.0, 2.0]})
    stat_fn = lambda d: {"always_nan": float("nan")}
    with pytest.raises(ValueError, match="nan"):
        event_bootstrap(df, stat_fn, B=50, seed=0)


def test_stat_fn_exception_propagates_uncaught():
    df = pl.DataFrame({"event_id": ["e1", "e2"], "market_id": ["m1", "m2"], "y": [1.0, 2.0]})

    def stat_fn(d):
        raise RuntimeError("deliberate failure")

    with pytest.raises(RuntimeError, match="deliberate failure"):
        event_bootstrap(df, stat_fn, B=10, seed=0)


def test_coverage_correct_cluster_bootstrap_vs_broken_row_level():
    """Discriminating coverage check: a DGP with strong intra-cluster
    correlation (cluster-level effect tau=1.0 dominating within-cluster
    noise sigma=0.05) means the true effective sample size is n_clusters,
    not n_rows. A row-level bootstrap (ignoring cluster structure — done
    here by reusing event_bootstrap with cluster_col pointed at a
    per-row-unique column) drastically understates sampling variance and
    should undercover badly (~70% or worse); the correct event-clustered
    bootstrap should sit near the nominal 95%. Parameters were tuned
    empirically (not guessed) to produce this separation reliably at
    n_reps=60, B=200 — fast enough for a test suite, still discriminating."""
    n_clusters = 30
    rows_per_cluster = 5
    tau, sigma = 1.0, 0.05
    true_mean = 0.0
    n_reps = 60
    B = 200

    correct_hits = 0
    broken_hits = 0

    for rep in range(n_reps):
        rng = np.random.default_rng(1000 + rep)
        cluster_means = rng.normal(true_mean, tau, size=n_clusters)
        event_ids, row_ids, ys = [], [], []
        counter = 0
        for c in range(n_clusters):
            for _ in range(rows_per_cluster):
                event_ids.append(f"e{c}")
                row_ids.append(f"r{counter}")
                ys.append(cluster_means[c] + rng.normal(0, sigma))
                counter += 1
        df = pl.DataFrame({"event_id": event_ids, "market_id": row_ids, "row_id": row_ids, "y": ys})
        stat_fn = lambda d: {"mean": d["y"].mean()}

        correct = event_bootstrap(df, stat_fn, cluster_col="event_id", id_col="market_id", B=B, seed=rep)
        broken = event_bootstrap(df, stat_fn, cluster_col="row_id", id_col="row_id", B=B, seed=rep)

        if correct.ci_low["mean"] <= true_mean <= correct.ci_high["mean"]:
            correct_hits += 1
        if broken.ci_low["mean"] <= true_mean <= broken.ci_high["mean"]:
            broken_hits += 1

    correct_rate = correct_hits / n_reps
    broken_rate = broken_hits / n_reps

    assert correct_rate >= 0.85, f"correct cluster bootstrap undercovered: {correct_rate:.2%}"
    assert broken_rate <= 0.80, f"broken row-level bootstrap should undercover clearly, got {broken_rate:.2%}"
    assert correct_rate - broken_rate >= 0.15, "DGP should separate correct from broken, not just both pass"
