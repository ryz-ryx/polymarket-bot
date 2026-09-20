# Pre-registration: cross-sectional daily crypto rules (test X)

Written before any daily data was downloaded. Paper only. No trading advice.

## Question
Do simple cross-sectional rules (rank 20 liquid coins, hold the top or bottom 3 for a day) beat the equal-weight universe after fees?

## Universe (fixed now)
BTC ETH BNB XRP ADA DOGE SOL TRX LINK AVAX DOT LTC BCH ATOM UNI ETC XLM NEAR FIL AAVE (all USDT spot, Binance).
Known bias: coins that exist today survived; this inflates results, so a failure is more informative than a pass.
Coins missing for part of the sample are ranked only while they have data.

## Data
Binance monthly daily klines (data.binance.vision), Jan 2022 to Aug 2026. Split: first 60% development
(sanity only), last 40% sealed holdout, run once (lock file).

## Rules (4, fixed)
At each UTC daily close t, rank coins by return over the formation window W in {1 day, 7 days}.
- REV1, REV7: hold the 3 worst equal-weight for the next day (reversal).
- MOM1, MOM7: hold the 3 best equal-weight for the next day (momentum).
Long only, fully invested, rebalance daily. Signal at close t, return t to t+1 (no look-ahead).

## Costs
0.12% per side on traded weight (taker 0.10% + 0.02% slippage), applied on turnover.

## Statistic
Daily excess return = strategy net return minus equal-weight universe return that day.
Mean excess with 95% circular block bootstrap CI (block 10 days, 5,000 resamples), Bonferroni 4 rules: use 98.75% CI.

## PASS (all for at least one rule)
Holdout excess CI lower bound > 0 after costs, dev mean excess > 0, holdout n >= 300 days.
## KILL: otherwise. No retuning, no second holdout run.

## Note
Even a pass is gross of survivorship bias and of capacity limits; with $50 it earns cents to dollars.

## Result (2026-09-20)
Development split (first 60%, 1,015-1,021 days, 20 coins, 0.12% per side on turnover):
REV1 excess -26.9 bps/day CI98.75 [-39.0,-14.9]; REV7 -17.9 [-29.5,-7.1]; MOM1 -22.7 [-36.9,-7.3]; MOM7 -6.2 [-21.7,+10.1].
No rule has dev mean excess > 0: KILL. Holdout NOT opened. Daily full turnover costs about 24 bps/day; that alone explains the sign.
