# Backtest verification — the numbers are real, the conclusion isn't

Staged `backtest.py` + all 3 raw CSVs directly and recomputed independently.

## What checks out
The reported numbers match the raw CSVs exactly — n=17,274 per asset, Brier 0.1591/0.1604/0.1568, no misreporting this time. No outright lookahead: `strike_k` = window-open price, `close_price` (window-close, the true future outcome) is used only for the label, never fed into the probability calc.

## What doesn't check out: the comparison is the wrong test

**"+36% improvement vs 50/50" is not evidence of trading edge.** `mid_spot` (fed into the model as "current price") is the close of the *3rd minute* of a 5-minute window — i.e., 60% of the window's own price path has already played out by the time the model makes its "prediction." Of course that beats a static coin flip. Proof — I fed the dumbest possible non-model, just a hard 0/1 guess on which side of strike `mid_spot` already sits, no vol, no z-score, no calibration at all:

| Asset | Naive hard-guess Brier | Full model Brier | 50/50 Brier | Naive's own "improvement" |
|---|---|---|---|---|
| BTC | 0.2178 | 0.1591 | 0.2500 | **12.9%** |
| ETH | 0.2067 | 0.1604 | 0.2500 | **17.3%** |
| SOL | 0.2077 | 0.1568 | 0.2500 | **16.9%** |

A model with zero sophistication clears a big chunk of that "50/50 baseline" bar just by looking at where price already is. The right test for trading edge is model vs. **Polymarket's actual live market price at that same moment** — which this backtest can't do, because there's no historical Polymarket order-book data to replay (exactly the limitation I flagged when speccing this). This backtest can only show the pricing formula isn't broken, which was never in doubt — it can't show it beats the market.

**Momentum isn't doing anything — the "momentum continuation" narrative isn't supported by this data.** I recomputed each asset's probability using only the vol/moneyness term (z_base), dropping momentum entirely, and compared to the full model:

| Asset | Brier (z_base only) | Brier (full, w/ momentum) | Momentum's contribution |
|---|---|---|---|
| BTC | 0.1596 | 0.1591 | 0.19 percentage points of the 36.37% total |
| ETH | 0.1611 | 0.1604 | 0.28 pp of 35.83% |
| SOL | 0.1574 | 0.1568 | 0.24 pp of 37.28% |

Essentially all of the reported edge is the vol/moneyness term alone. Momentum contributes almost nothing here, despite the summary calling out "momentum continuation in 5-minute crypto windows is stronger than standard Brownian motion implies" as a key takeaway.

**The claim that this "explains why the live bot's selective high-conviction entries produce high win rates" is directly contradicted by the bot's own real trade history** — already computed from your actual `fills_log*.jsonl`/`trade_events*.csv` last round: real executed-trade win rates are 46.7% (BTC), 38.5% (ETH), 43.0% (SOL) — at or below breakeven, not "high." This backtest doesn't and can't speak to that claim one way or the other; it shouldn't have been stated as if it does.

**Minor implementation drift from spec:** `mid_spot` is read at minute-index 2's close (~180s elapsed) while `tau_seconds=150.0` is hardcoded (implying 150s elapsed) — a ~30s mismatch between the labeled decision time and the data actually used. Also, momentum normalization in `backtest.py` (`mom_dollar / (mid_spot * 0.001)`) doesn't match production's normalization in `claud_quant.py`'s `evaluate()` (`momentum / 50.0`) — the spec asked to call production code directly to avoid exactly this kind of drift. Doesn't change the conclusion above (momentum's contribution is negligible either way) but worth fixing if this script gets reused.

## Bottom line
This backtest is real data, honestly computed, but answers a different question than "does the bot have edge over the market." It shows the Black-Scholes/Student-t formula isn't nonsensical — which was never in doubt — not that it beats Polymarket's actual prices. We still don't have a real answer to that question, because we still don't have historical Polymarket order-book data. Nothing here changes the live-data findings from last round (executed trades sub-50% win rate, all three assets net-negative in the current regime) — those remain the best real signal we have.

**No code action from this.** Don't message Gemini to "fix" the backtest reporting unless you want the record corrected — the live bot's actual logic (Phase 1 changes) isn't affected by any of this, since `backtest.py` is a standalone analysis script, not part of the trading path.
