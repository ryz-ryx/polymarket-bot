# Polymarket short-horizon bot: research, common problems, and implementation plan

Prepared 2026-09-19. Informational planning only: no trading, sizing, or investment recommendations. Any
execution-capable step needs its own written approval, a dedicated wallet, hard limits, a kill switch and a dry
run first.

Evidence labels used throughout: **[V]** verified by us on our own data or live systems; **[P]** primary source
(official docs, exchange, peer-reviewed or arXiv, security-firm report); **[S]** secondary (news, blog, dev post);
**[A]** anecdote or self-reported (social media, screenshots, influencer claims); **[?]** conflicting or unverified.
Primary Polymarket docs pages could not be fetched from this environment (connection refused), so fee, rebate and
rate-limit numbers come from search snippets and third-party summaries.

## 0. Risk-review output (prediction-market risk gates)

| Gate | Result | Note |
|---|---|---|
| Advice boundary | PASS | This document contains no buy/sell/size recommendations. |
| Venue and regulatory | **FAIL (blocking for any live step)** | Polymarket's geoblock endpoint returns `blocked: true` for our Railway server IP (Netherlands). Reads work; orders from a blocked IP are rejected. The user's own physical jurisdiction is unstated. VPN circumvention is prohibited by the terms. |
| Data quality | WARN | Historical outcomes span rule regimes; the resolution reference is Chainlink, not Binance (Section 1). |
| Security | WARN | No keys are in scope today. Malicious "Polymarket bot" repos are common (Section 2E). |
| Privacy | PASS | Wallet IDs in downloaded trade data are truncated to 10 hex characters and not analysed for identity. |

Blocked actions: any order placement, wallet funding, key handling, VPN/geo workaround, running third-party bot code.
Required mitigations: Phase 0 eligibility gate, dedicated hot wallet, secrets store, pinned dependencies, kill switch.
Safe next step: Phase 0 (measurement and truth-fixing only).

## 1. What we have measured ourselves [V]

- Frozen directional taker bot: 29 settled trades, 41.4% win, net -$5.57; forward test pace about 1 trade/day.
- Pre-registered offline tests on real data: model does not beat the market price out of sample (Brier CI includes
  0); the market is calibrated in every price bucket over 45,667 windows and no bucket beats the taker fee
  0.07*p*(1-p) (about 1.75c/share at 50c).
- Trade-tape tests (672 windows, 2.86M trades, sealed 40% holdout, cluster bootstrap, Bonferroni):
  wallet-skill persistence KILL (top-decile holdout PnL +$3.65/window but rank correlation 0.04 vs 0.10 bar);
  maker upper bound KILL (3,320 fills, mean 30s markout -1.06c/share, CI [-2.4c, +0.2c], optimistic no-queue);
  tape lead-lag PASS but fragile: +13.6%/$ at 2s entry delay and 1c slippage, +8.6% at 2c, gone at 3c or with
  conservative fills, negative at 4s or more; random-direction placebo -10% to -15%.
- Live executable-price confirmation of lead-lag is pre-registered (`scripts/lead_lag_l2.py`) and waits on L2 data.
- Live maker replay so far: 8 fills, all adverse. YES+NO<1 episodes exist but last under 0.5s and do not clear fees.
- Infrastructure: bot egress is Amsterdam; REST round trip to Polymarket CLOB p50 42ms / p95 60ms, Gamma p50 21ms,
  Binance REST p50 227ms / p95 654ms (Binance is the slow leg). The bot polls about every 2.3s from a book cache
  up to 20s old.
- **Settlement rule (new, decisive):** live market rules name the Chainlink `btc-usd-twap-60s` stream and state the
  market is not about spot. Over 672 windows, "60s TWAP at both ends" reproduces the actual outcome 96.6% of the time
  from Binance data (90.5% when the move was under 3 bps); 30s TWAP 94.0%; point-in-time 83.0%; our bot's convention
  (TWAP end vs point-price strike) only 90.2% (76.9% in hard cases). Conclusion: the strike/reference is a TWAP, the
  bot's strike definition is wrong for the current rule, and the earlier "9.6% Binance-vs-Chainlink disagreement" is
  mostly our own convention error (true residual about 3.4%). Several web sources claiming a 30s window for 5-minute
  markets are contradicted by this measurement.

