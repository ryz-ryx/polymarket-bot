"""
Pre-registered historical tests T1-T3 on the downloaded trade tape (analysis only). Committed BEFORE
being run on the real data. Sealed design: windows are ordered by time; the first 60% are the explore
split, the last 40% the holdout. Every verdict is computed on the HOLDOUT only, once.

Common: trades are restricted to [w, w+300) of their own window (post-close prints are redemptions).
Each token price is converted to a YES-equivalent (outcome_idx 1 -> 1 - price). Fee(p) = 0.07*p*(1-p).
CIs are cluster bootstraps over windows at a Bonferroni-corrected two-sided level (3 tests, z = 2.4).

  T1 Wallet persistence. Per (wallet, window) PnL = sum(signed shares * token payoff) + cash flow (no fees).
     Wallets with >= 30 explore windows are ranked by mean explore PnL/window; the top decile's mean
     HOLDOUT PnL/window must have a corrected CI entirely above 0, AND the Spearman correlation between
     explore and holdout mean PnL (wallets with >= 30 explore and >= 10 holdout windows) must be >= 0.10.
     KILL T1 otherwise (persistent skill not shown).
  T2 Maker upper bound (side-agnostic, optimistic: ignores queue position). Every 10s in 30 <= tau <= 270 a
     hypothetical YES bid is posted at last YES-equivalent price - 0.02 (floored to 1c), cancelled after
     10s. It fills at its price if a distinct match prints strictly below the bid within 10s. Markout =
     last YES-equivalent price at fill + 30s minus bid price, plus an optimistic rebate of 20% of fee(bid).
     PASS T2 only if holdout fills >= 300 and mean net markout per share has a corrected CI above 0.
  T3 Tape lead-lag. On a 1s grid (YES-equivalent last price, filter 0.15-0.85 and 30 <= tau <= 270):
     (a) OLS slope of the next-5s price change on the last-5s Binance 1s log return, holdout, must have a
     corrected CI above 0; (b) tradable: |return| > 3 bps, one trade per 10s, entry price = last price at
     +2s plus 1c slippage, exit at +12s minus 1c slippage, both-leg fees; PASS only if holdout n >= 200 and
     corrected CI of mean net per $ above 0.
  A test that fails is a KILL. Survivors need a fresh 300-window sample and live L2 confirmation.

Usage: python scripts/hist_tests.py [--trades "data/trades/chunk_*.csv.gz"] [--bn1s data/bn1s]
"""
import argparse
import bisect
import csv
import glob
import gzip
import json
import math
import os
import random
import sys
import urllib.request
from collections import defaultdict

FEE, Z, N_BOOT, W = 0.07, 2.4, 2000, 300


def fee(p):
    return FEE * p * (1.0 - p)


def yes_price(idx, price):
    return price if idx == 0 else 1.0 - price


def load_trades(pattern):
    """{window: [(ts, wallet, side, idx, price, size, tx)]} restricted to the window; plus {window: resolved_up}."""
    by_w, res = defaultdict(list), {}
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for r in csv.reader(f):
                if len(r) < 9 or r[7] == "":
                    continue
                w, ts = int(r[0]), int(r[1])
                if not (w <= ts < w + W):
                    continue
                by_w[w].append((ts, r[2], r[3], int(r[4]), float(r[5]), float(r[6]), r[8]))
                res[w] = int(r[7])
    return by_w, res


def pnl_by_wallet_window(by_w, res):
    out = {}
    for w, trades in by_w.items():
        acc = defaultdict(lambda: [0.0, 0.0, 0.0])            # wallet -> [cash, shares_idx0, shares_idx1]
        for ts, wal, side, idx, p, sz, _ in trades:
            sgn = 1.0 if side == "B" else -1.0
            a = acc[wal]
            a[0] -= sgn * p * sz
            a[1 + idx] += sgn * sz
        for wal, (cash, s0, s1) in acc.items():
            out[(wal, w)] = cash + s0 * res[w] + s1 * (1 - res[w])
    return out


def cluster_ci(vals_by_cluster, stat, seed=1):
    keys = list(vals_by_cluster)
    if len(keys) < 5:
        return stat([x for k in keys for x in vals_by_cluster[k]]), None, None
    rng = random.Random(seed)
    boots = []
    for _ in range(N_BOOT):
        pick = [x for _ in keys for x in vals_by_cluster[keys[rng.randrange(len(keys))]]]
        v = stat(pick)
        if v is not None:
            boots.append(v)
    boots.sort()
    point = stat([x for k in keys for x in vals_by_cluster[k]])
    if not boots:
        return point, None, None
    lo_i, hi_i = int(0.008 * len(boots)), int(0.992 * len(boots)) - 1
    return point, boots[lo_i], boots[max(hi_i, lo_i)]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


