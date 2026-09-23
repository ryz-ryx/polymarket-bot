# Pre-registration: does BTC lead altcoins on a 5-15 min horizon? (test L)

Written before downloading any new data. Paper only, no trading advice. Last untested item from the Expansionist
advisor's council list (lead-lag, lower turnover than the fast rules); the other three untested ideas (maker-quote
simulation, wide-spread market making, triangular/cross-venue gaps) need bid/ask or multi-venue data we don't have
historically, so they can't be sped up this way and stay deferred.

## Question
Does BTC's return over the prior 15 minutes predict SOLUSDT, XRPUSDT and BNBUSDT's next 15-minute return, net of costs?

## Data
Binance spot 1-minute klines, same source and period as test F/O (data.binance.vision monthly archives,
Oct 2025 - Aug 2026, 11 months), for BTCUSDT (already downloaded) plus SOLUSDT, XRPUSDT, BNBUSDT (new download).
Chronological split: first 60% development (sanity only), last 40% sealed holdout, run once (lock file).

## Rule (fixed, no grid)
Every 15 minutes (non-overlapping), if BTC's trailing 15-min return > 0, go long the alt for the next 15 minutes;
if < 0, stay flat (long-only, matching test F's convention). One position at a time per alt. Entry at next-bar open,
exit 15 minutes later at open.

## Costs
Round trip = 0.24% (2 x taker 0.10% + slippage), same as test F. Sensitivity: 0.12%.

## Statistics
Per alt, net return per trade, 95% cluster bootstrap by day (5,000 resamples), Bonferroni for 3 alts x 2 costs
(6 tests, 99.2% CI).

## PASS (all): holdout mean net return per trade CI99.2 lower bound > 0 at 0.24% cost for at least one alt,
n >= 300 holdout trades for that alt, same sign in development.
## KILL: otherwise. No retuning, no second holdout run, no further alt substitution after this result.

## Result (2026-09-23)
Development split, 3 alts x 2 costs, n=9,623 non-overlapping 15-min blocks per alt (32x the 300 minimum):
SOLUSDT [-0.00248,-0.00225] mean -0.236%; XRPUSDT [-0.00251,-0.00224] mean -0.237%; BNBUSDT [-0.00245,-0.00224]
mean -0.234% (all at 0.24% cost). At 0.12% cost all three stay fully negative too ([-0.0013,-0.0010] range).
Decision gate (matching test F's precedent: no positive CI after costs in dev): KILL. Holdout NOT opened.
Conclusion: BTC's trailing 15-min return carries no exploitable predictive signal for these 3 alts' next 15 minutes;
the mean loss is close to the cost itself (about -0.24% at 0.24% cost), meaning the gross signal is near zero, not
negative-but-real. Output: scripts/leadlag_backtest.py, data/fast_leadlag/.

