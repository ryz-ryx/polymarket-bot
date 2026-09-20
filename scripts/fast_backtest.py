"""Test F: fast intraday rules on Binance 1m klines. See docs/PREREG_fast_intraday.md.

Default = development split (first 60%). `--holdout` runs the sealed 40% once (lock file).
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "fast"
LOCK = CACHE / "F_RAN.lock"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
URL = "https://data-api.binance.vision/api/v3/klines?symbol={s}&interval=1m&limit=1000&startTime={t}"
DAYS = 365
RULES = {"R1_momentum": ("mom", 30), "R2_reversion": ("rev", 30), "R3_breakout": ("brk", 60)}
COSTS = {"0.24%": 0.0024, "0.12%": 0.0012}
RESAMPLES = 5000


def fetch(symbol):
    path = CACHE / f"{symbol}_1m.npy"
    if path.exists():
        return np.load(path)
    now = int(time.time() * 1000)
    t = now - DAYS * 86_400_000
    rows = []
    while t < now:
        with urllib.request.urlopen(URL.format(s=symbol, t=t), timeout=30) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        rows += [[int(b[0]), float(b[1]), float(b[4])] for b in batch if int(b[6]) < now]
        t = batch[-1][0] + 60_000
    arr = np.array(rows)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(path, arr)
    return arr


def signals(kind, c):
    n = len(c)
    if kind in ("mom", "rev"):
        lr = np.zeros(n)
        lr[1:] = np.diff(np.log(c))
        cs, cs2 = np.cumsum(lr), np.cumsum(lr ** 2)
        w = 1440
        var = np.full(n, np.nan)
        var[w:] = (cs2[w:] - cs2[:-w]) / w - ((cs[w:] - cs[:-w]) / w) ** 2
        sd = np.sqrt(np.maximum(var, 0)) * np.sqrt(15)
        r15 = np.full(n, np.nan)
        r15[15:] = c[15:] / c[:-15] - 1
        with np.errstate(invalid="ignore"):
            s = (r15 > 2 * sd) if kind == "mom" else (r15 < -2 * sd)
        return np.nan_to_num(s).astype(bool)
    w = 240
    prior_max = np.full(n, np.nan)
    prior_max[w:] = sliding_window_view(c, w).max(axis=1)[: n - w]  # max of c[t-w..t-1]
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(c > prior_max).astype(bool)


def trades(arr, kind, hold, lo, hi, cost):
    o, c = arr[:, 1], arr[:, 2]
    idx = np.flatnonzero(signals(kind, c))
    idx = idx[(idx >= lo) & (idx < hi - hold - 2)]
    out, free = [], 0
    for i in idx:
        e = i + 1
        if e < free:
            continue
        x = e + hold
        out.append((int(arr[e, 0] // 86_400_000), o[x] / o[e] - 1 - cost))
        free = x
    return out


def cluster_ci(tr, seed=5):
    if not tr:
        return None
    days = np.array([d for d, _ in tr])
    r = np.array([v for _, v in tr])
    ud, inv = np.unique(days, return_inverse=True)
    s = np.bincount(inv, weights=r)
    k = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ud), (RESAMPLES, len(ud)))
    means = s[pick].sum(1) / np.maximum(k[pick].sum(1), 1)
    return [round(float(v), 5) for v in np.percentile(means, [0.4, 99.6])], round(float(r.mean()), 5), len(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    data = {s: fetch(s) for s in SYMBOLS}
    if a.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")
    tag = "HOLDOUT" if a.holdout else "DEV"
    passed = []
    for s, arr in data.items():
        n = len(arr)
        lo, hi = (int(n * 0.6), n) if a.holdout else (1500, int(n * 0.6))
        print(f"{s}: {n} minutes, {tag} range {lo}-{hi}")
        for rn, (kind, hold) in RULES.items():
            for cl, cost in COSTS.items():
                res = cluster_ci(trades(arr, kind, hold, lo, hi, cost))
                print(rn, s, "cost", cl, "-> (CI99.2, mean/trade, n):", res)
                if a.holdout and cl == "0.24%" and res and res[0][0] > 0 and res[2] >= 300:
                    passed.append((rn, s))
    if a.holdout:
        print("VERDICT:", "PASS (dev-sign check pending)" if passed else "KILL", passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
