"""W4a cost model: fees, spread haircut bands, carry.

Per docs/W4_SPEC_ADDENDUM.md §1.4/§2 (W4a). Pure functions only -- no
panel/dataframe dependency, no OOS access. Every genuinely unresolved
input below is reported as a band, never collapsed to one number, so the
report shows the actual uncertainty rather than hiding it behind a
plausible-looking single figure.

--- FEE FORMULA: resolved (with one flagged residual tension) ---

2026-08-08 investigation (time-boxed ~30 min, see DECISIONS.md): three
candidate formulas were in play --
  (1) documented quadratic: fee = C * feeRate * p * (1-p)
      (docs.polymarket.com/trading/fees)
  (2) a "contract" formula quoted in Polymarket/py-clob-client GitHub
      issue #326: fee = feeRateBps * min(p,1-p) * outcomeTokens /
      (p * BPS_DIVISOR)
  (3) a simpler linear formula, fee = C * feeRate * (1-p), which the
      same issue's reporter found matched 5 real on-chain BUY fills
      (NHL/MLB/NBA/ATP) to 0-1% error, using the rate documented AT THE
      TIME (April 2026).

Resolution: fetched docs.polymarket.com/trading/fees directly (through
this project's DNS-pinned session -- the domain is affected by the same
ISP-level block as clob/gamma) and checked formula (1) against
Polymarket's OWN official worked-example table ("Fee Tables, 100 Shares")
across its full price grid, not just one point: at every price checked
(p=0.05, 0.10, 0.25, 0.50, 0.90), C * feeRate * p * (1-p) reproduces the
table's USDC fee to the cent (e.g. crypto, p=0.50: 100*0.07*0.5*0.5=1.75,
table says $1.75). That is Polymarket's own live, self-consistent,
current documentation -- the strongest verification available without a
funded live order of our own.

Formula (2), the GitHub-issue-quoted "contract" formula, matches NEITHER
the official worked table NOR the reporter's own real fills (checked
directly: at their MLB example, p=0.65, rate=0.03, it predicts 0.0808
shares of fee against an actual 0.0525) -- rejected by two independent
sources, kept below ONLY as a labeled, discredited footnote in case a
future reader wants the number.

RESIDUAL, UNRESOLVED TENSION (flagged, not swept under the rug): formula
(3) reproduces the April-2026 real fills exactly, and formula (1) is
what's live now -- these could both be right if the underlying mechanism
changed between the report and now without a formula being announced, or
the April report's own ATP/NBA rows (where "ordered" shares were already
non-integer, e.g. 5.07/5.26) may have pre-compensated for an assumed fee
in the order sizing, making that comparison partly circular; the cleaner
NHL/MLB rows don't have this issue. Not resolved further within the
time-box. If this later turns out to matter, the fix is cheap: formula
(3)'s multiplier is a fourth key to add to fee_cost's returned dict.

fee_cost therefore returns TWO fee estimates (not four): "base"/"upper"
(rate source, unchanged from before) both computed under formula (1),
plus "footnote_contract_formula_base"/"..._upper" under the REJECTED
formula (2), retained only as a labeled footnote per the resolution
above -- not a live sensitivity band.

Also surfaced during this investigation, relevant to whoever wires this
into W4b: R1's frozen rule (§1.1) means BOTH legs buy a token priced near
certainty. The favorite leg buys Yes directly at p in [0.90,0.98]. The
longshot leg buys NO (betting against the longshot), which trades at
(1 - p_yes) -- also in [0.90,0.98], not at the snapshot's own low p. The
`price` argument passed into fee_cost must be the price of the token
ACTUALLY BOUGHT, not the panel's raw p column, or the fee magnitude will
be badly wrong in exactly the direction that would understate cost on
the longshot leg (since p*(1-p) and the rejected formula both depend on
which side's price is used).

--- FEE RATE: published category schedule (base case) vs. the ingested
    field (upper-bound sensitivity only) ---

`taker_base_fee` (Gamma's field, ingested into markets.parquet) is a
point-in-time snapshot of CURRENT market configuration at ingestion time
(pulled July 2026, after Polymarket's full fee rollout) -- not a
historical record of what applied when a trade would have entered a
market earlier. Confirmed empirically (2026-08-08, see DECISIONS.md):
every fees_enabled=True row across all 7 categories carries the
identical taker_base_fee=1000 (bps; confirmed via the same GitHub issue
above -- "1000 bps = 10%"), including all 323 Geopolitics rows, which the
published schedule states are exempt. A uniform 10% is not a plausible
read of a schedule described as differentiated -- most likely reading:
the field is a contract-level configuration ceiling, not the rate
actually charged. Used ONLY as an upper-bound sensitivity band
(rate_upper), never as the base case.

Base case instead uses the published per-category schedule, confirmed
directly from docs.polymarket.com/trading/fees (the same fetch that
resolved the formula above): crypto 0.07; sports 0.05; finance/politics/
mentions/tech 0.04; economics/culture/weather/other 0.05; geopolitics 0,
confirmed EXPLICITLY stated as exempt -- "Geopolitical and world events
markets are fee-free. Polymarket does not charge fees or profit from
trading activity on these markets" -- not merely absent from the table.

This project's 7-category taxonomy is coarser than V2's finer split, so
two categories require a resolved approximation, documented rather than
picked silently: "Econ/Finance" spans V2's Finance (0.04) and Economics
(0.05) -- resolved to 0.05 (the higher, conservative choice). "Other"
spans V2's Tech/Mentions (0.04) and Weather/Other (0.05) -- resolved to
0.05 for the same reason. Revisit if this mapping needs to be split
before Gate F.

--- FEE ACTIVATION DATES: verified externally, not from memory ---

Crypto 2026-01-05 (15-minute markets first, funded by a maker-rebate
program), Sports 2026-02-18 (piloted narrowly: NCAA basketball + Italian
Serie A first), all other categories under the broad V2 rollout,
2026-03-30. The crypto date was not previously recorded anywhere in this
repo (DECISIONS.md's 2026-07-13 entry only had the two later ones) and
was independently verified here before use, not carried over from
memory.

--- SPREAD: one-sided (entry only) ---

R1 holds to resolution -- there is no exit leg before resolution, so the
spread cost is the entry-side half-spread only, not a round-trip. (R2's
"round-trip cost" language in the addendum applies only to R2, which is
not built here.)

--- CARRY: ACT/365 ---

A stated day-count convention choice, not derived from anything in the
spec.
"""

