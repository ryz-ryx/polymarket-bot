"""
Forward-test scorecard for the frozen BTC model (council rec #3).

Counts only BTC trades that resolved AFTER data/model_freeze.json's freeze_ts, from
fills_log.jsonl (WIN/LOSS rows carry cost + net_pnl). Reports profit rate on capital
risked with a bootstrap 95% CI, and a PASS/FAIL/KEEP-GOING verdict:

  PASS        n >= MIN_TRADES and CI lower bound > 0
  FAIL        n >= MIN_TRADES and CI upper bound < 0 (or point estimate <= 0 at n >= MIN_TRADES)
  KEEP-GOING  otherwise (not enough evidence yet)

fills_log.jsonl lives on the deployed machine (Railway); pass its path with --fills.
Usage: python scripts/forward_test_report.py [--fills data/fills_log.jsonl] [--min-trades 300]
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.model_freeze import load_freeze, check_drift  # noqa: E402


def load_resolved_trades(path: str, since_ts: float, asset: str = "BTC"):
    trades = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("asset") != asset or d.get("type") not in ("WIN", "LOSS"):
                continue
            if d.get("ts", 0) < since_ts:
                continue
            trades.append((float(d["cost"]), float(d["net_pnl"])))
    return trades


def bootstrap_profit_rate_ci(trades, n_boot=10000, seed=42):
    rng = random.Random(seed)
    n = len(trades)
    rates = []
    for _ in range(n_boot):
        sample = [trades[rng.randrange(n)] for _ in range(n)]
        risked = sum(c for c, _ in sample)
        rates.append(sum(p for _, p in sample) / risked if risked else 0.0)
    rates.sort()
    return rates[int(0.025 * n_boot)], rates[int(0.975 * n_boot)]


def verdict(n, lo, hi, point, min_trades):
    if n < min_trades:
        return "KEEP-GOING"
    if lo > 0:
        return "PASS"
    if hi < 0 or point <= 0:
        return "FAIL"
    return "KEEP-GOING"


def timeboxed_verdict(base: str, elapsed_days: float, max_days: float) -> str:
    """Pre-registered stop rule: an inconclusive test does not run forever (or past the
    hosting budget). If the deadline passes without a PASS, the project stops."""
    if base == "KEEP-GOING" and elapsed_days >= max_days:
        return "STOP-TIMEBOX"
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills", default="data/fills_log.jsonl")
    ap.add_argument("--min-trades", type=int, default=300)
    ap.add_argument("--max-days", type=float, default=60.0,
                    help="deadline (days since freeze) after which no PASS means STOP")
    ap.add_argument("--asset", default="BTC")
    args = ap.parse_args()

    frozen = load_freeze()
    if not frozen:
        print("No data/model_freeze.json -- run scripts/model_freeze.py first.")
        return 1
    drift = check_drift(frozen)
    if drift:
        print(f"WARNING: live config drifted from the freeze in {drift}. Forward test is contaminated; re-freeze with --force to restart the clock.")

    trades = load_resolved_trades(args.fills, frozen["freeze_ts"], args.asset.upper())
    n = len(trades)
    elapsed_days = (time.time() - frozen["freeze_ts"]) / 86400.0
    print(f"Forward test [{args.asset.upper()}] since freeze (commit {frozen['manifest']['git_commit'][:8]}): {n}/{args.min_trades} resolved trades, day {elapsed_days:.1f}/{args.max_days:.0f}")
    if n == 0:
        print(f"Verdict: {timeboxed_verdict('KEEP-GOING', elapsed_days, args.max_days)} (no resolved trades yet)")
        return 0
    risked = sum(c for c, _ in trades)
    pnl = sum(p for _, p in trades)
    wins = sum(1 for _, p in trades if p > 0)
    point = pnl / risked if risked else 0.0
    lo, hi = bootstrap_profit_rate_ci(trades)
    print(f"Win rate: {wins/n*100:.1f}% | Net PnL: ${pnl:+.2f} on ${risked:.2f} risked")
    print(f"Profit rate: {point*100:+.2f}%  (95% bootstrap CI {lo*100:+.2f}% .. {hi*100:+.2f}%)")
    print(f"Verdict: {timeboxed_verdict(verdict(n, lo, hi, point, args.min_trades), elapsed_days, args.max_days)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
