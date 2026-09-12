"""
Monte Carlo bankroll simulation: bootstrap-resamples REAL historical trades
(p_model, entry_price, win/loss outcome) from this bot's own paper-trading
history, then replays them through the actual production RiskManager sizing
logic to project many possible future bankroll trajectories starting from
the current $20 balance.

This is deliberately NOT a synthetic win-rate/payout assumption -- every
sampled trade is a real (win_probability, entry_price, result) triple this
strategy actually produced, joined from data/trade_events.csv (EXECUTED
signals) and data/fills_log.jsonl (resolved WIN/LOSS outcomes).

Caveat: only 64 resolved trades exist to bootstrap from. That's a real
sample, not invented, but small enough that these percentages have wide
uncertainty bands of their own -- treat this as a rough sense of the
distribution of outcomes, not a precise forecast.
"""
import csv
import json
import random
import argparse
from typing import List, Dict, Any

from loguru import logger
from config import config
from src.risk_manager import RiskManager

logger.remove()  # silence RiskManager's per-trade info logging -- this runs thousands of times per path


def load_historical_trades() -> List[Dict[str, Any]]:
    fills_by_window = {}
    with open("data/fills_log.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") in ("WIN", "LOSS"):
                fills_by_window[d["window_id"]] = d

    trades = []
    with open("data/trade_events.csv", "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] != "EXECUTED":
                continue
            wid = int(row["window_id"])
            fill = fills_by_window.get(wid)
            if not fill:
                continue
            cost = float(fill["cost"])
            if cost <= 0:
                continue
            trades.append({
                "p_model": float(row["p_model"]),
                "entry_price": float(row["direct_ask"]),
                "return_multiple": float(fill["net_pnl"]) / cost,  # pnl per $1 staked
            })
    return trades


def simulate_path(trades: List[Dict[str, Any]], risk_manager: RiskManager, starting_bankroll: float,
                   target_bankroll: float, max_trades: int, ruin_floor: float) -> Dict[str, Any]:
    bankroll = starting_bankroll
    trajectory = [bankroll]
    for i in range(max_trades):
        if bankroll >= target_bankroll:
            return {"outcome": "TARGET_HIT", "trades_taken": i, "final_bankroll": bankroll, "trajectory": trajectory}
        if bankroll < ruin_floor:
            return {"outcome": "RUINED", "trades_taken": i, "final_bankroll": bankroll, "trajectory": trajectory}

        t = random.choice(trades)
        odds = 1.0 / max(t["entry_price"], 0.05)
        size = risk_manager.calculate_position_size(
            win_probability=t["p_model"],
            odds=odds,
            bankroll=bankroll,
            confidence_weight=1.0
        )
        if size <= 0:
            trajectory.append(bankroll)
            continue

        bankroll += size * t["return_multiple"]
        bankroll = max(bankroll, 0.0)
        trajectory.append(bankroll)

    return {"outcome": "MAX_TRADES", "trades_taken": max_trades, "final_bankroll": bankroll, "trajectory": trajectory}


def percentile(values: List[float], p: float) -> float:
    s = sorted(values)
    idx = int(round(p * (len(s) - 1)))
    return s[idx]


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo bankroll simulation ($20 -> $100 goal)")
    parser.add_argument("--paths", type=int, default=5000, help="Number of simulated bankroll paths")
    parser.add_argument("--max-trades", type=int, default=500, help="Max trades per simulated path before giving up")
    parser.add_argument("--start", type=float, default=20.0, help="Starting bankroll")
    parser.add_argument("--target", type=float, default=100.0, help="Target bankroll")
    parser.add_argument("--ruin-floor", type=float, default=1.0, help="Bankroll below which a path is 'ruined'")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    trades = load_historical_trades()
    if len(trades) < 10:
        raise SystemExit(f"Only {len(trades)} resolved historical trades found -- not enough to bootstrap from.")

    wins = sum(1 for t in trades if t["return_multiple"] > 0)
    print(f"Bootstrapping from {len(trades)} real historical trades (win rate {wins/len(trades)*100:.1f}%)")
    print(f"Live production config: kelly_fraction={config.kelly_fraction}, max_position_usd={config.max_position_usd}")
    print(f"Simulating {args.paths} paths, up to {args.max_trades} trades each, ${args.start} -> ${args.target} target\n")

    risk_manager = RiskManager(
        max_position_usd=config.max_position_usd,
        max_daily_loss_usd=1e12,  # daily circuit breaker disabled for this simulation -- irrelevant at $20 scale, would just add noise from an artificial daily reset boundary
        kelly_fraction=config.kelly_fraction,
        state_file=None
    )

    results = []
    for _ in range(args.paths):
        risk_manager.daily_pnl = 0.0
        risk_manager.circuit_breaker_triggered = False
        results.append(simulate_path(trades, risk_manager, args.start, args.target, args.max_trades, args.ruin_floor))

    hit_target = [r for r in results if r["outcome"] == "TARGET_HIT"]
    ruined = [r for r in results if r["outcome"] == "RUINED"]
    ran_out = [r for r in results if r["outcome"] == "MAX_TRADES"]

    final_bankrolls = [r["final_bankroll"] for r in results]

    print("=" * 55)
    print("MONTE CARLO RESULTS")
    print("=" * 55)
    print(f"Reached ${args.target:.0f}+:        {len(hit_target)}/{args.paths} ({len(hit_target)/args.paths*100:.1f}%)")
    print(f"Ruined (< ${args.ruin_floor:.0f}):        {len(ruined)}/{args.paths} ({len(ruined)/args.paths*100:.1f}%)")
    print(f"Neither (hit trade cap): {len(ran_out)}/{args.paths} ({len(ran_out)/args.paths*100:.1f}%)")
    print()
    if hit_target:
        trades_to_target = [r["trades_taken"] for r in hit_target]
        print(f"Among successes, trades needed to hit target:")
        print(f"  Median: {percentile(trades_to_target, 0.50):.0f}")
        print(f"  10th percentile (fast):  {percentile(trades_to_target, 0.10):.0f}")
        print(f"  90th percentile (slow):  {percentile(trades_to_target, 0.90):.0f}")
        print()

    print(f"Final bankroll distribution across ALL {args.paths} paths:")
    print(f"  10th percentile: ${percentile(final_bankrolls, 0.10):.2f}")
    print(f"  25th percentile: ${percentile(final_bankrolls, 0.25):.2f}")
    print(f"  Median:          ${percentile(final_bankrolls, 0.50):.2f}")
    print(f"  75th percentile: ${percentile(final_bankrolls, 0.75):.2f}")
    print(f"  90th percentile: ${percentile(final_bankrolls, 0.90):.2f}")
    print("=" * 55)


if __name__ == "__main__":
    main()
