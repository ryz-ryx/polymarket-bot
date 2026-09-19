"""
Pre-registered test V: which exchange's price move best predicts Polymarket's next mid change? Analysis only.
Committed BEFORE the collector's cross-venue rows ("t":"x") had enough data to run it.

Why: the executable-price lead-lag edge (lead_lag_l2.py) lives inside about 2 seconds, and Binance is the slow
leg from our host (REST p50 225ms; Kraken 42ms, Coinbase 123ms). If a nearer venue predicts Polymarket about as
well as Binance, the speed disadvantage shrinks.

Data: data/l2 rows: "s" (Polymarket top-of-book snapshots, k=Y/N) and "x" (venue bid/ask on the same receive
clock). Sample: every second t in [w+35, w+250] where Polymarket YES mid in [0.15, 0.85] and quotes are at most
3s stale.
  Feature r_v(t) = ln(mid_v(t) / mid_v(t-1s)), venue mids from the latest row at or before each time (max 1.5s stale).
  Target   d(t)  = PolymarketYESmid(t+2s) - PolymarketYESmid(t).
  Statistic per venue: OLS slope of d on r_v and R^2; slope CI by cluster bootstrap over 5-minute windows,
  Bonferroni level z = 2.5. Rows use the first 60% of windows only as a burn-in; verdicts use the last 40%.

  A venue is a LEAD PREDICTOR only if its slope CI is entirely above 0.
  A nearer venue (kraken or coinbase) is SUFFICIENT only if it is a lead predictor AND its R^2 >= 0.8 x Binance's.
  Needs at least 500 holdout samples per venue. This is informational (which feed to react to); it is not an
  edge test and must still clear fees at executable prices.

Usage: python scripts/venue_lead_test.py [--l2 "data/l2_pulled/l2/l2_*.jsonl*"]
"""
import argparse
import bisect
import glob
import gzip
import json
import math
import random
import sys
from collections import defaultdict

Z_LO, Z_HI, N_BOOT = 0.00625, 0.99375, 2000
STALE_PM, STALE_V = 3.0, 1.5
VENUES = ("binance", "coinbase", "kraken")


def load(pattern):
    pm = defaultdict(lambda: ([], []))            # window -> (ts list, yes mid list)
    ven = {v: ([], []) for v in VENUES}
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("t") == "s" and r["k"] == "Y" and r["b"] and r["a"]:
                    pm[r["w"]][0].append(r["ts"])
                    pm[r["w"]][1].append((r["b"][0][0] + r["a"][0][0]) / 2.0)
                elif r.get("t") == "x" and r.get("v") in ven:
                    ven[r["v"]][0].append(r["ts"])
                    ven[r["v"]][1].append((r["b"] + r["a"]) / 2.0)
    return pm, ven


def at(series, t, stale):
    ts, vals = series
    i = bisect.bisect_right(ts, t) - 1
    return vals[i] if i >= 0 and t - ts[i] <= stale else None


def samples(pm, ven):
    out = defaultdict(list)                        # venue -> [(window, r, d)]
    for w in sorted(pm):
        ser = pm[w]
        for t in range(w + 35, w + 250):
            m0, m2 = at(ser, t, STALE_PM), at(ser, t + 2, STALE_PM)
            if m0 is None or m2 is None or not 0.15 <= m0 <= 0.85:
                continue
            for v in VENUES:
                a, b = at(ven[v], t, STALE_V), at(ven[v], t - 1, STALE_V)
                if a and b:
                    out[v].append((w, math.log(a / b), m2 - m0))
    return out


def ols(items):
    n = len(items)
    if n < 30:
        return None
    mx, my = sum(i[1] for i in items) / n, sum(i[2] for i in items) / n
    sxx = sum((i[1] - mx) ** 2 for i in items)
    syy = sum((i[2] - my) ** 2 for i in items)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((i[1] - mx) * (i[2] - my) for i in items)
    return sxy / sxx, sxy * sxy / (sxx * syy)


def cluster_slope_ci(items, seed=5):
    groups = defaultdict(list)
    for it in items:
        groups[it[0]].append(it)
    keys, rng, slopes = list(groups), random.Random(seed), []
    for _ in range(N_BOOT):
        pick = [x for _ in keys for x in groups[keys[rng.randrange(len(keys))]]]
        r = ols(pick)
        if r:
            slopes.append(r[0])
    slopes.sort()
    return (slopes[int(Z_LO * len(slopes))], slopes[int(Z_HI * len(slopes)) - 1]) if slopes else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2", default="data/l2_pulled/l2/l2_*.jsonl*")
    args = ap.parse_args()
    pm, ven = load(args.l2)
    ws = sorted(pm)
    if len(ws) < 30:
        sys.exit(f"only {len(ws)} windows with Polymarket snapshots")
    split_w = ws[int(len(ws) * 0.6)]
    per = samples({w: pm[w] for w in ws if w >= split_w}, ven)
    print(f"windows {len(ws)} (holdout from {split_w}); venue rows: " + ", ".join(f"{v}={len(ven[v][0])}" for v in VENUES))
    res = {}
    for v in VENUES:
        items = per.get(v, [])
        r = ols(items)
        if not r or len(items) < 500:
            print(f"{v:<9} insufficient samples ({len(items)}; need 500)")
            continue
        lo, hi = cluster_slope_ci(items)
        res[v] = {"slope": r[0], "r2": r[1], "lo": lo, "n": len(items)}
        print(f"{v:<9} n={len(items):>6}  slope {r[0]:+9.2f}  R^2 {r[1]:.5f}  corrected CI [{lo:+.2f}, {hi:+.2f}]  "
              f"{'LEAD PREDICTOR' if lo is not None and lo > 0 else 'no lead shown'}")
    b = res.get("binance")
    for v in ("kraken", "coinbase"):
        if b and v in res:
            ok = res[v]["lo"] is not None and res[v]["lo"] > 0 and res[v]["r2"] >= 0.8 * b["r2"]
            print(f"{v} SUFFICIENT vs Binance: {'YES' if ok else 'NO'} (R^2 ratio {res[v]['r2'] / b['r2']:.2f})")


if __name__ == "__main__":
    main()
