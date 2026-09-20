"""
Power calculation for test L, written BEFORE the fresh sample is evaluated. Reads no fresh data and no outcomes.

PASS L needs corrected CI lower bound > 0, i.e. mean - z*sd/sqrt(n) > 0 with z = 2.5 (Bonferroni), and n >= 100.
From the old holdout (15 trades, mean +0.586/$, CI [-0.095, +1.40]) the per-trade sd is about
  sd = (hi - lo)/2 / z * sqrt(n) = (1.40 + 0.095)/2 / 2.5 * sqrt(15) ~ 1.16
which is what this script uses. It prints, for a grid of TRUE per-trade edges, the trades needed to clear the CI
(and whether the pre-registered 100-trade floor or the CI is the binding constraint), then the expected trades in the
fresh sample assuming the old qualifying rate (15 trades per old-holdout windows).

Usage: python scripts/power_test_l.py [--fresh-windows 2016] [--old-windows N --old-trades 15]
"""
import argparse
import math

Z, SD, FLOOR = 2.5, 1.16, 100


def needed(edge):
    """Trades needed for mean - Z*SD/sqrt(n) > 0 when the true mean equals edge."""
    return math.ceil((Z * SD / edge) ** 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh-windows", type=int, default=2016)
    ap.add_argument("--old-windows", type=int, default=None, help="windows in the old 40 percent holdout")
    ap.add_argument("--old-trades", type=int, default=15)
    args = ap.parse_args()
    print(f"assumed per-trade sd {SD}, z {Z}, floor {FLOOR} trades")
    for edge in (0.6, 0.3, 0.2, 0.1, 0.05):
        n = needed(edge)
        print(f"true edge {edge:+.2f}/$ -> {n} trades to clear the CI (binding: {'CI' if n > FLOOR else 'floor 100'})")
    if args.old_windows:
        rate = args.old_trades / args.old_windows
        print(f"old rate {rate:.4f} trades/window -> expected fresh trades ~ {rate * args.fresh_windows:.0f} "
              f"from {args.fresh_windows} windows")
    else:
        print("pass --old-windows to project the fresh-sample trade count")


if __name__ == "__main__":
    main()
