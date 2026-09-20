# Pre-registration: daily trend rule on crypto spot (test T)

Written before any data was downloaded. Paper only. No trading advice.

## Question
Does a slow trend rule on BTC and ETH beat its own beta after realistic EU-retail spot fees?

## Data
Binance daily klines (public API), BTCUSDT and ETHUSDT, first available day to latest closed day.
Chronological split: first 60% development (code sanity only, no tuning), last 40% sealed holdout, run once.

## Rule (fixed, no tuning)
- Signal on day t close: long if close > SMA(50) of closes, else cash.
- Position applies to day t+1 return (no look-ahead).
- Cost: 0.80% of traded notional per side (Kraken taker, lowest tier, verified 2026-09-20). Sensitivity: 0.40%.
- No leverage, no shorting.
- SMA(100) and SMA(200) reported as sensitivity only; they cannot rescue a fail.

## Metrics (holdout only for verdict)
- Alpha: mean daily (strategy net return - beta * buy-and-hold return), annualized, where beta = mean
  position over the evaluated period (fixed before bootstrap). Amended before any data was downloaded:
  using per-day exposure would make alpha equal to minus costs by construction.
- Pooled = equal-weight average of the BTC and ETH daily alpha series.
- 95% circular block bootstrap CI (block 20 days, 5,000 resamples) on alpha.
- Also: net return, Sharpe, max drawdown vs buy-and-hold; trades per year.

## PASS (all required)
1. Pooled alpha 95% CI lower bound > 0 after 0.80% cost, and neither asset alpha point estimate < 0.
2. Holdout net Sharpe >= buy-and-hold Sharpe, per asset.

## KILL
Any pass condition fails: idea killed. No rule changes, no second holdout run.
"Insufficient evidence" (wide CI including zero) counts as no pass.

## Notes
With $50, a pass earns dollars per year and gains are taxed as income in Denmark (unverified rate).
A pass is not a reason to trade real money without a legal and tax check.
