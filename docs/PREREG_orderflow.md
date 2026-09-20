# Pre-registration: order-flow imbalance on 1-minute bars (test O)

Written before any imbalance statistic was computed. Paper only. No trading advice.

## Question
Does recent buyer-initiated volume imbalance predict the next-window BTC/ETH return by more than trading costs?

## Data
Binance spot 1-minute klines (monthly archives already downloaded: Oct 2025 to Aug 2026, 482,400 bars per coin).
Fields used: open, close, volume, taker_buy_base_volume. Split: first 60% development (thresholds fitted here only),
last 40% sealed holdout, run once (lock file).

## Signal (fixed)
imbalance_k(t) = sum over last k minutes of (2*taker_buy - volume) / sum of volume, in [-1, 1]. k in {5, 15, 60}.
Window h = k minutes: enter at next-minute open after minute t closes, exit h minutes later at open.
Threshold: 95th percentile (for CONT) or 5th percentile (for REV) of imbalance_k measured on the DEVELOPMENT split only,
then frozen and applied on the holdout.
- CONT: go long when imbalance_k >= p95 (continuation).
- REV: go long when imbalance_k <= p5 (reversal).
Long only. One position per coin at a time. 3 windows x 2 directions x 2 coins = 12 tests.

## Statistics
Per test: mean gross return per trade (zero cost) and net at 0.12% and 0.24% round trip; 95% cluster bootstrap by day
(5,000 resamples), Bonferroni for 12 tests: 99.6% CI (0.2/99.8 percentiles).

## Levels
- REAL gross edge: dev and holdout both have gross CI lower bound > 0, n >= 300 holdout trades.
- TRADABLE: holdout net mean CI lower bound > 0 at 0.12% round trip (maker-like, assumption unverified).
## KILL: not REAL on development split -> holdout NOT opened. No retuning, no second holdout run.

## Result (2026-09-20)
Development split, 12 tests, 99.58% CI (Bonferroni 12), zero cost:
gross mean per trade ranged from -3.95 to +0.64 bps; no test had gross CI > 0; CONT on BTC k=15 was significantly NEGATIVE (-1.64 bps, CI [-3.0,-0.3]).
Net at 0.12% round trip: -11.4 to -15.9 bps; net at 0.24%: -23.4 to -27.9 bps.
Verdict: KILL. Holdout NOT opened. Order-flow imbalance from taker-buy volume carries no measurable next-window gross edge
on BTC/ETH spot 1-minute bars; costs (12-24 bps) exceed any effect by more than 10x.
