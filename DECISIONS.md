2026-07-09 — Gate A (n=207, stratified 3y × 3 vol terciles): GO. prices-history
coverage 97% at fidelity 1440; gaps concentrated in low-volume 2024 markets
(suspect AMM-era, enableOrderBook=false — verify in W1). Token alignment clean:
0/31 inversion suspects.

2026-07-09 — Market openness at snapshot t determined by actual resolution
(umaEndDate/closedTime), never scheduled endDate: 74–83% of 2025–26 markets
resolve early (median 45d, p90 206d). Using endDate would introduce look-ahead.

2026-07-09 — P2 (deadline-anchored panel) downgraded to exploratory per plan
§6 contingency (early-resolution share >> 15%). P1 (calendar panel) is the sole
primary design. Per-category early share to be measured at Gate B.

2026-07-12 — resolve_y relaxed: status=="proposed" with degenerate prices and
resolution_ts >24h old is now treated as resolved (not ambiguous), matching
status=="resolved". Empirical driver: 20/232 Gate-A markets (8.6%, above the
<3% acceptance bar) were long-settled 2024 events (Oscars, primaries, Super
Bowl LVIII) permanently stuck in "proposed" — likely because nobody called
on-chain settlement on small/old markets, not because the outcome was
unclear (all 20 checked: 853-913 days old, fully degenerate prices). 24h
buffer (vs the ~2h UMA dispute window) guards against trusting a market
still inside its actual dispute window if this pipeline is ever rerun
near-live. Verified clean at full scale: ambiguous-resolution share 0.3%
across all 331,035 markets in the real M1 pull.

