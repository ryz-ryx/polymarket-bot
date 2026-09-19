"""
Phase 2: maker replay on the collector's L2 log with a CONSERVATIVE fill model (analysis only).

Two-sided quoting is modelled as a resting bid on the YES token and a resting bid on the NO
token (buying NO at q equals selling YES at 1-q). Fills come only from taker SELL prints that
trade through our bid, or hit it after the queue that was ahead of us is consumed (src/mm/fills).
Fair value comes from Binance spot in data/tick_log.jsonl (fair_prob_up); quotes are pulled when
spot moves more than pull_move_bps in 2s.

Metrics per fill: 5s and 30s mark-out of the token mid, settlement PnL (outcome inferred from the
last YES mid: >0.9 up, <0.1 down), and an OPTIMISTIC rebate (20% of the taker fee on the shares
we fill, i.e. assuming we are the only maker on that print).

Kill rule (pre-registered): with >= 300 fills, KILL if mean 30s mark-out + rebate per share <= 0,
or if the edge appears only when queue position is ignored (--no-queue). Fewer fills = INSUFFICIENT.

Usage: python scripts/mm_replay.py --l2 "data/l2/l2_*.jsonl*" --ticks data/tick_log.jsonl [--no-queue]
"""
import argparse
import bisect
import glob
import gzip
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.mm.arb import FEE_RATE  # noqa: E402
from src.mm.fills import RestingBid  # noqa: E402
from src.mm.quoter import QuoterParams, fair_prob_up, quote_bid, should_pull  # noqa: E402

REBATE_SHARE = 0.20
WINDOW = 300


def load_l2(pattern):
    rows = []
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    rows.sort(key=lambda r: r["ts"])
    return rows


