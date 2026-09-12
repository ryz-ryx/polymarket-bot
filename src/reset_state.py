import os
import csv
import json
import time
from loguru import logger

from config import config

DATA_DIR = "data"
MARKER_FILE = os.path.join(DATA_DIR, ".reset_marker")


def reset_all_state_if_requested():
    """
    One-shot full reset of trading history, balance, and logs, gated by the
    RESET_ON_BOOT env var. Runs once per distinct value of RESET_ON_BOOT --
    a marker file records the value already applied, so leaving the var set
    across future restarts/redeploys does NOT repeatedly wipe state. To reset
    again later, set RESET_ON_BOOT to a new value (e.g. a fresh timestamp).

    This exists because manually deleting files through Railway's volume file
    browser is fragile (its Console/terminal doesn't reliably accept scripted
    keystrokes, and some app-managed files don't reliably surface in its file
    listing) -- driving the reset from code that runs with the app's own
    filesystem access is far more reliable than clicking through a remote UI.
    """
    requested = os.environ.get("RESET_ON_BOOT", "").strip()
    if not requested:
        return

    already_applied = None
    if os.path.exists(MARKER_FILE):
        try:
            with open(MARKER_FILE, "r", encoding="utf-8") as f:
                already_applied = f.read().strip()
        except Exception:
            already_applied = None

    if already_applied == requested:
        logger.info(f"reset_state: RESET_ON_BOOT={requested!r} already applied (marker matches) -- skipping.")
        return

    logger.warning(f"reset_state: RESET_ON_BOOT={requested!r} -- performing full state reset now.")
    os.makedirs(DATA_DIR, exist_ok=True)
    now = time.time()

    with open(os.path.join(DATA_DIR, "calibration_log.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "timestamp", "window_id", "tau_sec", "moneyness", "vol_annualized",
            "ofi", "cbi", "z", "p_model", "p_model_shadow", "p_market", "realized_up",
            "spot_lead_lag", "twap_dev", "book_depth_skew"
        ])

    with open(os.path.join(DATA_DIR, "trade_events.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "timestamp", "window_id", "tau_sec", "outcome", "z", "p_model",
            "direct_ask", "direct_spread", "real_edge", "hurdle", "status", "size_usd"
        ])

    with open(os.path.join(DATA_DIR, "arbitrage_scan.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["timestamp", "window_id", "yes_ask", "no_ask", "combined", "gross_edge"])

    open(os.path.join(DATA_DIR, "fills_log.jsonl"), "w", encoding="utf-8").close()

    with open(os.path.join(DATA_DIR, "pending_window_observations.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(os.path.join(DATA_DIR, "pending_resolutions.json"), "w", encoding="utf-8") as f:
        json.dump([], f)

    with open(os.path.join(DATA_DIR, "open_positions.json"), "w", encoding="utf-8") as f:
        json.dump({"positions": [], "simulated_balance": config.starting_balance_usd, "updated_at": now}, f, indent=2)

    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    iso_now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))
    with open(os.path.join(DATA_DIR, "risk_state.json"), "w", encoding="utf-8") as f:
        json.dump({"current_day": today, "daily_pnl": 0.0, "circuit_breaker_triggered": False, "updated_at": iso_now}, f, indent=2)
    with open(os.path.join(DATA_DIR, "risk_state_portfolio.json"), "w", encoding="utf-8") as f:
        json.dump({"current_day": today, "daily_pnl": 0.0, "max_portfolio_daily_loss_usd": config.max_daily_loss_usd, "circuit_breaker_triggered": False, "updated_at": iso_now}, f, indent=2)

    with open(MARKER_FILE, "w", encoding="utf-8") as f:
        f.write(requested)

    logger.warning(f"reset_state: Full reset complete. Balance reset to ${config.starting_balance_usd:.2f}.")
