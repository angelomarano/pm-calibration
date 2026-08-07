"""W4a cost model: fees, spread haircut bands, carry.

Per docs/W4_SPEC_ADDENDUM.md §1.4/§2 (W4a). Pure functions only -- no
panel/dataframe dependency, no OOS access. Every genuinely unresolved
input below is reported as a band, never collapsed to one number, so the
report shows the actual uncertainty rather than hiding it behind a
plausible-looking single figure.

--- FEE FORMULA: two disputed variants, both always reported ---

Polymarket's public documentation states the taker fee as
    fee = C * feeRate * p * (1 - p)
-- quadratic in price, peaking at p=0.5, shrinking toward zero at the
extremes (marketmath.io, "Polymarket Fees Explained: Per-Category Taker
Fees (2026)").

Polymarket's deployed on-chain contract computes it differently:
    fee = feeRateBps * min(p, 1-p) * outcomeTokens / (p * BPS_DIVISOR)
(Polymarket/py-clob-client GitHub issue #326, which reports the
documentation/contract discrepancy directly, with worked examples). This
does NOT shrink to zero in the tails: as p -> 0 or p -> 1, min(p,1-p)/p
-> 1, so the contract's fee multiplier approaches the FULL rate exactly
where R1 trades (p in [0.02,0.10] or [0.90,0.98]).

The ratio contract/documented = 1/(p*(1-p)) is independent of the rate
and ranges from ~11x at p=0.10/0.90 to ~51x at p=0.02/0.98 across R1's
entire trading range (verified 2026-08-08) -- the single largest source
of uncertainty in this cost model. fee_cost returns both variants always;
the deployed contract is what actually executes trades, so its variant is
the conservative default, and the documented variant is the optimistic
bound. If R1's edge survives under the contract variant, the result is
robust to the dispute; if it only survives under the documented one, that
must be stated plainly, never buried.

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

Base case instead uses the published "Fee Structure V2" (2026-03-30)
per-category schedule (marketmath.io, same source as the formula above --
table: politics/finance/tech/mentions 0.04; sports/economics/culture/
weather/other 0.05; crypto 0.07; geopolitics 0, confirmed EXPLICITLY
stated as exempt -- "Geopolitics and world-events markets charge no
taker fee and no maker fee at any price" -- not merely absent from the
table).

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
    """Returns all four combinations of {rate source} x {formula},
    always, never collapsed to one number:
      - base_contract, base_documented: published V2 category rate
      - upper_contract, upper_documented: ingested taker_base_fee / 1e4
    (upper-bound sensitivity only, see module docstring)
    contract = rate * min(p,1-p)/p (what the deployed contract executes).
    documented = rate * p*(1-p) (Polymarket's written docs).
    All four are 0.0 if is_fee_bearing is False -- every in-sample
    (2024-2025) trade is therefore unconditionally all-zero, since no fee
    regime existed yet.
    """
    if not is_fee_bearing(category, created_at):
        return {"base_contract": 0.0, "base_documented": 0.0, "upper_contract": 0.0, "upper_documented": 0.0}

    p = min(max(price, FEE_PRICE_EPS), 1 - FEE_PRICE_EPS)
    contract_mult = min(p, 1 - p) / p
    documented_mult = p * (1 - p)

    rate_base = CATEGORY_FEE_RATE.get(category, 0.0)
    rate_upper = taker_base_fee / TAKER_BASE_FEE_BPS_DIVISOR

    return {
        "base_contract": notional * rate_base * contract_mult,
        "base_documented": notional * rate_base * documented_mult,
        "upper_contract": notional * rate_upper * contract_mult,
        "upper_documented": notional * rate_upper * documented_mult,
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
