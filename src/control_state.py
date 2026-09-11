"""
Shared pause/resume control state, read by the trading loop (src/bot.py) and
written by the dashboard's POST /api/control endpoint (dashboard.py).

File-backed (data/control.json) so it works whether the two run in the same
process (railway_entrypoint.py, sharing this module's in-memory cache
directly) or as separate processes (local run.py + dashboard.py on Windows).
"""
import json
import os
import time

CONTROL_FILE = os.path.join("data", "control.json")
_CACHE_TTL_SEC = 3.0

_cache = {"paused": False, "updated_at": None, "updated_by": None}
_cache_ts = 0.0


def read_control_state(force: bool = False) -> dict:
    global _cache, _cache_ts
    now = time.time()
    if not force and (now - _cache_ts) < _CACHE_TTL_SEC:
        return _cache
    if os.path.exists(CONTROL_FILE):
        try:
            with open(CONTROL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = {
                "paused": bool(data.get("paused", False)),
                "updated_at": data.get("updated_at"),
                "updated_by": data.get("updated_by"),
            }
        except Exception:
            pass
    _cache_ts = now
    return _cache


def is_paused() -> bool:
    return bool(read_control_state().get("paused", False))


def write_control_state(paused: bool, updated_by: str = "dashboard") -> dict:
    global _cache, _cache_ts
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    payload = {
        "paused": bool(paused),
        "updated_at": time.time(),
        "updated_by": updated_by,
    }
    tmp = f"{CONTROL_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, CONTROL_FILE)
    _cache = dict(payload)
    _cache_ts = time.time()
    return payload