def t1(by_w, res, split_w):
    pnl = pnl_by_wallet_window(by_w, res)
    exp = defaultdict(list)
    hold = defaultdict(lambda: defaultdict(list))
    for (wal, w), v in pnl.items():
        if w < split_w:
            exp[wal].append(v)
        else:
            hold[wal][w].append(v)
    ranked = sorted((wal for wal, v in exp.items() if len(v) >= 30), key=lambda x: -mean(exp[x]))
    if len(ranked) < 20:
        return {"verdict": "INSUFFICIENT (fewer than 20 wallets with >= 30 explore windows)", "qualified_wallets": len(ranked)}
    top = ranked[: max(len(ranked) // 10, 1)]
    by_win = defaultdict(list)
    for wal in top:
        for w, v in hold[wal].items():
            by_win[w].extend(v)
    point, lo, hi = cluster_ci(by_win, mean)
    both = [wal for wal in ranked if sum(len(v) for v in hold[wal].values()) >= 10]
    rho = spearman([mean(exp[x]) for x in both], [mean([v for vv in hold[x].values() for v in vv]) for x in both]) if len(both) >= 10 else None
    ok = lo is not None and lo > 0 and rho is not None and rho >= 0.10
    return {"qualified_wallets": len(ranked), "top_n": len(top), "holdout_mean_pnl_per_window": point, "ci": (lo, hi),
            "spearman": rho, "verdict": "PASS" if ok else "KILL (persistent skill not shown)"}


def grid_prices(trades, w):
    """1s grid of YES-equivalent last price for t in [w, w+300)."""
    ev = sorted((ts, yes_price(idx, p)) for ts, _, _, idx, p, _, _ in trades)
    times = [e[0] for e in ev]
    out = {}
    for t in range(w, w + W):
        i = bisect.bisect_right(times, t) - 1
        if i >= 0:
            out[t] = ev[i][1]
    return out


def t2(by_w, split_w):
    per_window = defaultdict(list)
    for w, trades in by_w.items():
        if w < split_w:
            continue
        matches = sorted({(ts, yes_price(idx, p), sz, tx) for ts, _, _, idx, p, sz, tx in trades})
        times = [m[0] for m in matches]
        grid = grid_prices(trades, w)
        for t in range(w + 30, w + 270, 10):
            if t not in grid:
                continue
            bid = math.floor((grid[t] - 0.02) * 100 + 1e-9) / 100.0
            if bid < 0.03:
                continue
            i = bisect.bisect_right(times, t)
            fill = next((m for m in matches[i:] if m[0] <= t + 10 and m[1] < bid - 1e-9), None)
            if not fill:
                continue
            later = grid.get(min(fill[0] + 30, w + W - 1))
            if later is None:
                continue
            per_window[w].append(later - bid + 0.20 * fee(bid))
    n = sum(len(v) for v in per_window.values())
    point, lo, hi = cluster_ci(per_window, mean)
    ok = n >= 300 and lo is not None and lo > 0
    return {"fills": n, "mean_net_markout": point, "ci": (lo, hi), "verdict": "PASS" if ok else "KILL (no positive markout, or < 300 fills)"}


def load_bn1s(w, cache_dir):
    path = os.path.join(cache_dir, f"{w}.json")
    if os.path.exists(path):
        return json.load(open(path))
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={(w - 10) * 1000}&limit=330"
    with urllib.request.urlopen(url, timeout=20) as r:
        rows = json.load(r)
    data = {str(int(k[0] // 1000)): float(k[4]) for k in rows}
    os.makedirs(cache_dir, exist_ok=True)
    json.dump(data, open(path, "w"))
    return data


def t3(by_w, split_w, cache_dir):
    pts, trades_net = defaultdict(list), defaultdict(list)
    for w, trades in by_w.items():
        if w < split_w:
            continue
        try:
            spot = {int(k): v for k, v in load_bn1s(w, cache_dir).items()}
        except Exception:
            continue
        grid = grid_prices(trades, w)
        last_trade = -1e9
        for t in range(w + 35, w + 265):
            if t not in grid or (t - 5) not in spot or t not in spot or (t + 5) not in grid:
                continue
            p0 = grid[t]
            if not 0.15 <= p0 <= 0.85:
                continue
            r = math.log(spot[t] / spot[t - 5])
            pts[w].append((r, grid[t + 5] - p0))
            if abs(r) * 1e4 > 3.0 and t - last_trade >= 10 and (t + 12) in grid and (t + 2) in grid:
                up = r > 0
                pe = grid[t + 2] if up else 1.0 - grid[t + 2]
                px = grid[t + 12] if up else 1.0 - grid[t + 12]
                pa, pb = pe + 0.01, px - 0.01
                if 0.02 < pa < 0.98:
                    trades_net[w].append((pb - pa - fee(pa) - fee(pb)) / pa)
                    last_trade = t

    def slope(items):
        n = len(items)
        if n < 30:
            return None
        mx, my = sum(i[0] for i in items) / n, sum(i[1] for i in items) / n
        sxx = sum((i[0] - mx) ** 2 for i in items)
        return sum((i[0] - mx) * (i[1] - my) for i in items) / sxx if sxx else None

    s, slo, shi = cluster_ci(pts, slope)
    m, lo, hi = cluster_ci(trades_net, mean)
    n = sum(len(v) for v in trades_net.values())
    ok = slo is not None and slo > 0 and n >= 200 and lo is not None and lo > 0
    return {"slope": s, "slope_ci": (slo, shi), "trades": n, "mean_net_per_dollar": m, "ci": (lo, hi),
            "verdict": "PASS" if ok else "KILL (lag not shown, or not tradable after fees/slippage)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/trades/chunk_*.csv.gz")
    ap.add_argument("--bn1s", default="data/bn1s")
    ap.add_argument("--only", default="123")
    args = ap.parse_args()
    by_w, res = load_trades(args.trades)
    ws = sorted(by_w)
    if len(ws) < 100:
        sys.exit(f"only {len(ws)} windows loaded; need >= 100")
    split_w = ws[int(len(ws) * 0.6)]
    print(f"windows {len(ws)} (explore {int(len(ws) * 0.6)} / holdout {len(ws) - int(len(ws) * 0.6)}), "
          f"trades {sum(len(v) for v in by_w.values())}")
    if "1" in args.only:
        print("T1", t1(by_w, res, split_w))
    if "2" in args.only:
        print("T2", t2(by_w, split_w))
    if "3" in args.only:
        print("T3", t3(by_w, split_w, args.bn1s))


if __name__ == "__main__":
    main()
