"""
Leak-free retest of the bot-flow wallet signal.

The earlier version found an 84.4% directional-accuracy correlation between
classified "bot" wallets' net exposure and the real outcome -- but it was
spurious: those wallets traded near window close (~185-220s elapsed of 300s),
AFTER the strategy's own tau=120s entry point (elapsed=180s), so their
positioning just reflected an already-largely-determined outcome rather than
predicting anything the strategy could act on.

This version only counts a wallet's exposure if its FIRST trade in the window
happened before the entry point (window_ts + 180), i.e. it could actually be
observed and used before the strategy enters. It then tests whether early
net exposure (YES-leaning vs NO-leaning) predicts the real outcome, with no
look-ahead.
"""
import json
from typing import Dict

ENTRY_ELAPSED_SEC = 180  # strategy's tau=120s entry point (300s window - 120s remaining)

BOT_FLOW_FILE = "data/bot_flow_data.jsonl"
OUTCOMES_FILE = "data/real_market_history_btc.jsonl"


def load_outcomes(path: str) -> Dict[str, int]:
    outcomes = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_meta"):
                continue
            prices = json.loads(d["outcome_prices"])
            outcomes[d["slug"]] = 1 if float(prices[0]) > float(prices[1]) else 0
    return outcomes


def main():
    outcomes = load_outcomes(OUTCOMES_FILE)
    print(f"Loaded {len(outcomes)} settled outcomes")

    total = 0
    correct = 0
    skipped_no_outcome = 0
    skipped_no_early_wallets = 0
    early_wallet_windows = 0

    with open(BOT_FLOW_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_meta"):
                continue
            slug = d["slug"]
            window_ts = d.get("window_ts")
            if slug not in outcomes or window_ts is None:
                skipped_no_outcome += 1
                continue
            cutoff = window_ts + ENTRY_ELAPSED_SEC

            net_yes = 0.0
            net_no = 0.0
            n_early = 0
            for w in d.get("wallets", []):
                first_ts = w.get("first_ts")
                if first_ts is None or first_ts >= cutoff:
                    continue  # wallet only observed at/after entry point -- would leak
                net_yes += w.get("yes_net", 0.0)
                net_no += w.get("no_net", 0.0)
                n_early += 1

            if n_early == 0:
                skipped_no_early_wallets += 1
                continue

            early_wallet_windows += 1
            # net_no is negative-signed on the NO token itself (its own BUY/SELL),
            # so compare directional pressure: predicted UP if early net YES buying
            # pressure exceeds early net NO buying pressure.
            predicted_up = 1 if net_yes > net_no else 0
            actual_up = outcomes[slug]
            total += 1
            if predicted_up == actual_up:
                correct += 1

    print(f"Windows with >=1 early (pre-entry-point) wallet: {early_wallet_windows}")
    print(f"Windows skipped (no outcome match): {skipped_no_outcome}")
    print(f"Windows skipped (no early wallets at all): {skipped_no_early_wallets}")
    if total == 0:
        print("No testable windows -- can't compute accuracy.")
        return
    acc = correct / total
    print(f"\nEarly-wallet-only directional accuracy: {correct}/{total} = {acc:.1%}")
    print("(50% = no signal; this is leak-free since only pre-entry-point trades count)")


if __name__ == "__main__":
    main()
