# Cumulative test ledger

One shared alpha budget across every statistical test ever run against this account's strategies, crypto or
Polymarket, backtest or live. The council's point (all 3 advisors on multi-bot architecture, 2026-09-23): the
family-wise error rate does not reset when a new strategy or batch is added, so the correction denominator is the
running total below, not just the tests in the newest batch. A future "win" only counts if its p-value clears
alpha / (total tests at the time it is evaluated), not alpha / (its own batch size).

alpha = 0.05 (two-sided), matching every pre-registration's own Bonferroni sections.

| Test | Doc | Sub-tests | Result | Cumulative total after |
|---|---|---|---|---|
| Polymarket (11 tests, pre-repo-convention) | (see project memory) | 11 | 11 KILL | 11 |
| T trend | docs/PREREG_trend_spot.md | 1 | KILL | 12 |
| F fast rules | docs/PREREG_fast_intraday.md | 6 | KILL | 18 |
| O order-flow | docs/PREREG_orderflow.md | 12 | KILL | 30 |
| X cross-sectional | docs/PREREG_xs_crypto.md | 4 | KILL | 34 |
| V vol regime | docs/PREREG_vol_regime.md | 6 | KILL | 40 |
| L lead-lag (3 alts) | docs/PREREG_leadlag.md | 6 | KILL | 46 |
| M maker sim | docs/PREREG_maker_sim.md | 0 (explicitly exploratory, no PASS/KILL gate, excluded from this budget by its own doc) | n/a | 46 |
| P pairs spread reversion | docs/PREREG_pairs.md | 2 | KILL | 48 |
| L2 lead-lag wide (10 more alts) | docs/PREREG_leadlag_wide.md | 20 | KILL (all 10) | 68 |
| S intraday seasonality | docs/PREREG_seasonality.md | 93 (31 buckets x 3 symbols, Bonferroni within-test; cumulative budget binds) | pending | 161 |
| W vol-timed breakout | docs/PREREG_vol_timing.md | 3 (3 symbols, Bonferroni within-test; cumulative budget binds) | pending | 164 |
| A rolling parameter self-adaptation | docs/PREREG_adaptive.md | 1 (holdout-only, adaptive vs frozen) | KILL (degenerate on dev, holdout never opened) | 165 |

**Current alpha budget: 0.05 / 165 = 0.000303 (two-sided) for any test evaluated right now (includes S, W and A's
own sub-test counts, pre-registered before any was run, per the rule below).**
Once L2 and P run, the denominator updates to include them from the moment they're evaluated, and every prior
KILL stays exactly as reported (this ledger doesn't retroactively change past results, only the threshold future
ones must clear).

## Rule
Before reporting any new strategy as PASS, divide alpha by the total row above at the time of that evaluation, not
by the new batch alone. Add every new test to this table, pre-registered, before running it.
