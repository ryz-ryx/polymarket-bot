"""
Pre-registered test C: does Polymarket's book lag Binance spot, and is it tradable after fees?
Analysis only. Committed BEFORE being run on real data.

Data: tick_log rows {ts, window_id, spot, strike, yes_bid, yes_ask, no_bid, no_ask} (~2.3s cadence).
Sample filter (fixed in advance): YES mid in [0.15, 0.85], 30s <= tau <= 270s (tau from the 300s window
start), and all lookups within 3s tolerance of the target time.
Uncertainty: cluster bootstrap over 5-minute windows (ticks inside a window are not independent),
Bonferroni-corrected two-sided level 98.75% (four ideas in the sprint).

  C1  Lag exists. OLS slope of the YES-mid change over the next H=4s on the spot log-return over the
      last L=4s. PASS C1 only if the slope's corrected CI is entirely above 0.
  C2  Tradable. Signal: |spot log-return over L=4s| > 3 bps, max one trade per 10s. Direction up buys
      YES at the ask, down buys NO at the ask, filled one tick (>= 2s) LATE, exited at the same
      token's bid 10s after entry. Net per share = exit_bid - entry_ask - fee(entry) - fee(exit),
      fee(p) = 0.07*p*(1-p). PASS C2 only if n >= 200 trades and the corrected CI of mean net per
      $ staked is entirely above 0.
  KILL C if C2 fails. C1 alone is not a trading edge. Also printed for context: gross (no fees) and
  the no-delay variant, to show how fast any edge decays.

Usage: python scripts/lead_lag_test.py [--ticks data/tick_log_pulled.jsonl.gz]
"""
import argparse
import bisect
import gzip
import json
import math
import random
import sys

FEE = 0.07
L, H, HOLD, DELAY, TOL = 4.0, 4.0, 10.0, 2.0, 3.0
THR_BPS = 3.0
Z = 2.5          # two-sided z at alpha = 0.05 / 4
N_BOOT = 3000


def fee(p):
    return FEE * p * (1.0 - p)


def load(path):
    opener = gzip.open if path.endswith(".gz") else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("spot") and r.get("yes_bid") is not None and r.get("yes_ask") is not None:
                    rows.append(r)
    rows.sort(key=lambda r: r["ts"])
    return rows


