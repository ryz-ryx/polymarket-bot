# Finding a Real Edge in BTC 5-Minute Markets — Research Plan

## 1. Where things actually stand

The bot already has a full quantitative pricing model (`ClaudQuantBinaryOptionStrategy`): a Black-Scholes-style z-score against the 5-minute strike, a TWAP/Asian-option variance adjustment for Polymarket's trailing-60-second settlement window, a drift term blending order-flow imbalance (weight 0.12), momentum (0.08), and contract-book imbalance (0.08), and a fee-aware entry hurdle (Polymarket's real taker fee is `0.07 * (1 - price)` per side). On top of that sits an empirical calibrator that corrects the model's raw probability against realized outcomes (logistic scaling under 300 settled windows, nonparametric isotonic regression above it).

None of that is broken. It is also, as of every audit run this session, not beating the market:

| Audit | Resolved windows | Model Brier | Market Brier | Model better? |
|---|---|---|---|---|
| Full-history | 286 | — | — | net −$41.47, profit factor 0.960 (98 trades, fee-adjusted) |
| Session check 1 | 57 (7d rolling) | 0.1804 | 0.1455 | No |
| Session check 2 | 77 (7d rolling) | 0.1726 | 0.1553 | No |
| Session check 3 | 90 (7d rolling) | 0.1641 | 0.1464 | No |

A lower Brier score is better. In every measurement, the model's probability estimate has been *worse* than simply using Polymarket's quoted price as the probability. Separately, an ad-hoc correlation check between the logged edge (`model_probability − market_price`) and actual trade outcome came back at r = −0.105 and flipped sign under simulated threshold changes — i.e., at the current sample size, the "edge" the strategy logs has no reliable relationship with whether the trade wins.

The funnel data reinforces this: over a 4-hour window, the strategy sees on the order of 13,000 ticks and executes 2–14 trades. It is already extremely selective. The problem isn't over-trading — it's that the small number of trades it does make aren't demonstrably better than chance.

## 2. Why this is probably happening

**No informational asymmetry.** The model's inputs — Binance spot price, Deribit DVOL, order-flow imbalance from the visible book — are the same public, cheap-to-replicate signals any market maker quoting the Polymarket book can and likely does use. Reproducing a Black-Scholes fair value from public inputs doesn't create an edge against a market maker doing the same thing, possibly with lower latency.

**Hand-set weights.** The OFI/momentum/CBI drift weights (0.12/0.08/0.08) and the entry filters (`min_abs_z=0.40`, `min_strike_distance_pct=0.0003`, `min_edge=0.03`) are constants chosen a priori, not fit against outcome data. There's no evidence yet that these particular numbers are load-bearing rather than arbitrary.

**Sample size.** 60–100 resolved windows is a small sample for a roughly 50/50 binary outcome. A back-of-envelope estimate: the standard error of a Brier score estimated from n binary trials scales like `sqrt(Var/n)` with `Var ≈ 0.25` for a coin-flip-like outcome — reliably distinguishing a 0.01–0.02 Brier gap from noise needs on the order of several hundred to low thousands of independent windows, not dozens. The 300-window isotonic threshold exists for exactly this reason, and BTC hasn't cleared it yet.

**Fee drag raises the bar a lot.** A 7%-of-price taker fee on a 5-minute binary option is large relative to the size of mispricing you'd expect to find in an actively-quoted, short-dated market. The model has to be right by more than the fee-implied hurdle just to break even, which is a materially higher bar than "the model's probability differs from the market's."

## 3. The actual plan

### Phase A — Keep collecting, but log more than we trade on

No strategy changes yet. Let BTC-only trading continue accumulating toward 300 distinct settled windows (already faster now that all ticks go to one asset). In parallel, add shadow logging of a wider feature set on every tick — not just the ones that produce a trade — so new hypotheses can be tested against real history without waiting weeks for enough live trades:

- Polymarket book mid-price vs. Binance spot price, tick by tick, to test lead-lag.
- Order-book depth imbalance at multiple price levels (not just top-of-book CBI).
- Realized vol vs. Deribit DVOL divergence.
- Time-bucketed moneyness (distance from strike) vs. realized outcome, independent of the current model's z-score.

### Phase B — Hypothesis-driven search for a real, mechanical edge

Test specific, falsifiable ideas against the logged data — not "does the existing model do better if I nudge a constant."

1. **Lead-lag mispricing.** Does Polymarket's quoted price take a few seconds to catch up after a Binance spot move? If the book reprices with measurable lag, that's a mechanical edge independent of any pricing model — cross-correlate spot returns against book mid-price returns at various lags (0–10s) and look for a consistent, positive lag.
2. **TWAP-averaging mispricing.** Settlement is a trailing-60-second average, not the instantaneous spot price at expiry. Retail-style intuition tends to bet on "will spot be above strike," not on "will the average of the last 60 seconds be above strike." Check whether the market has a persistent directional bias in the last 30–60 seconds of a window relative to what the *TWAP* (not the spot) implies.
3. **Re-fit the edge/hurdle relationship properly.** Instead of a hand-picked `min_edge=0.03`, bucket the actual historical `(model_prob − market_price)` against realized outcome and see if *any* threshold, fit on one half of the data, holds up on the other half.
4. **Leave ETH/SOL out.** Don't re-add them until BTC shows a validated edge in isolation — adding assets back now just dilutes the sample further.

### Phase C — Validate before deploying anything

Any change coming out of Phase B needs walk-forward validation, not an in-sample fit: fit on the first half of the historical window, test on the untouched second half. Run it in **shadow mode** — logging what the new logic would have done, in parallel with the live model, without executing trades on it — for a window count comparable to Phase A, before it ever touches live paper trading.

### Phase D — Go-live gate (unchanged, restated)

The existing gate in `analyze_calibration.py` already requires ≥30 settled windows and a net-of-fee profit factor > 1.20 before considering real capital. Add two conditions on top, given what Phase A–C establish: isotonic calibration must be active (≥300 windows), and the model's Brier score must beat the market's **out of sample**, not just in the fit period.

## 4. What not to do right now

- Don't hand-tune `min_edge` or the hurdle further based on the current 60–100 trade sample — already shown to be fitting noise (r = −0.105, sign-flipping under simulated thresholds).
- Don't increase position size or the Kelly fraction while the model's Brier score is still worse than the market's.
- Don't re-add ETH/SOL before BTC has a validated edge.

## 5. Concrete next actions

1. Add a lightweight shadow logger for spot-vs-book lead-lag and TWAP-bias data (Phase A), running continuously, no trading-logic changes.
2. Extend `analyze_calibration.py` (or a new script) to support a walk-forward split for any newly proposed feature or threshold, so nothing gets deployed on an in-sample fit again.
3. Checkpoint: re-run the full calibration/profitability audit once BTC crosses 300 distinct settled windows — that's the first point where the isotonic calibrator is live and Brier comparisons stop being noise-dominated.