## 2. Common problems bots like this face

### A. Economics (the fundamental problems)
1. **Taker fee vs edge.** Crypto taker fee = shares x feeRate x p x (1-p), taker-only, introduced to stop latency
   arbitrage; makers pay nothing and share a rebate pool (about 20% of crypto taker fees, daily, pro rata, $1
   minimum) [S/P-snippet]. Social and HN evidence agrees pure latency arb and directional taking mostly stopped
   working after the fee [A/S]. Consistent with our results.
2. **Adverse selection for makers.** Informed flow hits stale quotes; inventory builds one-sided; a 30-40% adverse
   move erased months of rebates in a builder post-mortem [S]. Whelan's Kalshi study (300k+ contracts) finds makers
   beat takers in aggregate and takers lose most on longshots [P]; a 588M-trade study reported via HN finds winners
   mostly post limit orders [S]. These aggregate results are not specific to 5-minute crypto and conflict mildly
   with our negative maker markout; we cannot see queue position, rebates, or rewards in historical data.
3. **Latency.** Polymarket quotes react to >=5 bp one-second Binance moves after a median 347ms (arXiv OpenMarket,
   Feb-May 2026) [P]; competitors reported under 100ms [A]; Polymarket cut its taker delay 500ms -> 250ms -> 50ms
   (Feb-Aug 2026) [P via official dev post]; CLOB is in AWS London behind Cloudflare, Binance matches in Tokyo [S].
   Our tape edge lives inside about 2 seconds.
4. **Unpaired inventory.** "Arbitrage" across YES/NO or sub-markets becomes a directional bet when one leg does not
   fill (HN arb operator: +$8.3k arb vs -$3.2k directional) [A].

### B. Settlement and oracle
5. Resolution is a Chainlink TWAP, not Binance [V]. Reported switch to TWAP on 2026-08-07 to curb last-seconds
   manipulation (Stanford/SMU arXiv 2606.31675: 821 accounts, about $8.2M, retail bore about 93% of losses) [P].
   Any backtest labelled with Binance point prices, or scored across the regime change, is contaminated. Our own
   history (672 sampled windows are all after Aug 7) needs re-labelling with the 60s/60s rule.

