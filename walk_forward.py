"""
Walk-forward validation on real Polymarket history (council rec #4).

Splits the real windows chronologically (default 70% train / 30% test) and reports,
per split, the profit rate after real fees with a bootstrap 95% CI:

  1. FROZEN: today's live BTC params, no fitting -- the honest out-of-sample number.
  2. FIT-ON-TRAIN: pick the best params from the sweep grid using ONLY the train
     split, then score them on the held-out test split. If this beats FROZEN on test
     only by chance, that shows up as a wide CI / negative test result.

Unlike replay_real_market.py, spot and market price are time-aligned (no look-ahead;
see build_inputs). Same other caveats as replay_real_market.py: CBI/OFI drift inputs are 0.0 (no historical
order-flow feed), yes_bid is assumed ask-0.02, one $1 stake per fired signal.

KNOWN LIMITATION (2026-09-23): this dataset has no historical order-book depth, only one ask price per window
(yes_price_tau120). The live bot's BLOCKED_PHANTOM / BLOCKED_HURDLE / BLOCKED_LATE_WINDOW rejection paths
(src/bot.py ~L1099-1174) all depend on real-time book depth and spread this replay cannot see, so it cannot
reproduce situations where the live bot correctly refuses to trade. A run on 2026-09-23 (BTC, FROZEN params)
showed 67-72% win rate / +29-35% profit rate out-of-sample even after applying the strategy's slippage_buffer
to the fill price (run(..., slip=True)) -- the slippage fix only moved the number from +34.54% to +29.23%,
confirming the gap is structural (missing depth/rejection modelling), not primarily a fill-price optimism
artifact. The live frozen paper bot, same params, real constraints: 9 trades, near breakeven, trailing
buy-and-hold. Do not treat this script's PASS-looking numbers as evidence of live-tradeable edge; the live
paper bot's own trade count is the only trustworthy signal for this strategy. Mirrors the exact failure mode
documented in docs/case_study_no_edge.md (a backtest that looked good because it was missing a real-world
friction the live system has).

Usage: python walk_forward.py [--asset BTC] [--train-frac 0.7] [--min-trades 30]
"""
import argparse
import random
import pandas as pd
from replay_real_market import load_real_windows, load_binance_spot
from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy, estimate_taker_fee_fraction

# Mirrors btc_params in src/bot.py (scripts/model_freeze.py holds the same dict).
FROZEN_BTC = dict(min_edge=0.03, slippage_buffer=0.02, cbi_drift_weight=1.0, min_abs_z=0.55,
                  min_strike_distance_pct=0.0003, tail_dof=None, min_entry_price=0.25, max_entry_price=0.55)


def path_for(asset: str):
    a = asset.lower()
    return f"data/real_market_history_{a}_extended.jsonl", f"data/cache/{asset.upper()}USDT_1m_1095d.csv"


def build_inputs(real: pd.DataFrame, spot: pd.DataFrame):
    """Precompute per-window model inputs once so the grid doesn't redo the spot joins."""
    spot_groups = {wts: g.sort_values("minute_idx").reset_index(drop=True) for wts, g in spot.groupby("window_ts")}
    rows = []
    for r in real.itertuples():
        g = spot_groups.get(r.window_ts)
        if g is None or len(g) != 5:
            continue
        # yes_price_tau120 is the market price at 120s ELAPSED (fetch_real_market_history.py:
        # target_ts = window_ts + 120), so the spot snapshot must also be from 120s elapsed:
        # minute idx 1's close. replay_real_market.py/sweep_real_market.py use minute idx 2
        # (180s elapsed) against that same price -- 60s of look-ahead that inflates their
        # win rate to ~82%, versus 40-46% in live paper trading.
        mid, vol = g.loc[1, "close"], g.loc[1, "vol_ann"]
        if pd.isna(vol) or vol <= 0:
            continue
        rows.append({
            "window_ts": r.window_ts, "strike": g.loc[0, "open"], "mid": mid, "vol": float(vol),
            "mom": max(min(((mid - g.loc[0, "open"]) / mid) / 0.006, 1.0), -1.0),
            "yes_ask": r.yes_ask_real, "realized_up": r.realized_up,
        })
    return sorted(rows, key=lambda x: x["window_ts"])