from __future__ import annotations

from datetime import datetime, timezone

FEE_PRICE_EPS = 1e-6  # guards the contract formula's /p term at the clip bounds

CATEGORY_FEE_RATE: dict[str, float] = {
    "Crypto": 0.07,
    "Sports": 0.05,
    "Econ/Finance": 0.05,  # conservative: max(V2 Finance=0.04, V2 Economics=0.05)
    "Culture": 0.05,
    "Other": 0.05,  # conservative: max(V2 Tech/Mentions=0.04, V2 Weather/Other=0.05)
    "Politics": 0.04,
    "Geopolitics": 0.0,
}

TAKER_BASE_FEE_BPS_DIVISOR = 1e4  # ingested taker_base_fee -> fraction, upper-bound band only

CATEGORY_FEE_ACTIVATION: dict[str, datetime] = {
    "Crypto": datetime(2026, 1, 5, tzinfo=timezone.utc),
    "Sports": datetime(2026, 2, 18, tzinfo=timezone.utc),
}
BROAD_V2_ACTIVATION = datetime(2026, 3, 30, tzinfo=timezone.utc)  # every other category


def fee_activation_date(category: str) -> datetime:
    """Category's fee activation date -- Crypto and Sports piloted early,
    every other category (and any Crypto/Sports market created before
    its own pilot date) starts under the broad V2 rollout."""
    return CATEGORY_FEE_ACTIVATION.get(category, BROAD_V2_ACTIVATION)


