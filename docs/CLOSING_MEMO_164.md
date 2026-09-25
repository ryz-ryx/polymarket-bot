# Closing memo: 164 pre-registered tests, 164 KILL (2026-09-23)

Written to stop this project from silently restarting the search at "test #165" without a written reason. If
you're reading this before adding a new test, read the Stop Rule at the bottom first.

## The number
164 pre-registered statistical tests run against this account's strategies (crypto backtest + Polymarket, spanning
trend, order-flow, cross-sectional, vol regime, lead-lag in three separate forms, pairs, maker-sim, funding carry,
plus the two most recently pre-registered: intraday seasonality and vol-timed breakout, both left `pending` in
docs/TEST_LEDGER.md since their data pipeline stalled and was stopped, not run). Every evaluated test: KILL.
Current cumulative alpha budget: 0.05 / 164 = 0.00030 (two-sided) for any test evaluated from this point forward.

Full ledger: docs/TEST_LEDGER.md. Full narrative of the first ~15 tests: docs/case_study_no_edge.md.

## The backtest artifact (why it doesn't change the answer)
On 2026-09-23, `walk_forward.py` was run against the live BTC paper bot's frozen parameters on real historical
Polymarket windows. It showed 67-72% win rate and +29-35% profit rate out-of-sample, CI clearly above zero --
a result that would look like a clear PASS.

It isn't one. Diagnosis:
- The historical dataset has no order-book depth, only one ask price per window (`yes_price_tau120`). The live
  bot's `BLOCKED_PHANTOM` / `BLOCKED_HURDLE` / `BLOCKED_LATE_WINDOW` rejection paths (`src/bot.py` ~L1099-1174)
  depend on real-time book depth and spread this dataset cannot supply, so the backtest cannot reproduce
  situations where the live bot correctly refuses to trade.
- `ofi_normalized` and `cbi` are hardcoded to 0.0 (no historical order-flow feed exists), which may loosen the
  strategy's real admission checks relative to live.
- Applying the strategy's own `slippage_buffer` to the fill price (not just the admission hurdle) only moved the
  headline number from +34.54% to +29.23% -- confirming the gap is structural (missing depth/rejection modelling),
  not primarily fill-price optimism.
- The live frozen paper bot, same parameters, real constraints: 9 trades, near breakeven, trailing buy-and-hold.

This is the same failure shape documented in docs/case_study_no_edge.md under "One lucky trade can fake an edge"
and the look-ahead bug that inflated `replay_real_market.py`'s win rate to ~82% versus 40-46% live. A backtest
that looks good because it's missing a real-world friction the live system has is not evidence of edge. The
limitation is now recorded permanently in `walk_forward.py`'s own docstring so this can't be re-cited by accident.

## What this means
The honest posterior on "does simple, retail-accessible statistical edge exist in these markets after real fees,
at this frequency, with this infrastructure" is: no. 164 independently pre-registered attempts, spanning most of
the strategy families a solo retail operator can realistically build and test, found nothing that survives
multiple-comparison correction. That is itself a complete, useful, and expensive-to-produce answer -- not a
queue of failures on the way to a different one.

## Stop rule (read this before test #165)
No new statistical test may be pre-registered and run against this project's strategies without first writing,
in this file or a successor to it, an explicit answer to:

1. **Why isn't this already answered by one of the 164 tests above?** Name the specific test(s) it's genuinely
   distinct from, not just a re-parameterization.
2. **What is the alpha bar it must clear at the time it's evaluated?** (0.05 / running ledger total, per
   docs/TEST_LEDGER.md's existing rule -- this only gets stricter, never resets.)
3. **What would make you stop looking?** If the honest answer to this question keeps changing to accommodate the
   next idea, that's the sunk-cost pattern this rule exists to catch, not a reason to loosen the bar.

Absent a written answer to all three, any new test result does not count as evidence for this project, regardless
of what it shows.