def run(inputs, slip=False, **params):
    """slip=True applies the strategy's own slippage_buffer to the FILL price, not just the
    admission hurdle. Without this, market_price is the raw historical top-of-book ask with an
    implied zero-latency, zero-slippage, guaranteed fill -- unlike the live bot, which re-fetches
    the book after a simulated latency and walks the ask ladder for a real VWAP (src/bot.py
    ~L1099-1112). This dataset has no historical order-book depth to replicate that walk exactly,
    so slip=True is an approximation: it charges the same slippage_buffer the strategy already
    uses to decide whether to trade, against the price actually paid, instead of letting that
    buffer sit unused after the admission check."""
    strat = ClaudQuantBinaryOptionStrategy(**params)
    pnls = []
    for w in inputs:
        no_ask = 1.0 - w["yes_ask"]
        sig = strat.evaluate(
            spot_price=w["mid"], momentum_normalized=w["mom"],
            market_info={"strike_price": w["strike"], "time_remaining_sec": 180.0, "annualized_vol": w["vol"],
                         "ofi_normalized": 0.0, "known_avg_price": None, "twap_window_sec": 60.0,
                         "regime_factor": 1.0, "yes_ask": w["yes_ask"], "yes_bid": max(w["yes_ask"] - 0.02, 0.01),
                         "no_ask": no_ask},
            order_book={"cbi": 0.0}, confidence_weight=1.0)
        if sig is None:
            continue
        p = sig["market_price"]
        if slip:
            p = min(p + strat.slippage_buffer, 0.99)
        won = (sig["outcome"] == "YES") == (w["realized_up"] == 1)
        pnls.append((1.0 / p if won else 0.0) - 1.0 - estimate_taker_fee_fraction(p))
    return pnls


def summarize(pnls, n_boot=5000, seed=42):
    n = len(pnls)
    if n == 0:
        return {"n": 0, "win": None, "rate": None, "lo": None, "hi": None}
    rng = random.Random(seed)
    rates = sorted(sum(pnls[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return {"n": n, "win": sum(p > 0 for p in pnls) / n, "rate": sum(pnls) / n,
            "lo": rates[int(0.025 * n_boot)], "hi": rates[int(0.975 * n_boot)]}


def fmt(label, s):
    if not s["n"]:
        return f"{label:<26} n=0 (no trades fired)"
    return (f"{label:<26} n={s['n']:>5}  win={s['win']*100:5.1f}%  profit rate={s['rate']*100:+6.2f}% "
            f"(95% CI {s['lo']*100:+6.2f}% .. {s['hi']*100:+6.2f}%)")


def grid():
    for min_edge in (0.02, 0.03, 0.05):
        for z in (0.25, 0.40, 0.55, 0.70):
            for lo, hi in ((0.15, 0.55), (0.25, 0.55), (0.333, 0.50), (0.30, 0.60)):
                yield dict(min_edge=min_edge, slippage_buffer=0.02, cbi_drift_weight=1.0, min_abs_z=z,
                           min_strike_distance_pct=0.0003, tail_dof=None, min_entry_price=lo, max_entry_price=hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=30, help="min train trades for a grid combo to be eligible")
    args = ap.parse_args()
    asset = args.asset.upper()

    real_path, spot_path = path_for(asset)
    inputs = build_inputs(load_real_windows(real_path), load_binance_spot(spot_path))
    cut = int(len(inputs) * args.train_frac)
    train, test = inputs[:cut], inputs[cut:]
    print(f"[{asset}] {len(inputs)} matched windows | train {len(train)} (to ts {train[-1]['window_ts']}) | test {len(test)} (from ts {test[0]['window_ts']})")
    print("NOTE: CBI/OFI inputs = 0.0 in replay (no historical order-flow feed). One $1 stake per signal, real fees.\n")

    print("== FROZEN live params (no fitting), no slippage on fill (optimistic, matches earlier run) ==")
    print(fmt("train", summarize(run(train, slip=False, **FROZEN_BTC))))
    print(fmt("TEST (out-of-sample)", summarize(run(test, slip=False, **FROZEN_BTC))))

    print("\n== FROZEN live params, slippage_buffer charged on fill (approximates live's re-fetch+VWAP-walk gap) ==")
    print(fmt("train", summarize(run(train, slip=True, **FROZEN_BTC))))
    print(fmt("TEST (out-of-sample)", summarize(run(test, slip=True, **FROZEN_BTC))))

    print("\n== FIT ON TRAIN, SCORE ON TEST ==")
    best, best_rate = None, None
    for params in grid():
        s = summarize(run(train, **params), n_boot=1)  # CI unused for selection
        if s["n"] >= args.min_trades and (best_rate is None or s["rate"] > best_rate):
            best, best_rate = params, s["rate"]
    if best is None:
        print("No grid combo reached the min-trades bar on train.")
        return
    print("chosen on train:", {k: best[k] for k in ("min_edge", "min_abs_z", "min_entry_price", "max_entry_price")})
    print(fmt("train (in-sample, biased)", summarize(run(train, **best))))
    print(fmt("TEST (out-of-sample)", summarize(run(test, **best))))
    print("\nRead: a real edge shows a TEST CI lower bound > 0. Test CI spanning 0 = no proven edge.")


if __name__ == "__main__":
    main()
