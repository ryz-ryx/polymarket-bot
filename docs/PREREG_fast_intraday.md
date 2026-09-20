# Pre-registration: fast intraday rules on crypto spot (test F)

Written before any 1-minute data was downloaded. Paper only. No trading advice.

## Question
Does any fast (minutes to hours) rule on BTC/ETH beat costs? User goal: fast, aggressive, small-to-large in hours.

## Data
Binance spot 1-minute klines, BTCUSDT and ETHUSDT, last 365 days. Chronological split: first 60% development
(code sanity only), last 40% sealed holdout, run once (lock file).

## Rules (three, fixed parameters, no grid)
R1 momentum: if 15-min return > +2 rolling-sigma (sigma = std of 15-min returns over prior 24h), go long; hold 30 min.
R2 reversion: if 15-min return < -2 rolling-sigma, go long; hold 30 min.
R3 breakout: if close > max close of prior 240 minutes, go long; hold 60 min.
Long only. One position at a time per asset. Entry at next-minute open, exit at open after hold. No leverage in the test.

## Costs
Round trip = 2 x (0.10% taker fee + 0.02% slippage) = 0.24%. Sensitivity: 0.12% (maker-like). Fee source: Binance spot
standard tier; Danish eligibility of Binance is UNVERIFIED and a real account is out of scope.

## Statistics
Per rule and asset, net return per trade; mean with 95% cluster bootstrap by day (5,000 resamples); Bonferroni for
3 rules x 2 assets = 6 tests (99.2% CI). Also net Sharpe of daily P&L and max drawdown.

## PASS (all): holdout mean net return per trade CI lower bound > 0 at 0.24% cost, for at least one rule,
n >= 300 holdout trades for that rule, and same sign in development.
## KILL: otherwise. No retuning, no second holdout run.

## Aggression check (separate, no data)
Monte Carlo of the goal "$50 to $500 within 12 hours" with leverage L in {1,5,10,20} and edge = 0 (fee-only),
reporting probability of success and of ruin. It quantifies why fast and aggressive means mostly ruin.

## Result (2026-09-20)
Data note: the API download was too slow, so Binance monthly archive files (data.binance.vision) were used instead:
11 months, Oct 2025 to Aug 2026, 482,400 one-minute bars per coin, 0 gaps (post-2025 archive timestamps are microseconds; normalised).
Development split (first 60%), all three rules, both coins, net of 0.24% round trip: mean -0.24 to -0.26% per trade, CI99.2 fully below zero, n about 1,000-1,100.
At 0.12% cost: still -0.11 to -0.14%, CI fully below zero.
Decision gate (no positive CI after costs): KILL. Holdout NOT opened.
Cost-floor study (zero fee, development only, holds 1/5/15/60/240 min): gross mean per trade between -6.5 and +3.2 bps, every CI99.2 includes zero;
hurdle is 12 bps (maker-like) to 24 bps (taker). Output: data/fast/F_costfloor_dev.txt.
Conclusion: no measurable gross edge in these fast rules on BTC/ETH spot; fees alone decide the sign.
