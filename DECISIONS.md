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

2026-07-22 — W2d horizon-tercile planning: cluster count, not row count,
is the binding constraint for any future stratification in this project.
Checked all 7 categories' thinnest days_to_sched_end tercile (within-
category qcut) by distinct-cluster count (event_id coalesced with
market_id), not row count: Other (85 clusters), Econ/Finance (139), and
Culture (192) all fail a 200-cluster floor outright despite comfortable
row counts (569-1,660 rows in the failing cells) — a handful of very
long-lived markets contribute many repeated snapshot rows per cluster,
concentrated unevenly across horizon terciles. Sports clears the floor
only barely (204 clusters) despite being the largest category by rows
(18,793) and total clusters (2,680), for the same underlying reason.
Geopolitics (260) and Crypto (264) clear comfortably enough to split but
are noted as thin. Politics (501) is the only category with real margin.

Mitigation: build_horizon_stratified_report checks n_clusters_per_cell
(src/inference/bootstrap.py, now a shared utility) per category at
runtime; any category whose thinnest tercile falls below 200 clusters
gets one pooled row (role=SECONDARY_POOLED, with a note) instead of three
unreliable sub-cells. n_clusters is reported in every row regardless, so
degradation is visible rather than trusted blindly. This rule -- check
cluster count, not row count, before trusting any stratified cell --
applies to W3's reconciliation grid too (design x horizon x sample x
period), which stratifies further still and will hit the same
constraint, likely worse.

2026-08-06 — W3b (reconciliation grid): volume weighting does not mainly
shift the point estimate, it collapses effective sample size. Kish's
n_eff = (sum w)^2 / sum(w^2) computed per cell shows the weighted fits
using a fraction of the nominal rows: ran_to_term/2024 goes from n=6,135
(equal weight) to n_eff=46.2 (~133x collapse), ex_Sports/2024 from 8,988
to 109.0, and every one of the 8 equal-vs-weighted pairs widens the beta
CI (ratios 2.05x to 14.32x, none narrower). Cause: Polymarket volume is
heavily right-skewed, so a handful of large markets dominate the weighted
likelihood.

Relevance beyond this project: a trade-level analysis implicitly volume-
weights (a market with 1,000 trades enters 1,000 times), so a nominal N
in the hundreds of millions of trades can carry an effective sample size
orders of magnitude smaller. This reverses the usual objection to
market-level inference ("you discarded millions of observations"): on
this data, the trade-weighted design has less effective information, not
more. Quantified on the same panel, same estimator, same bootstrap — the
only thing that changes is the weighting.

2026-08-08 — W4a: fees_enabled/taker_base_fee (Gamma fields) are a
point-in-time snapshot of CURRENT market configuration at ingestion time
(pulled July 2026, after Polymarket's full fee rollout), not a historical
record of what a trader would have paid entering a market on any earlier
date. Confirmed empirically before assuming otherwise: every
fees_enabled=True row across all 7 categories carries the identical
taker_base_fee=1000 (bps units, confirmed via a Polymarket py-clob-client
GitHub issue), including all 323 Geopolitics rows — directly
contradicting Polymarket's own 2026-03-30 "Fee Structure V2" announcement,
which describes differentiated per-category rates (crypto 7%, sports 3%,
finance/politics/mentions/tech 4%, economics/culture/weather/other 5%,
geopolitics not listed). Using fees_enabled directly would have applied
2026 fee configuration retroactively to 2024-2025 in-sample trades, which
never faced any fee regime at all.

Fixed with a temporal rule instead of the flag: a position's fee applies
only if its market's created_at postdates that market's category's fee
activation date. Dates externally verified rather than taken from memory
(this repo's own Gate A/M2 notes only had the two later ones, not the
crypto date): Crypto 2026-01-05 (15-minute markets first), Sports
2026-02-18 (pilot: NCAA basketball + Serie A initially), all other
categories under the broad V2 rollout, 2026-03-30. In-sample (2024-2025):
fee=0 always, unconditionally — no fee regime existed yet. For the OOS
window (H1 2026), computed a fee-bearing share using ONLY
markets.parquet's scheduling metadata (created_at, start_date,
end_date_sched, resolution_ts, category, panel_eligible) — no price or
outcome data read, keeping the OOS panel itself untouched at this stage:
of 58,472 panel-eligible markets whose active window overlaps Jan-Jun
2026, 58.7% are fee-bearing under the temporal rule (market-count
weighted, not trade-weighted). Sports (68.4%) and Crypto (64.0%) drive
this up; every other category sits at 24-32%. Not a small share — the
fee-formula dispute (see src/strategy/costs.py's module docstring:
Polymarket's documented p*(1-p) formula vs. its deployed contract's
min(p,1-p)/p, which diverge sharply exactly at R1's tail trading points)
therefore matters for the OOS cost accounting, not a second-order detail
to wave through.

2026-08-08 — W4a fee formula, time-boxed (~30 min) investigation:
resolved, with one flagged residual tension. Fetched
docs.polymarket.com/trading/fees directly (through this project's
DNS-pinned session — the domain sits behind the same ISP-level block as
clob/gamma). Two corrections to the previous entry's numbers first: the
official table gives Sports = 0.05, not the 0.03 quoted there (that 0.03
came from a since-superseded reading of a GitHub issue filed against an
earlier version of the docs); the official per-category table is crypto
0.07, sports 0.05, finance/politics/mentions/tech 0.04,
economics/culture/weather/other 0.05, geopolitics 0 (explicitly stated
fee-free, not merely absent).

Formula: checked the documented fee = C*feeRate*p*(1-p) against
Polymarket's own worked-example table ("Fee Tables, 100 Shares") across
its full price grid (p=0.05/0.10/0.25/0.50/0.90) — matches the table's
USDC fee to the cent at every point checked. That table is Polymarket's
own live, internally self-consistent documentation, the strongest
verification available short of a funded live order. The "contract"
formula quoted in Polymarket/py-clob-client issue #326
(min(p,1-p)/p) matches NEITHER that table NOR the issue's own reported
real fills — rejected by two independent sources, demoted to a labeled
footnote in fee_cost, not a live band. Base case is now the documented
p*(1-p) formula with the corrected rate table above.

Residual tension, not resolved further within the time-box: issue #326's
reporter measured 5 real on-chain BUY fills (NHL/MLB/NBA/ATP) matching a
simpler linear fee = C*feeRate*(1-p) to 0-1% error, using the rate
documented in April 2026. That contradicts the p*(1-p) resolution above.
Possible explanations neither confirmed nor ruled out: the rate or
formula changed between April and now without an announcement, or the
reporter's NBA/ATP rows (non-integer "ordered" share counts, e.g.
5.07/5.26) may have already absorbed an assumed fee into their order
sizing, making that specific comparison partly circular — their NHL/MLB
rows don't have this issue and still support the linear reading. Flagged
in costs.py's docstring; not built as a third live band, since the
official table's full-grid match is stronger evidence and adding a third
band would over-complicate the matrix for a residual doubt this specific.

