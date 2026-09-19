"""
Daily scorecard for the frozen forward test (FREEZE.md).

Prints how many post-freeze BTC trades have settled, the trade rate, and the projected
date to reach 200, then runs the random-side baseline pass/fail checks.

Usage: python scripts/daily_scorecard.py [--url URL | --fills PATH] [--since EPOCH]
"""
import argparse
import datetime as dt
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.random_baseline import DEFAULT_URL, freeze_ts, load_fills, net_pnl, paired_trades  # noqa: E402

TARGET = 200
DEADLINE = dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc)  # FREEZE.md schedule amendment
PACE_DATE = dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc)
PACE_MIN_TRADES = 30
LOOKS = (60, 100, 150)  # FREEZE.md futility-stop amendment
N_BOOT = 10_000


def settled_in_order(fills, since_ts, asset="BTC"):
    """(settle_ts, price, stake, won) for post-freeze settled trades, oldest first."""
    buys = {f["window_id"]: f for f in fills if f.get("asset") == asset and f.get("type") == "BUY"}
    out = []
    for f in fills:
        if f.get("asset") != asset or f.get("type") not in ("WIN", "LOSS") or f["ts"] < since_ts:
            continue
        b = buys.get(f["window_id"])
        if b is None or b["ts"] < since_ts:
            continue
        out.append((f["ts"], float(b["price"]), float(b["size_usd"]), f["type"] == "WIN"))
    return sorted(out)


def contamination_check(fills, since_ts, asset="BTC"):
    """Flag post-freeze trades whose result is not on-chain confirmed, and any CORRECTION rows.
    A Binance-fallback WIN/LOSS can be wrong until Polymarket resolves, and a CORRECTION row does
    not amend the original WIN/LOSS row, so either can skew the 200-trade tally."""
    post = [f for f in fills if f.get("asset") == asset and f.get("ts", 0) >= since_ts]
    settled = [f for f in post if f.get("type") in ("WIN", "LOSS")]
    off_chain = [f for f in settled if f.get("source") != "POLYMARKET_ONCHAIN"]
    corrections = [f for f in post if f.get("type") == "CORRECTION"]
    if not off_chain and not corrections:
        return [f"contamination check: clean ({len(settled)} settled, all POLYMARKET_ONCHAIN, 0 corrections)"]
    lines = [f"contamination check: WARNING {len(off_chain)} settled not on-chain confirmed, "
             f"{len(corrections)} CORRECTION rows (windows: "
             f"{sorted({f.get('window_id') for f in off_chain + corrections})})"]
    lines.append("  -> re-score with those windows excluded, and with them flipped, before trusting the verdict")
    return lines


def futility_checks(fills, since_ts):
    """FREEZE.md futility stop: at each look, kill if the 95% bootstrap UPPER bound of net PnL per $ staked < 0."""
    trades = settled_in_order(fills, since_ts)
    lines = []
    for look in LOOKS:
        if len(trades) < look:
            lines.append(f"futility look @{look}: not reached ({len(trades)}/{look})")
            continue
        per = [(net_pnl(p, s, w), s) for _, p, s, w in trades[:look]]
        rng = random.Random(42)
        boot = []
        for _ in range(N_BOOT):
            pick = [per[rng.randrange(look)] for _ in range(look)]
            boot.append(sum(x for x, _ in pick) / sum(s for _, s in pick))
        boot.sort()
        hi = boot[int(0.975 * N_BOOT)]
        lines.append(f"futility look @{look}: upper bound {hi:+.1%} -> "
                     f"{'KILL (upper bound < 0)' if hi < 0 else 'continue'}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--fills", default=None)
    ap.add_argument("--since", type=float, default=None)
    args = ap.parse_args()

    since = args.since if args.since is not None else freeze_ts()
    fills = load_fills(args.url, args.fills)
    n = len(paired_trades(fills, since))
    days = max((time.time() - since) / 86400.0, 1e-9)
    print(f"freeze start: {dt.datetime.fromtimestamp(since, dt.timezone.utc):%Y-%m-%d %H:%M} UTC   elapsed: {days:.2f} d")
    print(f"settled BTC trades since freeze: {n} / {TARGET}")
    if n:
        rate = n / days
        eta = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=max(TARGET - n, 0) / rate)
        print(f"rate: {rate:.1f}/day   projected {TARGET} trades: {eta:%Y-%m-%d}   "
              f"(hard deadline {DEADLINE:%Y-%m-%d}: {'ON TRACK' if eta <= DEADLINE else 'WILL MISS'})")
        print("note: early rates are noisy; pre-freeze data showed ~2/hour on one day, ~0 on others.")
    else:
        print("rate: n/a (no post-freeze settlements yet)")
    now = dt.datetime.now(dt.timezone.utc)
    if now >= PACE_DATE:
        print(f"pace checkpoint {PACE_DATE:%Y-%m-%d}: {n}/{PACE_MIN_TRADES} -> "
              f"{'OK' if n >= PACE_MIN_TRADES else 'FAIL: infeasible, kill rule applies'}")
    else:
        print(f"pace checkpoint {PACE_DATE:%Y-%m-%d}: need {PACE_MIN_TRADES}, have {n} "
              f"({(PACE_DATE - now).days} days left)")
    for line in contamination_check(fills, since) + futility_checks(fills, since):
        print(line)
    print("-" * 60)
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "random_baseline.py"),
           "--since", str(since)]
    cmd += ["--fills", args.fills] if args.fills else ["--url", args.url]
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
