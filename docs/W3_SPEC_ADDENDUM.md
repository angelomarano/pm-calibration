# W3 Spec Addendum — Reconciliation Grid

Companion to docs/W1_SPEC.md, docs/M3_SPEC_ADDENDUM.md, docs/W2_SPEC_ADDENDUM.md.
Same contract: read fully, propose a plan per milestone, code only after approval.

## 0. What W3 is for

Published analyses of this same data disagree on whether the favorite-longshot
bias exists. W3 tests the hypothesis that the disagreement is a design
artifact: that the same panel produces different answers depending on how the
analysis is set up, and that some of those setups are defensible while others
aren't.

The centerpiece is the **clock comparison** (W3a). Everything else is the grid
built around it (W3b-c).

## 1. Fixed decisions

- **Grid scope:** the full 4-axis grid runs on **pooled ex-Sports only**.
  Per-category cuts are limited to the weighting axis alone. Rationale: 2
  weightings x 3 clocks x 3 samples x 2 periods = 36 pooled cells; crossing
  with 7 categories would be 252 cells, and W2d already established that
  cluster counts fall below the 200 floor well before that depth.
- **Volume weighting:** weighted IRLS (weights in the likelihood) as primary.
  Weighted resampling inside the bootstrap is an optional robustness cut only
  if time allows; do not build it speculatively.
- **Cluster floor:** the `n_clusters_per_cell` utility from W2d applies
  unchanged. Every grid cell reports `n_clusters` alongside `n`. Cells below
  200 clusters are computed but flagged `LOW_POWER` in a dedicated column,
  never silently dropped and never presented as equal to the rest.
- **Inference:** `event_bootstrap` remains the single source of every CI, at
  B=2000. No analytical SEs anywhere in the primary output.
- **OOS lock:** unchanged. All W3 work goes through `load_panel()` defaults.
  2026 stays sealed until W4.
- **Data:** all clocks are computed from columns already in `p1.parquet`
  (`snapshot_date`, `days_to_sched_end`, `days_to_resolution`). No re-ingestion,
  no new API calls. If something needed is genuinely absent, stop and flag it
  rather than adding a pull.

## 2. W3a — The clock comparison (centerpiece, build first)

### The question

W2d measured horizon as `days_to_sched_end`: ex-ante, known at snapshot time,
non-anticipative. Under that clock, the literature's "compression rises with
horizon" pattern did not reproduce (Gate D: only Geopolitics rises
directionally, CIs overlapping; Crypto/Politics/Sports non-monotonic).

Published work generally measures horizon as time remaining until **actual
resolution**. That quantity is not knowable at snapshot time, and it is
correlated with the outcome: markets that resolve early are systematically
different from those that run to term (Gate B: 21-40% of eligible markets in
every category resolve >2 days early). A short-τ bin is therefore enriched
with early resolvers, which is a selected subpopulation, not a horizon effect.

**So: does the published pattern reappear on this panel when the clock is
switched, holding everything else identical?**

If yes, that is a quantified claim that a chunk of the published horizon
effect is stopping-time selection. If no, the pattern's absence here is
something else and needs a different explanation. Either answer is a result.

### Design

Same rows, same estimator, same inference. Only the stratification variable
changes:

- **Clock A (ex-ante):** `days_to_sched_end` terciles, within category.
  Already implemented in W2d's `horizon_tercile`.
- **Clock B (ex-post / literature):** `days_to_resolution` terciles, within
  category. This column exists in `p1.parquet` and has been reserved for
  exactly this purpose since M3 (addendum §1: "ex-post, label only, reserved
  for W3's stopping-time comparability analysis").
- **Clock C (deadline-anchored):** rows binned by `days_to_sched_end` but
  restricted to markets that actually ran to term (resolution within ~2 days
  of scheduled end). This isolates the mechanism: if Clock B differs from
  Clock A mainly because of early resolvers, then Clock C should look like
  Clock A on the subset where the two clocks nearly coincide. Report the
  share of rows this restriction drops.

### Required output

A single table, one row per (category, clock, tercile): β, CI, n, n_clusters,
LOW_POWER flag. Plus a summary block stating, per category, whether the β
sequence rises under each clock and whether CIs are non-overlapping (reuse
Gate D's verdict logic — "rising with non-overlapping CIs" vs "directionally
rising but overlapping" vs "not monotonic").

### Interpretation guard

The comparison is descriptive. Clock B is **not** endorsed as a valid design
here; it is reproduced to show what it does. Any narrative text in the report
must say so explicitly, so nobody reads the Clock B column as this project's
estimate.

### Suggested module

`src/calibration/clocks.py`, reusing `calibration_stat_fn`, `event_bootstrap`,
`n_clusters_per_cell`. Report script `spikes/w3a_clocks.py` following the
Gate A-D pattern.

## 3. W3b — The reconciliation grid

Pooled ex-Sports, all 36 cells:

| Axis | Levels |
|---|---|
| Weighting | equal, volume-weighted |
| Clock | ex-ante (A), ex-post (B), ran-to-term (C) |
| Sample | all, ex-Sports, top liquidity tercile |
| Period | 2024, 2025 |

Note the sample axis interacts with the pooled scope: "all" includes Sports,
"ex-Sports" is the headline population, "top liquidity tercile" tests whether
the effect concentrates in thin markets. Period splits test stability across
the 2024 election cycle versus 2025.

Output: one row per cell with β, CI, n, n_clusters, LOW_POWER. Plus a
**design-sensitivity figure**: β with CI across cells, grouped so the reader
can see at a glance which axis moves the estimate most. One figure, not eight.

Weighted IRLS: extend `fit_calibration_regression` with an optional `weights`
argument (default None preserving current behavior exactly, with a test
asserting that). Weights are `volume_num` normalized within the cell. The
existing quasi-separation safeguard applies unchanged.

## 4. W3c — Per-category weighting cut

The weighting axis alone, per category, on the ex-ante clock and the full
in-sample period. Seven categories x 2 weightings = 14 cells. This is the
direct analogue of the published trade-level vs market-level divergence, cut
by category so the reader can see where volume weighting matters most.

## 5. Gate E

Report-only spike, same pattern as Gates A-D:

1. Does any single axis flip the sign of the pooled result (β crossing 1.0)?
   Name it explicitly if so.
2. Widest and narrowest cells: which design produces the most and least
   precise estimates, and does that track n_clusters as expected?
3. LOW_POWER cell count, and confirmation that none of them are being read
   as headline findings.
4. Clock comparison summary restated in one paragraph: does the published
   pattern reappear under Clock B, and what does Clock C say about the
   mechanism.
5. Reconciliation against W2d's headline (pooled ex-Sports, equal weight,
   ex-ante clock, full period should reproduce β=1.165 exactly, since it's
   the same computation). If it doesn't match to the last digit, something
   is wrong in the grid plumbing.

Exit: W3 closes, W4 (out-of-sample + costs) opens.

## 6. Ordering and commits

W3a (clocks module + report) → commit → W3b (weighted IRLS + grid + figure) →
commit → W3c → commit → Gate E → commit. Propose the plan for W3a first only.
Per the granularity rule, a module and its tests passing together is a commit,
regardless of whether the milestone is finished.
