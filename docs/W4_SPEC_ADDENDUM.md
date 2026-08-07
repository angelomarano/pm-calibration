# W4 Spec Addendum — Out-of-Sample Test and Cost Model

Companion to W1_SPEC.md, M3_SPEC_ADDENDUM.md, W2_SPEC_ADDENDUM.md,
W3_SPEC_ADDENDUM.md. Same contract: read fully, propose a plan per
milestone, code only after approval.

**This is the addendum that breaks the OOS seal.** Everything in §1 must be
committed and pushed before any code reads a 2026 row. That commit hash is
the evidence that the rule predates the result. If §1 gets edited after the
first OOS number is computed, the OOS test is worthless and the honest thing
is to say so in DECISIONS.md rather than quietly proceed.

---

## 1. Frozen before the unlock

Nothing in this section may change after W4c runs. Any change afterwards
gets a DECISIONS.md entry stating what changed, when relative to first
seeing OOS output, and why the result should still be trusted (or not).

### 1.1 Trading rule R1 (primary)

Fixed thresholds, chosen ex ante, deliberately NOT derived from the
in-sample calibration map. Deriving them would add tuning degrees of
freedom exactly where the test's value depends on the rule being frozen
blind. The in-sample map determines only the *direction* of the trade, not
where the thresholds sit.

- **Longshot leg:** at a snapshot with p ∈ [0.02, 0.10], buy No (i.e. bet
  against the longshot) if the in-sample map says prices in that bucket are
  too high.
- **Favorite leg:** at a snapshot with p ∈ [0.90, 0.98], buy Yes if the
  in-sample map says prices in that bucket are too low.
- Direction per leg is read once from the frozen in-sample (≤2025-12-31)
  calibration map, recorded explicitly in the report, and not revisited.
- Equal notional per trade. Hold to resolution. Taker entry.
- One position per (market, snapshot). A market appearing at several
  snapshots generates several positions; they are NOT netted, and the
  event-clustered bootstrap handles the resulting dependence as everywhere
  else in this project.

### 1.2 R2 (exploratory, only if time allows)

Trade every bucket where the in-sample |gap| exceeds the estimated
round-trip cost. Explicitly labeled exploratory in every output; never
reported alongside R1 without that label. Do not build it before R1 is
complete.

### 1.3 Censoring

- OOS positions only on markets with `resolution_ts` on or before the data
  collection cutoff. Everything else is right-censored and excluded.
- Report the excluded share overall and per category, **and the profile of
  what was excluded** (horizon distribution, category mix, volume tercile
  mix) against what was kept. If the censored set is systematically
  different, the usable OOS sample is not representative and the report
  must say so. This is the same failure mode the ran-to-term restriction
  surfaced in W3a: a restriction that looks like "fewer rows" but is
  actually "different population."
- Mark-to-market at last observed price is a robustness cut only, clearly
  labeled, never the headline.

### 1.4 Cost model

Three components, each with its own confidence level, reported separately
before being combined:

**Fees (known exactly).** Per-market `fees_enabled` and `taker_base_fee`
from `markets.parquet` (M1). Fee applies only where `fees_enabled` is true;
the value is read second, the flag first (the invariant test written in
W2's ingestion work). Zero otherwise.

**Spread (assumed, banded).** `/orderbook-history` returns correctly-shaped
but consistently empty responses, including inside a market's own known
trading window (probed 2026-08-06, see DECISIONS.md) — so historical
spreads are not recoverable. Instead:

- Sample several hundred live order books via `/book` (which works),
  stratified by category and volume tercile, to get a contemporary
  half-spread per stratum.
- Apply those retroactively as the 1× band, with 0.5× and 2× bands
  reported alongside. State plainly in the report that these are
  contemporary spreads applied to a historical period.
- **Cross-sectional consistency check** (this is what makes the retroactive
  assumption defensible rather than merely declared): compute a Roll-style
  estimator on the historical `prices.parquet` daily series and compare the
  *ordering* of strata against the live-book ordering. The absolute level
  from daily bars is not a usable spread estimate — daily price moves are
  dominated by genuine information, not bid-ask bounce — so it must be
  presented as a relative/ordinal check only, never as "estimated
  historical spread." If the ordering broadly holds (liquid strata tighter
  than thin ones, in both), the retroactive application is reasonable. If
  it inverts, say so and widen the bands.
- Roll is undefined where the autocovariance of consecutive changes is
  positive, which is common on daily data. Report the share of markets
  yielding no estimate. If it exceeds ~50%, the check is too weak to
  support anything and the report must say the ordering check was
  inconclusive rather than quietly reporting whatever survived.

