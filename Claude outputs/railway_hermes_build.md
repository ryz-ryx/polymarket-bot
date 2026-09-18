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

**Python version pin.** Your local venv is running Python 3.14.2 (`venv/pyvenv.cfg`). Add a `.python-version` file (repo root) containing `3.14` so Nixpacks builds against the same version rather than whatever it defaults to — confirm Railway's Nixpacks provider actually supports 3.14 before deploying, since it's recent enough that I can't verify that from here without it going stale by the time you read this.

**Important, easy to miss: the calibration history won't carry over on first deploy.** `.gitignore` correctly excludes all of `data/*.csv`, `*.json`, `*.jsonl` — that's right for keeping secrets/state out of git, but it also means a fresh Railway deploy starts with a genuinely empty `data/` volume. Concretely: the calibrator's Platt curve (currently fitted on 290+ real windows for BTC) resets to zero and sits back in "not enough data" mode until 30+ windows re-accumulate, and — more importantly — `get_confidence_weight()` resets to full trust (1.0) with no track record behind it, meaning BTC's current `LOW_CONFIDENCE_PAUSED` state (the thing correctly protecting it right now) disappears on day one of a cold Railway deploy. If you want Railway to start "warm" rather than relearn from scratch, the current `data/calibration_log*.csv` (and ideally the risk_state files) need to be manually copied onto the Railway volume after the first deploy, before the bot's first tick. Worth deciding on purpose, not discovering by accident.

---

## Hermes — concrete runbook (paste into Hermes once it's running; not a code change to the bot)

Hermes takes natural-language scheduling rather than a config file, so this is written as the actual instructions to give it, not fabricated syntax I can't verify against its real interface.

**Initial framing message / character setup:**
> You are a read-only monitor for a Polymarket 5-minute crypto trading bot (BTC/ETH/SOL). You have no ability to place trades, change config, or write to any file the bot uses — you only read `trade_events*.csv`, `fills_log*.jsonl`, `calibration_log*.csv`, and `risk_state*.json` under the bot's `data/` folder (or via the dashboard's `/api/state` endpoint once deployed), and post findings to Telegram. Never suggest or attempt to modify the bot itself.

**Scheduled checks to set up (natural language, each as its own schedule):**
1. Every 4 hours: "Read `risk_state.json`, `risk_state_eth.json`, `risk_state_sol.json`. If any `circuit_breaker_triggered` changed from `false` to `true` since your last check, alert immediately with the asset and `daily_pnl`."
2. Daily at a fixed time: "Read the last 20 rows with `status=EXECUTED` in each asset's `trade_events*.csv`. Report win rate and mean entry price per asset. Flag BTC specifically if any executed trade's entry price falls outside the live entry band (BTC 0.25–0.55, ETH/SOL 0.15–0.55; see `src/bot.py`) — that would mean the band filter isn't holding."
3. Daily: "Check whether ETH has executed any trade in the last 24 hours (`trade_events_eth.csv`, `status=EXECUTED`). If yes, this is the first ETH trade in a while — flag it explicitly, since ETH has been confidence-paused most days."
4. Weekly: "Using `calibration_log*.csv`, dedupe to one row per `window_id` at `tau_sec` nearest 150, and report Brier score of `p_model` vs `realized_up` per asset, trailing 7 days only. Note the trend versus the prior week."
5. **Highest priority, check every 15 minutes, not lumped with the daily ones:** "Check whether `data/risk_state_drawdown.json` exists. If it now exists and `circuit_breaker_triggered` is `true`, this is the permanent portfolio-wide drawdown breaker — alert immediately, marked urgent, distinct from the daily per-asset breakers. This one doesn't reset at UTC rollover and means all trading is halted until someone manually clears it."
6. Every 4 hours: "Check the funnel counts in the latest `[ROLLUP ...]` log lines (or recompute from `trade_events*.csv`) for a spike in `BLOCKED_PHANTOM` relative to its recent baseline rate. A sustained spike (not the usual background rate already established) could mean a real connectivity problem rather than the known thin-liquidity pattern — worth a second look, not an automatic assumption it's business as usual."
7. Daily: "If SOL's `circuit_breaker_triggered` flips from `true` back to `false` (i.e. it re-armed at UTC day rollover), note it as informational — not urgent, just confirms the day reset correctly."

None of this runs anywhere yet — it's the exact text to hand Hermes once you stand it up, so that step takes minutes instead of another design pass.
