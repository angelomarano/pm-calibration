#!/usr/bin/env python3
"""Gate A feasibility spike — pm-calibration project.

Checks (plan §3, W0):
  A1  prices-history coverage for RESOLVED markets (fidelity 1440, fallback 720)
  A2  resolution concordance: degenerate outcomePrices vs last observed price
  A3  endDate reliability: prevalence of early/late resolution vs scheduled endDate
  A4  operational: pacing / 429s, availability of feesEnabled + resolution fields

Usage:
  python spikes/gate_a.py --smoke      # ~9 markets/year, prints one raw market JSON (field discovery)
  python spikes/gate_a.py              # full spike: ~70 markets/year x 3 years

Outputs:
  data/raw/gate_a_cache/   raw JSON per market + price history (reusable in W1)
  spikes/gate_a_report.txt
No API key, no wallet, read-only public endpoints.
"""

import argparse
import json
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "raw" / "gate_a_cache"
SLEEP = 0.25  # polite pacing between calls (seconds)
VOL_MIN = 10_000
# (year, end_date_min, end_date_max) — 2026 capped at our study window
WINDOWS = [
    (2024, "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z"),
    (2025, "2025-01-01T00:00:00Z", "2025-12-31T23:59:59Z"),
    (2026, "2026-01-01T00:00:00Z", "2026-06-30T23:59:59Z"),
]

session = requests.Session()
session.headers["User-Agent"] = "pm-calibration-gate-a/0.1 (research spike)"
CALLS = Counter()
HTTP_ERR = Counter()


# ----------------------------- helpers ------------------------------------
def get_json(url, params=None, tries=5):
    host = url.split("/")[2]
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(2**attempt)
            continue
        CALLS[host] += 1
        if r.status_code == 200:
            time.sleep(SLEEP)
            try:
                return r.json()
            except ValueError:
                return None
        HTTP_ERR[f"{host}:{r.status_code}"] += 1
        if r.status_code == 429:
            time.sleep(2**attempt + random.random())
        else:
            time.sleep(1 + attempt)
    return None


def pjson(x):
    """Gamma often returns lists encoded as JSON strings."""
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return None
    return x


def iso_dt(s):
    if not s:
        return None
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if len(s) > 3 and s[-3] in "+-" and ":" not in s[-3:]:
        s += ":00"  # "+00" -> "+00:00" (closedTime format)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def epoch_dt(t):
    t = float(t)
    if t > 1e12:  # milliseconds
        t /= 1000.0
    return datetime.fromtimestamp(t, tz=timezone.utc)


def fnum(m, *keys):
    for k in keys:
        v = m.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


# ----------------------------- pulling ------------------------------------
def pull_year(year, dmin, dmax, pages=5, page_size=100):
    """Gamma caps /markets pages at 100 rows (500 was silently truncated)."""
    out, seen = [], set()
    for offset in range(0, page_size * pages, page_size):
        params = {
            "closed": "true",
            "limit": page_size,
            "offset": offset,
            "end_date_min": dmin,
            "end_date_max": dmax,
            "volume_num_min": VOL_MIN,
        }
        data = get_json(f"{GAMMA}/markets", params)
        if not data or not isinstance(data, list):
            break
        for m in data:
            mid = m.get("id") or m.get("conditionId")
            if mid not in seen:
                seen.add(mid)
                out.append(m)
        if len(data) < page_size:
            break
    # sanity: did the server honor the date filter?
    yrs = Counter((m.get("endDate") or "")[:4] for m in out)
    if yrs and yrs.most_common(1)[0][0] != str(year):
        print(f"  [WARN] {year}: end_date filter may be ignored; endDate years seen: {dict(yrs)}")
    print(f"  {year}: pulled {len(out)} closed markets (vol>=${VOL_MIN:,})")
    return out