**Carry (deterministic).** Days from entry to resolution × the risk-free
rate (3M Treasury, FRED DGS3MO, matched to entry date). Note asymmetry
where relevant: Kalshi pays interest on idle balances, Polymarket USDC
locked in a position does not, so the full carry applies here.

### 1.5 Reporting metrics

- Mean net edge per trade with event-clustered bootstrap CI (same engine,
  B=2000).
- Full per-trade distribution, not just the mean — binary payoffs are
  heavily skewed and a mean alone misleads.
- Chronological PnL for drawdown and sequencing.
- Annualized return on capital deployed vs the risk-free rate.
- **Break-even half-spread**: the level at which net edge hits zero. This
  is the single most useful number in the whole report — it converts an
  assumption into a testable threshold a reader can check against their own
  execution.
- No Sharpe ratio. Binary payoffs with lumpy resolution timing make it
  meaningless here; say so once in the report rather than computing it with
  caveats.

---

## 2. Milestones

### W4a — Cost model (built and tested BEFORE the unlock)

`src/strategy/costs.py`. Fee calculation, spread haircut bands, carry.
Pure functions over already-available in-sample data. No 2026 rows touched.

Also: the live `/book` sampling spike and the Roll ordering check, both
report-only under `spikes/`. These can run against in-sample markets and
current books without touching the OOS panel.

Commit before proceeding.

### W4b — Strategy mechanics (built and tested on IN-SAMPLE data)

`src/strategy/rules.py`. R1 position generation, entry/exit, per-trade
gross and net PnL. Exercised end-to-end on ≤2025 data as a dry run, to
confirm the mechanics work and produce sane numbers, before the rule ever
sees 2026.

The in-sample dry run is NOT a result. It is a smoke test, and any output
from it is labeled as such — reporting in-sample strategy PnL as if it
meant something is exactly the circularity this whole design exists to
avoid (the rule was derived from the map fit on that same data).

Commit before proceeding.

### W4c — The unlock

Flip `oos_locked: false` in `config/spec.yaml` **as its own commit**, so the
moment of unlock is visible in the history and separable from everything
before it. Then run R1 on 2026 H1 with the frozen rule and the W4a cost
model, through `load_panel(..., allow_oos=True)`.

Report: gross edge, net edge under each spread band, break-even half-spread,
per-trade distribution, chronological PnL, censoring accounting per §1.3.

Also here: OOS calibration itself (α, β on 2026 H1, same estimator as W2d),
compared formally against the in-sample values — the persistence test. This
is separate from the strategy result and reported separately; a
miscalibration can persist without being tradeable, and that distinction is
the point.

### W4d — Fee-regime sub-cut (exploratory)

Polymarket's fee rollout (Crypto Jan 2026, Sports Feb 2026, broad V2 on
2026-03-30, geopolitics exempt, and fees applying only to markets deployed
after activation) splits H1-2026 into a natural pre/post comparison. Report
calibration and net edge either side of 2026-03-30 for affected categories.
Exploratory, small samples, labeled as such.

### Gate F — Close-out

1. Does the in-sample miscalibration persist OOS? State it plainly with the
   formal comparison.
2. Does R1 produce positive net edge under any spread band? Under which,
   and what is the break-even half-spread?
3. Censoring profile: is the usable OOS sample representative of the
   population, or systematically different?
4. Reconciliation: the in-sample dry run from W4b must reproduce exactly
   when re-run, and the OOS calibration cell must match the standalone
   estimator on the same rows. Hard assertions, same discipline as Gate E's
   W2d check.
5. Summary of record for W4, in the style of Gate E's §0: what the OOS test
   established, in plain terms, including whichever of the four possible
   outcomes actually occurred (persists and tradeable / persists but not
   net of costs / decayed / never robust in the first place).

---

## 3. What a negative result means

Worth stating in the spec, so it is not decided after seeing the number: **a
finding that the edge does not survive costs is a complete and publishable
result**, not a failure of the project. It is evidence of limits to
arbitrage — the miscalibration is a premium for frictions and locked
capital rather than free money. That framing is available in advance
precisely because it is written here, before the number exists.

The same applies to decay: if the H1-2026 calibration has moved toward 1.0
relative to 2024-25, that is a post-publication decay result on data no
published paper covers.

The one outcome that would require real caution is a large positive net
edge, because the most likely explanation for "free money in a public
market" is a bug in the cost model or a look-ahead leak, not a genuine
inefficiency. If that is what appears, Gate F's first job is an adversarial
review of the pipeline, not a celebration.

## 4. Ordering

W4a → commit → W4b → commit → **unlock commit** → W4c → commit → W4d →
Gate F. Propose W4a first only.
