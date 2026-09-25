"""Test W: volatility-timed breakout entries. See docs/PREREG_vol_timing.md.

Default = dev split (first 70%): checks forecast calibration only, no PASS/KILL claim.
--holdout evaluates the sealed last 30% once (lock file), only if calibration passed on dev.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "seasonality"
LOCK = CACHE / "W_RAN.lock"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
COST = 0.0024
HOLD = 60         # bars (minutes), matches test F's R3
BREAK_W = 240      # prior-high window, matches test F's R3
HALFLIFE = 30      # minutes, EWMA halflife for vol forecast
FWD_VOL_W = 15     # minutes, forecast target horizon
ALPHA_BONF = 0.05 / len(SYMBOLS)
RESAMPLES = 5000


def load_1min(symbol):
    arr = np.load(CACHE / f"{symbol}_1s.npy")
    ts, close = arr[:, 0].astype(np.int64), arr[:, 1]
    minute = ts // 60_000
    _, last_idx = np.unique(minute[::-1], return_index=True)
    last_idx = len(minute) - 1 - last_idx
    order = np.argsort(minute[last_idx])
    last_idx = last_idx[order]
    return ts[last_idx], close[last_idx]


def ewma_vol_forecast(close):
    lr = np.zeros(len(close))
    lr[1:] = np.diff(np.log(close))
    lam = np.exp(np.log(0.5) / HALFLIFE)
    ewvar = np.zeros(len(lr))
    v = lr[0] ** 2
    for i in range(len(lr)):
        v = lam * v + (1 - lam) * lr[i] ** 2
        ewvar[i] = v
    return np.sqrt(ewvar)  # forecast AT t, usable to gate entries at t (no look-ahead: uses returns up to t)


def realized_fwd_vol(close, w=FWD_VOL_W):
    lr = np.zeros(len(close))
    lr[1:] = np.diff(np.log(close))
    n = len(lr)
    out = np.full(n, np.nan)
    cs2 = np.cumsum(lr ** 2)
    out[: n - w] = (cs2[w:] - cs2[: n - w]) / w
    return out


def prior_high_break(close, w=BREAK_W):
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(close)
    prior_max = np.full(n, np.nan)
    prior_max[w:] = sliding_window_view(close, w).max(axis=1)[: n - w]
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(close > prior_max).astype(bool)


def cluster_ci(vals, day_id, alpha, seed=11):
    if len(vals) == 0:
        return None
    ud, inv = np.unique(day_id, return_inverse=True)
    s = np.bincount(inv, weights=vals)
    k = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ud), (RESAMPLES, len(ud)))
    means = s[pick].sum(1) / np.maximum(k[pick].sum(1), 1)
    lo, hi = 100 * alpha / 2, 100 * (1 - alpha / 2)
    ci = [round(float(v), 5) for v in np.percentile(means, [lo, hi])]
    return ci, round(float(vals.mean()), 5), len(vals)


def run(symbol, lo, hi, alpha):
    ts, close = load_1min(symbol)
    forecast = ewma_vol_forecast(close)
    thresh = np.nanpercentile(forecast[max(0, lo - 43200):hi], 80)  # top quintile, trailing ~30d up to hi
    expansion = forecast > thresh
    brk = prior_high_break(close)
    idx = np.flatnonzero(brk & expansion)
    idx = idx[(idx >= max(lo, BREAK_W)) & (idx < hi - HOLD - 2)]
    out_ret, out_day = [], []
    free = 0
    for i in idx:
        e = i + 1
        if e < free:
            continue
        x = e + HOLD
        out_ret.append(close[x] / close[e] - 1 - COST)
        out_day.append(ts[e] // 86_400_000)
        free = x
    ci = cluster_ci(np.array(out_ret), np.array(out_day), alpha) if out_ret else None

    fv = realized_fwd_vol(close)[lo:hi]
    fc = forecast[lo:hi]
    valid = ~np.isnan(fv)
    q = np.nanpercentile(fc[valid], [20, 40, 60, 80])
    top_mask = fc[valid] >= q[3]
    other_mask = ~top_mask
    calibrated = bool(np.nanmedian(fv[valid][top_mask]) > np.nanmedian(fv[valid][other_mask])) if top_mask.any() else False
    return ci, calibrated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    if a.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")

    passed = []
    for s in SYMBOLS:
        ts, close = load_1min(s)
        n = len(ts)
        lo, hi = (int(n * 0.7), n) if a.holdout else (0, int(n * 0.7))
        print(f"{s}: {n} minutes, {'HOLDOUT' if a.holdout else 'DEV'} range {lo}-{hi}")
        ci, calibrated = run(s, lo, hi, ALPHA_BONF)
        print(f"  calibration (top-quintile forecast fwd-vol > median other): {calibrated}")
        if ci is None:
            print("  no trades fired")
            continue
        band, mean, cnt = ci
        print(f"  n={cnt} mean/trade={mean:+.5f} CI{100*(1-ALPHA_BONF):.2f}%={band}")
        if a.holdout and calibrated and band[0] > 0 and cnt >= 50:
            passed.append(s)
    if a.holdout:
        print("VERDICT:", "PASS" if passed else "KILL", passed)
    else:
        print("Dev run complete. Check calibration before running --holdout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
