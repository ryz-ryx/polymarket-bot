"""
Daily scorecard for the frozen forward test (FREEZE.md).

Prints how many post-freeze BTC trades have settled, the trade rate, and the projected
date to reach 200, then runs the random-side baseline pass/fail checks.

Usage: python scripts/daily_scorecard.py [--url URL | --fills PATH] [--since EPOCH]
"""
import argparse
import datetime as dt
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.random_baseline import DEFAULT_URL, freeze_ts, load_fills, paired_trades  # noqa: E402

TARGET = 200
DEADLINE = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)


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
    print("-" * 60)
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "random_baseline.py"),
           "--since", str(since)]
    cmd += ["--fills", args.fills] if args.fills else ["--url", args.url]
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