### C. Protocol and operations (mostly incidental, only matter once there is an edge)
6. Balance/allowance/invalid-signature errors from wrong `signature_type`/funder (EOA vs proxy vs Safe): the largest
   issue cluster in py-clob-client [P: GitHub issues #264, #277, #287, #297].
7. CLOB V2 cutover 2026-04-28: pUSD collateral (`wrap()` required), EIP-712 v2, order identity by ms timestamp, all
   V1 signing rejected, open orders wiped; `order_version_mismatch`, EOA orders rejected ("deposit wallet flow"),
   phantom balance lock after FAK fills (#342), `/positions` lag [P].
8. WebSocket silent stalls while "connected" (about 20 min in real-time-data-client #26); need liveness watchdog,
   PING every 10s, REST resnapshot after reconnect, discard prices older than about 30s [P/S].
9. Cloudflare 403/429 on order placement and key creation from datacenter/VPN IPs [P: #143, #91, poly-market-maker
   #72]; rate limits roughly 9,000/10s CLOB general, order POST burst 3,500-5,000/10s, per-signer token buckets from
   2026-07-24 (standard 40 orders/s) [S: figures conflict]; throttling is queued, not rejected.
10. Order lifecycle bugs: tick/size/precision rejections, post-only crossing, FOK/FAK/GTC semantics, partial fills,
    status frozen at MATCHED while LIVE, data API lagging matching engine (404 on fresh orders) [P/S].
11. Reconciliation: `get_balance_allowance` inconsistent, `trader_side` inverted on sells, `size_matched` ignores fee,
    fractional shares unsellable after fee, SDK redeem sends 0 USDC (redeem via CTF contract directly) [P: #245,
    #265, #294, #295, #300]. Treat Data API and on-chain state as truth.
12. Paper-to-live gap: one report shows paper +$20/min vs live -$130 over five sessions; a "522x" simulated engine
    lost 49.5% live [A]. Perfect-fill backtests overstate returns.

### D. Adversarial and manipulation
13. Ghost fills and bot-baiting: arXiv 2606.16852 "Ghosts of Polymarket" reports about 980k fills reverted on-chain,
    at least $1.49M profit to attackers, up to 24.3% of fills reverted at peak, reward farming by post-and-cancel,
    and fake-arbitrage quotes that forced a bot to dump inventory [P]. Mitigation tooling exists (ghostguard) [S].
14. Settlement manipulation via one-sided Binance orders in the final seconds (largely fixed for 15m; TWAP now)
    [P].

### E. Security and scams (the most verifiable social theme)
15. Malicious "Polymarket bot" repos and npm/PyPI packages that exfiltrate `.env` private keys: a hijacked
    verified GitHub org (StepSecurity), SafeDep fake arbitrage bot with ten npm accounts, SlowMist warnings
    (Dec 2025, Jul 2026), copy-trading bots draining wallets, one operator reportedly losing about $230k [P/S].
    Red flags: polished README plus inflated stars, "guaranteed arbitrage", instructions to paste keys or run
    `npm install`, Telegram/Discord funnels.
16. LLM-agent risks: prompt injection from fetched text; bugs in AI-written bots (a reported sign-flip) [S/A].

### F. Legal, regulatory, tax
17. International site blocks US persons (Polymarket US is a separate CFTC-regulated product with a flat 0.30% taker
    fee and -0.20% maker rebate, reported) and several countries; API and frontend rules differ (Netherlands and
    Ireland reported close-only on the frontend with the API open) but orders from blocked IPs are rejected and VPN
    use violates the terms [P/S]. **Our server IP is blocked [V].** Tax classification is unsettled; keep full
    trade-level records.

### G. Statistical and process
18. Multiple testing and backtest overfitting (Bailey and Lopez de Prado deflated Sharpe, PBO) [P]; survivorship in
    wallet leaderboards and copy trading [P/A]; high win rate can still lose money (negative skew, 95.7% win rate
    that lost live) [A]; small samples (29 trades cannot separate skill from luck).

## 3. What social media says (public content only; Discord/Telegram not readable)

| Platform group | Coverage | What it adds | Reliability |
|---|---|---|---|
| Hacker News, blogs (Reddit and StackOverflow blocked to the search tool) | partial | Fees ended many bots; arb operator P&L split; Chainlink vs Binance gap; malware bots | mixed; HN authors give first-hand data |
| X / Threads / Bluesky (snippets only) | partial | Official dev posts (speed bump 500 -> 250 -> 50ms; CLOB V2; Chainlink TWAP); profitable-bot claims with wallets; scam networks | official posts high; profit claims low |
| YouTube / TikTok / Instagram / LinkedIn / podcasts (titles and snippets only) | weak | Mass of "76-98% win rate" and "Claude built a profitable bot" content; sales funnels | mostly promotional |
| GitHub, dev.to, Medium, Substack | good | Issue trackers (real bugs), honest post-mortems (maker P&L wiped by adverse selection), SEO-spam repos | issues high; blogs mixed |

Patterns worth knowing:
- The most-cited "proof" wallet (about $313 -> $438k, 98% win rate, 15-minute markets, Dec 2025-Jan 2026) is on-chain
  and was latency arbitrage against Polymarket's lag, from before the fee and the speed-bump cuts; it gets recycled
  as if reproducible [A].
- Most retail-facing content is affiliate, referral or vendor marketing (Telegram bots, VPS providers, "best bot"
  listicles by the same author clusters). Unverifiable one-day or 20-bet samples are common.
- Honest sources (Substack post-mortems, HN authors, GitHub issues) say the same things our data says: fees dominate,
  execution and order lifecycle matter more than signal, maker inventory and adverse selection are the trap, and
  the edge is regime dependent.
- Support for our findings: fee kills directional edge (supported); maker fills adverse (supported); lead-lag edge
  short-lived (consistent; sources quote 0.3-0.5s oracle lag to a 2.7s window, none primary); Binance-vs-Chainlink
  mismatch (directionally supported; our measured rate is now explained by strike convention).
- Contradictions to keep in view: aggregate studies say makers win overall; promotional posts claim 20-50% annual
  maker ROI (backtests); "30s window for 5-minute markets" (contradicted by our measurement).

## 4. Council verdict (two councils plus five research agents)

Agreement: no edge is demonstrated; the taker model is dead; historical simulation can falsify but not validate;
survivors must be confirmed on executable prices and live paper; write the whole-project stop rule now; operational
problems are secondary until an edge exists; keep the frozen test running to its pace checkpoint; do not use
third-party bot code; verify legal eligibility before any funding.

Clashes: (1) maker/rebate/rewards structure (Expansionist: best upside; Contrarian and Executor: skip, adverse and
bound negative) - resolved by measuring rebate-adjusted markout on live L2 before any build. (2) Chainlink near-strike
basis edge (Expansionist option 2) vs "probably arbitraged away" (Contrarian) - resolved cheaply by a corrected-strike
calibration test. (3) US-eligible venues (Polymarket US, Kalshi) as a pivot - only relevant if eligibility requires it.

Blind spots caught: our own settlement convention was wrong; our server is geoblocked; the user's jurisdiction is
unknown; costs beyond fees (gas, hosting, time) and independent review of results are unaccounted for.

## 5. Implementation plan (about 6 weeks; research first, live is never the default)

Standing rules: pre-register every test before running it; keep a test ledger (count every variant tried, deflate
significance); sealed holdout touched once; frozen bot code is not changed; the frozen forward test keeps running
(pace checkpoint 2026-10-17 needs at least 30 trades, hard cap 2026-12-31).

**Phase 0 (days 0-3): fix the truth, measure the pipe, settle eligibility.**
- 0.1 Adopt the measured settlement rule (60s TWAP at both ends) in all analysis tooling: extract
  `scripts/twap_window_check.py` logic into a reusable resolution module; re-label historical windows.
- 0.2 Collector health: `scripts/health_check.py` (L2 hours collected, gaps over 60s, signal count, volume free
  space; volume has about 260MB free against a 120MB cap) plus a notifier alert; schedule `research_check.ps1`
  daily (Windows Task Scheduler).
- 0.3 Latency budget: measured CLOB REST 42ms p50; add measured Binance WebSocket latency from Amsterdam and test
  nearer-venue leads (Coinbase, Kraken, Bybit) for lead-lag; log source and ingest timestamps; check time sync.
- 0.4 Eligibility gate (BLOCKING for anything live): confirm the user's physical jurisdiction and Polymarket terms;
  the current host IP is `blocked: true`; any future host must pass `GET https://polymarket.com/api/geoblock` from
  the deployment IP, with no VPN or workaround; if the user is a US person, evaluate Polymarket US or Kalshi paths
  instead (informational review only).
- Exit: resolution module tested; collector shows 12h or more without gaps; eligibility answered in writing.
- Kill: collector cannot hold for 72h -> fix before anything else.

**Phase 1 (days 3-14): settle the edge question offline.**
- 1.1 Run `lead_lag_l2.py` once at least 200 signals exist (prefer 48-72h of L2). Pass rule as pre-registered
  (delay 2s, no extra slippage, cluster-bootstrap CI above 0); the plan additionally requires a positive result at
  2c slippage and at a realistic 300-500ms end-to-end delay before Phase 2.
- 1.2 New pre-registered test: corrected-strike near-strike calibration. Re-price the model with the 60s/60s rule and
  test whether it beats the market price in windows ending within 3 bps of strike (Bonferroni over the sprint).
- 1.3 Maker markout on live L2 with rebate accounting (about 20% of taker fee, optimistic) once at least 300 fills;
  rewards and queue priority cannot be measured historically.
- 1.4 Multiple-testing ledger and deflated-significance memo covering every test run so far.
- Exit: one memo giving net expected value per trade at 300-500ms latency with confidence intervals.
- **Whole-project kill:** if the L2 executable edge is <= 0 after 2c slippage AND the corrected-strike and maker
  tests fail, archive the trading line (keep the harness, data, collector and monitoring), as pre-registered.

**Phase 2 (weeks 3-4, only if Phase 1 passes): execution realism, still no funds.**
- Separate shadow service, not `src/bot.py`. Event-driven (WebSocket, not 2.3s polling); stale-book guard (refuse if
  book older than 2s); latency injector (100-400ms), partial fills, tick/size rejections; liveness watchdog with
  PING 10s and REST resnapshot; 403/429 backoff with a token bucket; reconciliation against recorded L2 and the Data
  API; user-channel fills. Tests in `tests/test_executor_realism.py`.
- Exit: 14 days of shadow trading with zero reconciliation mismatches and PnL within a pre-set band of the L2 replay,
  with at least 200 shadow trades and a CI above 0 on expectancy (not win rate).
- Kill: repeated unexplained paper-vs-replay gap.

**Phase 3 (weeks 5-6): live-readiness package (docs and code only, no funds).**
- Kill switch (file flag plus env var), position, order-size and daily-loss caps in config; dedicated hot-wallet
  runbook; pinned and hash-checked dependencies; secrets only in the platform store; CLOB V2 pUSD wrap and EIP-712
  v2 signing tested against dry-run calls; `signature_type`/funder/allowance checks; redeem via the CTF contract;
  Data API and on-chain state as truth; ghost-fill/manipulation defences (cross-check any apparent arbitrage,
  detect quote-and-cancel baiting); trade log for tax records; alert on every breaker.
- Exit: a signed-off checklist. Live remains blocked until a separate written approval sets wallet, cap and stop.

**Phase 4 (only after that explicit approval): minimal live pilot.** Dry run first, then the smallest size the
venue allows, treated as tuition; hard daily-loss stop; human approval above a size threshold; halt on stale data,
API errors, or state mismatch. Kill: net negative after a pre-set number of fills, or any limit breach.

**Skip:** colocation and latency racing (competitors reportedly under 100ms), further dashboards, ETH/SOL expansion,
tuning the frozen bot, copy-trading wallets, running any third-party bot repository.

## 6. Decisions needed from the user
1. Physical jurisdiction and eligibility (blocking for any live step).
2. Time and cost budget (hosting, gas, data, hours) versus expected return; who reviews the results independently.
3. Whether to relocate or add hosting only in a jurisdiction that passes the geoblock check (never by circumvention).

## 7. Key sources
Polymarket/Chainlink and protocol: docs.polymarket.com (v2-migration, fees, rate-limits, geoblock, chainlink-twap),
x.com/PolymarketDevs/status/2089295325660172578, github.com/Polymarket/py-clob-client issues, real-time-data-client.
Research: arxiv.org/abs/2606.31675, arxiv.org/abs/2606.16852, arxiv.org/html/2607.26245, karlwhelan.com/Papers/Kalshi.pdf,
davidhbailey.com/dhbpapers/backtest-prob.pdf. Security: stepsecurity.io (malicious Polymarket bot), safedep.io,
panewslab.com (SlowMist). Builders: dev.to (benjamin_cup, lkto1m), tezlee.substack.com, casatrick.substack.com,
news.ycombinator.com items 48461522, 48573307, 48221877. Vendor and promotional content was treated as low quality.
