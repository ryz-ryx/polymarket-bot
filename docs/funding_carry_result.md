# Funding-rate carry on BTC/ETH (descriptive, 2026-09-20)

Data: Binance USDT-M perpetual funding history (data.binance.vision), Jan 2022 to Aug 2026, 5,112 events per coin.
Strategy: long spot + short perpetual, no leverage, collect funding. Full output: `data/funding/carry_result.txt`.

| Year | BTC annualized funding on notional | ETH |
|---|---|---|
| 2022 | 4.2% | 0.8% |
| 2023 | 7.9% | 8.3% |
| 2024 | 11.9% | 12.9% |
| 2025 | 5.1% | 4.9% |
| 2026 (to Aug) | 2.6% | 1.6% |

- Whole period: BTC 6.6%, ETH 6.0% on notional. It is falling: 2026 is 2-3%.
- After costs (0.30% round trip, taker), a 365-day hold nets about 6.4-7.0%; 30-day holds net 2.3-2.9% annualized with 30-38% losing holds.
- Funding was negative 28-33% of the time in 2026 (the short leg then pays).
- On $50: spot leg and perp margin both need cash, so about $25 earns the carry: roughly $0.4-1.8 per year.

Not included: basis risk, margin calls or liquidation on the perp leg, exchange counterparty risk, taxes (Danish treatment unverified),
and whether Danish retail may trade perpetuals at all (unverified; ESMA leverage rules and MiFID apply to derivatives).
Verdict: the only strategy with a documented positive net return, but at $50 it is cents to a couple of dollars a year.
