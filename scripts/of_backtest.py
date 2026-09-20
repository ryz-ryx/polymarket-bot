"""Test O: order-flow imbalance on 1-minute bars. See docs/PREREG_orderflow.md.

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
CACHE = ROOT / "data" / "fast_of"
LOCK = CACHE / "O_RAN.lock"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
WINDOWS = (5, 15, 60)
RESAMPLES = 5000
PCT = (0.208, 99.792)  # Bonferroni for 12 tests


def load(sym):
    path = CACHE / f"{sym}.npy"
    if path.exists():
        return np.load(path)
    rows = []
    for f in sorted(glob.glob(str(ZIPS / f"{sym}-1m-*.zip"))):
        z = zipfile.ZipFile(f)
        for line in z.read(z.namelist()[0]).decode().splitlines():
            p = line.split(",")
            t = int(p[0])
            t = t // 1000 if t > 10 ** 14 else t
            rows.append([t, float(p[1]), float(p[4]), float(p[5]), float(p[9])])
    a = np.array(rows)
    a = a[np.argsort(a[:, 0])]
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(path, a)
    return a


def imbalance(a, k):
    v, tb = a[:, 3], a[:, 4]
    net = np.cumsum(2 * tb - v)
    vol = np.cumsum(v)
    out = np.full(len(a), np.nan)
    out[k:] = (net[k:] - net[:-k]) / np.maximum(vol[k:] - vol[:-k], 1e-12)
    return out


def trades(a, sig_idx, h, lo, hi):
    o = a[:, 1]
    out, free = [], 0
    for i in sig_idx[(sig_idx >= lo) & (sig_idx < hi - h - 2)]:
        e = i + 1
        if e < free:
            continue
        x = e + h
        out.append((int(a[e, 0] // 86_400_000), o[x] / o[e] - 1))
        free = x
    return out


def ci(tr, cost, seed=5):
    days = np.array([d for d, _ in tr])
    r = np.array([v for _, v in tr]) - cost
    ud, inv = np.unique(days, return_inverse=True)
    s, k = np.bincount(inv, weights=r), np.bincount(inv).astype(float)
    pick = np.random.default_rng(seed).integers(0, len(ud), (RESAMPLES, len(ud)))
    m = s[pick].sum(1) / np.maximum(k[pick].sum(1), 1)
    lo, hi = np.percentile(m, PCT)
    return r.mean() * 1e4, lo * 1e4, hi * 1e4, len(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a_ = ap.parse_args()
    if a_.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")
    tag = "HOLDOUT" if a_.holdout else "DEV"
    real = []
    print("bps per trade; CI is 99.58% (Bonferroni 12). gross = zero cost")
    for s in SYMBOLS:
        a = load(s)
        n = len(a)
        cut = int(n * 0.6)
        lo, hi = (cut, n) if a_.holdout else (1500, cut)
        for k in WINDOWS:
            imb = imbalance(a, k)
            dev = imb[1500:cut]
            p95, p5 = np.nanpercentile(dev, 95), np.nanpercentile(dev, 5)
            for name, sig in (("CONT", np.flatnonzero(imb >= p95)), ("REV", np.flatnonzero(imb <= p5))):
                tr = trades(a, sig, k, lo, hi)
                if len(tr) < 30:
                    print(s, k, name, "too few trades", len(tr))
                    continue
                g, n12, n24 = ci(tr, 0), ci(tr, 0.0012), ci(tr, 0.0024)
                print(f"{tag} {s} k={k:>2} {name}: n={g[3]:>5} gross={g[0]:6.2f} [{g[1]:6.1f},{g[2]:6.1f}] "
                      f"net0.12%={n12[0]:6.1f} [{n12[1]:6.1f},{n12[2]:6.1f}] net0.24%={n24[0]:6.1f}")
                if g[1] > 0:
                    real.append((s, k, name, "gross CI>0"))
                if n12[1] > 0:
                    real.append((s, k, name, "TRADABLE at 0.12%"))
    print("SIGNIFICANT:", real if real else "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