2026-07-12 — Gamma /markets pagination switched from offset-based to
/markets/keyset + after_cursor. Offset hard-ceilings around offset=2000
(HTTP 422 "offset too large, use /markets/keyset for deeper pagination"),
hit for real during the full M1 pull once monthly volumes exceeded ~2000
rows/window (from 2025-03 onward — month-over-month market counts grew far
faster than Gate A's small stratified sample suggested). The continuation
param name (after_cursor) was found by brute force: cursor / next_cursor /
starting_after / page_cursor / after are all silently ignored by the server
(HTTP 200, page 1 repeated, no error) — added a same-page-id-overlap guard
in fetch_window_pages that raises loudly if pagination ever stalls this way
again, rather than silently looping or duplicating data.

2026-07-12 — Added scheduled_life_hours (float) and panel_eligible (bool,
= scheduled_life_hours >= 168h) columns to markets.parquet. 168h (7 days)
is the structural minimum for P2's T-7 leg — a market must still be open 7
days before its own scheduled deadline to ever appear there; anything
shorter-lived can also only hit a P1 monthly snapshot by near-zero-
probability coincidence. Non-anticipatory: derived from startDate/endDate,
known at market creation, not from realized outcomes. This is a resource
optimization for M2 (only panel_eligible markets get a CLOB price-history
call), not a sample restriction — every volume-qualifying market is still
kept in markets.parquet regardless of eligibility, for population-size
honesty. Empirically, 28.5% of the real 331,035-market population is
panel-eligible. This also closes a concern raised earlier the same day:
Gamma's real population turned out to be 46.5% Crypto, dominated by ~24h
"Up or Down" micro-markets (title text implied 5-15min windows; verified
against real startDate/endDate fields instead, which is what surfaced the
true ~24h life) — Crypto collapses to 5.2% of the panel-eligible population
while Sports rises to 66.7%. The panel design already structurally excludes
almost all of the Crypto volume; no separate ex-Crypto headline treatment
(alongside the already-planned ex-Sports one) is needed for W2/W3.

2026-07-12 — Full M1 pull (331,035 distinct markets, 43.6 min total
runtime) hit 2 non-recoverable gaps: window 2025-11 (19,100 markets
captured before the gap) and 2025-12 (24,000 captured), each a persistent
"internal server error" (HTTP 500) from Gamma's /markets/keyset at one
exact cursor — confirmed non-transient via 5/5 identical manual retries
against the live API, not something backoff/retry fixes. True totals for
these 2 of ~24 study months are somewhat higher than captured. A skip-
and-log-gap policy (fetch_window_pages, MAX_PAGE_RETRY_ROUNDS=3) logs both
to data/raw/gamma/_gaps.jsonl and continues rather than blocking the whole
pull. Accepted as a bounded, documented undercount affecting 2 of ~24
study months; no re-pull attempted (retrying the identical cursor is known
not to work).

2026-07-13 — Full M2 run (94,442 panel-eligible markets, 11.33h runtime,
0 gaps): coverage 98.86% (bar >=95%, pass), but unexplained share 1.135%
(bar <1%, fail) — 1,072 markets with zero price history at either
fidelity, live-verified, not amm_era (enable_order_book=True,
volume_clob≈volume_num). Concentrated: 90.7% Sports, 98.2% with 2026
end_date_sched, all above-median volume (p50 $89k vs $60.8k overall),
100% already uma_resolution_status=="resolved". Root cause unconfirmed
after a 4-round live investigation: (1) fresh /prices-history refetch,
cache bypassed — still empty, not a caching artifact; (2) fresh Gamma
market lookup by market_id on 5 affected markets — clobTokenIds and
conditionId match our stored values exactly, negRisk/negRiskOther both
False, no token migration or reissuance signal; (3) CLOB /book endpoint
— 404 on all 5, but a control token that DOES have price history also
404s post-resolution, so this is normal teardown behavior, not
diagnostic; (4) date-clustering check against the known 2026-02-18
(sports taker fees) and 2026-03-30 (Fee Structure V2) rollout dates — no
clean clustering, 2/5 sampled markets predate both changes by ~a year;
(5) full raw top-level-field diff between the 5 affected markets and 5
matched controls (same category/volume range/2026 dates, WITH history) —
no field showed a confident systematic split; a few (comboStatus,
secondsDelay, customLiveness) showed soft directional skew at n=5 per
group but no plausible causal link to missing price history and no
statistical confidence at that sample size. Accepted as a bounded,
documented gap (1.14% of panel-eligible markets) — same treatment as the
M1 pagination gaps — rather than pursuing further investigation; 1.14%
vs. a 1% bar did not warrant more time than this five-round check.

Gate B (2026-07-13): panel-eligible-only early-resolution share (the
population that could actually appear in P1/P2) shows every category
at or above ~21%, none below the 15% bar — including Econ/Finance
(20.9% eligible-only vs 12.9% on the full population, the wrong
direction for the macro/Fed-resolves-on-schedule hypothesis raised at
Gate A). The full-population table was misleading: diluted by
structurally short-lived markets that mechanically cannot resolve >2
days early relative to their own lifespan. P2 (deadline-anchored panel)
is confirmed globally exploratory per the plan §6 contingency, no
per-category reinstatement. P1 (calendar panel) is the sole primary
design, as decided 2026-07-09.

2026-07-13 — Gate C (M3e): investigated missing_price (8.49% of P1
candidate pairs, 8,896/104,795). Initial hypothesis was that this
overlapped substantially with M2's 1,072-market unexplained-price gap
(2026-07-13 entry) given a similar surface pattern (Sports-heavy,
2026-skewed). Quantified rather than assumed: only 1.0% (85/8,896)
actually trace to that known gap — hypothesis rejected. Real cause: 75.9%
of missing_price rows have no CLOB price point before the snapshot date
at all (monthly snapshots landing close to a market's creation, before
enough trading history accumulates), and 24.1% have a point that exceeds
the 72h staleness bound (median 96h, max 120h — modest sparse-trading
gaps). Concentration by category (Sports 10.2% vs Politics 5.5%) and year
(3.8% in 2024 -> 10.8% in 2026) reflects the panel-eligible population's
own growth skew (Gate B), not a data-quality defect. No action needed —
expected behavior of monthly snapshots against a growing, unevenly-aged
market population.

Category x is_oos counts (Gate C) all clear the ~200/cell calibration
target on non-OOS rows (smallest: Other at 1,708). W2 watch-item, not a
blocker: if horizon/vol_tercile sub-splitting shrinks the smaller
categories (Other, Geopolitics, Crypto) further, headroom there is much
tighter than Politics/Sports — revisit once that cell structure is
decided.