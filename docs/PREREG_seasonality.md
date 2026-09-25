# Pre-registration: intraday time-of-day / day-of-week seasonality (test S)

Written 2026-09-23, before any of this test's data exists. Paper only, no trading advice. New data: Binance 1s
klines, BTCUSDT/ETHUSDT/SOLUSDT, last 30 days (data/seasonality/*.npy, downloaded fresh for this test).

## Question
Does average return during specific hour-of-day (UTC) or day-of-week windows differ enough from the unconditional
mean to beat round-trip cost, once multiple-comparison corrected? This is a calendar-effect test, distinct from
test F (momentum/reversion rules) and test T (SMA trend) already KILLed.

## Method
Resample 1s klines to 1-minute closes. For each symbol, compute log return over each of the 24 UTC hour-of-day
buckets and each of the 7 day-of-week buckets (31 buckets x 3 symbols = 93 cells), using the 60-second forward
return from the start of each bucket occurrence. Compare each cell's mean forward return against the full-sample
unconditional mean.

## Split
First 70% of the 30-day window = dev (build/inspect freely). Last 30% = sealed holdout, opened once, after the
dev-stage bucket selection is frozen in this doc's Result section (i.e. which buckets if any look promising on dev
must be named here before the holdout file is read).

## Statistics
93 cells x 3 symbols is too many independent looks; Bonferroni correction across 93 cells: alpha 0.05/93 = 0.000538
per cell on dev. Any cell clearing that bar on dev gets exactly one holdout look, at the SAME Bonferroni-adjusted
threshold (not relaxed), cluster-bootstrapped by day (5,000 resamples).

Per this project's cumulative ledger (docs/TEST_LEDGER.md), a PASS also needs cumulative-budget clearance:
0.05 / (total ledger tests at evaluation time), which is stricter than the per-test Bonferroni above whenever the
running total exceeds 93.

## Costs
Round-trip cost 0.10% (maker-leaning, matches this bot's typical fill) and 0.24% (taker, matches test F) both
reported; PASS requires clearing 0.24%.

## PASS
At least one hour-of-day or day-of-week cell, named in advance from dev, whose holdout mean net return (after
0.24% round-trip cost) has a 95% cluster-bootstrap CI (Bonferroni-widened per above) entirely above zero, AND
n >= 100 in that cell on holdout.

## KILL
Otherwise. No new cells may be proposed after the holdout is opened. No re-running with a different symbol list,
lookback window, or cost assumption after this point.

## Before this test
No seasonality test has been run on this project (checked docs/TEST_LEDGER.md and docs/case_study_no_edge.md).
Distinct from test F (fixed-horizon momentum/reversion, not calendar-indexed) and test T (daily SMA trend).
