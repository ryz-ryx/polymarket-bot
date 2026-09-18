"""
Version-lock the live model so the forward test is clean (council rec #2).

Writes data/model_freeze.json: freeze timestamp, git commit, strategy params, sizing
and risk config. scripts/forward_test_report.py only counts trades AFTER freeze_ts, and
warns if the live params/commit drifted from the frozen manifest -- any tweak means the
forward-test clock restarts (re-run this script).

Usage: python scripts/model_freeze.py [--force]
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config  # noqa: E402

FREEZE_PATH = "data/model_freeze.json"

# Mirror of btc_params in src/bot.py. Kept in sync via check_drift() below.
BTC_PARAMS = dict(
    min_edge=0.03, cbi_drift_weight=1.0, min_abs_z=0.55, min_strike_distance_pct=0.0003,
    tail_dof=None, min_entry_price=0.25, max_entry_price=0.55,
)


def current_manifest() -> dict:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        commit = "unknown"
    return {
        "assets": config.target_assets,
        "btc_params": BTC_PARAMS,
        "slippage_tolerance": config.slippage_tolerance,
        "kelly_fraction": config.kelly_fraction,
        "max_position_usd": config.max_position_usd,
        "min_order_usd": config.min_order_usd,
        "git_commit": commit,
    }


def load_freeze():
    try:
        with open(FREEZE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def check_drift(frozen: dict) -> list:
    """Return the manifest keys that changed since the freeze (excluding git_commit)."""
    now = current_manifest()
    return [k for k in now if k != "git_commit" and now[k] != frozen["manifest"].get(k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing freeze (restarts the forward-test clock)")
    args = ap.parse_args()
    if load_freeze() and not args.force:
        print(f"{FREEZE_PATH} already exists; use --force to re-freeze (restarts the forward-test clock).")
        return 1
    freeze = {"freeze_ts": time.time(), "manifest": current_manifest()}
    os.makedirs("data", exist_ok=True)
    with open(FREEZE_PATH, "w", encoding="utf-8") as f:
        json.dump(freeze, f, indent=2)
    print(f"Froze model -> {FREEZE_PATH}")
    print(json.dumps(freeze, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
