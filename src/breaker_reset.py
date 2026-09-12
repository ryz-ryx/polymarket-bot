"""
Shared "please clear the daily circuit breaker" request, read by RiskManager
(src/risk_manager.py) and written by the dashboard's POST
/api/reset_daily_breaker endpoint (dashboard.py). Same file-backed +
in-memory-cache pattern as src/control_state.py, so it works whether the
bot and dashboard run in the same process (railway_entrypoint.py) or as
separate processes (local run.py + dashboard.py).

Deliberately only clears circuit_breaker_triggered, never daily_pnl -- if
the underlying daily loss is still past max_daily_loss_usd, RiskManager's
next can_trade() check re-trips it immediately. This makes a reset request
against a genuinely bad day a safe no-op instead of something that could
mask real losses.
"""
import json
import os
import time

REQUEST_FILE = os.path.join("data", "breaker_reset_request.json")
_CACHE_TTL_SEC = 3.0

_cache = {"requested_at": 0.0, "updated_by": None}
_cache_ts = 0.0


def read_reset_request(force: bool = False) -> dict:
    global _cache, _cache_ts
    now = time.time()
    if not force and (now - _cache_ts) < _CACHE_TTL_SEC:
        return _cache
    if os.path.exists(REQUEST_FILE):
        try:
            with open(REQUEST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = {
                "requested_at": float(data.get("requested_at", 0.0)),
                "updated_by": data.get("updated_by"),
            }
        except Exception:
            pass
    _cache_ts = now
    return _cache


def request_reset(updated_by: str = "dashboard") -> dict:
    os.makedirs(os.path.dirname(REQUEST_FILE), exist_ok=True)
    payload = {"requested_at": time.time(), "updated_by": updated_by}
    tmp = f"{REQUEST_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, REQUEST_FILE)
    global _cache, _cache_ts
    _cache = dict(payload)
    _cache_ts = time.time()
    return payload
