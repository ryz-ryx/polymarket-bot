"""
Random-side baseline for the frozen forward test (FREEZE.md pass rule #4).

For each settled BTC trade after the freeze, keep the bot's window, entry price and
stake, but flip a fair coin for the side. If the coin picks the bot's side the trade
resolves exactly as the bot's did; otherwise it takes the opposite side, priced at
1 - entry (spread ignored) and resolved the other way. Fees are charged on every BUY
with the same taker-fee schedule as the bot. Repeats N_SIMS times and reports where
the bot's real net PnL per $ staked falls in that distribution, plus a bootstrap 95%
CI of the bot's own mean.

Usage: python scripts/random_baseline.py [--url URL | --fills PATH] [--since EPOCH]
  --url    live bot base URL (default https://polymarketbot.up.railway.app), reads /api/fills
  --fills  local fills_log.jsonl, or a saved /api/fills JSON response
  --since  epoch seconds; default = commit time of FREEZE.md
"""
import argparse
import json
import os
import random
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.strategies.claud_quant import estimate_taker_fee_fraction  # noqa: E402

N_SIMS = 10_000
DEFAULT_URL = "https://polymarketbot.up.railway.app"


def load_fills(url: str, path: str):
    if path:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        try:
            return json.loads(text)["fills"]  # saved /api/fills response
        except (json.JSONDecodeError, KeyError, TypeError):
            return [json.loads(line) for line in text.splitlines() if line.strip()]  # raw jsonl
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/fills?limit=100000", timeout=30) as r:
        return json.load(r)["fills"]


def freeze_ts() -> float:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Commit that ADDED the file, so later bug-fix-log edits don't move the freeze start.
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ct", "--", "FREEZE.md"],
                         capture_output=True, text=True, cwd=root).stdout.strip().splitlines()
    out = out[-1] if out else ""
    if not out:
        sys.exit("FREEZE.md has no commit yet; pass --since")
    return float(out)


def paired_trades(fills, since_ts: float, asset: str = "BTC"):
    """(entry_price, stake, bot_won) per settled trade whose BUY and result both post-date the freeze."""
    buys = {f["window_id"]: f for f in fills if f.get("asset") == asset and f.get("type") == "BUY"}
    trades = []
    for f in fills:
        if f.get("asset") != asset or f.get("type") not in ("WIN", "LOSS") or f["ts"] < since_ts:
            continue
        b = buys.get(f["window_id"])
        if b is None or b["ts"] < since_ts:
            continue
        trades.append((float(b["price"]), float(b["size_usd"]), f["type"] == "WIN"))
    return trades


def net_pnl(price: float, stake: float, won: bool) -> float:
    """payout - stake - taker fee, matching how the balance actually moves."""
    payout = stake / price if won else 0.0
    return payout - stake - stake * estimate_taker_fee_fraction(price)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--fills", default=None)
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    since = args.since if args.since is not None else freeze_ts()
    trades = paired_trades(load_fills(args.url, args.fills), since)
    n = len(trades)
    if n == 0:
        sys.exit("no settled BTC trades after the freeze yet")

    staked = sum(s for _, s, _ in trades)
    bot_pnl = sum(net_pnl(p, s, w) for p, s, w in trades)
    bot_rate = bot_pnl / staked

    rng = random.Random(args.seed)
    base = []
    for _ in range(N_SIMS):
        total = 0.0
        for p, s, w in trades:
            if rng.random() < 0.5:
                total += net_pnl(p, s, w)
            else:
                total += net_pnl(1.0 - p, s, not w)
        base.append(total / staked)
    base.sort()
    beat = sum(1 for x in base if x < bot_rate) / N_SIMS

    per = [(net_pnl(p, s, w), s) for p, s, w in trades]
    boot = []
    for _ in range(N_SIMS):
        pick = [per[rng.randrange(n)] for _ in range(n)]
        boot.append(sum(x for x, _ in pick) / sum(s for _, s in pick))
    boot.sort()
    lo, hi = boot[int(0.025 * N_SIMS)], boot[int(0.975 * N_SIMS)]

    print(f"trades after freeze: {n}   staked: ${staked:.2f}   (need >= 200)")
    print(f"bot     net PnL: ${bot_pnl:+.2f}  ({bot_rate:+.1%} of staked)  95% CI [{lo:+.1%}, {hi:+.1%}]")
    print(f"random  net PnL/staked: median {base[N_SIMS // 2]:+.1%}  "
          f"5th-95th [{base[int(0.05 * N_SIMS)]:+.1%}, {base[int(0.95 * N_SIMS)]:+.1%}]")
    print(f"bot beats {beat:.1%} of random-side sims")
    checks = {
        "n >= 200": n >= 200,
        "mean net > 0": bot_pnl > 0,
        "CI lower bound > 0": lo > 0,
        "beats random (>= 95%)": beat >= 0.95,
    }
    for name, ok in checks.items():
        print(f"  [{'x' if ok else ' '}] {name}")
    print("VERDICT:", "PASS" if all(checks.values()) else ("KEEP-GOING" if n < 200 else "FAIL"))


if __name__ == "__main__":
    main()