Also surfaced, relevant to W4b: R1's frozen rule means BOTH legs buy a
token priced near certainty — the favorite leg buys Yes at p in
[0.90,0.98] directly, and the longshot leg buys NO (betting against the
longshot), which trades at (1 - p_yes), also in [0.90,0.98], not at the
snapshot's own low p. Whoever wires fee_cost into R1's mechanics must
pass the price of the token actually bought, not the panel's raw p
column, or the longshot leg's fee will be computed at the wrong end of
the price range.

2026-08-08 — W4c OOS unlock (commit 8686595) and a bug caught on the
first real run, before drawing any conclusions from it. w4c_oos_result.py's
real-run branch called load_panel(..., allow_oos=True) directly instead of
going through load_calibration_frame(), so it never dropped the null-y /
resolution_ambiguous rows that every other calibration/strategy use in
this project drops (78 in-sample, 27 OOS rows). Consequence: a null y
makes build_r1_positions' `won` column null, and `if row["won"]:` in
attach_costs_and_pnl treats Python None as falsy — silently scoring an
unresolved position as a guaranteed loss. Quantified before fixing: 3 of
7,726 OOS positions (all one Culture market) were affected — small in
count, but it also produced a visibly wrong signal that made the bug easy
to catch: calibration_stat_fn's full-sample (non-resampled) fit returned
beta=nan for both in-sample and OOS in the persistence-check section,
since the IRLS fit chokes on null y propagating through the arithmetic.

Fixed two ways: (1) w4c_oos_result.py's real-run branch now applies the
same independent-count null-y/resolution_ambiguous drop
load_calibration_frame() uses, reporting dropped_null_y/dropped_ambiguous
counts same as everywhere else; (2) attach_costs_and_pnl now hard-asserts
`won`.null_count()==0 before doing anything else, converting this failure
mode into a loud crash for any future caller, not just this one script —
same discipline as Gate E's W2d reconciliation assertion. Regression test
added (test_attach_costs_and_pnl_rejects_null_won_loudly).

Corrected re-run: beta=nan resolved to real values (in-sample beta=1.165,
matching W2d's headline exactly; OOS beta=1.077), and the 3 affected
positions' removal shifted the headline numbers by less than 0.001 in
every reported band — the bug was real and worth fixing on principle
(silent wrong answers are not acceptable regardless of size), but not
large enough to have changed any qualitative reading of the result.

Also updated two stale tests that asserted the pre-unlock lock state
(test_load_spec_config_real_file, test_load_panel_real_p1_parquet_integration_check)
to reflect that config/spec.yaml's oos_locked is now false, per the W4c unlock.