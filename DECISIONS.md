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