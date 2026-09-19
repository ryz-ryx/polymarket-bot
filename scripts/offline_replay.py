"""
Offline what-if replay on settled BTC trades (analysis only, touches no bot code).

Question: was fee drag the whole gap, or is gross PnL negative on its own?
Scenarios, all keeping the bot's windows, sides, stakes and outcomes:
  actual        payout - cost - BUY taker fee          (matches the frozen-test net PnL)
  no-fee        payout - cost                          (upper bound for any fee removal)
  maker+Nc      price improved by N cents, no fee      (upper bound: assumes EVERY order fills;
                                                         real maker fills are adverse-selected)
If even the optimistic scenarios do not clear zero with a CI above 0, fee tricks cannot
rescue the signal.

Usage: python scripts/offline_replay.py [--fills PATH | --url URL] [--since EPOCH|all]
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.random_baseline import DEFAULT_URL, freeze_ts, load_fills, paired_trades  # noqa: E402
from src.strategies.claud_quant import estimate_taker_fee_fraction  # noqa: E402

N_BOOT = 10_000


def pnl(price: float, stake: float, won: bool, fee: bool) -> float:
    payout = stake / price if won else 0.0
    return payout - stake - (stake * estimate_taker_fee_fraction(price) if fee else 0.0)


def summarize(name: str, per, rng: random.Random) -> None:
    n = len(per)
    staked = sum(s for _, s in per)
    total = sum(x for x, _ in per)
    boot = []
    for _ in range(N_BOOT):
        pick = [per[rng.randrange(n)] for _ in range(n)]
        boot.append(sum(x for x, _ in pick) / sum(s for _, s in pick))
    boot.sort()
    lo, hi = boot[int(0.025 * N_BOOT)], boot[int(0.975 * N_BOOT)]
    print(f"{name:<12} net ${total:+8.2f}  {total / staked:+7.1%} of staked  95% CI [{lo:+.1%}, {hi:+.1%}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--fills", default=None)
    ap.add_argument("--since", default=None, help="epoch seconds, or 'all'; default = freeze commit time")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    since = 0.0 if args.since == "all" else (float(args.since) if args.since else freeze_ts())
    trades = paired_trades(load_fills(args.url, args.fills), since)
    if not trades:
        sys.exit("no settled BTC trades in range")

    rng = random.Random(args.seed)
    wins = sum(1 for _, _, w in trades if w)
    avg_entry = sum(p for p, _, _ in trades) / len(trades)
    print(f"trades: {len(trades)}   win rate: {wins / len(trades):.1%}   avg entry: {avg_entry:.3f}")
    summarize("actual", [(pnl(p, s, w, True), s) for p, s, w in trades], rng)
    summarize("no-fee", [(pnl(p, s, w, False), s) for p, s, w in trades], rng)
    for cents in (1, 2):
        d = cents / 100
        summarize(f"maker+{cents}c", [(pnl(max(p - d, 0.01), s, w, False), s) for p, s, w in trades], rng)


if __name__ == "__main__":
    main()
