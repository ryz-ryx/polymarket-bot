# Railway + Hermes — concrete build (still not deployed/activated)

Checked the actual repo structure first: it's already a git repo (`.git` exists — Railway can deploy straight from it), `requirements.txt` already has everything `backtest.py` needs (`requests` included), and `dashboard.py` already exists as a separate, working HTTP server reading `data/` state — that turns out to be the cleanest possible healthcheck host, with zero changes needed to the actual trading process (`run.py`/`bot.py`).

## Railway: concrete files to send Gemini

**`Procfile`** (repo root) — two process types, so the healthcheck doesn't require the trading loop itself to open a port:
```
worker: python run.py
web: python dashboard.py
```

**Add a `/healthz` route to `dashboard.py`** — small, additive, doesn't touch anything trading-related. It checks whether any asset's `trade_events*.csv` has been written to in the last 30s (every tick writes to this file, so staleness = the loop is stuck even if the process is alive):
```python
import time  # add to existing imports

# inside DashboardHandler.do_GET, alongside the existing elif chain:
elif path == "/healthz":
    self.serve_healthz()

# new method on DashboardHandler:
def serve_healthz(self):
    now = time.time()
    freshest = None
    for suffix in ("", "_eth", "_sol"):
        fp = os.path.join(BASE_DIR, "data", f"trade_events{suffix}.csv")
        if os.path.exists(fp):
            mtime = os.path.getmtime(fp)
            freshest = mtime if freshest is None else max(freshest, mtime)
    age = (now - freshest) if freshest else None
    healthy = age is not None and age < 30
    body = json.dumps({"healthy": healthy, "age_sec": age}).encode()
    self.send_response(200 if healthy else 503)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)
```

**`railway.json`** (repo root) — points Railway's healthcheck at the new route:
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "deploy": {
    "healthcheckPath": "/healthz",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5
  }
}
```

**Volume:** mount at `data/` in Railway's dashboard (Settings → Volumes), sized small (a few hundred MB is generous headroom for the CSV/JSON/JSONL growth rate seen so far).

**Env vars:** everything currently in `.env` gets typed into Railway's Variables tab — full list was already given last round, unchanged.

**Dependency check:** `requirements.txt` is already complete — nothing to add for Railway itself. One loose end, unrelated to Railway: `websockets>=12.0` is still listed even though `book_ws.py` moved to `aiohttp.ClientSession.ws_connect` a while back — harmless if unused, but worth Gemini confirming nothing else still imports it before dropping it.

**Still a real decision for you, not a build task:** paid always-on tier (~$5-10/mo) — a `worker` process on Railway's free tier still sleeps on inactivity the same as `web` does.

---

## Hermes — concrete runbook (paste into Hermes once it's running; not a code change to the bot)

Hermes takes natural-language scheduling rather than a config file, so this is written as the actual instructions to give it, not fabricated syntax I can't verify against its real interface.

**Initial framing message / character setup:**
> You are a read-only monitor for a Polymarket 5-minute crypto trading bot (BTC/ETH/SOL). You have no ability to place trades, change config, or write to any file the bot uses — you only read `trade_events*.csv`, `fills_log*.jsonl`, `calibration_log*.csv`, and `risk_state*.json` under the bot's `data/` folder (or via the dashboard's `/api/state` endpoint once deployed), and post findings to Telegram. Never suggest or attempt to modify the bot itself.

**Scheduled checks to set up (natural language, each as its own schedule):**
1. Every 4 hours: "Read `risk_state.json`, `risk_state_eth.json`, `risk_state_sol.json`. If any `circuit_breaker_triggered` changed from `false` to `true` since your last check, alert immediately with the asset and `daily_pnl`."
2. Daily at a fixed time: "Read the last 20 rows with `status=EXECUTED` in each asset's `trade_events*.csv`. Report win rate and mean entry price per asset. Flag BTC specifically if any executed trade's entry price falls outside [0.333, 0.50] — that would mean the payout filter isn't holding."
3. Daily: "Check whether ETH has executed any trade in the last 24 hours (`trade_events_eth.csv`, `status=EXECUTED`). If yes, this is the first ETH trade in a while — flag it explicitly, since ETH has been confidence-paused most days."
4. Weekly: "Using `calibration_log*.csv`, dedupe to one row per `window_id` at `tau_sec` nearest 150, and report Brier score of `p_model` vs `realized_up` per asset, trailing 7 days only. Note the trend versus the prior week."

None of this runs anywhere yet — it's the exact text to hand Hermes once you stand it up, so that step takes minutes instead of another design pass.
