"""Test L: does BTC's 15-min return lead SOL/XRP/BNB's next 15-min return? See docs/PREREG_leadlag.md.
Default = development split (first 60%). `--holdout` runs the sealed 40% once (lock file).
"""
import argparse
import glob
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ZIPS = ROOT / "data" / "fast_zip"
CACHE = ROOT / "data" / "fast_leadlag"
LOCK = CACHE / "L_RAN.lock"
ALTS = ["SOLUSDT", "XRPUSDT", "BNBUSDT"]
COSTS = {"0.24%": 0.0024, "0.12%": 0.0012}
RESAMPLES = 5000
STEP = 15  # minutes, non-overlapping


def load(sym):
    path = CACHE / f"{sym}_1m.npy"
    if path.exists():
        return np.load(path)
    rows = []
    for f in sorted(glob.glob(str(ZIPS / f"{sym}-1m-*.zip"))):
        z = zipfile.ZipFile(f)
        for line in z.read(z.namelist()[0]).decode().splitlines():
            p = line.split(",")
            t = int(p[0])
            t = t // 1000 if t > 10 ** 14 else t  # 2025+ archives are microseconds
            rows.append([t, float(p[1]), float(p[4])])  # ts_ms, open, close
    a = np.array(rows)
    a = a[np.argsort(a[:, 0])]
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(path, a)
    return a


def align(btc, alt):
    """Inner-join on timestamp so BTC's and the alt's minute bars line up 1:1."""
    bt, at = btc[:, 0], alt[:, 0]
    common = np.intersect1d(bt, at)
    bi = np.searchsorted(bt, common)
    ai = np.searchsorted(at, common)
    return btc[bi], alt[ai]


def trades(btc, alt, lo, hi, cost):
    """Non-overlapping 15-min blocks: BTC's trailing 15-min return signs the next-block long/flat call on the alt."""
    bc, ac_o, ac_c = btc[:, 2], alt[:, 1], alt[:, 2]
    n = len(btc)
    out = []
    i = lo
    while i + 2 * STEP < min(hi, n):
        btc_ret = bc[i] / bc[i - STEP] - 1
        e, x = i, i + STEP
        if btc_ret > 0:
            out.append((int(btc[e, 0] // 86_400_000), ac_o[x] / ac_o[e] - 1 - cost))
        i += STEP
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
    btc = load("BTCUSDT")
    if a.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")
    tag = "HOLDOUT" if a.holdout else "DEV"
    passed = []
    for sym in ALTS:
        alt = load(sym)
        b, al = align(btc, alt)
        n = len(b)
        lo, hi = (int(n * 0.6), n) if a.holdout else (100, int(n * 0.6))
        print(f"{sym}: {n} aligned minutes, {tag} range {lo}-{hi}")
        for cl, cost in COSTS.items():
            res = cluster_ci(trades(b, al, lo, hi, cost))
            print(sym, "cost", cl, "-> (CI99.2, mean/trade, n):", res)
            if a.holdout and cl == "0.24%" and res and res[0][0] > 0 and res[2] >= 300:
                passed.append(sym)
    if a.holdout:
        print("VERDICT:", "PASS (dev-sign check pending)" if passed else "KILL", passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