def stratified_sample(markets, n_per_year, seed=42):
    ok = [m for m in markets if pjson(m.get("clobTokenIds"))]
    ok.sort(key=lambda m: fnum(m, "volumeNum", "volume"))
    k = max(1, len(ok) // 3)
    terciles = [ok[:k], ok[k : 2 * k], ok[2 * k :]]
    rng = random.Random(seed)
    sample = []
    for i, t in enumerate(terciles):
        take = min(len(t), max(1, n_per_year // 3))
        for m in rng.sample(t, take):
            m["_vol_tercile"] = i + 1
            sample.append(m)
    return sample


def fetch_history(token_id):
    for fid in (1440, 720):
        data = get_json(
            f"{CLOB}/prices-history",
            {"market": token_id, "interval": "max", "fidelity": fid},
        )
        hist = (data or {}).get("history") or []
        if hist:
            return hist, fid
    return [], None


# ----------------------------- per-market checks ---------------------------
def analyze(m, year):
    rec = {"year": year, "id": m.get("id"), "q": (m.get("question") or "")[:80]}
    rec["vol"] = fnum(m, "volumeNum", "volume")
    rec["tercile"] = m.get("_vol_tercile")

    tokens = pjson(m.get("clobTokenIds")) or []
    outcomes = pjson(m.get("outcomes")) or []
    prices = [float(p) for p in (pjson(m.get("outcomePrices")) or [])]

    # Yes leg: align on outcomes list, default index 0
    yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0
    token = tokens[yes_idx] if len(tokens) > yes_idx else (tokens[0] if tokens else None)

    # field availability (A4)
    rec["has_feesEnabled"] = "feesEnabled" in m
    rec["res_fields"] = [k for k in ("umaResolutionStatus", "resolutionStatus", "closedTime", "umaResolutionStatuses") if m.get(k)]

    # A2: winner from degenerate outcomePrices
    rec["degenerate"] = bool(prices) and max(prices) >= 0.99
    rec["yes_won"] = (prices[yes_idx] >= 0.99) if (rec["degenerate"] and len(prices) > yes_idx) else None

    # A1: price history
    hist, fid = fetch_history(token) if token else ([], None)
    rec["n_pts"], rec["fid"] = len(hist), fid
    rec["has_hist"] = len(hist) >= 5

    start = iso_dt(m.get("startDate") or m.get("createdAt"))
    end = iso_dt(m.get("endDate"))
    res_ts = iso_dt(m.get("umaEndDate")) or iso_dt(m.get("closedTime"))  # actual resolution
    if hist:
        first_t, last_t = epoch_dt(hist[0]["t"]), epoch_dt(hist[-1]["t"])
        rec["last_p"] = float(hist[-1]["p"])
        rec["span_d"] = (last_t - first_t).days
        rec["life_d"] = (end - start).days if (start and end) else None
        rec["start_lag_d"] = (first_t - start).days if start else None
        # A1b: does the price series reach the actual resolution?
        rec["hist_gap_d"] = (res_ts - last_t).total_seconds() / 86400 if res_ts else None
        # A2 cross-check: last price vs declared winner
        if rec["yes_won"] is not None:
            target = 1.0 if rec["yes_won"] else 0.0
            rec["target"] = target
            rec["concord"] = abs(rec["last_p"] - target) <= 0.05
            rec["inv_suspect"] = (not rec["concord"]) and abs(rec["last_p"] - target) >= 0.9
        # A3: early / late resolution vs scheduled endDate (positive = early)
        if end and res_ts:
            rec["days_early"] = (end - res_ts).total_seconds() / 86400
            rec["early"] = rec["days_early"] > 2
            rec["late"] = rec["days_early"] < -2
    return rec


# ----------------------------- reporting -----------------------------------
def pct(nums):
    nums = [n for n in nums if n is not None]
    return f"{100*sum(nums)/len(nums):.0f}% ({sum(nums)}/{len(nums)})" if nums else "n/a"


def report(recs, elapsed):
    L = ["", "=" * 21 + " GATE A REPORT " + "=" * 21]
    years = sorted({r["year"] for r in recs})

    L.append("\n[A1] prices-history coverage (>=5 points), by year:")
    overall = []
    for y in years:
        R = [r for r in recs if r["year"] == y]
        fids = Counter(r["fid"] for r in R if r["fid"])
        L.append(f"  {y}: {pct([r['has_hist'] for r in R])}   fidelity used: {dict(fids)}")
        overall += [r["has_hist"] for r in R]
    cov = sum(overall) / len(overall) if overall else 0
    L.append(f"  by volume tercile: " + "  ".join(
        f"T{t}: {pct([r['has_hist'] for r in recs if r['tercile'] == t])}" for t in (1, 2, 3)))
    lags = [r["start_lag_d"] for r in recs if r.get("start_lag_d") is not None]
    if lags:
        lags.sort()
        L.append(f"  history start lag vs market start (days): median {lags[len(lags)//2]}, p90 {lags[int(len(lags)*0.9)]}")

    L.append("\n[A2] resolution concordance:")
    L.append(f"  degenerate outcomePrices (>=0.99): {pct([r['degenerate'] for r in recs])}")
    reach = [r["hist_gap_d"] is not None and abs(r["hist_gap_d"]) <= 3 for r in recs if r.get("has_hist")]
    L.append(f"  price series reaches resolution (+-3d of umaEndDate): {pct(reach)}")
    L.append(f"  last price agrees with winner (+-0.05): {pct([r.get('concord') for r in recs])}")
    mism = [r for r in recs if r.get("concord") is False]
    inv = sum(1 for r in mism if r.get("inv_suspect"))
    L.append(f"  token-inversion suspects (|last_p - winner| >= 0.9): {inv}/{len(mism)} mismatches")
    for r in mism:
        L.append(
            f"    - {r['year']} last_p={r.get('last_p'):.2f} -> winner={r.get('target'):.0f}"
            f"  early={r.get('early')}  hist_gap_d={r.get('hist_gap_d') if r.get('hist_gap_d') is None else round(r['hist_gap_d'], 1)}"
            f"  | {r['q'][:60]}"
        )
    L.append(f"  resolution-ish fields seen: {Counter(f for r in recs for f in r['res_fields']).most_common()}")

    L.append("\n[A3] actual resolution (umaEndDate) vs scheduled endDate:")
    for y in years:
        R = [r for r in recs if r["year"] == y]
        L.append(f"  {y}: early {pct([r.get('early') for r in R])}   late {pct([r.get('late') for r in R])}")
    de = sorted(r["days_early"] for r in recs if r.get("days_early") is not None and r["days_early"] > 2)
    if de:
        L.append(f"  among early resolvers, days early: median {de[len(de)//2]:.0f}, p90 {de[int(len(de)*0.9)]:.0f}")

    L.append("\n[A4] operational:")
    L.append(f"  calls: {dict(CALLS)}   http errors: {dict(HTTP_ERR) or 'none'}")
    L.append(f"  elapsed: {elapsed:.0f}s  ->  ~{sum(CALLS.values())/max(elapsed,1):.1f} req/s sustained")
    L.append(f"  feesEnabled field present: {pct([r['has_feesEnabled'] for r in recs])}")

    L.append("\n[DECISION] per plan §3/W0:")
    if cov >= 0.90:
        L.append(f"  coverage {cov:.0%} >= 90%  ->  GO. Proceed to W1.")
    elif cov >= 0.70:
        L.append(f"  coverage {cov:.0%} in [70%,90%)  ->  GO with window shift (check which vintages fail; log in DECISIONS.md).")
    else:
        L.append(f"  coverage {cov:.0%} < 70%  ->  STOP. Becker trade-tape fallback decision (+10-15h re-scope).")
    L.append("=" * 57)
    txt = "\n".join(L)
    print(txt)
    (Path(__file__).resolve().parent / "gate_a_report.txt").write_text(txt)


# ----------------------------- main ----------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="tiny run + dump one raw market JSON")
    ap.add_argument("--n", type=int, default=70, help="markets per year (default 70)")
    args = ap.parse_args()
    n_per_year = 9 if args.smoke else args.n

    CACHE.mkdir(exist_ok=True)
    t0 = time.time()
    recs, dumped = [], False
    for year, dmin, dmax in WINDOWS:
        markets = pull_year(year, dmin, dmax)
        if not markets:
            print(f"  [WARN] {year}: nothing pulled — check filters/connectivity")
            continue
        for m in stratified_sample(markets, n_per_year):
            if args.smoke and not dumped:
                print("\n--- RAW MARKET SAMPLE (field discovery) ---")
                print(json.dumps(m, indent=2)[:4000])
                print("--- END RAW SAMPLE ---\n")
                dumped = True
            rec = analyze(m, year)
            recs.append(rec)
            (CACHE / f"{rec['id']}.json").write_text(json.dumps(m))
    report(recs, time.time() - t0)


if __name__ == "__main__":
    main()