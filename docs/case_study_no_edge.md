# How I falsified my own trading bot: a pre-registered case study

A short-horizon crypto bot on a prediction market looked promising on paper. I tested it the way most retail bot
sellers do not: every hypothesis was written down with its kill rule before the data was touched, the last 40% of
the data was sealed and opened once, confidence intervals were clustered by market window, and multiple-comparison
corrections were applied. Nothing survived. This is the record, published because an honest "no edge" is more useful
than a backtest that only looks good.

Nothing here is trading or investment advice. No live trading was done; the bot ran only as a paper test.

## The setup

- Market: 5-minute "BTC up or down" markets on Polymarket. Taker fee about `0.07 * p * (1 - p)` per share, roughly
  1.75c per share at a 50c price.
- Data: 45,667 historical market windows, a 2.86M-trade tape over 672 sampled windows, and a live order-book
  recorder (Polymarket book, plus Binance, Coinbase and Kraken top-of-book quotes on one receive clock).
- Method: 60/40 chronological split, sealed holdout run once, cluster bootstrap by window, Bonferroni-corrected
  intervals (about z = 2.5), a public test ledger, and a whole-project kill rule.

## Results

| Idea | Result | Evidence |
|---|---|---|
| Directional taker model beats the market price | Killed | Out-of-sample Brier CI includes zero |
| Mispriced probability buckets | Killed | Market is calibrated in every bucket over 45,667 windows; no bucket beats the fee |
| Skilled-wallet persistence | Killed | Holdout rank correlation 0.04 against a 0.10 bar |
| Market making, optimistic upper bound | Killed | 3,320 fills, mean 30s markout -1.06c/share |
| Market making, live replay with queue model | Killed | About -4c/share over 1,140 simulated fills; 58% adverse |
| YES+NO < 1 arbitrage | Killed | Gaps last under 0.5s; artifacts of books updating separately |
| Model with corrected strike vs the market | Killed | Holdout Brier: market 0.0905, model 0.0889; blend minus market CI [-0.0019, +0.0003] |
| Lead-lag on the trade tape | Passed but fragile | Dies at 3c slippage or 4s delay |
| Lead-lag on executable order-book prices | Killed | n=126 at 2s delay, mean -1.1%/$, -5.5%/$ with 1c slippage; needed n >= 200 and a CI above zero |
| Taker entry one tick late, exit after 10s | Killed | n=179, -9.1%/$ after fees, CI fully below zero |
| Fast 1-minute rules on BTC/ETH spot, 3 rules x 5 holds (test F) | Killed | 482k bars per coin; net -0.25%/trade at 0.24% cost, CI below zero; zero-fee gross edge -6.5 to +3.2 bps vs 12-24 bps cost |
| Cross-sectional daily rules across 20 coins (test X) | Killed | Dev 1,020 days: excess vs equal-weight -6 to -27 bps/day after 0.12%/side; holdout not opened |
| Order-flow imbalance (taker-buy volume) on BTC/ETH 1-min bars (test O) | Killed | 12 tests, dev gross -4 to +0.6 bps (no CI above zero) vs 12-24 bps cost; holdout not opened |
| Late-window locked-in TWAP (test L) | Unresolved | Needed a fresh sample that became unavailable; 15 holdout trades had a CI including zero |
| Daily SMA(50) trend on BTC/ETH spot, 0.8% fee (test T) | Killed | Holdout 1,328 days: pooled alpha +8.4%/yr, CI [-15.4%, +33.7%]; BTC Sharpe 0.80 vs buy-and-hold 0.98 |

## What I learned

1. **The market's own settlement rule mattered more than the model.** The markets settle on a 60-second time-weighted
   average of a Chainlink stream at both ends. Using that rule reproduced 96.6% of real outcomes from Binance data;
   my bot's point-price strike reproduced only 90.2%. My earlier "the model is worse than the market" finding was
   mostly this bug. Fixing it brought the model to parity with the market, not above it.
2. **One lucky trade can fake an edge.** The paper ledger showed +$30 over 49 trades. A single win at 25c produced
   +$22.9 of it; the other trades netted about zero. The 19 settled outcomes I spot-checked matched an independent
   price source, so the ledger was honest; it just did not show skill.
3. **A pre-registered kill rule protects you from yourself.** Several results looked encouraging until costs were
   applied (slippage, fees, queue position, 2-second delays). Because the rules were fixed in advance, none of them
   could be quietly redefined into a pass.
4. **Power analysis belongs before the run.** For the last open idea I computed, before looking, how many trades were
   needed to clear the confidence interval (about 94 trades if the true edge were +0.30/$, 841 if +0.10/$) and
   that the sample would likely fall short. A "no evidence" result is not a "maybe".
5. **Practical blocks are part of the result.** The venue is geo-blocked from the hosting region and court-blocked
   in the trader's country, so even a passing test would have been research only.

## Reusable pieces

- Resolution reconstruction from a public price feed (`src/resolution.py`)
- Order-book recorder with local book rebuild and compact snapshots (`scripts/collect_l2.py`)
- Conservative maker-fill replay with a queue model (`src/mm/`, `scripts/mm_replay.py`)
- Health check for recorded data (gaps, feeds, sizes) (`scripts/health_check.py`)
- Run-once harness that refuses to evaluate a sample twice (`scripts/run_test_l_once.py`)

## Limits of this study

One venue, one asset, a few weeks of data, and a small trade count for the paper run. It does not show that no
profitable strategy exists anywhere. It shows that the ones tested here do not beat costs.
