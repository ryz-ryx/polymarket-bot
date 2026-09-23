"""Test P: BTC/ETH spread mean-reversion. See docs/PREREG_pairs.md.
Default = development split (first 60%). `--holdout` runs the sealed 40% once (lock file).
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fast_backtest as fb  # noqa: E402 - reuses fetch(), cluster_ci()

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "fast"
LOCK = CACHE / "P_RAN.lock"
COSTS = {"0.24%": 0.0024, "0.12%": 0.0012}
W = 1440  # 24h rolling window, minutes
Z_ENTRY = 1.5
MAX_HOLD = 240


def rolling_z(ratio):
    n = len(ratio)
    lr = np.log(ratio)
    cs, cs2 = np.cumsum(lr), np.cumsum(lr ** 2)
    mean = np.full(n, np.nan)
    var = np.full(n, np.nan)
    mean[W:] = (cs[W:] - cs[:-W]) / W
    var[W:] = (cs2[W:] - cs2[:-W]) / W - mean[W:] ** 2
    sd = np.sqrt(np.maximum(var, 0))
    z = np.full(n, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        z[W:] = (lr[W:] - mean[W:]) / sd[W:]
    return z


def trades(btc_c, eth_c, lo, hi, cost):
    ratio = btc_c / eth_c
    z = rolling_z(ratio)
    out = []
    i = max(lo, W + 1)
    pos = None  # (leg='BTC'|'ETH', entry_idx)
    while i < min(hi, len(z)) - 1:
        if pos is None:
            if not np.isnan(z[i]):
                if z[i] < -Z_ENTRY:
                    pos = ("BTC", i + 1)
                elif z[i] > Z_ENTRY:
                    pos = ("ETH", i + 1)
            i += 1
            continue
        leg, e = pos
        held = i - e
        reverted = not np.isnan(z[i]) and ((leg == "BTC" and z[i] >= 0) or (leg == "ETH" and z[i] <= 0))
        if reverted or held >= MAX_HOLD:
            c = btc_c if leg == "BTC" else eth_c
            x = i
            if x > e:
                out.append((int(x // 1440), c[x] / c[e] - 1 - cost))
            pos = None
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    btc, eth = fb.fetch("BTCUSDT"), fb.fetch("ETHUSDT")
    n = min(len(btc), len(eth))
    btc_c, eth_c = btc[:n, 2], eth[:n, 2]
    if a.holdout:
        if LOCK.exists():
            print("REFUSED: holdout already run", file=sys.stderr)
            return 2
        CACHE.mkdir(parents=True, exist_ok=True)
        LOCK.write_text("ran")
    tag = "HOLDOUT" if a.holdout else "DEV"
    lo, hi = (int(n * 0.6), n) if a.holdout else (1500, int(n * 0.6))
    print(f"BTC/ETH ratio: {n} minutes, {tag} range {lo}-{hi}")
    passed = False
    for cl, cost in COSTS.items():
        res = fb.cluster_ci(trades(btc_c, eth_c, lo, hi, cost))
        print("pairs cost", cl, "-> (CI99.2ish, mean/trade, n):", res)
        if a.holdout and cl == "0.24%" and res and res[0][0] > 0 and res[2] >= 300:
            passed = True
    if a.holdout:
        print("VERDICT:", "PASS (dev-sign check pending)" if passed else "KILL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
