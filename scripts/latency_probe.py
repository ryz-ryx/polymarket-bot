"""
Multi-venue REST latency probe (read-only, informational). Run it ON the deployment host (Railway) to see
which price feeds and which Polymarket endpoints are closest, e.g.:
  railway ssh "echo <base64 of this file> | base64 -d | python3 -"

Sends 30 small public GET requests per endpoint over one keep-alive session and prints p50 / p95 / min in ms.
This measures round-trip time only, not end-to-end signal-to-order latency. No authentication, no orders.
"""
import asyncio
import statistics
import time

import aiohttp

ENDPOINTS = [
    ("Polymarket CLOB /time", "https://clob.polymarket.com/time"),
    ("Polymarket Gamma", "https://gamma-api.polymarket.com/events?limit=1"),
    ("Binance /ping", "https://api.binance.com/api/v3/ping"),
    ("Coinbase /time", "https://api.exchange.coinbase.com/time"),
    ("Kraken /Time", "https://api.kraken.com/0/public/Time"),
    ("Bybit /time", "https://api.bybit.com/v5/market/time"),
    ("OKX /time", "https://www.okx.com/api/v5/public/time"),
]
N = 30


async def probe(session, url):
    xs, fails = [], 0
    for _ in range(N):
        t0 = time.perf_counter()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                await r.read()
            xs.append((time.perf_counter() - t0) * 1000.0)
        except Exception:
            fails += 1
        await asyncio.sleep(0.1)
    return sorted(xs), fails


async def main():
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as s:
        for name, url in ENDPOINTS:
            xs, fails = await probe(s, url)
            if xs:
                print(f"{name:<24} n={len(xs):>2} fails={fails} p50={statistics.median(xs):>5.0f}ms "
                      f"p95={xs[max(int(len(xs) * 0.95) - 1, 0)]:>5.0f}ms min={xs[0]:>5.0f}ms")
            else:
                print(f"{name:<24} all {N} requests failed")


if __name__ == "__main__":
    asyncio.run(main())
