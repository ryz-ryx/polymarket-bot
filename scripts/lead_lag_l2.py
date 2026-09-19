"""
Pre-registered confirmation of T3 on EXECUTABLE prices (analysis only). Committed BEFORE the L2 collector
has enough data to run it. The tape test (hist_tests.py T3) passed only at <= ~2s entry delay and 1-2c
slippage, using last-print prices that are not guaranteed liquidity. This uses the collector's real
top-of-book (data/l2 snapshots, k=Y/N, latest snapshot at or before the target time, max 3s stale).

Signal (same as T3): |Binance 1s log return over the last 5s| > 3 bps, one trade per 10s, 35 <= t-w <= 250,
YES mid in [0.15, 0.85]. Up buys YES at the ask, down buys NO at the ask, entry `delay` seconds after the
signal, exit at the same token's bid 10s later. Net per $ = (bid - ask - fee(ask) - fee(bid)) / ask, with
fee(p) = 0.07*p*(1-p). Cluster bootstrap over 5-minute windows, two-sided 98.75% (Bonferroni, 4 ideas).

  PASS   only if, at delay 2s with NO extra slippage, n >= 200 trades and the corrected CI is above 0.
  REPORT (context, not part of the verdict): delays 1-4s, and the same with +1c slippage each way.
  KILL   otherwise. A PASS still needs live paper confirmation with a reaction time we can actually reach.

Usage: python scripts/lead_lag_l2.py [--l2 "data/l2_pulled/l2/l2_*.jsonl*"] [--bn1s data/bn1s]
"""
import argparse
import bisect
import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hist_tests import cluster_ci, fee, load_bn1s, mean  # noqa: E402

STALE, HOLD = 3.0, 10


def load_snaps(pattern):
    """{window: {"Y": ([ts], [(bid, ask)]), "N": ...}} from snapshot rows."""
    raw = defaultdict(lambda: {"Y": [], "N": []})
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("t") == "s" and r["b"] and r["a"]:
                    raw[r["w"]][r["k"]].append((r["ts"], r["b"][0][0], r["a"][0][0]))
    out = {}
    for w, d in raw.items():
        out[w] = {k: ([x[0] for x in sorted(v)], [(x[1], x[2]) for x in sorted(v)]) for k, v in d.items() if v}
    return out


def quote_at(series, t):
    times, quotes = series
    i = bisect.bisect_right(times, t) - 1
    return quotes[i] if i >= 0 and t - times[i] <= STALE else None


def simulate(snaps, cache, delay, slip=0.0):
    by_w = defaultdict(list)
    for w, d in snaps.items():
        if "Y" not in d or "N" not in d:
            continue
        try:
            spot = {int(k): v for k, v in load_bn1s(w, cache).items()}
        except Exception:
            continue
        last = -1e9
        for t in range(w + 35, w + 250):
            if t not in spot or (t - 5) not in spot:
                continue
            qy = quote_at(d["Y"], t)
            if not qy or not 0.15 <= (qy[0] + qy[1]) / 2 <= 0.85:
                continue
            r = math.log(spot[t] / spot[t - 5])
            if abs(r) * 1e4 <= 3.0 or t - last < 10:
                continue
            tok = "Y" if r > 0 else "N"
            ent, ex = quote_at(d[tok], t + delay), quote_at(d[tok], t + delay + HOLD)
            if not ent or not ex:
                continue
            pa, pb = ent[1] + slip, ex[0] - slip
            if 0.02 < pa < 0.98:
                by_w[w].append((pb - pa - fee(pa) - fee(pb)) / pa)
                last = t
    n = sum(len(v) for v in by_w.values())
    m, lo, hi = cluster_ci(by_w, mean)
    return n, m, lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2", default="data/l2_pulled/l2/l2_*.jsonl*")
    ap.add_argument("--bn1s", default="data/bn1s")
    args = ap.parse_args()
    snaps = load_snaps(args.l2)
    print(f"windows with L2 snapshots: {len(snaps)}")
    verdict, n2 = False, 0
    for delay in (1, 2, 3, 4):
        for slip in (0.0, 0.01):
            n, m, lo, hi = simulate(snaps, args.bn1s, delay, slip)
            ci = f"[{lo:+.4f},{hi:+.4f}]" if lo is not None else "n/a"
            print(f"delay {delay}s slip {slip:.2f}: n={n:>4} mean net/$ {m if m is None else round(m, 4)} CI {ci}")
            if delay == 2 and slip == 0.0:
                verdict = n >= 200 and lo is not None and lo > 0
                n2 = n
    print("VERDICT:", "PASS (executable prices confirm the lag edge at 2s)" if verdict
          else f"KILL / INSUFFICIENT (n={n2} at delay 2s; needs >= 200 and CI above 0)")


if __name__ == "__main__":
    main()
