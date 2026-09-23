# Pre-registration: does volatility regime rescue the fast rules? (test V)

Written before running. Paper only, no trading advice. Reuses the same data and rules as test F
(docs/PREREG_fast_intraday.md, data/fast/*.npy, already downloaded, KILL result already reached on the
unconditional rules) to check one narrower question cheaply, with no new data collection.

## Question
Test F killed R1/R2/R3 unconditionally. Does restricting to the highest-realized-volatility windows change the sign?
(High realized vol raises the size of the 15-min return relative to fixed costs, which is the one lever test F's
result leaves open per the Expansionist advisor's council note.)

## Method
For each of the 3 rules x 2 coins, take every trade test F already generated (same entries: R1/R2/R3, same hold,
same entry/exit convention) and split into the top tercile vs bottom two terciles by realized volatility of the
prior 24h (the same rolling-sigma test F already computes for R1/R2). Compare mean net return per trade (0.24% cost)
between the top-vol tercile and the full sample.

## Statistics
95% cluster bootstrap by day, 5,000 resamples, on the top-vol tercile only (one test per rule-coin, Bonferroni
6 tests, 99.2% CI, matching test F's correction).

## PASS: top-vol tercile mean net return per trade CI99.2 lower bound > 0, n >= 100 in that tercile, for at least
one rule-coin, AND the full-sample result stays consistent with test F (no contradiction).
## KILL: otherwise. No further slicing after this result (this is the one extra cut test F's council review allows).

## Result (2026-09-23)
Top-vol tercile, all 6 rule-coin combinations, net of 0.24% round trip, dev split, n=333-414 each:
CI99.2 fully below zero in every case (e.g. R1 BTCUSDT top tercile [-0.00353, -0.00207], mean -0.282%;
R2 ETHUSDT top tercile [-0.00307, -0.00022], mean -0.17%, the closest to zero and still fully negative).
Full-sample CIs are consistent (also fully negative), so no contradiction. Output: scripts/vol_regime_backtest.py.
Decision gate: no top-vol-tercile CI crosses zero, n requirement met in all 6. KILL.
Conclusion: higher realized volatility narrows the loss slightly in some cases (notably R2/ETH) but never flips the
sign. Fees still exceed the gross move even in the most volatile third of the sample. No rescue.
