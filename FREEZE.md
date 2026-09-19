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

## Amendment: clock restart (written 2026-09-19 ~08:35 UTC, before any post-restart trade had settled)
The test never actually ran. The portfolio drawdown breaker tripped 2026-09-18 16:21 UTC, before the futility amendment above, and halted all trading until 2026-09-19 08:28 UTC. Zero post-freeze trades settled in that time (last fill 16:15 UTC on 09-18).
- Cause: config mismatch, not real losses. `TARGET_ASSETS=BTC,ETH,SOL` is filtered to BTC by `proven_assets`, so one engine exists and holds a ~$25 slice, but `STARTING_BALANCE_USD=75` put the floor at $56.25. The breaker sums existing engines' cash ($20.86 = $25 - the $4.14 baseline loss) and tripped immediately.
- Fix, config/state only, no code: cleared the tripped flag in `data/risk_state_drawdown.json` (original kept as `data/risk_state_drawdown.tripped_20260918.json` on the volume) and set `STARTING_BALANCE_USD=25` on Railway. Floor is now $18.75 on $25.
- The 14-day clock restarts at the 2026-09-19 08:28 UTC redeploy. New dates: freeze to 2026-10-03, max 2026-10-17. Pass rule, kill rule and futility looks are unchanged (60/100/150 post-freeze BTC trades).
- Trade counting is unaffected: no trade settled between the original freeze commit and the restart, so scripts that anchor on the FREEZE.md commit time still count exactly the post-restart trades.
- Consequence to watch: the $18.75 floor leaves about $2.11 of headroom over $20.86 cash. The breaker can trip again after a short losing run and needs a manual clear. If it does, that is the pre-registered capital-preservation rule working, and it goes in the bug-fix log.

## Amendment: schedule and pace rule (written 2026-09-19 ~13:00 UTC; 1 post-restart trade settled, a loss; nothing here depends on outcomes)
The 200-trade requirement cannot be met by 2026-10-17 at the observed pace (about 1 trade/day since the restart; the scorecard projects 2027-03). Only the calendar changes. The pass rule, kill rule and futility looks (60/100/150) above are unchanged. The sample requirement is NOT lowered.
- Hard cap replaces the 2026-10-17 max: the test ends at 200 settled post-freeze BTC trades or 2026-12-31, whichever is first. If the cap arrives with fewer than 200 trades the verdict is INCONCLUSIVE, which is treated as FAIL under the kill rule: no live money.
- Pace checkpoint, evaluated on 2026-10-17: fewer than 30 settled post-freeze BTC trades means the test is infeasible in time and the kill rule applies (archive, keep reusable pieces). This is a pure pace test, not a performance test.
- The code and parameter freeze (bug-fix-only rule) stays in force until the test ends, not just to 2026-10-03.
- `scripts/daily_scorecard.py` deadline updated to 2026-12-31 and it now prints the pace checkpoint. Analysis-only.

## Amendment: drawdown floor widened (written 2026-09-19 ~13:30 UTC; before any post-change trade settled)
Cash had fallen to $19.43 against the $18.75 floor ($0.68 headroom) after 1 post-restart trade and a -$1.37 day, so the breaker would have re-halted the test within a trade or two. `MAX_PORTFOLIO_DRAWDOWN_PCT` set from 0.25 to 0.5 on Railway (env only, no code): floor is now $12.50 on the $25 paper account. The breaker stays permanent and manual-clear. This weakens a capital-preservation stop, deliberately, because the account is paper money and a stalled test answers nothing. It does not touch trading, sizing or filters, and it changes no pass, kill or futility criterion.
- Trades taken while cash is between $12.50 and $18.75 count normally.
- If the $12.50 floor trips, it is treated as a kill-rule trigger for the test, not a reason to widen again.

## Known open items (not fixed, by design)
- `daily_pnl` and the drawdown breakers use payout - cost without the buy fee, so they run slightly optimistic. Fixing it changes live risk behavior, so it waits until after the test.
- Paper fills assume the quoted ask. Real order-book depth is not logged at signal time.
  (Correction 2026-09-18: paper fills are priced at the walked VWAP after a simulated latency re-fetch of the book, `bot.py` ~L1060-1073. What was not logged was the ladder itself; see bug-fix log.)

## Bug-fix log
- 2026-09-18: added observation-only `_log_book_depth` in `src/bot.py`. Appends the top-5 ask ladder (before and after the simulated latency re-fetch) to `data/book_depth_log.jsonl` at signal time. Best-effort, never raises, no input to any decision, sizing or filter. Tests in `tests/test_book_depth_log.py`. Also: `scripts/random_baseline.py` now takes the freeze start from the commit that added FREEZE.md, so editing this log does not move it; added `scripts/daily_scorecard.py`.
- 2026-09-19: added observation-only `_log_tick` in `src/bot.py`. Appends one row per tick (spot, strike, top-of-book yes/no bid/ask) to `data/tick_log.jsonl` right after `log_observation`, for offline spot-vs-book lead-lag analysis. Best-effort, never raises, no input to any decision, sizing or filter. Tests in `tests/test_tick_log.py`.
- 2026-09-19: `scripts/random_baseline.py` `load_fills` now accepts raw jsonl as well as a saved API response (analysis script, no trading effect). Added `scripts/offline_replay.py` (analysis only).
- 2026-09-19: added alert-only `_halt_reminder` in `src/bot.py`: while a portfolio breaker is tripped, re-sends a "STILL HALTED" notifier alert every 3600s (first one 1h after start/trip). Never changes breaker state or trading. Tests in `tests/test_halt_reminder.py`. Correction to the amendment above: the one-shot trip alert did exist (`notifier.alert` on trip); it was never repeated. Also noted, not fixed: Railway has `DISCORD_WEBHOOK` but the code reads `DISCORD_WEBHOOK_URL`, so Discord alerts never send (Telegram is configured).
- 2026-09-19: checked `book_depth_skew` live-input change (`9c4edc9`, 2026-09-18 15:09 +0200) against the freeze commit (`bd8b2d2`, 18:46 +0200): it predates the freeze, so it is part of the frozen baseline.
- 2026-09-19: launched an observation-only L2 order-book/trade collector (`scripts/collect_l2.py`, helpers in `src/mm/`) as an isolated child process started from `railway_entrypoint.py` (kill switch: `RUN_L2_COLLECTOR=0`). It has its own websocket, writes only `data/l2/*` (gzip, hourly rotation, 120MB cap), and shares no state with the bot. No trading, sizing or filter change. Also added analysis-only `scripts/arb_scan.py` and `scripts/mm_replay.py` (maker-replay with a conservative fill model) for the maker/parity research plan. Tests: `tests/test_mm.py`, `test_collect_l2.py`, `test_mm_replay.py`.
- 2026-09-19 ~13:30 UTC: set `MAX_PORTFOLIO_DRAWDOWN_PCT=0.5` on Railway (floor $18.75 -> $12.50). Env only; see the floor amendment above.
- 2026-09-19 08:28 UTC: cleared the tripped drawdown breaker and set `STARTING_BALANCE_USD=25` on Railway. See the clock-restart amendment above. Config/state change, not a code change.
