"""Test T: daily SMA trend rule on crypto spot. See docs/PREREG_trend_spot.md.

Default run = development split only (first 60%). `--holdout` runs the sealed 40% once (lock file).
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "trend"
LOCK = CACHE / "T_RAN.lock"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
URL = "https://data-api.binance.vision/api/v3/klines?symbol={s}&interval=1d&limit=1000&startTime={t}"
COST = 0.008
SMA_N = 50
BLOCK, RESAMPLES = 20, 5000


def fetch(symbol):
    path = CACHE / f"{symbol}_1d.json"
    if path.exists():
        return json.loads(path.read_text())
    rows, t = [], 0
    while True:
        with urllib.request.urlopen(URL.format(s=symbol, t=t), timeout=30) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        rows += batch
        t = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    now_ms = int(time.time() * 1000)
    out = [[int(r[0]), float(r[4])] for r in rows if int(r[6]) < now_ms]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def run_rule(closes, n, cost):
    """Return (net strategy returns, position, asset returns) for days 1..T-1."""
    c = np.asarray(closes, float)
    ret = c[1:] / c[:-1] - 1
    sma = np.full(len(c), np.nan)
    csum = np.cumsum(np.insert(c, 0, 0))
    sma[n - 1:] = (csum[n:] - csum[:-n]) / n
    sig = (c > sma).astype(float)  # nan compares False -> cash
    pos = sig[:-1]  # signal at close t applies to return t -> t+1
    prev = np.concatenate([[0.0], pos[:-1]])
    strat = pos * ret - cost * np.abs(pos - prev)
    return strat, pos, ret


def block_bootstrap_ci(x, block=BLOCK, k=RESAMPLES, seed=11):
    rng = np.random.default_rng(seed)
    n = len(x)
    nb = int(np.ceil(n / block))
    means = np.empty(k)
    for i in range(k):
        starts = rng.integers(0, n, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]) % n
        means[i] = x[idx.ravel()[:n]].mean()
    return np.percentile(means, [2.5, 97.5])


def stats(r):
    sd = r.std(ddof=1)
    sharpe = r.mean() / sd * np.sqrt(365) if sd > 0 else float("nan")
    eq = np.cumprod(1 + r)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return [round(float(eq[-1] - 1), 3), round(float(sharpe), 3), round(float(dd), 3)]


def evaluate(data, lo_frac, hi_frac, n, cost):
    per, alphas = {}, []
    for s, rows in data.items():
        strat, pos, ret = run_rule([r[1] for r in rows], n, cost)
        a, b = int(len(strat) * lo_frac), int(len(strat) * hi_frac)
        strat, pos, ret = strat[a:b], pos[a:b], ret[a:b]
        alpha = strat - pos.mean() * ret
        alphas.append(alpha)
        trades = np.abs(np.diff(np.concatenate([[0.0], pos]))).sum()
        per[s] = dict(
            days=len(strat), beta=round(float(pos.mean()), 3),
            trades_per_year=round(float(trades / len(strat) * 365), 1),
            alpha_ann=round(float(alpha.mean() * 365), 4),
            alpha_ci_ann=[round(float(v * 365), 4) for v in block_bootstrap_ci(alpha)],
            strat_ret_sharpe_dd=stats(strat), buyhold_ret_sharpe_dd=stats(ret),
        )
    m = min(len(x) for x in alphas)
    pooled = np.mean([x[-m:] for x in alphas], axis=0)
    per["pooled"] = dict(alpha_ann=round(float(pooled.mean() * 365), 4),
                         alpha_ci_ann=[round(float(v * 365), 4) for v in block_bootstrap_ci(pooled)])
    return per


def verdict(res):
    ok_ci = res["pooled"]["alpha_ci_ann"][0] > 0 and all(res[s]["alpha_ann"] >= 0 for s in SYMBOLS)
    ok_sharpe = all(res[s]["strat_ret_sharpe_dd"][1] >= res[s]["buyhold_ret_sharpe_dd"][1] for s in SYMBOLS)
    return "PASS" if ok_ci and ok_sharpe else "KILL", f"alpha_ci_ok={ok_ci}", f"sharpe_ok={ok_sharpe}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true", help="run sealed holdout ONCE")
    args = ap.parse_args()
    data = {s: fetch(s) for s in SYMBOLS}
    for s, rows in data.items():
        print(f"{s}: {len(rows)} days")
    if args.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")
        lo, hi = 0.6, 1.0
    else:
        lo, hi = 0.0, 0.6
    tag = "HOLDOUT" if args.holdout else "DEV"
    for label, n, cost in [("MAIN sma50 cost0.8%", SMA_N, COST), ("sens sma50 cost0.4%", SMA_N, 0.004),
                           ("sens sma100 cost0.8%", 100, COST), ("sens sma200 cost0.8%", 200, COST)]:
        res = evaluate(data, lo, hi, n, cost)
        print(f"\n== {label} ({tag}) ==")
        print(json.dumps(res))
        if label.startswith("MAIN") and args.holdout:
            print("VERDICT:", verdict(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
