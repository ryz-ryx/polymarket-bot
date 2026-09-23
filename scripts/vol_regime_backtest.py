"""Test V: does the top realized-volatility tercile rescue the fast rules that test F killed? See docs/PREREG_vol_regime.md.
Reuses test F's already-downloaded data (data/fast/*.npy) and dev-split trades; no new data collection.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_backtest as fb  # noqa: E402

COST = 0.0024


def realized_vol_24h(c):
    """Same rolling-24h realized-vol formula fast_backtest.signals() uses for R1/R2, exposed standalone
    so R3 (breakout) trades can also be sliced by volatility regime even though its signal doesn't use it."""
    n = len(c)
    lr = np.zeros(n)
    lr[1:] = np.diff(np.log(c))
    cs, cs2 = np.cumsum(lr), np.cumsum(lr ** 2)
    w = 1440
    var = np.full(n, np.nan)
    var[w:] = (cs2[w:] - cs2[:-w]) / w - ((cs[w:] - cs[:-w]) / w) ** 2
    return np.sqrt(np.maximum(var, 0))


def main():
    for s in fb.SYMBOLS:
        arr = fb.fetch(s)
        n = len(arr)
        lo, hi = 1500, int(n * 0.6)  # same dev split as test F
        c = arr[:, 2]
        vol = realized_vol_24h(c)
        for rn, (kind, hold) in fb.RULES.items():
            tr = fb.trades(arr, kind, hold, lo, hi, COST)
            if not tr:
                print(rn, s, "-> no trades in dev split")
                continue
            idx = np.flatnonzero(fb.signals(kind, c))
            idx = idx[(idx >= lo) & (idx < hi - hold - 2)]
            entries, free = [], 0
            for i in idx:
                e = i + 1
                if e < free:
                    continue
                entries.append(e)
                free = e + hold
            v = vol[entries]
            valid = ~np.isnan(v)
            if valid.sum() < 30:
                print(rn, s, "-> too few valid-vol trades to tercile")
                continue
            thresh = np.percentile(v[valid], 200 / 3)
            top_tercile = [tr[i] for i in range(len(tr)) if valid[i] and v[i] >= thresh]
            full = fb.cluster_ci(tr)
            top = fb.cluster_ci(top_tercile)
            print(f"{rn} {s}: full (CI99.2, mean, n)={full}  top-vol-tercile={top}")


if __name__ == "__main__":
    sys.exit(main())
