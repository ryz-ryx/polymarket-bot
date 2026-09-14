"""
Runs on Railway (needs data/calibration_log_live.csv). Recomputes
get_confidence_weight()-equivalent Brier comparison at several points back in
time (using only rows available up to each point) so we can see whether the
post-fix confidence weight is trending up and holding, or was a one-off spike
from a favorable batch of windows.

Usage (via `railway ssh --service polymarket-bot -- ...`):
  python data/check_confidence_trend.py --log data/calibration_log_live.csv
"""
import argparse
import csv
from typing import Any, Dict, List


def load_window_groups(log_path: str) -> Dict[str, List[Dict[str, Any]]]:
    window_groups: Dict[str, List[Dict[str, Any]]] = {}
    with open(log_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("realized_up") in ("0", "1"):
                wid = row.get("window_id")
                window_groups.setdefault(wid, []).append(row)
    return window_groups


def confidence_weight_at(window_items, min_windows: int = 30, rolling_window: int = 100, k: float = 8.0):
    if len(window_items) < min_windows:
        return None
    recent = window_items[:rolling_window]
    model_sq_err, market_sq_err = [], []
    for _, rows in recent:
        best = min(rows, key=lambda r: abs(float(r["tau_sec"]) - 150.0))
        y = int(best["realized_up"])
        p_model = float(best["p_model"])
        model_sq_err.append((p_model - y) ** 2)
        try:
            market_sq_err.append((float(best["p_market"]) - y) ** 2)
        except (ValueError, KeyError, TypeError):
            pass
    if not model_sq_err or not market_sq_err:
        return None
    model_brier = sum(model_sq_err) / len(model_sq_err)
    market_brier = sum(market_sq_err) / len(market_sq_err)
    weight = 0.5 + (market_brier - model_brier) * k
    return max(0.0, min(1.0, weight)), model_brier, market_brier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="data/calibration_log_live.csv")
    ap.add_argument("--points", type=int, default=10, help="how many trend checkpoints to print")
    args = ap.parse_args()

    window_groups = load_window_groups(args.log)
    window_items = []
    for wid, rows in window_groups.items():
        ts = max(float(r["timestamp"]) for r in rows)
        window_items.append((ts, rows))
    window_items.sort(key=lambda x: x[0])  # oldest -> newest

    n = len(window_items)
    if n < 30:
        print(f"Only {n} distinct settled windows -- need >=30 for a confidence weight.")
        return

    step = max(1, (n - 30) // args.points)
    print(f"{'up_to_idx':>10} {'n_windows':>10} {'weight':>8} {'model_brier':>12} {'market_brier':>13}")
    for end in range(30, n + 1, step):
        result = confidence_weight_at(list(reversed(window_items[:end])))
        if result is None:
            continue
        weight, model_brier, market_brier = result
        print(f"{end:>10} {min(end,100):>10} {weight:>8.3f} {model_brier:>12.4f} {market_brier:>13.4f}")

    # Always show the true latest state too
    result = confidence_weight_at(list(reversed(window_items)))
    if result:
        weight, model_brier, market_brier = result
        print(f"\nLatest (raw, pre-calibration) confidence weight: {weight:.3f} "
              f"(model_brier={model_brier:.4f}, market_brier={market_brier:.4f})")
        print("Note: production uses calibrated p_model in this comparison; this script "
              "uses raw p_model as a simpler proxy for trend-only purposes.")


if __name__ == "__main__":
    main()
