# W1 Specification — Ingestion & Panel Construction

Contract for milestone-by-milestone implementation. Read fully before coding.
Plan reference: project plan §3/W1. Gate A: **GO** (2026-07-09, n=207 stratified).

---

## 1. API facts (verified empirically at Gate A, 2026-07-09)

**Gamma** (`https://gamma-api.polymarket.com`, public, no auth):
- `/markets` honors `closed=true`, `end_date_min/max`, `volume_num_min`,
  `limit`, `offset`. **Pages are capped at 100 rows** regardless of `limit`.
- Market object fields confirmed present: `id`, `conditionId`, `question`,
  `slug`, `outcomes` (JSON string, e.g. `'["Yes","No"]'` — but sports markets
  may use team names), `outcomePrices` (JSON string; degenerate `"1"/"0"` on
  resolved markets — 100% of Gate A sample), `clobTokenIds` (JSON string,
  aligned with `outcomes`), `volumeNum`, `liquidityNum`, `startDate`,
  `createdAt`, `endDate` (scheduled), **`umaEndDate` (actual resolution, clean
  ISO)**, `closedTime` (actual close; format quirk: `"2024-06-01 06:40:11+00"`
  — space separator, `+00` offset), `umaResolutionStatus` (`"resolved"`),
  `feesEnabled`, `makerBaseFee`, `takerBaseFee`, `enableOrderBook`,
  `volumeAmm`, `volumeClob`, `orderPriceMinTickSize` (0.001), `archived`,
  `restricted`, `events` (list; `events[0].id` = clustering key).
- **Trap:** the `fee` field (`"20000000000000000"` = 2% in 1e18 fixed point)
  is the legacy AMM fee. Ignore it. CLOB-era fees live in
  `makerBaseFee`/`takerBaseFee` + `feesEnabled`.

**CLOB** (`https://clob.polymarket.com`, public reads, no auth):
- `/prices-history?market={token_id}&interval=max&fidelity=1440` returns
  `{"history":[{"t": epoch_s, "p": float}, ...]}` covering the full market
  life (median start lag 0 days). **Resolved markets serve only coarse
  buckets: fidelity 1440 works (720 as fallback); sub-12h returns empty.**
- Observed sustained throughput ~1.9 req/s with 0.25 s pacing, zero 429s on
  ~210 calls. Assume it can degrade; keep backoff.
- ~3% of sampled markets (all low-volume 2024) returned no history at any
  fidelity — hypothesis: AMM-era markets (`enableOrderBook=false` or
  `volumeClob≈0`). **Verify this hypothesis in M2 and report.**

**Resolution timing (Gate A headline):** 74–83% of 2025–26 markets resolve
*before* scheduled `endDate` (median 45 days early, p90 206). Consequences
baked into this spec: openness at a snapshot MUST use actual resolution
(`umaEndDate`, fallback `closedTime`), never `endDate`; and the pull windows
must extend past the study end (M1) or early-resolved long-dated markets get
silently dropped.

---

## 2. Milestones

### M1 — Gamma ingestion → `data/panel/markets.parquet`
**Pull.** All closed markets via monthly `end_date_min/max` windows from
**2024-01 through 2027-12**, plus one open-ended window `end_date_min=2028-01-01`
(long-dated "by 2030" markets that resolved early belong to our population;
their scheduled end is far beyond the study window). Per window: pages of 100
until short page. Server-side `volume_num_min=10000`. Dedupe by `id` across
windows. Cache one JSON file per (window, offset) under `data/raw/gamma/`;
skip-if-cached; resumable.

**Parse** to a typed parquet with columns:
`market_id, condition_id, question, slug, event_id, n_events, category,
tags_raw, outcomes, leg_idx, leg_label, clob_token_leg, outcome_prices,
y (int8, nullable), resolution_ambiguous (bool), uma_resolution_status,
volume_num, liquidity_num, volume_clob, volume_amm, created_at, start_date,
end_date_sched, uma_end_date, closed_time, resolution_ts, fees_enabled,
maker_base_fee, taker_base_fee, enable_order_book, tick_size, archived,
restricted, neg_risk (if present)`

Rules:
- `leg_idx` = index of `"Yes"` in outcomes if present, else 0 (deliberate,
  outcome-blind convention; record `leg_label` either way).
- `y` = 1 if `outcome_prices[leg_idx] >= 0.99`, 0 if the *other* leg ≥ 0.99,
  null + `resolution_ambiguous=true` otherwise (also when
  `uma_resolution_status != "resolved"`).
