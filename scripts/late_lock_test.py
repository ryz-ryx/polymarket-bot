"""
Pre-registered test L: late-window "locked-in TWAP" mispricing. Analysis only. Committed BEFORE running on data.

Settlement (src/resolution.py): Up wins when the 60s TWAP before expiry >= the 60s TWAP before the window start.
At tau seconds before expiry (t = t1 - tau), the strike is known and (60 - tau) of the 60 settlement seconds are
already fixed, so the outcome is mostly determined. Model (no fitted parameters):
  known_sum   = sum of Binance 1s closes over [t1-60, t)         (60 - tau seconds)
  required    = (60*strike - known_sum) / tau                    (mean the remaining tau seconds must reach)
  sigma_1s    = stdev of 1s log returns over the prior 300s; price sigma = sigma_1s * spot
  p_up        = Phi((spot - required) / (price_sigma * sqrt(tau/3)))   (variance of a tau-second mean is tau/3)
Trade rule (fixed in advance): at tau = 30s, if p_up >= 0.85 buy YES; if p_up <= 0.15 buy NO; only if the model
probability of the chosen side exceeds the entry price by at least 5 cents. Entry price = last YES-equivalent tape
print at or before t (must be at most 10s old) converted to the chosen side, PLUS slippage (1c primary, 2c
robustness). Hold to settlement. Net per $ = (payoff - entry - fee(entry)) / entry, payoff = 1 if the chosen side wins
(actual resolved outcome from the tape), fee(p) = 0.07*p*(1-p). One trade per window.
Uncertainty: cluster bootstrap over windows, Bonferroni z = 2.5. Windows ordered by time; verdict on the last 40%
(sealed holdout, run once). Parameters are NOT tuned on the first 60%.

  PASS L only if holdout trades >= 100 at tau = 30s AND the corrected CI of mean net per $ is above 0 at BOTH 1c and
  2c slippage. KILL otherwise. Context rows (not part of the verdict): tau = 20s and 10s.
  A PASS is not a trading claim: it still needs executable-price confirmation from the live L2 book.

Usage: python scripts/late_lock_test.py [--trades "data/trades/chunk_*.csv.gz"] [--cache data/bn1s_full]
"""
import argparse
import bisect
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hist_tests import fee, load_trades, yes_price  # noqa: E402
from src.resolution import strike_twap  # noqa: E402

N_BOOT, TWAP, WINDOW = 4000, 60, 300


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sigma_1s(closes, t, span=300):
    xs = [closes[k] for k in range(t - span, t + 1) if k in closes]
    if len(xs) < span * 0.8:
        return None
    rets = [math.log(b / a) for a, b in zip(xs, xs[1:]) if a > 0 and b > 0]
    if len(rets) < 30:
        return None
    m = sum(rets) / len(rets)
    return max(math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)), 1e-6)


def p_up(closes, w, tau):
    t1, t = w + WINDOW, w + WINDOW - tau
    strike, sig, spot = strike_twap(closes, w), sigma_1s(closes, t), closes.get(t)
    known = [closes[k] for k in range(t1 - TWAP, t) if k in closes]
    if strike is None or sig is None or spot is None or len(known) < (TWAP - tau) * 0.8:
        return None
    known_sum = sum(known) / len(known) * (TWAP - tau)          # fill any gap with the mean
    required = (TWAP * strike - known_sum) / tau
    return phi((spot - required) / (sig * spot * math.sqrt(tau / 3.0)))


def last_price(trades, t, max_age=10):
    ev = sorted((ts, yes_price(idx, p)) for ts, _, _, idx, p, _, _ in trades)
    times = [e[0] for e in ev]
    i = bisect.bisect_right(times, t) - 1
    return ev[i][1] if i >= 0 and t - ev[i][0] <= max_age else None


def trade_net(p, ypx, y, slip):
    """Chosen side and net per $ under the fixed rule, or None."""
    if p >= 0.85:
        side_p, entry, win = p, ypx + slip, y == 1
    elif p <= 0.15:
        side_p, entry, win = 1 - p, (1 - ypx) + slip, y == 0
    else:
        return None
    if side_p - entry < 0.05 or not 0.02 < entry < 0.98:
        return None
    return ((1.0 if win else 0.0) - entry - fee(entry)) / entry


def boot_ci(vals_by_w, seed=9):
    keys, rng, means = list(vals_by_w), random.Random(seed), []
    for _ in range(N_BOOT):
        pick = [x for _ in keys for x in vals_by_w[keys[rng.randrange(len(keys))]]]
        means.append(sum(pick) / len(pick))
    means.sort()
    return means[int(0.00625 * N_BOOT)], means[int(0.99375 * N_BOOT) - 1]


def run(by_w, res, cache, split_w, tau, slip):
    out = {}
    for w in sorted(by_w):
        if w < split_w or not os.path.exists(os.path.join(cache, f"{w}.json")):
            continue
        closes = {int(k): v for k, v in json.load(open(os.path.join(cache, f"{w}.json"))).items()}
        p = p_up(closes, w, tau)
        ypx = last_price(by_w[w], w + WINDOW - tau)
        if p is None or ypx is None:
            continue
        net = trade_net(p, ypx, res[w], slip)
        if net is not None:
            out[w] = [net]
    n = len(out)
    if n < 10:
        return n, None, None, None
    m = sum(v[0] for v in out.values()) / n
    lo, hi = boot_ci(out)
    return n, m, lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/trades/chunk_*.csv.gz")
    ap.add_argument("--cache", default="data/bn1s_full")
    args = ap.parse_args()
    by_w, res = load_trades(args.trades)
    ws = sorted(by_w)
    split_w = ws[int(len(ws) * 0.6)]
    print(f"windows {len(ws)}; holdout from {split_w} ({len(ws) - int(len(ws) * 0.6)} windows)")
    verdict = True
    for tau in (30, 20, 10):
        for slip in (0.01, 0.02):
            n, m, lo, hi = run(by_w, res, args.cache, split_w, tau, slip)
            ci = f"[{lo:+.4f}, {hi:+.4f}]" if lo is not None else "n/a"
            print(f"tau {tau:>2}s slip {slip:.2f}: trades {n:>3}  mean net/$ {m if m is None else round(m, 4)}  CI {ci}")
            if tau == 30:
                verdict = verdict and n >= 100 and lo is not None and lo > 0
    print("VERDICT L:", "PASS (still needs executable-price confirmation)" if verdict
          else "KILL (no locked-in mispricing beats fees at 1c and 2c slippage with >= 100 trades)")


if __name__ == "__main__":
    main()
