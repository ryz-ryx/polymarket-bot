# Pre-registration: does resting at the best bid/ask capture the spread net of adverse selection? (test M)

Written before running the simulation, though data collection (the paper bot's tick logs) was already underway for
other purposes. Paper only, no trading advice. This is the maker-quoting idea both the Executor and Contrarian
advisors flagged as needing real bid/ask tick data (not klines), which only exists in data/paper/ticks_*.csv(.gz)
(100ms snapshots from the live websocket, logged since 2026-09-20).

## Known limitation, stated up front
We only have top-of-book bid/ask, not the trade tape or queue depth. A fill can only be proxied as "the opposite
side's price crossed through our resting price," which cannot know whether our order was actually first in the
queue. Two variants are run to bound this:
- OPTIMISTIC: fill the instant price first crosses (best case, likely overstates real fills).
- PESSIMISTIC: require price to stay through our level for >= 2 consecutive seconds before counting a fill (proxies
  queue delay; still not a real fill-probability model).
Neither variant is a substitute for real order-book/queue data. Results are a bound, not a forecast.

## Question
Post a buy limit at the current best bid (0 taker fee, standard 0.10% maker-ish assumption not even needed since
no taker fee applies); if filled, exit at the best bid again after a fixed hold of 60 seconds. Does the captured
spread beat doing nothing, once the two fill variants above are applied?

## Data
All data/paper/ticks_*.csv(.gz) accumulated so far (BTCUSDT, ETHUSDT, 100ms resolution). No new collection; this
uses what the live paper bot has already logged. No dev/holdout split (too little data for that split to mean
anything yet) - this is reported as a single descriptive run, explicitly NOT a pre-registered pass/fail test.

## Statistics
Mean P&L per attempted quote, fraction filled, mean P&L per FILLED quote, for both fill variants, per coin.
No confidence interval claimed to be decisive given the small tick history; treat as a first look only.

## No PASS/KILL gate.
This is exploratory given the known fill-model limitation above; a positive result here does not clear the bar for
going live. Any further work (queue-aware simulation, or real order-book data) would need a fresh pre-registration.

## Result (2026-09-23)
Over 127,471 ticks per coin (~3.5 hours of logged data, all that exists so far):
- OPTIMISTIC: BTC 160 completed cycles, mean -0.0138%/cycle; ETH 184 cycles, mean +0.0220%/cycle. Both near zero,
  neither clearly positive nor negative at this sample size - this is a coin flip's worth of data, not a result.
- PESSIMISTIC (2s confirm): 0 fills on both coins. No crossing survived 2 consecutive seconds in this window at all,
  which given the sub-0.04bps spreads measured earlier may mean the 2-second bar is simply too strict for how tight
  this order book is, rather than "no fills are ever real" - can't tell apart with this little data.
Conclusion: not enough data to say anything yet, and this was never the point - it confirms the mechanism runs and
gives a first look. Needs weeks more tick logging before a real read is possible; even then it stays bounded by the
known limitation above (no real queue/trade data). Output: scripts/maker_sim.py.

