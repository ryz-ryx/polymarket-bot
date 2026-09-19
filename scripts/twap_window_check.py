"""
EXPLORATORY measurement of Polymarket's 5-minute settlement rule (analysis only, not a strategy test).

Question: which averaging rule, applied to Binance 1-second closes, best reproduces the actual resolved
outcomes? Sources disagree on whether 5-minute BTC markets settle on a 30s or a 60s Chainlink TWAP, and
whether the reference ("price to beat") is a point price or a TWAP too. Outcomes come from the downloaded
trade chunks (resolved_up per window). Binance is a proxy for Chainlink, so agreement will be below 100%
for every rule; the DIFFERENCE between rules is the signal, and the overall disagreement rate is the
basis risk of using Binance as ground truth.

Rules compared (t0 = window start, t1 = t0 + 300, close(t) = Binance 1s close at second t):
  point       : close(t1) >= close(t0)
  twapW/point : avg close over [t1-W, t1) >= close(t0)                  W in 10, 30, 60
  twapW/twapW : avg over [t1-W, t1) >= avg over [t0-W, t0)              W in 10, 30, 60
Also reported: agreement restricted to windows whose Binance move was within 3 bps (hard cases).

Usage: python scripts/twap_window_check.py [--trades "data/trades/chunk_*.csv.gz"] [--cache data/bn1s_full]
"""
import argparse
import csv
import glob
import gzip
import json
import os
import urllib.request
from collections import defaultdict

W = 300


def outcomes(pattern):
    res = {}
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for r in csv.reader(f):
                if len(r) >= 8 and r[7] != "":
                    res[int(r[0])] = int(r[7])
    return res


def closes(w, cache):
    path = os.path.join(cache, f"{w}.json")
    if os.path.exists(path):
        return {int(k): v for k, v in json.load(open(path)).items()}
    start = w - 70
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={start * 1000}&limit=400"
    with urllib.request.urlopen(url, timeout=20) as r:
        rows = json.load(r)
    data = {str(int(k[0] // 1000)): float(k[4]) for k in rows}
    os.makedirs(cache, exist_ok=True)
    json.dump(data, open(path, "w"))
    return {int(k): v for k, v in data.items()}


def avg(c, a, b):
    xs = [c[t] for t in range(a, b) if t in c]
    return sum(xs) / len(xs) if len(xs) >= (b - a) * 0.8 else None


def rules(c, t0):
    t1 = t0 + W
    out = {}
    if t0 in c and t1 in c:
        out["point"] = (c[t1] >= c[t0], abs(c[t1] / c[t0] - 1) * 1e4)
    for w in (10, 30, 60):
        end = avg(c, t1 - w, t1)
        if end is not None and t0 in c:
            out[f"twap{w}/point"] = (end >= c[t0], abs(end / c[t0] - 1) * 1e4)
        start = avg(c, t0 - w, t0)
        if end is not None and start is not None:
            out[f"twap{w}/twap{w}"] = (end >= start, abs(end / start - 1) * 1e4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/trades/chunk_*.csv.gz")
    ap.add_argument("--cache", default="data/bn1s_full")
    args = ap.parse_args()
    res = outcomes(args.trades)
    stats = defaultdict(lambda: [0, 0, 0, 0])            # rule -> [n, agree, n_hard, agree_hard]
    used = 0
    for w, y in sorted(res.items()):
        try:
            c = closes(w, args.cache)
        except Exception:
            continue
        used += 1
        for name, (up, bps) in rules(c, w).items():
            s = stats[name]
            s[0] += 1
            s[1] += int(int(up) == y)
            if bps < 3.0:
                s[2] += 1
                s[3] += int(int(up) == y)
    print(f"windows with outcomes: {len(res)}, with Binance 1s data: {used}")
    print(f"{'rule':<18}{'n':>6}{'agree':>9}{'hard n':>8}{'hard agree':>12}")
    for name in sorted(stats, key=lambda k: -stats[k][1] / max(stats[k][0], 1)):
        n, a, nh, ah = stats[name]
        print(f"{name:<18}{n:>6}{a / n:>9.1%}{nh:>8}{(ah / nh if nh else float('nan')):>12.1%}")


if __name__ == "__main__":
    main()
