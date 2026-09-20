# Verified facts for a Danish retail trader (checked 2026-09-20)

VERIFIED = page fetched and read (through a summarising fetch tool, so wording is the tool's rendering). UNVERIFIED = search snippet only.

## Tax
- Crypto gains: personal income, up to 53%; box 20; FIFO. VERIFIED (skat.dk, "calculate and declare gains and losses on cryptoassets").
- Crypto losses: deductible only at about 26%, declared separately (box 58), generally NOT offsettable against gains. VERIFIED (same page).
  Consequence: gains are taxed up to 53% while losses return about 26%, so a strategy with zero true edge has negative expected value after tax even before fees.
- No allowance or de minimis threshold found for crypto gains. VERIFIED absence on info.skat.dk; not proven across all Danish tax law.
- Speculative crypto trading is normally not "business" (Skattestyrelsen view); depends on finance background, organisation, scale. VERIFIED (info.skat.dk C.C.2.1.3.3.5.4). Confirm with an adviser.
- Shares 2026: 27% on the first DKK 79,400, 42% above. VERIFIED (skat.dk). ETF lager vs realisation rules: UNVERIFIED.
- Winnings from online operators without a Danish licence are tax-free only if the operator is in the EU/EEA and other conditions hold. VERIFIED (skat.dk). Kalshi and Polymarket are not EEA operators.

## Law and access
- 8 July 2026: Retten paa Frederiksberg ordered Polymarket and 97 other unlicensed services blocked. VERIFIED (TV2).
- Whether a Danish resident may use an unlicensed prediction market: UNVERIFIED; no source found. Ask a Danish lawyer.
- Kalshi Danish eligibility: UNVERIFIED (Member Agreement fetch rate-limited).
- ESMA retail CFD leverage caps: 30:1 major FX, 5:1 single stocks, 2:1 crypto. VERIFIED (ESMA).
- Saxo: 64% of retail CFD accounts lose money. VERIFIED (Saxo risk warning).
- MiCA (Reg. 2023/1114): crypto service providers must be authorised in a Member State. VERIFIED (EUR-Lex).

## Costs on a 350 DKK (about $50) trade, round trip
- Nordnet DK Standard: 0.10% with 25 DKK minimum -> about 14.3%. VERIFIED (nordnet.dk price list).
- Kraken spot: 0.40% maker, 0.80% taker -> 0.8% to 1.6%. VERIFIED. Minimum order 0.0001 BTC and 5 EUR on the standard form. VERIFIED (Kraken support).
- Trading 212: commission "Free", FX fee 0.15% per side. VERIFIED (help centre). API existence UNVERIFIED; Danish account eligibility not checked.
- Saxo Classic about 0.08% with about EUR 2 minimum (~8.6%), IBKR (~4% on US stocks, ~6-28% on Danish stocks): UNVERIFIED (official pages blocked).
- Binance spot 0.10% per side used in our tests: fee assumption; Danish eligibility of Binance UNVERIFIED.
- Takeaway: at this size fixed minimum fees dominate; only percentage-fee venues (Kraken) and zero-commission brokers scale down.

## Evidence on edge
- Novy-Marx and Velikov (NBER 20721): most anomalies below 50% monthly turnover keep significant net spreads when designed to mitigate costs; few above do; costs always reduce profitability. VERIFIED (paper text read).
- Retail day-trading studies (Brazil, Taiwan): UNVERIFIED snippets; consistent with our own 15 pre-registered nulls.
