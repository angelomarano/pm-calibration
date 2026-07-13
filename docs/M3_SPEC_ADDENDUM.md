# M3 Spec Addendum — Panel Builder

Refines docs/W1_SPEC.md §M3 in light of what M1/Gate B surfaced. Read the
original §M3 first; this addendum overrides it where they conflict, and
should be appended to W1_SPEC.md (or kept alongside it) before Claude Code
plans M3's implementation.

---

## 1. Openness-at-snapshot-t: resolution_ts, never end_date_sched

**Decided, not open for re-litigation in the M3 planning step.**

A market is open at snapshot t iff `created_at <= t < resolution_ts`.
`end_date_sched` must never be used for this test.

Two independent justifications, both now empirical, not just theoretical:

1. **Look-ahead (Gate A, 2026-07-09):** `resolution_ts` is only observable
   *after* it happens; at snapshot t we only know whether the market has
   *already* resolved by t, which is exactly `resolution_ts <= t`. Using
   `end_date_sched` instead would let a market that resolved weeks before t
   masquerade as "still open" at t — a market a trader could not actually
   have traded, since it was already settled.
2. **Materiality (Gate B, 2026-07-13):** on the panel-eligible population,
   every category shows ≥21% of markets resolving more than 2 days before
   their scheduled end (median days-early 7–63 depending on category, p90
   up to 379). This is not a rare edge case to special-case away — it's
   roughly a fifth-to-two-fifths of the eligible population in every
   category. Using `end_date_sched` for openness would systematically
   overstate how long a large fraction of markets stay "in the panel,"
   biasing calibration toward whatever the market looked like mid-life
   rather than near its true resolution.

`resolution_ts` is already computed in `markets.parquet` (M1,
`uma_end_date` preferred, `closed_time` fallback). M3 consumes it as-is; no
new parsing needed.

## 2. OOS boundary: build everything, gate access at the loader

**Decision: panel construction treats 2026+ snapshots as ingestion, not
analysis — M3 builds ALL snapshots, including OOS. The lock is enforced at
a shared loader function, not by refusing to build rows.**

Rationale: building a P1/P2 row for a 2026-03-01 snapshot is a deterministic
transformation of already-ingested, already-public market data (same
category as computing `scheduled_life_hours` in M1) — it produces no new
information and makes no modeling choice. It's mechanically identical to
building a 2024-01-01 row. Refusing to build it would just mean rebuilding
it later at W4 with extra ceremony, for no safety benefit — the actual risk
CLAUDE.md rule 5 is guarding against is a calibration/strategy analysis
being *fit or evaluated* on OOS data before W4, not the existence of the
rows on disk.

So the guard has to live at the boundary between "rows exist" and "rows get
read by analysis code," not at construction time. Concretely:

- Every row in `p1.parquet` / `p2.parquet` gets an `is_oos: bool` column
  (`snapshot_date >= 2026-01-01`).
- Add `src/panel/io.py` with:
  ```python
  def load_panel(path: Path, allow_oos: bool = False) -> pl.DataFrame:
      """The only sanctioned way to read a panel parquet. Raises if
      allow_oos=False (the default) and the config's oos_locked flag is
      true and the loaded frame contains any is_oos=True rows without
      them being filtered — filters OOS rows out by default rather than
      raising, UNLESS the caller explicitly passes allow_oos=True, in
      which case it checks config/spec.yaml's oos_locked flag and raises
      RuntimeError if that flag is still true (i.e. even an explicit
      opt-in can't bypass the config-level lock, only editing the config
      itself can, which is a deliberate, visible, one-line change plan
      participants would notice in a diff)."""
  ```
- W2/W3 calibration code (and everything until W4) imports and calls
  `load_panel(..., allow_oos=False)` — or just `load_panel(path)`, since
  that's the default — and structurally cannot see 2026+ rows without
  either passing `allow_oos=True` *and* someone having flipped
  `oos_locked: false` in `config/spec.yaml` first.
- M3 itself, and any Gate C/D-style sanity report on the panel, is allowed
  to read raw parquet directly (bypassing `load_panel`) purely to report
  row counts/shape — inspecting existence and shape is not "analysis" in
  the sense the lock cares about. But no actual calibration statistic
  (reliability diagrams, Brier, logit regression) gets computed on OOS
  rows before W4, full stop — that's enforced by nobody having a code path
  to fetch those rows through the sanctioned loader.

Test to add: `load_panel` with a synthetic frame containing both OOS and
non-OOS rows, `oos_locked: true` in a test config — confirms default call
returns only non-OOS rows, and `allow_oos=True` still raises while the
config flag is true.

## 3. Schema updates since the original §M3 draft

The original W1_SPEC.md §M3 config sketch predates `scheduled_life_hours`/
`panel_eligible` (added during the Crypto-volume investigation) and the
`resolution_ts`-vs-`end_date_sched` decision being empirically closed. Given
those:

```yaml
# config/spec.yaml (M3-relevant keys)
snapshot_dates: monthly, 1st of month 00:00 UTC, 2024-01-01 .. 2026-06-01  # P1
p2_horizons_days: [7, 30]              # exploratory only, per 2026-07-13 Gate B closure
staleness_max_hours: 72
price_clip: [0.01, 0.99]
oos_locked: true
oos_boundary: "2026-01-01"
```

`p1.parquet` / `p2.parquet` columns (supersedes W1_SPEC §M3's sketch):

```
market_id, event_id, category, design ("P1"|"P2"), snapshot_date, p, y,
volume_num, vol_tercile (computed within-snapshot, not global),
scheduled_life_hours, days_to_sched_end, days_to_resolution (ex-post,
label only), fees_enabled, taker_base_fee, leg_label, restricted,
is_oos, resolution_ambiguous (carried through — exclude ambiguous-y rows
at the calibration step, not silently here)
```

Note `panel_eligible` from `markets.parquet` is the *input filter* (only
eligible markets are candidates for panel rows at all) — it does not need
to be repeated as an output column, since every row in p1/p2 is by
construction eligible.

## 4. Milestones (unchanged from original §M3 structure, restated for clarity)

- **M3a — snapshot grid + openness:** given `config/spec.yaml`'s dates,
  build the (market, snapshot) candidate pairs using
  `created_at <= t < resolution_ts` (§1). No price attached yet.
- **M3b — price attachment:** for each candidate pair, find the last
  `prices.parquet` point with `ts <= t` within `staleness_max_hours`;
  drop + count as `missing_price` if none. Apply `price_clip`.
- **M3c — P2 (exploratory):** same openness rule, snapshots at
  `end_date_sched - {7, 30} days`. Written to a separate file, never
  merged with P1. Tag every row/output with "exploratory" in any report
  that surfaces it, per the 2026-07-13 DECISIONS.md closure.
- **M3d — io.py + tests:** the `load_panel` boundary from §2, with its
  OOS-filtering test.
- **M3e — Gate C:** panel sanity report (row uniqueness, y-per-market
  constant, missing_price/staleness distributions — the items §M4 of the
  original spec deferred because M3 didn't exist yet). This is what Gate
  B's report explicitly punted on.

Propose the plan (files, signatures, test list) for M3a+M3b together first,
same rhythm as M1 — don't jump to M3c/d/e until those are reviewed and
committed.
