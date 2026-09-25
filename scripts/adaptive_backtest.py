"""Test A: rolling parameter self-adaptation vs frozen baseline. See docs/PREREG_adaptive.md.

Adaptation rule is fully mechanical (fixed in the PREREG doc before this script touched any data):
every WINDOW resolved trades, recompute min_edge and min_abs_z from ONLY the trailing WINDOW trades'
win rate vs the breakeven win rate implied by their mean entry price. No other parameter changes.

Default = train split only (sanity-check for degenerate drift; no PASS/KILL claim). --holdout runs the
sealed test split once (lock file), per the doc's PASS/KILL criteria.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from walk_forward import FROZEN_BTC, build_inputs, path_for, summarize, fmt
from replay_real_market import load_real_windows, load_binance_spot
from src.strategies.claud_quant import ClaudQuantBinaryOptionStrategy, estimate_taker_fee_fraction

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "data" / "A_RAN.lock"
WINDOW = 20
MIN_EDGE_STEP, MIN_EDGE_LO, MIN_EDGE_HI = 0.005, 0.02, 0.05
Z_STEP, Z_LO, Z_HI = 0.05, 0.25, 0.90


def run_adaptive(inputs, slip=True):
    params = dict(FROZEN_BTC)
    pnls = []
    trail_pnls, trail_prices = [], []
    max_consec_at_bound, consec_at_bound = 0, 0
    param_history = []

    for w in inputs:
        strat = ClaudQuantBinaryOptionStrategy(**params)
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
        pnl = (1.0 / p if won else 0.0) - 1.0 - estimate_taker_fee_fraction(p)
        pnls.append(pnl)
        trail_pnls.append(pnl)
        trail_prices.append(p)

        if len(trail_pnls) == WINDOW:
            win_rate = sum(1 for x in trail_pnls if x > 0) / WINDOW
            breakeven = sum(trail_prices) / WINDOW
            if win_rate < breakeven - 0.03:
                params["min_edge"] = round(min(params["min_edge"] + MIN_EDGE_STEP, MIN_EDGE_HI), 4)
                params["min_abs_z"] = round(min(params["min_abs_z"] + Z_STEP, Z_HI), 4)
            elif win_rate > breakeven + 0.05:
                params["min_edge"] = round(max(params["min_edge"] - MIN_EDGE_STEP, MIN_EDGE_LO), 4)
                params["min_abs_z"] = round(max(params["min_abs_z"] - Z_STEP, Z_LO), 4)
            at_bound = params["min_edge"] in (MIN_EDGE_LO, MIN_EDGE_HI) and params["min_abs_z"] in (Z_LO, Z_HI)
            consec_at_bound = consec_at_bound + 1 if at_bound else 0
            max_consec_at_bound = max(max_consec_at_bound, consec_at_bound)
            param_history.append(dict(params))
            trail_pnls, trail_prices = [], []

    return pnls, max_consec_at_bound, param_history


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()

    real_path, spot_path = path_for(a.asset.upper())
    inputs = build_inputs(load_real_windows(real_path), load_binance_spot(spot_path))
    cut = int(len(inputs) * a.train_frac)
    train, test = inputs[:cut], inputs[cut:]

    from walk_forward import run as run_frozen

    if not a.holdout:
        print(f"[{a.asset}] DEV (train) split, n_windows={len(train)}")
        pnls, max_consec, hist = run_adaptive(train, slip=True)
        print(fmt("Adaptive (train)", summarize(pnls)))
        print(fmt("Frozen baseline (train)", summarize(run_frozen(train, slip=True, **FROZEN_BTC))))
        print(f"\nMax consecutive adaptation windows with both params at bound: {max_consec} "
              f"({'DEGENERATE -- do not proceed to holdout' if max_consec > 3 else 'OK, not degenerate'})")
        print(f"Adaptation windows: {len(hist)}. Final params: {hist[-1] if hist else FROZEN_BTC}")
        return 0

    if LOCK.exists():
        print("REFUSED: holdout already run", file=sys.stderr)
        return 2
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text("ran")

    print(f"[{a.asset}] HOLDOUT (test) split, n_windows={len(test)}")
    adaptive_pnls, max_consec, hist = run_adaptive(test, slip=True)
    frozen_pnls = run_frozen(test, slip=True, **FROZEN_BTC)
    adaptive_s = summarize(adaptive_pnls)
    frozen_s = summarize(frozen_pnls)
    print(fmt("Adaptive (HOLDOUT)", adaptive_s))
    print(fmt("Frozen baseline (HOLDOUT)", frozen_s))
    print(f"Max consecutive at-bound windows: {max_consec}")

    degenerate = max_consec > 3
    beats_zero = adaptive_s["n"] > 0 and adaptive_s["lo"] > 0
    beats_frozen = adaptive_s["n"] > 0 and frozen_s["n"] > 0 and adaptive_s["lo"] > frozen_s["rate"]
    verdict = "PASS" if (beats_zero and beats_frozen and not degenerate) else "KILL"
    print(f"\nVERDICT: {verdict}  (CI>0: {beats_zero}, beats frozen point estimate: {beats_frozen}, "
          f"degenerate: {degenerate})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
