# Polymarket Bot — Roadmap (BTC-first)

Hermes Agent = Nous Research's open-source AI agent platform (persistent memory, natural-language scheduling, subagent delegation, native Telegram/Discord/Slack/WhatsApp/Signal/Email integration, MIT license, self-hosted). It is **not** a trading/forecasting product — no backtesting, Monte Carlo, or market-pricing features exist in it. Role below: monitoring/notification layer, not the pricing engine.

## Phase 1 — Harden BTC-only (do this next)
Already built and running: Student-t/Gaussian fair-value model, EWMA vol, OFI, momentum, CBI, DVOL blend, TWAP adjustment, Platt calibration. Two new rules, both directly motivated by data already collected (not speculative tuning):

1. **Payout-ratio filter ("wins 2-3x")** — only enter when payout multiple `1/entry_price` is 2x–3x, i.e. entry price ∈ [0.33, 0.50] for the side bought. Skip the window entirely if no side is in that band, regardless of edge. This is also the fix for the SOL fee-drag pattern already flagged (cheap entries at 0.04–0.18 imply 6–24x payout and the worst fee zone — `fee_fraction = 0.07×(1−p)` is highest exactly there).
2. **Portfolio max-drawdown breaker** — hard stop at −25% of `STARTING_BALANCE_USD` ($70 → $52.50 floor). Separate object from the existing daily breakers: does **not** reset at UTC rollover, persists until manually cleared. This is capital preservation, not a daily budget.

Binance spot feed is already correct (`SPOT_EXCHANGE=binance`) — no change needed.

## Phase 2 — Real backtest framework (offline, separate from live paper trading)
Replay the exact fair-value formula against historical Binance klines + already-logged `calibration_log*.csv`, enforcing the two lessons already learned the hard way this project:
- One row per settled window (never raw ticks — intrawindow autocorrelation leaks the label).
- Any calibration curve fit strictly on a chronological train split; evaluate only on held-out data.
- No GBM/tree models — already ruled out (overfits at this sample size).
Output: Brier score model-vs-market and a simulated equity curve under the Phase 1 rules, per asset.

## Phase 3 — Monte Carlo
Once Phase 2 gives a stable win-rate/payout distribution per asset: simulate 10k+ equity paths to estimate probability of hitting the 25% drawdown stop, expected time-to-double, and variance under Kelly vs fixed sizing. Use this to sanity-check `KELLY_FRACTION=0.25` against the *actual* measured edge post-payout-filter, not the pre-filter numbers.

## Phase 4 — Hermes Agent as monitoring layer (optional polish, after BTC is proven)
Do not replace the deterministic quant strategy with an LLM. Run Hermes alongside as:
- A subscriber to the existing Telegram/Discord notifier + calibration logs.
- Persistent-memory pattern-flagging across days (e.g. "SOL entries clustering cheap for 3 days") — the kind of check this chat has been doing manually.
- Scheduled natural-language check-ins instead of manual "read logs" requests.
This is explicitly sequenced last per "create working BTC first."

## Phase 5 — Multi-asset rollout + Railway
Only after Phase 1–3 show BTC durably profitable (multi-day, not one day) under the payout filter + drawdown stop — then re-enable ETH/SOL under their existing tighter gates.

Railway-specific items to flag to Gemini before deploying (the bot currently assumes a local filesystem):
- Railway's filesystem is ephemeral on redeploy — `data/*.json`/`*.csv` state (risk state, open positions, calibration log) must live on a Railway persistent volume or it silently resets every deploy.
- Secrets (`.env`: Telegram/Discord tokens, any keys) go into Railway environment variables, never committed.
- Confirm outbound access to Polymarket, Binance, Coinbase, Deribit isn't blocked by Railway's network policy.
- Add a heartbeat/health check — Railway restarts on crash but not on silent hangs.

---

## Spec to send Gemini now (Phase 1 only)

> Implement two changes to the BTC strategy (hold ETH/SOL as-is):
>
> 1. Payout-ratio entry filter: reject any trade where `1/entry_price` is outside [2.0, 3.0] (i.e. entry price outside [0.333, 0.50]) for the side being taken. Add as a new funnel check, e.g. `BLOCKED_PAYOUT_RATIO`, evaluated alongside the existing edge/z-score hurdles.
> 2. Portfolio max-drawdown breaker: new persistent state (separate file/field from the daily `risk_state_portfolio.json`), trips when `simulated_balance <= STARTING_BALANCE_USD * 0.75`, does not reset on UTC day rollover, requires manual clear.
>
> Do not touch ETH/SOL parameters. Do not add ML models. Report back file diffs for verification before restart.