def is_fee_bearing(category: str, created_at: datetime) -> bool:
    """A position's fee applies only if its market's created_at postdates
    its category's fee activation date -- the temporal rule that replaces
    Gamma's fees_enabled flag (see module docstring: that flag reflects
    ingestion-time configuration, not the sample period)."""
    return created_at > fee_activation_date(category)


def fee_cost(
    category: str,
    created_at: datetime,
    price: float,
    taker_base_fee: float,
    notional: float = 1.0,
) -> dict[str, float]:
    """Returns four keys, never collapsed to one number:
      - base: published per-category rate, resolved formula (1)
        (p*(1-p) -- confirmed against Polymarket's own live worked-example
        table, see module docstring)
      - upper: ingested taker_base_fee / 1e4, same resolved formula
        (upper-bound rate sensitivity only, see module docstring)
      - footnote_contract_formula_base / _upper: same two rate sources,
        under the REJECTED "contract" formula (min(p,1-p)/p) -- retained
        only as a labeled footnote per the investigation's resolution,
        not a live sensitivity band.
    `price` must be the price of the token actually bought (see module
    docstring's note on R1's two legs), not necessarily the panel's raw
    p column. All four keys are 0.0 if is_fee_bearing is False -- every
    in-sample (2024-2025) trade is therefore unconditionally all-zero,
    since no fee regime existed yet.
    """
    if not is_fee_bearing(category, created_at):
        return {"base": 0.0, "upper": 0.0, "footnote_contract_formula_base": 0.0, "footnote_contract_formula_upper": 0.0}

    p = min(max(price, FEE_PRICE_EPS), 1 - FEE_PRICE_EPS)
    resolved_mult = p * (1 - p)
    rejected_mult = min(p, 1 - p) / p

    rate_base = CATEGORY_FEE_RATE.get(category, 0.0)
    rate_upper = taker_base_fee / TAKER_BASE_FEE_BPS_DIVISOR

    return {
        "base": notional * rate_base * resolved_mult,
        "upper": notional * rate_upper * resolved_mult,
        "footnote_contract_formula_base": notional * rate_base * rejected_mult,
        "footnote_contract_formula_upper": notional * rate_upper * rejected_mult,
    }


def spread_haircut(half_spread: float, band_multiplier: float, notional: float = 1.0) -> float:
    """One-sided (entry only) haircut -- see module docstring re: R1
    holding to resolution. band_multiplier is one of the live-book
    sampling spike's bands (0.5x / 1x / 2x)."""
    return notional * half_spread * band_multiplier


def carry_cost(days_held: float, annual_rate_pct: float, notional: float = 1.0) -> float:
    """ACT/365. annual_rate_pct is the 3M Treasury rate (FRED DGS3MO,
    percent, e.g. 5.0 for 5%) matched to the entry date."""
    return notional * (annual_rate_pct / 100.0) * (days_held / 365.0)


def total_cost(
    *,
    category: str,
    created_at: datetime,
    price: float,
    taker_base_fee: float,
    half_spread: float,
    band_multiplier: float,
    days_held: float,
    annual_rate_pct: float,
    notional: float = 1.0,
) -> dict[str, float]:
    """Combines all three components. Fee carries its four rate-source x
    formula keys through to four matching total_* keys; spread and carry
    are single numbers here (their own bands are the caller's
    band_multiplier sweep and the Roll ordering check, not this
    function)."""
    fee = fee_cost(category, created_at, price, taker_base_fee, notional)
    spread = spread_haircut(half_spread, band_multiplier, notional)
    carry = carry_cost(days_held, annual_rate_pct, notional)

    result = {f"fee_{k}": v for k, v in fee.items()}
    result["spread"] = spread
    result["carry"] = carry
    for k, v in fee.items():
        result[f"total_{k}"] = v + spread + carry
    return result
