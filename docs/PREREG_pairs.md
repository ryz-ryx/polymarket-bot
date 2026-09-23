# Pre-registration: BTC/ETH spread mean-reversion (test P)

Written before running. Paper only, no trading advice. Counted in docs/TEST_LEDGER.md's shared alpha budget before
running (see that doc for why: one budget across every strategy, not a fresh one per batch). This is a genuinely
different mechanism from every prior test: those bet on one coin's own price; this bets on the RATIO between two
correlated coins reverting, which is a different source of edge (statistical arbitrage) than direction or momentum.

## Question
Does the BTC/ETH price ratio mean-revert enough, net of costs, to profit from going long the laggard and short the
leader when the ratio strays from its recent average? (Long-only version: since spot shorting isn't available at
this account size/venue without margin, only the "buy the laggard" leg is tested - go long whichever of BTC/ETH has
underperformed the other over the trailing 24h, exit when the ratio reverts to its trailing mean or after a max
hold.)

## Data
Same source as test F: data/fast/*.npy, Binance 1-minute klines, Oct 2025 - Aug 2026, 11 months. Chronological
60/40 split, sealed holdout, run once (lock file).

## Rule (fixed, no grid)
ratio = close(BTC) / close(ETH). z = (ratio - rolling_mean_1440min(ratio)) / rolling_std_1440min(ratio).
If z < -1.5 (ETH has outperformed BTC over the trailing 24h beyond 1.5 sigma): go long BTC.
If z > +1.5 (BTC has outperformed ETH): go long ETH.
Exit when z crosses back through 0, or after a 240-minute max hold, whichever comes first. One position at a time.
Entry at next-minute open, exit at open of the exit-triggering minute.

## Costs
0.24% round trip (2 x 0.10% taker + slippage), same as test F. Sensitivity: 0.12%.

## Statistics
Net return per trade, 95% cluster bootstrap by day (5,000 resamples). This is 1 rule x 2 costs = 2 sub-tests; per
docs/TEST_LEDGER.md, the correction denominator is the CUMULATIVE total at evaluation time (46 + 2 = 48), so
alpha/48 = 0.00104, CI level 99.896%.

## PASS: holdout mean net return per trade CI lower bound > 0 at 0.24% cost, n >= 300 holdout trades, same sign in
development.
## KILL: otherwise. No retuning, no second holdout run.

## Result (2026-09-23)
Development split, n=753 trades (2.5x the 300 minimum): at 0.24% cost, mean -0.321%/trade, CI [-0.00447, -0.00199],
fully negative. At 0.12% cost, mean -0.201%/trade, CI [-0.00327, -0.00079], still fully negative.
Decision gate (same precedent as tests F, L: no positive CI in dev): KILL. Holdout NOT opened.
Conclusion: the BTC/ETH ratio does show reversion in principle (that's why the rule finds 753 setups), but the
average move after reversion doesn't clear even half the round-trip cost. A genuinely different mechanism
(spread reversion vs. directional prediction) still loses to the same fee floor. Output: scripts/pairs_backtest.py.

