# Pre-registration: volatility-timed breakout entries (test W)

Written 2026-09-23, before any of this test's data exists. Paper only, no trading advice. Reuses the same
1s-kline data as test S (data/seasonality/*.npy, BTCUSDT/ETHUSDT/SOLUSDT, 30 days), downloaded once for both.

## Question
Test F's R3 breakout rule (prior-240min high break) was KILLed unconditionally. Test V showed restricting to
high-realized-vol windows narrows but does not flip the loss. This test asks a different question: does FORECASTING
short-horizon vol expansion (rather than conditioning on already-high vol) and firing breakout entries only in
predicted-expansion windows produce a different result? Distinct from test V, which is a contemporaneous filter,
not a forecast.

## Method
1-minute closes resampled from the 1s klines. Realized vol forecast: EWMA of squared 1-min returns (halflife 30min)
used to predict the NEXT 15-minute realized vol at each 1-min timestamp (rolling forecast, no look-ahead: forecast
at t only uses returns up to t). Define "predicted expansion" as forecast vol in the top quintile of its own
trailing 30-day distribution at that point. Fire test F's R3 breakout rule (240-bar prior high break, 60-bar hold)
ONLY inside predicted-expansion windows; compare against firing unconditionally (test F's original result, for
reference only, not re-tested).

## Split
Same 70/30 chronological split as test S, on the same 30-day window. Dev used to confirm the forecast is
well-calibrated (predicted-expansion quintile actually has higher realized vol than others, out of sample within
dev) before any PASS/KILL evaluation touches holdout.

## Statistics
3 symbols x 1 rule = 3 tests. Bonferroni: alpha 0.05/3 = 0.0167 per symbol on the holdout-only evaluation.
Per docs/TEST_LEDGER.md, cumulative-budget clearance (0.05 / running ledger total at evaluation time) applies on
top of this and will be the binding constraint given the ledger is already past 68.

## Costs
0.24% round-trip (taker, matches test F for direct comparability).

## PASS
For at least one symbol: holdout mean net return per trade in predicted-expansion windows has a 95%
cluster-bootstrap-by-day CI (Bonferroni-widened per above) entirely above zero, n >= 50 trades in that symbol's
holdout expansion windows, AND the vol forecast calibration check on dev passes (predicted-expansion quintile
realized vol > median of other quintiles, out of sample within dev).

## KILL
Otherwise, including if the calibration check fails (no valid forecast = no valid test, treated as KILL not
inconclusive, per this project's own convention in docs/FREEZE.md).

## Before this test
Test V (docs/PREREG_vol_regime.md) sliced by CONTEMPORANEOUS realized vol tercile on test F's existing trades and
was KILLed. This test differs by forecasting forward vol and gating entries on the forecast, which V's own doc
notes was "the one extra cut test F's council review allows" — this is a second, genuinely different cut
(forecast vs contemporaneous), pre-registered separately rather than smuggled in as a V variant.
