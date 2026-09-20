"""Test X: cross-sectional daily crypto rules. See docs/PREREG_xs_crypto.md.

Default = development split (first 60%). `--holdout` runs the sealed 40% once (lock file).
"""
import argparse
import io
import json
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "xs"
LOCK = CACHE / "X_RAN.lock"
COINS = "BTC ETH BNB XRP ADA DOGE SOL TRX LINK AVAX DOT LTC BCH ATOM UNI ETC XLM NEAR FIL AAVE".split()
MONTHS = [f"{y}-{m:02d}" for y in range(2022, 2027) for m in range(1, 13) if (y, m) <= (2026, 8)]
URL = "https://data.binance.vision/data/spot/monthly/klines/{s}/1d/{s}-1d-{m}.zip"
SIDE_COST = 0.0012
K = 3
BLOCK, RESAMPLES = 10, 5000
RULES = {"REV1": (1, "worst"), "REV7": (7, "worst"), "MOM1": (1, "best"), "MOM7": (7, "best")}


def get_month(args):
    coin, month = args
    s = coin + "USDT"
    try:
        with urllib.request.urlopen(URL.format(s=s, m=month), timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        out = []
        for line in z.read(z.namelist()[0]).decode().splitlines():
            p = line.split(",")
            t = int(p[0])
            t = t // 1000 if t > 10 ** 14 else t
            out.append((t // 86_400_000, float(p[4])))
        return coin, out
    except Exception:
        return coin, []


def load():
    path = CACHE / "daily_close.json"
    if path.exists():
        return {c: {int(d): v for d, v in x.items()} for c, x in json.loads(path.read_text()).items()}
    data = {c: {} for c in COINS}
    with ThreadPoolExecutor(16) as ex:
        for coin, rows in ex.map(get_month, [(c, m) for c in COINS for m in MONTHS]):
            for d, v in rows:
                data[coin][d] = v
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def build(data):
    days = sorted(set(d for x in data.values() for d in x))
    d0, d1 = days[0], days[-1]
    grid = np.full((len(COINS), d1 - d0 + 1), np.nan)
    for i, c in enumerate(COINS):
        for d, v in data[c].items():
            grid[i, d - d0] = v
    return grid


def run_rule(grid, w, side):
    n_c, n_d = grid.shape
    strat, ew = np.full(n_d, np.nan), np.full(n_d, np.nan)
    prev_w = np.zeros(n_c)
    for t in range(w, n_d - 1):
        form = grid[:, t] / grid[:, t - w] - 1
        nxt = grid[:, t + 1] / grid[:, t] - 1
        ok = ~np.isnan(form) & ~np.isnan(nxt)
        if ok.sum() < 8:
            continue
        idx = np.flatnonzero(ok)
        order = idx[np.argsort(form[idx])]
        pick = order[:K] if side == "worst" else order[-K:]
        wt = np.zeros(n_c)
        wt[pick] = 1.0 / K
        turnover = np.abs(wt - prev_w).sum()
        prev_w = wt
        strat[t] = nxt[pick].mean() - SIDE_COST * turnover
        ew[t] = nxt[idx].mean()
    return strat, ew


def boot_ci(x, seed=9):
    rng = np.random.default_rng(seed)
    n = len(x)
    nb = int(np.ceil(n / BLOCK))
    m = np.empty(RESAMPLES)
    for i in range(RESAMPLES):
        st = rng.integers(0, n, nb)
        idx = (st[:, None] + np.arange(BLOCK)[None, :]) % n
        m[i] = x[idx.ravel()[:n]].mean()
    return np.percentile(m, [0.625, 99.375])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    grid = build(load())
    n_d = grid.shape[1]
    print("coins with data:", int((~np.isnan(grid)).any(1).sum()), "days:", n_d)
    if a.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")
    cut = int(n_d * 0.6)
    lo, hi = (cut, n_d) if a.holdout else (0, cut)
    passed = []
    for name, (w, side) in RULES.items():
        strat, ew = run_rule(grid, w, side)
        ex = (strat - ew)[lo:hi]
        ex = ex[~np.isnan(ex)]
        ci = boot_ci(ex)
        g = (np.nanmean(strat[lo:hi]), np.nanmean(ew[lo:hi]))
        print(f"{name}: n={len(ex)} excess/day={ex.mean()*1e4:.1f}bps CI98.75=[{ci[0]*1e4:.1f},{ci[1]*1e4:.1f}]bps "
              f"strat={g[0]*1e4:.1f}bps ew={g[1]*1e4:.1f}bps")
        if a.holdout and ci[0] > 0 and len(ex) >= 300:
            passed.append(name)
    if a.holdout:
        print("VERDICT:", "PASS (dev-sign check pending)" if passed else "KILL", passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
