# Frozen forward test — pre-registered rule

Written 2026-09-18, before any of the test data exists. Do not edit after the freeze starts.

## Question
Does the live BTC paper strategy have a positive edge after real fees? Nothing else is being tested.

## Freeze
- Starts at the commit that adds this file (see `git log -- FREEZE.md`). Only trades settled after that deploy count.
- Code and parameters are frozen for 14 days (to 2026-10-02). Only bug fixes that do not change trading or sizing behavior are allowed. Each one goes in the log below.
- BTC only. ETH and SOL stay off.
- Paper mode only. No live money before the rule below passes.

## Pass rule (all must hold on 2026-10-02 or once 200 trades are reached, whichever is later, max 2026-10-16)
1. At least 200 settled BTC trades after the freeze.
2. Mean net PnL per trade is positive, where net = payout - cost - BUY fee (`/api/edge_validation` now reports this).
3. 95% bootstrap lower bound of the mean net PnL per $ staked is above zero.
4. The bot beats a random-side baseline run over the same windows, entry prices and sizes.

## Kill rule
If any check fails at the deadline: stop, archive the repo, keep the redemption, monitoring and risk-breaker code as reusable pieces.

## Before this test
- Baseline at the freeze: 28 settled trades, 12 wins, gross -$2.74, fees $1.40, net -$4.14 (-10.8% of $38.22 staked). These do not count toward the 200.

## Amendment: futility stop (written 2026-09-18 ~17:45 UTC, before any post-freeze trade had settled)
Adds a way to end the test EARLY if it is clearly losing. It cannot produce an early pass, and it does not loosen any pass criterion above.
- Looks happen only at 60, 100 and 150 settled post-freeze BTC trades. No other peeking counts.
- At a look, if the 95% bootstrap UPPER bound of mean net PnL per $ staked is below 0, the kill rule applies immediately (stop, archive, keep the reusable pieces).
- If the upper bound is >= 0 at a look, the test continues unchanged toward the original pass rule.
- Reference point: at 60 trades the upper bound is only below 0 if the observed rate is roughly -20% or worse, so this stops clear losers, not merely unlucky runs.
- Checked by `scripts/daily_scorecard.py` (prints the check when n reaches a look).
- Pre-freeze evidence, for the record: 28 trades, average entry 0.485, win rate 42.9% (break-even before fees is about 48.5%), gross -$2.74, fees $1.40. Fees explain about a third of the loss; gross is negative on its own. Beats 31.6% of random-side sims; 95% CI of net PnL/staked is [-53%, +35%], i.e. uninformative at n=28.

## Known open items (not fixed, by design)
- `daily_pnl` and the drawdown breakers use payout - cost without the buy fee, so they run slightly optimistic. Fixing it changes live risk behavior, so it waits until after the test.
- Paper fills assume the quoted ask. Real order-book depth is not logged at signal time.
  (Correction 2026-09-18: paper fills are priced at the walked VWAP after a simulated latency re-fetch of the book, `bot.py` ~L1060-1073. What was not logged was the ladder itself; see bug-fix log.)

## Bug-fix log
- 2026-09-18: added observation-only `_log_book_depth` in `src/bot.py`. Appends the top-5 ask ladder (before and after the simulated latency re-fetch) to `data/book_depth_log.jsonl` at signal time. Best-effort, never raises, no input to any decision, sizing or filter. Tests in `tests/test_book_depth_log.py`. Also: `scripts/random_baseline.py` now takes the freeze start from the commit that added FREEZE.md, so editing this log does not move it; added `scripts/daily_scorecard.py`.
