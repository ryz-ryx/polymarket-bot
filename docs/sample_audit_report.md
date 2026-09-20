# Sample audit report: "Polymarket 5-minute BTC bot" (own project)

Purpose: show what a paid audit delivers. Subject: my own bot, which first looked profitable (+$30 paper over 49 trades).

## Claim audited
"The bot turns a small balance into profit on 5-minute BTC up/down markets."

## Findings (one row per check)

| Check | Result | Evidence |
|---|---|---|
| Ledger integrity | PASS | Arithmetic and balances reconcile; 19 of 19 settled outcomes match an independent price source |
| Concentration | FAIL | One win at 25c is +$22.9 of the +$30; the other 48 trades net about zero |
| Settlement rule | FAIL (fixed in research) | Markets settle on a 60-second time-weighted average at both ends; a point-strike convention reproduced 90.2% of outcomes vs 96.6% for the correct rule |
| Costs | FAIL | Taker fee about 1.75c/share at 50c; a 2-second delay or 3c slippage removed the only edge that looked real |
| Statistics | FAIL | Best holdout: 15 trades, CI [-9.5%, +140%] includes zero; sealed holdout, cluster bootstrap and multiple-comparison correction applied to all tests |
| Sample size | INSUFFICIENT | Power calculation: 94 trades needed at +0.30/$ true edge, 841 at +0.10/$; the available sample was smaller |

## Verdict
No evidence of edge. The paper profit was one lucky trade. Fixing the settlement error brought the model to parity with the market, not above it.

## What a paid audit delivers
- This table for the client's strategy, with exact numbers and scripts to rerun each check.
- A pre-registered test plan for anything left open, including the sample size needed before a verdict is possible.
- A plain statement of what was NOT checked (venue-specific legal, tax and execution risk).

## Limits
Independent analysis of the material provided; no trading advice, no guarantee of future results, and no access to keys or funds.
