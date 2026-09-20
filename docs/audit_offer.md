# Trading-bot and backtest audit (offer draft)

Status: DRAFT for the owner to edit. Fill in the bracketed items; do not publish claims you cannot back up.

## The one-line offer

I check whether your trading bot's or backtest's edge is real, and I tell you plainly if it is not, before you
put money behind it.

## Who it is for

Solo traders, small crypto teams and bot sellers who have a backtest, a paper-trading record or a live track record
and want an independent, evidence-based read on it.

## What I check

1. **Look-ahead and leakage:** does anything in the signal use information from after the decision time?
2. **Settlement and data rules:** does the backtest use the venue's real settlement and reference-price rule?
   (In my own project this single mismatch explained a false result: 96.6% vs 90.2% outcome agreement.)
3. **Costs:** fees, slippage, queue position for resting orders, and execution delay (a 2-second delay killed an edge
   that looked real).
4. **Concentration:** how much of the profit comes from one or two trades? (In my project one trade was almost all of
   a +$30 paper result.)
5. **Statistics:** sealed holdout, cluster-robust intervals, multiple-comparison correction, and whether the sample is
   large enough to conclude anything at all (a power calculation before the run).
6. **Verdict:** a short written report with pass, fail or insufficient-evidence for each claim and the exact numbers.

## What you get

- A written report (about 3 to 6 pages) with a clear verdict per claim.
- The scripts used, so you can rerun the checks.
- One follow-up call or message thread to walk through it.

## What I do not do

- I do not give trading or investment advice, or tell you what to buy or sell.
- I do not trade your money or ask for your keys, seed phrases or passwords.
- I do not promise your strategy is profitable. Often the honest result is "no edge found".

## Proof of work

Public case study: `docs/case_study_no_edge.md`, a full pre-registered falsification of my own bot, including the
results that disappointed me.

## Pricing (owner to decide; the numbers below are placeholders, not market research)

- Quick review of one backtest or paper record: [price]
- Full audit with report and rerunnable scripts: [price]
Compare a few similar freelance listings before choosing.

## Contact

[name] · [email] · [profile or repository link]

## Before you take a paying client (owner's checklist)

- Check how freelance income must be registered and taxed where you live (in Denmark this usually means checking
  business registration and reporting requirements with the tax authority).
- Keep clients' data and code confidential and delete it when the job ends.
- Put scope, price and deliverables in writing before starting.
