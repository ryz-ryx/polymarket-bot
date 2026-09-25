# Pre-registration: rolling parameter self-adaptation (test A)

Written 2026-09-25, before any of this test's evaluation has been run. Paper/backtest only, no trading
advice. Runs entirely separate from the live frozen BTC paper bot (docs/FREEZE.md) -- this test never
touches that bot's parameters, state, or trade count. Reuses the same real-market historical data and
harness as walk_forward.py (data/real_market_history_btc_extended.jsonl + data/cache/BTCUSDT_1m_1095d.csv,
already downloaded, no new fetch needed).

## Why this isn't already answered by one of the 164 killed tests
Every prior test evaluated a FIXED rule (or a fixed set of candidate rules chosen once, e.g. test F's
grid) against historical data. None of the 164 asked whether a rule that CHANGES ITS OWN THRESHOLDS over
time, based only on its own trailing realized performance, does better than the best fixed rule. That is
a genuinely different mechanism (an adaptive control loop, not a static filter), not a re-parameterization
of anything already tested. `walk_forward.py`'s own FIT-ON-TRAIN arm is the closest prior art, but it
picks ONE fixed parameter set from a grid once, using the full train split; it does not adapt within the
test window trade by trade.

## What would make this a false lead worth abandoning
If the adaptive variant's holdout result does not beat the frozen baseline's holdout result (same data,
same costs, both already computed in walk_forward.py) by more than the width of both their confidence
intervals combined, that is a clean "no benefit from adaptation" answer and this direction stops --
no retrying with a different adaptation formula, no widening the parameter bounds. One shot.

## The adaptation rule (fully mechanical, fixed before any data is touched)
Starting from the frozen baseline params (`FROZEN_BTC` in walk_forward.py), after every 20 resolved
trades (rolling, non-overlapping windows), recompute two parameters from ONLY the trailing 20 trades:

- `min_edge`: if trailing win rate < required breakeven win rate (implied by mean entry price) - 0.03,
  increase `min_edge` by 0.005, capped at 0.05. If trailing win rate > breakeven + 0.05, decrease
  `min_edge` by 0.005, floored at 0.02.
- `min_abs_z`: if trailing win rate < breakeven - 0.03, increase by 0.05, capped at 0.90. If trailing
  win rate > breakeven + 0.05, decrease by 0.05, floored at 0.25.

All other parameters (slippage_buffer, cbi_drift_weight, min_strike_distance_pct, tail_dof,
min_entry_price, max_entry_price) stay at `FROZEN_BTC`'s values, unchanged, for the entire run. The
first 20 trades of any run use the unmodified frozen params (no adaptation possible before the first
window closes). This is the ONLY adaptation rule evaluated -- no grid, no alternatives, no tuning the
rule itself on this data.

## Split
Same chronological 70% train / 30% test split as `walk_forward.py --asset BTC` (train to fit nothing --
the rule has no free parameters left to fit; train is used only to sanity-check the rule doesn't
misbehave, e.g. drift to a degenerate all-blocked state, before the holdout is touched).

## Statistics
One test (adaptive vs frozen, holdout only). Per docs/TEST_LEDGER.md, cumulative-budget clearance
(0.05 / running ledger total at evaluation time, which will be 0.05/165 = 0.000303 once this row is
added) is the binding threshold. 95% cluster-bootstrap-by-day CI on the holdout, 5,000 resamples, same
method as walk_forward.py's `summarize()`. Both the frozen and adaptive arms run with `slip=True`
(realistic fill, matching the standard this project settled on after the fake-backtest incident in
docs/CLOSING_MEMO_164.md) -- the `slip=False` optimistic number is reported for reference only, never
for the PASS/KILL decision.

## PASS
Holdout mean net profit rate for the adaptive rule has a 95% CI entirely above zero, AND entirely above
the frozen baseline's holdout point estimate (not just "not worse" -- must show a real edge over the
static rule, since the whole point is to justify the added complexity), AND the adaptive rule did not
degenerate (no window where both parameters sit at their bound for more than 3 consecutive windows,
which would indicate runaway drift rather than genuine adaptation).

## KILL
Otherwise, including a degenerate run. If KILL: this closes the "should the bot adapt its own
parameters" question for this strategy family, same as every other closed question in
docs/CLOSING_MEMO_164.md. No live deployment of any adaptive variant follows from a KILL here under any
circumstance -- this test existing at all does not pre-authorize touching the frozen live bot.

## Result (2026-09-25)
Dev (train) split, n=31,966 windows, slip=True: the adaptive rule fired 981 trades, win rate 62.3%,
profit rate +30.57% (95% CI +23.56%..+37.51%) -- already below the frozen baseline's train profit rate
of +43.24% (95% CI +33.80%..+52.43%) on the same split. More decisively: the adaptation rule spent 36
CONSECUTIVE 20-trade windows with both `min_edge` and `min_abs_z` pinned at their upper bounds (0.05 and
0.90), far past the 3-window degenerate threshold set in this doc before any data was touched. This is
not adaptation -- it is the rule ratcheting to its ceiling once and never coming back, which defeats the
premise of a responsive control loop.

Per this doc's own pre-registered rule ("if the calibration check fails... treated as KILL not
inconclusive"), the holdout split was never opened. Output: `scripts/adaptive_backtest.py --asset BTC`
(no `--holdout` flag used).

**Decision gate: degenerate on dev (36 > 3 consecutive at-bound windows). KILL.**

Conclusion: this specific mechanical adaptation rule (trailing-20-trade win-rate-vs-breakeven threshold
nudge) does not produce stable, responsive parameter adaptation on this data -- it collapses to its most
conservative setting and stays there. Per the doc's own scope, this closes the question for this rule;
no retrying with a different formula or wider bounds. Added to docs/TEST_LEDGER.md as KILL.

## Explicitly out of scope
This test evaluates the adaptation MECHANISM on historical data. It is not a proposal to let the live
bot in docs/FREEZE.md adapt its own parameters -- that would require its own separate pre-registration,
a new freeze period, and would only be considered at all if this test PASSes, which per the project's
own base rate (0/164 so far, plus tests S/W still pending) is not the way to bet.
