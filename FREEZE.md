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

## Known open items (not fixed, by design)
- `daily_pnl` and the drawdown breakers use payout - cost without the buy fee, so they run slightly optimistic. Fixing it changes live risk behavior, so it waits until after the test.
- Paper fills assume the quoted ask. Real order-book depth is not logged at signal time.

## Bug-fix log
(none yet)
