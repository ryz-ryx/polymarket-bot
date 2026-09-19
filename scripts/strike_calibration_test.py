"""
Pre-registered test S (Phase 1.2): does a model using the CORRECT settlement convention add information over
the market price? Analysis only. Committed BEFORE being run on the data.

Settlement convention (measured, src/resolution.py): strike = 60s TWAP before the window start, final = 60s
TWAP before expiry, Up wins when final >= strike.

At t = w + 210 (90 seconds before expiry) the strike is fully known and the averaging window has not started:
  d = ln(spot_t / strike) / (sigma_1s * sqrt(T)),   T = 30 + 60/3 = 50 seconds
      (30s until the averaging window starts, plus the variance of a 60s average, which is 60/3)
  p_model = Phi(d);  sigma_1s = stdev of Binance 1s log returns over [t-300, t)
  p_market = last YES-equivalent trade price at or before t from the downloaded tape
Windows without a market price or enough Binance data are dropped. Windows ordered by time: first 60% explore,
last 40% sealed holdout, touched once.

  Explore: fit the blend weight w in 0..1 (step 0.05), p = w*p_model + (1-w)*p_market, minimizing Brier.
  Holdout statistic: mean per-window Brier difference (blend minus market) with a paired bootstrap CI at the
  Bonferroni level used by the sprint (z = 2.5, 99.375% one-sided bound on the upper end).
  Subsets: ALL windows, and NEAR-STRIKE windows (|d| < 0.5).
  PASS S only if the upper CI bound of the blend-minus-market difference is below 0 in ALL or in NEAR-STRIKE with
  at least 60 holdout windows in that subset. KILL otherwise (the correct-convention model adds nothing beyond
  the market). Context rows, not part of the verdict: pure model Brier, and the same model using the OLD point
  strike convention (strike = close at w).
  A PASS is information only: it must still clear fees and executable prices in later phases.

Usage: python scripts/strike_calibration_test.py [--trades "data/trades/chunk_*.csv.gz"] [--cache data/bn1s_full]
"""
import argparse
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hist_tests import grid_prices, load_trades  # noqa: E402
from src.resolution import strike_twap  # noqa: E402

T_VAR = 30.0 + 60.0 / 3.0
N_BOOT = 4000
W_GRID = [i / 20 for i in range(21)]


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def one_second_sigma(closes, t, span=300):
    xs = [closes[k] for k in range(t - span, t + 1) if k in closes]
    if len(xs) < span * 0.8:
        return None
    rets = [math.log(b / a) for a, b in zip(xs, xs[1:]) if a > 0 and b > 0]
    if len(rets) < 30:
        return None
    m = sum(rets) / len(rets)
    return max(math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)), 1e-6)


def model_p(spot, strike, sigma):
    d = math.log(spot / strike) / (sigma * math.sqrt(T_VAR))
    return phi(d), d


def build_rows(by_w, res, cache):
    rows = []
    for w in sorted(by_w):
        path = os.path.join(cache, f"{w}.json")
        if not os.path.exists(path):
            continue
        closes = {int(k): v for k, v in json.load(open(path)).items()}
        t = w + 210
        grid = grid_prices(by_w[w], w)
        s, sigma, strike = closes.get(t), one_second_sigma(closes, t), strike_twap(closes, w)
        if t not in grid or s is None or sigma is None or strike is None or w not in closes:
            continue
        p_new, d = model_p(s, strike, sigma)
        p_old, _ = model_p(s, closes[w], sigma)
        rows.append({"w": w, "y": res[w], "pm": grid[t], "p_new": p_new, "p_old": p_old, "d": d})
    return rows


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def blend_p(wt, r):
    return wt * r["p_new"] + (1 - wt) * r["pm"]


def boot_upper(diffs, seed=3):
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(N_BOOT))
    return means[int(0.00625 * N_BOOT)], means[int(0.99375 * N_BOOT) - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/trades/chunk_*.csv.gz")
    ap.add_argument("--cache", default="data/bn1s_full")
    args = ap.parse_args()
    by_w, res = load_trades(args.trades)
    rows = build_rows(by_w, res, args.cache)
    cut = int(len(rows) * 0.6)
    explore, hold = rows[:cut], rows[cut:]
    if len(hold) < 60:
        sys.exit(f"only {len(hold)} holdout windows")
    wt = min(W_GRID, key=lambda x: brier([blend_p(x, r) for r in explore], [r["y"] for r in explore]))
    print(f"usable windows {len(rows)}: explore {len(explore)} / holdout {len(hold)}   fitted blend weight w = {wt:.2f}")
    verdict = False
    for name, sub in (("ALL", hold), ("NEAR-STRIKE |d|<0.5", [r for r in hold if abs(r["d"]) < 0.5])):
        if len(sub) < 10:
            print(f"{name}: only {len(sub)} windows")
            continue
        ys = [r["y"] for r in sub]
        diffs = [(blend_p(wt, r) - r["y"]) ** 2 - (r["pm"] - r["y"]) ** 2 for r in sub]
        lo, hi = boot_upper(diffs)
        print(f"{name:<20} n={len(sub):>4}  Brier market {brier([r['pm'] for r in sub], ys):.4f}  "
              f"model(new) {brier([r['p_new'] for r in sub], ys):.4f}  model(old strike) {brier([r['p_old'] for r in sub], ys):.4f}  "
              f"blend {brier([blend_p(wt, r) for r in sub], ys):.4f}   blend-market {sum(diffs) / len(diffs):+.5f} CI [{lo:+.5f}, {hi:+.5f}]")
        if len(sub) >= 60 and hi < 0:
            verdict = True
    print("VERDICT S:", "PASS (information beyond the market; still needs fee/executable-price tests)" if verdict
          else "KILL (the correct-convention model adds nothing beyond the market price)")


if __name__ == "__main__":
    main()