- `resolution_ts` = `uma_end_date` if present else parsed `closed_time`.
- `category`: map event tags → {Politics, Sports, Crypto, Econ/Finance,
  Culture, Geopolitics, Other}; keep `tags_raw`. If nested `events` lack tags,
  probe first; if truly absent, add a batched secondary pull by event id.
  Report unmapped share.

**Acceptance:** row count printed by year; duplicates = 0; parse failures = 0
(or itemized); ambiguous-resolution share < 3%; schema types validated;
pytest green on M1 edge cases (§3).

### M2 — Price histories → `data/panel/prices.parquet`
For every market passing static filters (`y` not null, volume ≥ 10k, has
`clob_token_leg`): pull `/prices-history` at fidelity 1440, fallback 720.
Cache per token under `data/raw/prices/`; resumable; progress every 500
markets with ETA. Classify empty responses: `amm_era` (enable_order_book
false or volume_clob < 1% of volume_num) vs `unexplained` — report both.
Write long-format parquet: `clob_token_leg, ts (UTC), p`.

**Acceptance:** coverage ≥ 95% of eligible non-AMM markets; `unexplained`
< 1% (else stop and investigate); runtime and 429 count reported.

### M3 — Panel builder → `data/panel/p1.parquet` (+ `p2.parquet`, exploratory)
Driven entirely by `config/spec.yaml`:
```yaml
snapshot_dates: 1st of month 00:00 UTC, 2024-01-01 .. 2026-06-01   # 30 dates
staleness_max_hours: 72
price_clip: [0.01, 0.99]
volume_min: 10000
oos_locked: true          # analysis of snapshots >= 2026-01-01 forbidden until W4
```
Openness at snapshot `t`: `created_at <= t < resolution_ts`. Snapshot price:
last history point with `ts <= t`, within staleness bound, else row dropped
and counted as `missing_price`. Row = one (market, snapshot); columns:
`market_id, event_id, category, snapshot_date, p, y, volume_num,
vol_tercile (within snapshot), days_to_sched_end, days_to_resolution
(ex-post covariate — analysis-only label), fees_enabled, taker_base_fee,
leg_label, restricted`.

P2 (exploratory, plan §6 contingency triggered): snapshots at
`end_date_sched − {7, 30} days`, same openness rule via `resolution_ts`;
separate file, `design="P2"` column, never merged silently with P1.

**Acceptance:** (market_id, snapshot_date) unique; `y` constant per market;
no row violates clip/staleness/openness (property tests); missing_price share
reported per year.

### M4 — Gate B report → `spikes/gate_b.py` + `gate_b_report.txt`
Printed checks, each with a pass/attention verdict:
1. Rows and distinct markets by category × year × design; distinct events;
   markets-per-event distribution.
2. Base rate of `y` by category and by snapshot year.
3. Duplicates = 0; missing_price and staleness distributions.
4. **Early-resolution share and days-early distribution per category** (this
   decides P2's fate per plan §6: P2 stays exploratory unless a category
   shows < 15% early share).
5. AMM-era exclusions and `unexplained` empties recap from M2.
6. Spot-checks: 2024 presidential-winner market present, snapshot prices in
   plausible range mid-2024; top-10 markets by volume eyeballed.

**Acceptance:** user reviews the report, writes DECISIONS.md entries, sends
report out for external review (that's Gate B sign-off).

---

## 3. Edge cases → required pytest coverage
- `outcomes` without `"Yes"` (team names) → leg_idx 0, leg_label recorded.
- `outcome_prices` non-degenerate (e.g. `["0.5","0.5"]`) → y null, flagged.
- `outcomes`/`clobTokenIds` length mismatch or missing → skip + count.
- `closedTime` space/`+00` format; missing `umaEndDate` (fallback path).
- Early resolver: open-at-snapshot logic must exclude a market with
  `resolution_ts` < t even when `end_date_sched` > t (look-ahead guard).
- Staleness: point 71h old kept, 73h dropped.
- Dedupe across overlapping windows (same id twice).
- History timestamps in ms vs s (normalize).
- Multi-market event: same event_id on both rows.

## 4. W1 definition of done
All four milestones accepted, pytest green, `gate_b_report.txt` reviewed, raw
caches intact and re-runnable, DECISIONS.md updated by the user, one commit
per milestone.
