"""
EXPLORATORY robustness check for T3 (tape lead-lag). Not a new pre-registered test: it stress-tests the
pre-registered PASS by making the fill assumptions harsher and by adding a random-direction placebo.
Holdout windows only (same 60/40 split as hist_tests.py).

  slippage grid : entry price = last YES-equivalent print at t+delay PLUS s, exit = print at entry+HOLD MINUS s
  delay grid    : seconds between signal and entry
  placebo       : same trade times, direction chosen at random -> shows the cost floor of the simulation
  conservative  : entry uses the WORST (highest for a buy) print in the 3s before entry, exit the lowest in the 3s
                  before exit, so a stale or favorable last print cannot help us.

Usage: python scripts/hist_robustness.py
"""
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hist_tests import cluster_ci, fee, grid_prices, load_bn1s, load_trades, mean  # noqa: E402

HOLD = 10


def trade_list(by_w, split_w, cache):
    """Signal times per holdout window: (w, t, up, grid)."""
    out = []
    for w, trades in by_w.items():
        if w < split_w:
            continue
        try:
            spot = {int(k): v for k, v in load_bn1s(w, cache).items()}
        except Exception:
            continue
        grid = grid_prices(trades, w)
        last = -1e9
        for t in range(w + 35, w + 250):
            if t not in grid or (t - 5) not in spot or t not in spot:
                continue
            if not 0.15 <= grid[t] <= 0.85:
                continue
            r = math.log(spot[t] / spot[t - 5])
            if abs(r) * 1e4 > 3.0 and t - last >= 10:
                out.append((w, t, r > 0, grid))
                last = t
    return out


def simulate(sigs, slip, delay, placebo=False, conservative=False, seed=7):
    rng = random.Random(seed)
    by_w = defaultdict(list)
    for w, t, up, grid in sigs:
        if placebo:
            up = rng.random() < 0.5
        te, tx = t + delay, t + delay + HOLD
        if te not in grid or tx not in grid:
            continue
        if conservative:
            we = [grid[k] for k in range(te - 3, te + 1) if k in grid]
            wx = [grid[k] for k in range(tx - 3, tx + 1) if k in grid]
            if not we or not wx:
                continue
            pe = max(we) if up else 1.0 - min(we)
            pxv = min(wx) if up else 1.0 - max(wx)
        else:
            pe = grid[te] if up else 1.0 - grid[te]
            pxv = grid[tx] if up else 1.0 - grid[tx]
        pa, pb = pe + slip, pxv - slip
        if 0.02 < pa < 0.98:
            by_w[w].append((pb - pa - fee(pa) - fee(pb)) / pa)
    n = sum(len(v) for v in by_w.values())
    m, lo, hi = cluster_ci(by_w, mean)
    return n, m, lo, hi


def main():
    by_w, res = load_trades("data/trades/chunk_*.csv.gz")
    ws = sorted(by_w)
    split_w = ws[int(len(ws) * 0.6)]
    sigs = trade_list(by_w, split_w, "data/bn1s")
    print(f"holdout signals: {len(sigs)}")
    print("delay slip    n   mean net/$   corrected CI          (placebo mean)")
    for delay in (2, 4, 6, 10):
        for slip in (0.01, 0.02, 0.03, 0.05):
            n, m, lo, hi = simulate(sigs, slip, delay)
            pn, pm, _, _ = simulate(sigs, slip, delay, placebo=True)
            print(f"{delay:>4}s {slip:.2f} {n:>4}  {m:+.4f}   [{lo:+.4f},{hi:+.4f}]   ({pm:+.4f})")
    for slip in (0.01, 0.02, 0.03):
        n, m, lo, hi = simulate(sigs, slip, 2, conservative=True)
        print(f"conservative fills, delay 2s, slip {slip:.2f}: n={n} mean {m:+.4f} CI [{lo:+.4f},{hi:+.4f}]")


if __name__ == "__main__":
    main()