class Ticks:
    def __init__(self, rows):
        rows = sorted((r for r in rows if r.get("spot")), key=lambda r: r["ts"])
        self.ts = [r["ts"] for r in rows]
        self.spot = [r["spot"] for r in rows]
        self.strike = {}
        for r in rows:
            self.strike.setdefault(int(r["ts"] // WINDOW) * WINDOW, r.get("strike") or r["spot"])

    def spot_at(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        return self.spot[i] if i >= 0 and t - self.ts[i] < 10 else None

    def vol_at(self, t, default=0.5):
        i = bisect.bisect_right(self.ts, t) - 1
        j = bisect.bisect_left(self.ts, t - 300)
        if i - j < 20:
            return default
        rets = [math.log(self.spot[k + 1] / self.spot[k]) for k in range(j, i) if self.spot[k] > 0]
        if len(rets) < 10:
            return default
        dt = (self.ts[i] - self.ts[j]) / max(i - j, 1)
        return max(statistics.pstdev(rets) * (31557600.0 / max(dt, 1e-3)) ** 0.5, 0.05)


def queue_ahead_at(snapshot_bids, price):
    """Displayed size at our price if visible; better-priced size if we sit inside the top levels;
    otherwise all visible size (conservative)."""
    if not snapshot_bids:
        return 0.0
    for p, s in snapshot_bids:
        if abs(p - price) < 1e-9:
            return s
    if price > snapshot_bids[0][0]:
        return 0.0                      # we would improve the best bid: front of queue
    return sum(s for _, s in snapshot_bids)


def simulate(events, ticks, params, use_queue=True, vol_default=0.5):
    resting, inventory, mids = {}, defaultdict(float), defaultdict(list)
    fills = []
    for ev in events:
        if ev.get("t") not in ("s", "t"):      # ignore other row types, e.g. cross-venue quotes ("x")
            continue
        w, k, ts = ev["w"], ev["k"], ev["ts"]
        key = (w, k)
        if ev["t"] == "s":
            bids, asks = ev["b"], ev["a"]
            if bids and asks:
                mids[key].append((ts, (bids[0][0] + asks[0][0]) / 2.0))
            tau = w + WINDOW - ts
            spot, strike = ticks.spot_at(ts), ticks.strike.get(w)
            before = ticks.spot_at(ts - 2.0)
            q = None
            if spot and strike and not (before and should_pull(spot, before, params)):
                fy = fair_prob_up(spot, strike, tau, ticks.vol_at(ts, vol_default))
                q = quote_bid(fy if k == "Y" else 1.0 - fy, inventory[key], tau, params)
                if q and asks:
                    q["price"] = min(q["price"], round(asks[0][0] - params.tick, 4))
                    if q["price"] < params.min_price:
                        q = None
            cur = resting.get(key)
            if q is None:
                resting.pop(key, None)
            elif cur is None or abs(cur.price - q["price"]) > 1e-9:
                resting[key] = RestingBid(q["price"], q["size"], queue_ahead_at(bids, q["price"]) if use_queue else 0.0)
        elif ev["t"] == "t":
            bid = resting.get(key)
            if bid:
                got = bid.on_trade(ev["p"], ev["s"], ev["d"])
                if got > 0:
                    inventory[key] += got
                    fills.append({"ts": ts, "w": w, "k": k, "price": bid.price, "shares": got})
    return fills, mids


def mid_after(series, t):
    i = bisect.bisect_left([x[0] for x in series], t)
    return series[i][1] if i < len(series) else None


def outcome_of(mids, w):
    s = mids.get((w, "Y"))
    if not s:
        return None
    last = s[-1][1]
    return 1 if last > 0.9 else 0 if last < 0.1 else None


def summarize(fills, mids):
    rows = []
    for f in fills:
        s = mids.get((f["w"], f["k"]), [])
        m5, m30 = mid_after(s, f["ts"] + 5), mid_after(s, f["ts"] + 30)
        out = outcome_of(mids, f["w"])
        payoff = None if out is None else (out if f["k"] == "Y" else 1 - out)
        rows.append({"m5": None if m5 is None else m5 - f["price"], "m30": None if m30 is None else m30 - f["price"],
                     "settle": None if payoff is None else payoff - f["price"], "shares": f["shares"],
                     "rebate": REBATE_SHARE * FEE_RATE * f["price"] * (1 - f["price"])})
    return rows


def mean_of(rows, key):
    xs = [r[key] for r in rows if r[key] is not None]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2", default="data/l2/l2_*.jsonl*")
    ap.add_argument("--ticks", default="data/tick_log.jsonl")
    ap.add_argument("--no-queue", action="store_true", help="ignore queue position (optimistic control)")
    ap.add_argument("--half-spread", type=float, default=0.02)
    args = ap.parse_args()

    events = load_l2(args.l2)
    opener = gzip.open if args.ticks.endswith(".gz") else open
    with opener(args.ticks, "rt", encoding="utf-8") as f:
        ticks = Ticks([json.loads(x) for x in f if x.strip()])
    if not events or not ticks.ts:
        sys.exit("need both L2 events and tick_log rows (collector/tick logger must have run)")
    params = QuoterParams(half_spread=args.half_spread)
    fills, mids = simulate(events, ticks, params, use_queue=not args.no_queue)
    rows = summarize(fills, mids)
    n = len(rows)
    print(f"events {len(events)}  fills {n}  shares {sum(r['shares'] for r in rows):.0f}  queue-model {'off' if args.no_queue else 'on'}")
    if not n:
        sys.exit("no simulated fills")
    for name in ("m5", "m30", "settle"):
        m, c = mean_of(rows, name)
        print(f"mean {name:<6} per share: {m:+.4f}  (n={c})" if m is not None else f"mean {name}: n/a")
    reb = sum(r["rebate"] for r in rows) / n
    adverse = sum(1 for r in rows if r["m30"] is not None and r["m30"] < 0) / max(mean_of(rows, "m30")[1], 1)
    print(f"optimistic rebate per share: {reb:+.4f}   adverse-selection share (30s markout<0): {adverse:.0%}")
    m30 = mean_of(rows, "m30")[0]
    if args.no_queue:
        print("CONTROL ONLY (queue ignored = optimistic upper bound, not a verdict). Per the pre-registration, an "
              "edge that appears only without queue modelling is a KILL.")
    elif n < 300 or m30 is None:
        print("VERDICT: INSUFFICIENT (need >= 300 fills)")
    else:
        print("VERDICT:", "KILL (mean 30s markout + rebate <= 0)" if m30 + reb <= 0 else "SURVIVES this test: go to shadow mode")


if __name__ == "__main__":
    main()
