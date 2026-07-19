# pm-calibration

Are Polymarket prices well-calibrated probabilities? And where they're not, does the gap survive honest statistical treatment: inference that respects dependence between markets, timing that a trader could actually have observed in real time, and a real out-of-sample test, net of trading costs?

This repo is the data pipeline and (soon) the analysis behind that question, built end to end from public Polymarket endpoints. No API key, no wallet, no scraped dataset. Everything here is reproducible from the two read-only Polymarket APIs plus public DNS resolvers.

## Why this exists

Prediction market calibration got a lot of attention in 2025-2026: a handful of papers analyzed the same Polymarket/Kalshi data and reached opposite conclusions on whether the classic favorite-longshot bias is present. The disagreement traces to design choices these papers made differently: whether inference is done at the trade level or the market level, which time reference "how far from resolution" is measured against, and how they handle markets that resolve well before their scheduled end date (a majority of them, it turns out, once you check).

This project runs the same question with three things fixed: a market-level panel with event-clustered inference (a trade tape overstates precision when thousands of trades share one outcome), snapshot timing anchored to actual resolution rather than scheduled end date (verified necessary, not assumed), and an out-of-sample split at end of 2025, so H1 2026 tests whatever the published results claimed against data none of them had.

## Current status

The ingestion and panel-construction pipeline (what the project plan calls W1) is complete and passing its own acceptance checks.

| | |
|---|---|
| Resolved markets ingested | 331,035 |
| Distinct events | 203,087 |
| Panel-eligible markets (scheduled life ≥ 168h) | 94,442 |
| Price history coverage | 98.86% |
| Panel rows built (monthly snapshots, Jan 2024 - Jun 2026) | 95,899 |
| Of which usable now (pre out-of-sample lock) | 52,388 |
| Tests passing | 125 |

Every non-trivial design decision along the way, including the ones that reversed an earlier assumption, is logged in [DECISIONS.md](DECISIONS.md) with the date, the number that drove it, and what changed. A few examples: the pagination scheme had to switch from offset-based to cursor-based once markets past ~2,000 rows per window started returning errors, the population turned out to be 46% crypto micro-markets that a 168-hour minimum lifetime filter naturally excludes from the panel, and 21-40% of markets in every category resolve more than two days before their scheduled end date, which is why snapshot openness is computed against actual resolution time and not the scheduled one.

## Primary specification

Committed here before any calibration statistic gets computed on the full sample, which is the point: a pre-registered spec means results can't get quietly adjusted to look better after the fact.

- **Population:** binary Polymarket markets resolved between 2024-01-01 and 2026-06-30, volume ≥ $10,000, scheduled lifetime ≥ 168 hours.
- **Panel design:** monthly snapshots (1st of month, UTC), one row per market per snapshot where the market was open (created before the snapshot, not yet resolved by it). Price taken from the last available point within 72 hours of the snapshot.
- **Inference unit:** market, not trade. Standard errors via block bootstrap resampled by event, since markets in the same event share one realized outcome.
- **Primary estimand:** calibration regression logit(P(Y=1)) = α + β·logit(p) by category, tested against α=0, β=1. β>1 means prices are compressed toward 0.5 (the favorite-longshot signature).
- **Out-of-sample split:** fit through 2025-12-31, evaluate January-June 2026. That window is technically locked at the code level (`src/panel/io.py`, `load_panel()`) so nothing before W4 can read it, whether or not it's an accident.
- **Costs:** exact per-market fee flags where Polymarket enabled them, spread from historical order books where available, capital cost from lockup duration against the risk-free rate.

## Repo layout

```
config/spec.yaml         snapshot dates, filters, OOS boundary, the pre-registered numbers
src/ingest/               Gamma and CLOB API clients, DNS handling, caching, retry logic
src/panel/                resolution parsing, category mapping, panel construction, the OOS loader
spikes/                   one-off feasibility and sanity checks (Gate A/B/C reports)
tests/                    125 tests, mostly edge cases the real data actually produced
DECISIONS.md              dated log of every deviation from the original plan and why
```

## Running it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -q
```

Rebuilding the panel from scratch means re-pulling ~331k markets and ~94k price histories from Polymarket's public API, which takes a few hours. Cached raw responses aren't committed (data/ is gitignored) but the ingestion scripts are resumable and will pick up where they left off.

## What's next

Calibration proper: reliability diagrams, Brier score decomposition, the logit regression above, then a reconciliation step that reproduces the published disagreement on purpose by varying weighting scheme and time reference, before the out-of-sample test on H1 2026 net of costs.
