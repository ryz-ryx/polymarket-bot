# Polymarket5mBot — Independent Profit-Rate Analysis
**Analyst:** Claude (independent run, not coordinated with Gemini's methodology)
**Data source:** `data/fills_log*.jsonl`, `data/calibration_log*.csv`, `data/risk_state*.json` — pulled directly from the bot's Windows machine via the device bridge on 2026-09-09.
**Script:** `analyze_profit_rate.py` (attached) — reproducible, seeded (`random.seed(42)`), no external dependencies beyond the standard library.

---

## Important data caveat — read this first

`data/fills_log*.jsonl` only exists from the point persistent file-logging was added (mid-session today). It does **not** cover the full trading day — an earlier chunk of trades happened before that file existed and is unrecoverable at the individual-trade level. `data/risk_state*.json` (RiskManager's own persisted daily PnL) *does* cover the full day, so the two disagree:

| Asset | Full-day PnL (risk_state.json) | Tracked-portion PnL (fills_log) | Untracked gap |
|---|---|---|---|
| BTC | **+$89.01** | +$54.90 | +$34.10 |
| ETH | **-$69.46** | -$98.00 | +$28.54 |
| SOL | **+$87.36** | -$72.57 | +$159.93 |
| **Combined** | **+$106.91** | **-$115.67** | +$222.58 |

Everything below (win rate, Monte Carlo, calibration) is computed only on the **tracked portion** (90 trades total) — it's the only slice with full per-trade granularity to analyze. Treat the risk_state.json full-day number as the "official" scoreboard, and everything else as a statistically-useful but partial sample. Going forward this gap disappears since fills_log now captures every trade going forward.

---

## 1. Empirical profit rate (real executed paper trades, tracked portion)

| Asset | Trades (W/L) | Win rate | Capital risked | Net PnL | **Profit rate** | Profit factor |
|---|---|---|---|---|---|---|
| BTC | 37 (17/20) | 45.9% | $863.78 | +$54.90 | **+6.36%** | 1.12 |
| ETH | 18 (5/13) | 27.8% | $354.56 | -$98.00 | **-27.64%** | 0.61 |
| SOL | 35 (14/21) | 40.0% | $843.31 | -$72.57 | **-8.61%** | 0.85 |
| **Combined** | 90 (36/54) | 40.0% | $2,061.65 | -$115.67 | **-5.61%** | — |

Profit rate = net PnL ÷ capital risked (dollars actually staked), not return on starting bankroll — this avoids distortion from the bot's unusual ~$500 starting balance (the intended $50 reset was never applied, as flagged earlier this session).

Per-trade return volatility is enormous relative to the mean (BTC: ±132.78% std dev per trade against a +3.59% average) — Sharpe-like ratios are all near zero or negative (BTC +0.027, ETH -0.243, SOL -0.077), meaning at this sample size **the results are statistically indistinguishable from noise.** 90 trades is not enough to confirm or rule out a real edge.

---

## 2. Monte Carlo simulation (10,000 bootstrap paths, seed=42)

Method: resample with replacement from the actual (stake, outcome) pairs that occurred — preserves the real joint distribution of bet size and win/loss rather than assuming a synthetic edge. Path length matches each asset's historical trade count.

| Asset | Median PnL | 5th–95th pctile | P(ends negative) | P(trips per-asset breaker, ≤ -$50) |
|---|---|---|---|---|
| BTC (37 trades) | +$48.63 | -$235.94 to +$380.63 | 39.8% | 29.3% |
| ETH (18 trades) | -$101.97 | -$255.43 to +$79.92 | 82.8% | 69.0% |
| SOL (35 trades) | -$83.33 | -$383.38 to +$272.78 | 65.9% | 56.6% |
| **Portfolio (90 trades)** | **-$123.98** | **-$586.30 to +$384.90** | **66.3%** | **53.2%** (portfolio breaker, ≤ -$100) |

Reading this: even resampling from the bot's own actual trade outcomes, a portfolio-level run of this length ends net negative about 2 times in 3, and trips the new $100 portfolio circuit breaker more than half the time. The dispersion (5th to 95th percentile spans nearly $1,000 on the portfolio) says variance currently dominates whatever edge exists.

---

## 3. Model calibration / edge validity (backtest vs. calibration_log.csv)

A full re-execution backtest isn't possible — no historical order-book/spread data is persisted, only `p_model` and `p_market` per tick. Instead this checks whether the strategy's own probability estimate (`p_model`) is *better calibrated* against what actually happened than the market's own implied probability (`p_market`) — i.e., does the model have real statistical edge, independent of execution and fees. Lower Brier score is better; 0.25 = pure coin-flip.

| Asset | Distinct windows | Model Brier | Market Brier | Verdict |
|---|---|---|---|---|
| BTC | 157 | **0.1575** | 0.1898 | **Model beats market** (+0.0323 edge) |
| ETH | 47 | 0.1811 | **0.1642** | Market beats model (-0.0169) |
| SOL | 47 | 0.2000 | **0.1703** | Market beats model (-0.0297) |

BTC's calibration curve is close to a clean diagonal (0.0-0.1 bucket → 5.6% actual up-rate, 0.9-1.0 bucket → 93.3%) — genuinely well-calibrated. ETH and SOL's curves are noisier and non-monotonic in places, but also have a third of BTC's sample size (47 vs 157 windows), so "market beats model" there is a real yellow flag worth more data before concluding the model is actually mis-specified for those two assets — it isn't yet a large enough sample to be conclusive either way.

---

## Bottom line

- **The only number with a full-day, fully-reconciled record is risk_state.json's +$106.91 combined.** Take that as today's actual result.
- On the trade-level sample that can be analyzed in detail, the strategy's edge is not yet statistically distinguishable from noise (Sharpe-like ratios near zero, Monte Carlo shows a coin-flip-ish chance of ending negative over a similar-length run).
- BTC shows the most promising signal — both a positive realized profit rate on the tracked sample and genuine calibration edge over the market. ETH and SOL are the weaker links on both counts, on smaller samples.
- None of this is enough data to declare the strategy validated or invalidated — it's one day, ~90-150 observations per asset. Worth re-running this same script after several more days of data to see if BTC's edge holds and ETH/SOL's edge materializes or the model there gets revised.

Send `analyze_profit_rate.py` to Gemini to run independently against the same data files — if the two of you land on materially different headline numbers from the *same* input files, that's a bug in one implementation worth finding before trusting either one.