class Index:
    def __init__(self, rows):
        self.rows = rows
        self.ts = [r["ts"] for r in rows]

    def at_or_before(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        return self.rows[i] if i >= 0 and t - self.ts[i] <= TOL else None

    def at_or_after(self, t):
        i = bisect.bisect_left(self.ts, t)
        return self.rows[i] if i < len(self.rows) and self.ts[i] - t <= TOL else None


def in_sample(r):
    mid = (r["yes_bid"] + r["yes_ask"]) / 2.0
    tau = int(r["ts"] // 300) * 300 + 300 - r["ts"]
    return 0.15 <= mid <= 0.85 and 30.0 <= tau <= 270.0


def c1_points(idx):
    """(window, spot_return, yes_mid_change) per in-sample tick."""
    pts = []
    for r in idx.rows:
        if not in_sample(r):
            continue
        prev, fut = idx.at_or_before(r["ts"] - L), idx.at_or_after(r["ts"] + H)
        if not prev or not fut or fut["window_id"] != r["window_id"] or prev["window_id"] != r["window_id"]:
            continue
        ret = math.log(r["spot"] / prev["spot"])
        d = (fut["yes_bid"] + fut["yes_ask"]) / 2.0 - (r["yes_bid"] + r["yes_ask"]) / 2.0
        pts.append((r["window_id"], ret, d))
    return pts


def slope(pts):
    n = len(pts)
    if n < 10:
        return None
    mx, my = sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n
    sxx = sum((p[1] - mx) ** 2 for p in pts)
    return sum((p[1] - mx) * (p[2] - my) for p in pts) / sxx if sxx > 0 else None


def cluster_boot(items, stat, seed=42):
    """items: [(cluster_id, payload...)]. Returns (point, lo, hi) at the corrected level."""
    groups = {}
    for it in items:
        groups.setdefault(it[0], []).append(it)
    keys = list(groups)
    rng = random.Random(seed)
    vals = []
    for _ in range(N_BOOT):
        pick = []
        for _ in keys:
            pick.extend(groups[keys[rng.randrange(len(keys))]])
        v = stat(pick)
        if v is not None:
            vals.append(v)
    vals.sort()
    if not vals:
        return stat(items), None, None
    return stat(items), vals[int(0.00625 * len(vals))], vals[int(0.99375 * len(vals)) - 1]


def c2_trades(idx, delay=DELAY, fees=True):
    """(window, net_per_dollar) trades under the pre-registered rule."""
    out, last_trade_ts = [], -1e18
    for r in idx.rows:
        if not in_sample(r) or r["ts"] - last_trade_ts < 10.0:
            continue
        prev = idx.at_or_before(r["ts"] - L)
        if not prev or prev["window_id"] != r["window_id"]:
            continue
        bps = math.log(r["spot"] / prev["spot"]) * 1e4
        if abs(bps) <= THR_BPS:
            continue
        side_ask, side_bid = ("yes_ask", "yes_bid") if bps > 0 else ("no_ask", "no_bid")
        ent = idx.at_or_after(r["ts"] + delay)
        if not ent or ent["window_id"] != r["window_id"]:
            continue
        ex = idx.at_or_after(ent["ts"] + HOLD)
        if not ex or ex["window_id"] != r["window_id"]:
            continue
        pa, pb = ent.get(side_ask), ex.get(side_bid)
        if not pa or pb is None or pa <= 0.02 or pa >= 0.98:
            continue
        net = pb - pa - ((fee(pa) + fee(pb)) if fees else 0.0)
        out.append((r["window_id"], net / pa))
        last_trade_ts = r["ts"]
    return out


def mean_stat(pick):
    return sum(x[1] for x in pick) / len(pick) if pick else None


def report(name, trades):
    if not trades:
        print(f"{name}: no trades")
        return None
    m, lo, hi = cluster_boot(trades, mean_stat)
    print(f"{name}: n={len(trades)}  mean net/$ {m:+.4f}  corrected CI [{lo:+.4f}, {hi:+.4f}]")
    return len(trades), m, lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", default="data/tick_log_pulled.jsonl.gz")
    args = ap.parse_args()
    rows = load(args.ticks)
    if len(rows) < 2000:
        sys.exit(f"only {len(rows)} usable ticks; need >= 2000")
    idx = Index(rows)
    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600.0
    print(f"ticks {len(rows)} over {span_h:.1f}h, windows {len({r['window_id'] for r in rows})}")

    pts = c1_points(idx)
    s, lo, hi = cluster_boot(pts, slope)
    if s is not None and lo is not None:
        print(f"C1 slope (dYesMid over {H:g}s per unit {L:g}s spot log-return): {s:+.2f}  n={len(pts)}  "
              f"corrected CI [{lo:+.2f}, {hi:+.2f}]")
    else:
        print("C1: insufficient data")
    c1 = s is not None and lo is not None and lo > 0
    print("C1:", "PASS (book lags spot)" if c1 else "FAIL (no demonstrable lag)")

    print("\nC2 tradable test, taker entry one tick late, exit after 10s:")
    net = report("  with fees          ", c2_trades(idx))
    report("  gross (no fee)     ", c2_trades(idx, fees=False))
    report("  no delay, with fees", c2_trades(idx, delay=0.0))
    ok = bool(net and net[0] >= 200 and net[2] is not None and net[2] > 0)
    print("\nC2:", "PASS" if ok else "FAIL")
    print("VERDICT C:", "SURVIVES (shadow-test it)" if ok else "KILL (not tradable after fees at this speed)")


if __name__ == "__main__":
    main()
