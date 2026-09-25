"""Test S: intraday time-of-day / day-of-week seasonality. See docs/PREREG_seasonality.md.

Default = dev split (first 70%, all 93 cells scanned freely). --holdout evaluates ONLY cells named as
candidates in docs/PREREG_seasonality.md's Result section, on the sealed last 30%, once (lock file).
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "seasonality"
LOCK = CACHE / "S_RAN.lock"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
COST = 0.0024
N_CELLS = 31 * len(SYMBOLS)  # 24 hour + 7 dow buckets, per symbol
ALPHA_BONF = 0.05 / N_CELLS
RESAMPLES = 5000


def load_1min(symbol):
    arr = np.load(CACHE / f"{symbol}_1s.npy")
    ts, close = arr[:, 0].astype(np.int64), arr[:, 1]
    minute = ts // 60_000
    _, last_idx = np.unique(minute[::-1], return_index=True)
    last_idx = len(minute) - 1 - last_idx
    order = np.argsort(minute[last_idx])
    last_idx = last_idx[order]
    m_ts, m_close = ts[last_idx], close[last_idx]
    fwd_ret = np.full(len(m_close), np.nan)
    fwd_ret[:-1] = m_close[1:] / m_close[:-1] - 1
    hour = (m_ts // 3_600_000) % 24
    dow = ((m_ts // 86_400_000) + 4) % 7  # epoch day 0 = Thursday
    return m_ts, fwd_ret, hour, dow


def cluster_ci(ret, day_id, alpha, seed=7):
    ok = ~np.isnan(ret)
    ret, day_id = ret[ok], day_id[ok]
    if len(ret) == 0:
        return None
    ud, inv = np.unique(day_id, return_inverse=True)
    s = np.bincount(inv, weights=ret - COST)
    k = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ud), (RESAMPLES, len(ud)))
    means = s[pick].sum(1) / np.maximum(k[pick].sum(1), 1)
    lo, hi = 100 * alpha / 2, 100 * (1 - alpha / 2)
    ci = [round(float(v), 5) for v in np.percentile(means, [lo, hi])]
    return ci, round(float((ret - COST).mean()), 5), len(ret)


def scan(symbol, lo, hi, alpha):
    m_ts, ret, hour, dow = load_1min(symbol)
    ret, hour, dow = ret[lo:hi], hour[lo:hi], dow[lo:hi]
    day_id = m_ts[lo:hi] // 86_400_000
    results = {}
    for h in range(24):
        results[("hour", h)] = cluster_ci(ret[hour == h], day_id[hour == h], alpha)
    for d in range(7):
        results[("dow", d)] = cluster_ci(ret[dow == d], day_id[dow == d], alpha)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--cells", nargs="*", default=[],
                     help="candidate cells from dev, format SYMBOL:hour:H or SYMBOL:dow:D")
    a = ap.parse_args()
    if a.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        if not a.cells:
            print("REFUSED: holdout requires --cells naming dev candidates (see PREREG Result section)",
                  file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")

    passed = []
    for s in SYMBOLS:
        m_ts, _, _, _ = load_1min(s)
        n = len(m_ts)
        lo, hi = (int(n * 0.7), n) if a.holdout else (0, int(n * 0.7))
        print(f"{s}: {n} minutes, {'HOLDOUT' if a.holdout else 'DEV'} range {lo}-{hi}")
        res = scan(s, lo, hi, ALPHA_BONF)
        for (kind, val), r in sorted(res.items()):
            if r is None:
                continue
            ci, mean, cnt = r
            tag = f"{s}:{kind}:{val}"
            if a.holdout and tag not in a.cells:
                continue
            flag = " <-- CI excludes 0" if ci[0] > 0 or ci[1] < 0 else ""
            print(f"  {tag:<20} n={cnt:<7} mean={mean:+.5f} CI{100*(1-ALPHA_BONF):.4f}%={ci}{flag}")
            if ci[0] > 0 and cnt >= (100 if not a.holdout else 50):
                passed.append(tag)
    if a.holdout:
        print("VERDICT:", "PASS" if passed else "KILL", passed)
    else:
        print(f"\nDev candidates clearing alpha={ALPHA_BONF:.6f} (n={N_CELLS} cells): {passed}")
        print("Next: name these explicitly in docs/PREREG_seasonality.md Result section, then run --holdout --cells ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
