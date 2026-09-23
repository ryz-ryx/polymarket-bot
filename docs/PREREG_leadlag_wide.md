# Pre-registration: BTC-leads-alt lead-lag, wide universe (test L2)

Written before downloading the extra coins. Paper only, no trading advice. Test L found no BTC-lead signal on
3 alts and pre-registered "no further alt substitution after this result" to block quietly retrying more coins
until one looks good. This is a separate, explicitly confirmatory test on a wider universe, not a substitution -
same rule, same method as test L, no rule changes based on L's result.

## Question
Same as test L (does BTC's trailing 15-min return predict the next 15-min return, long-only, net of 0.24%/0.12%
cost), run across a wider basket of liquid coins instead of 3, to check test L's null isn't an artifact of coin
choice.

## Universe
BTCUSDT (lead) vs ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, DOTUSDT, LTCUSDT, MATICUSDT, TRXUSDT, ATOMUSDT, NEARUSDT
(10 new alts, chosen for liquidity/listing history, not performance - picked before running). Same period as test
L/F/O: Binance monthly archives, Oct 2025 - Aug 2026, 11 months, 1-minute klines.

## Method, costs, statistics
Identical to test L: same rule, same 0.24%/0.12% costs, cluster bootstrap by day, Bonferroni for 10 alts x 2 costs
(20 tests, CI level adjusted to 99.75%). Development split only (first 60%); given test L's result, no holdout is
opened unless development shows a positive CI (same precedent as test F and test L).

## PASS: dev CI adjusted lower bound > 0 at 0.24% cost for at least one alt, n >= 300.
## KILL: otherwise. This is the final coin-universe test for this rule; no further alt sets after this.
