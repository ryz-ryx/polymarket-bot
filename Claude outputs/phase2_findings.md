# Phase 2 — Backtest/Monte Carlo findings (from real logs, not simulated)

## Data access constraint (important, affects what's feasible from here)
A real historical-market backtest (months of Binance klines) can't be built from this session: cloud egress blocks `api.binance.com` (org policy only allowlists package registries), and `device_bash` on your machine still fails to mount the bot folder (same "no Plan9 drive shares mounted" error seen throughout this project). Your bot's own machine already has working Binance access — the practical path is Gemini building the backtest script *there* (spec below), with results staged back here for me to verify independently.

## What I found instead, using your actual live logs (staged + verified directly, not from Gemini's summary)

**1. Overconfidence on the trades actually taken (new finding).**
The calibrator itself is correctly built — verified `calibrator.py` dedupes to one representative row per window (tau≈150s) before fitting Platt/isotonic, no leakage there. But on the subset of windows that actually clear the trading hurdle (the extreme/selected tail), the post-calibration probability runs hot:

| Asset | Executed trades (all-time) | Win rate on side traded | Mean p_model | Mean entry price |
|---|---|---|---|---|
| BTC | 90 | 46.7% | 0.576 | 0.439 |
| ETH | 39 | 38.5% | 0.533 | 0.417 |
| SOL | 76 | 43.0%* | 0.550 | 0.430 |

This is a classic selection-effect ("winner's curse"): calibrating on the general population, then trading only the extreme tail, tends to overstate confidence on that tail even when the general calibration is sound. Since Kelly sizing uses `p_model` directly, this means current position sizes are likely larger than the true edge justifies.

**2. Plain economics: negative mean $ P&L per trade, all three assets, even post-tightening.**
Restricting to the *current* regime only (since the 2026-09-10 13:43 reset to $70 starting balance / $5 max position — confirmed via `fills_log*.jsonl`, cost dropped from a flat ~$25/trade to $2-6/trade at that exact point):

| Asset | Trades (current regime) | Win rate | Mean $ P&L/trade | Total $ so far |
|---|---|---|---|---|
| BTC | 10 | 50% | −$0.66 | −$6.62 |
| ETH | 12 | 42% | −$1.44 | −$17.23 |
| SOL | 18 | 22% | −$1.16 | −$20.93 |

Sample sizes are tiny (10-18 trades) — wide uncertainty on the true mean. But directionally, BTC is no longer the clear standout it was earlier in the project; all three are currently net-negative.

**3. Monte Carlo (bootstrapped from real current-regime $ outcomes, 20k sims).**
Extrapolating this exact historical rate forward (no change), the new $157.50 portfolio floor (25% of $210 combined starting capital, matching the just-verified Phase 1 code) gets breached with very high probability within a few weeks of continued trading at this pace. Caveats: (a) this is a small-sample bootstrap, so it re-draws from only 10-18 real outcomes per asset — treat it as "this is the shape of the risk," not a precise forecast; (b) it reflects *before* today's payout-ratio filter and portfolio-wide drawdown fix went live, so it's a baseline, not a verdict on those changes.

## What this means, concretely
- The 25% drawdown breaker just verified isn't a formality — the live numbers say it's a real, plausible near-term outcome if nothing changes. Good that it's in place.
- BTC's payout-ratio filter is the one lever already shipped that's relevant here — worth watching closely now that it's about to run live, since BTC is the asset most likely to show real edge.
- New, not-yet-implemented candidate given the overconfidence finding: reduce Kelly sizing further (e.g. quarter-Kelly → eighth-Kelly) or apply an explicit haircut to `p_model` specifically for *sizing* (separate from the trade/no-trade decision), until more executed-trade data resolves whether this is noise or persistent miscalibration. **Not sending this to Gemini yet** — flagging it, want your call given it directly reduces trade size/frequency.

## Spec to send Gemini for Phase 2 (real backtest, run on their machine — has real Binance access)
> Build `backtest.py`, run locally (not asking me to run it — this session's network can't reach Binance).
> 1. Pull Binance 1-minute klines for BTC/ETH/SOL, 60-90 days.
> 2. Reconstruct 5-min windows: strike = open price, realized outcome = close vs strike.
> 3. Import and call the actual production `calculate_fair_probability()` from `src/strategies/claud_quant.py` directly — do not reimplement the math, to avoid any drift from what's actually trading.
> 4. Only feed inputs computable from spot history alone: realized vol, momentum. Set `ofi_normalized=0.0` and `cbi_normalized=0.0` explicitly (no historical order-book data exists to compute these honestly — do not approximate them).
> 5. Log exactly one row per window to `data/backtest_log_{asset}.csv` (window_id, p_model, z, realized_up) — no per-tick rows.
> 6. No calibration fitting inside this script, no ML models. Just the raw math vs. outcome.
> Send back the raw output CSVs (not a summarized Brier score) — I'll compute the statistics independently.
