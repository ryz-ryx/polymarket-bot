# Prep work: Railway deployment + Hermes monitoring agent
Both stay OFF/unused until BTC proves real edge under Phase 1. This is scoping only.

---

## Railway deployment prep (spec to send Gemini)

The bot currently assumes a local filesystem and never leaves your machine. Four things need to change before it can run on Railway at all — none of this touches the trading logic.

> 1. **Persistent state.** Every file under `data/` (`risk_state*.json`, `open_positions*.json`, `risk_state_drawdown.json`, `risk_state_portfolio.json`, `calibration_log*.csv`, `trade_events*.csv`, `fills_log*.jsonl`, `pending_resolutions*.json`, `fallback_reconciliation*.json`, `pending_window_observations*.json`) must live on a Railway Volume mounted at `data/`, not the container's own ephemeral disk. Without this, every redeploy silently wipes the drawdown breaker's tripped state, the day's risk counters, and the entire calibration history — a redeploy would look indistinguishable from a manual reset, with no record it happened.
> 2. **Secrets migration.** Everything currently in `.env` (Polymarket keys, Discord/Telegram tokens) moves to Railway's environment variable settings. `.env` itself must never be committed or uploaded as part of the deploy — confirm `.gitignore` still excludes it and that the deploy method (git push vs CLI upload) doesn't bypass that.
> 3. **Process definition.** Add a `Procfile` or `railway.json` specifying the start command (`python run.py` via the existing venv/requirements), and pin the Python version. Railway auto-restarts on a hard crash, but won't catch a silent hang (process alive, WebSocket dead) — add a lightweight heartbeat: either an HTTP healthcheck endpoint (Railway can poll this and restart if it stops responding) or a periodic Telegram "still alive" ping, so a stalled process without a book/feed doesn't sit silently unprofitable for hours before anyone notices. `dashboard.py` may already be a natural place to expose this if it runs its own HTTP server — check before building a new one.
> 4. **Network confirmation.** Once deployed, verify outbound access actually works to: `clob.polymarket.com` + its WebSocket (`wss://ws-subscriptions-clob.polymarket.com`), Binance, Coinbase, and Deribit. Railway's outbound network is generally unrestricted, but confirm rather than assume — a silent connectivity gap here would look identical to the phantom-liquidity issue already diagnosed, and could get misdiagnosed the same way.

**Not a Gemini implementation detail, a decision for you:** Railway's free tier sleeps inactive services; a 24/7 trading bot on 5-minute markets needs an always-on plan. Worth pricing out before committing, since that's an ongoing cost this project hasn't had before (everything's been running on your own machine so far).

---

## Hermes agent — monitoring layer design (for you to set up; not something Gemini builds into the bot)

Hermes Agent (Nous Research, self-hosted, MIT license) is a general-purpose AI agent with persistent memory, natural-language scheduling, and native Telegram/Discord integration — it has no trading or forecasting features of its own. Its role here is strictly read-only monitoring, replacing the manual "read logs" checks that have been happening in this chat.

**Hard boundary:** Hermes never touches `config.py`, `.env`, risk state, or the trading engine. It only reads — the existing Telegram/Discord channel `notifier.py` already posts to, and/or the CSV/JSON logs directly if run on the same machine. No write path back into the bot at all.

**What it would do, concretely** — each of these is something I've been doing by hand this project, formalized as a scheduled Hermes check:
- Daily (or every few hours): read `trade_events*.csv` + `fills_log*.jsonl`, report win rate and entry-price distribution per asset, flag if any asset's trailing win rate crosses a threshold (e.g. drops below its own historical baseline) or if ETH executes its first trade at all.
- Watch for entry-price clustering (the SOL cheap-entry/high-fee pattern already found) — flag if any asset's average entry price drifts back toward the high-fee zone for several consecutive days.
- Watch circuit breaker state — flag immediately (not just at next scheduled check) if any `circuit_breaker_triggered` flips to `true`, especially the permanent drawdown breaker.
- Weekly: summarize Brier/calibration trend if calibration_log data supports it.

**Setup needed when you're ready:** a Nous Portal account (or local model backend) for Hermes itself, a Telegram bot token for it to post through (can be a second bot separate from the trading bot's own notifications, so the two streams don't get confused), and read access to wherever the logs live (same machine, or the Railway volume once deployed).

This stays unbuilt until BTC's numbers justify moving past Phase 1 — flagging it here so the design is ready to execute quickly once that happens, not so it starts now.
