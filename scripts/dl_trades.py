"""
Download historical Polymarket trades for BTC 5-minute windows. Run ON RAILWAY (this machine cannot
reach Polymarket hosts); it prints a gzip+base64 CSV between BEGIN/END markers and writes nothing.

  python3 dl_trades.py <window_ts,window_ts,...> [taker_only=1|0]

Columns: window_ts,ts,wallet,side,outcome_idx,price,size,resolved_up,tx
  wallet = first 10 hex chars of proxyWallet, side = B/S as reported by the API, outcome_idx 0=Up/YES 1=Down/NO,
  resolved_up = 1/0 from the market's final outcomePrices (empty if unresolved).
Trades are paged 500 at a time (newest first) until a short page or the window's start is passed, and
deduplicated on (tx, ts, price, size, wallet, outcome_idx). A trailing "#STATS ..." line reports per-window counts.
"""
import asyncio
import base64
import csv
import gzip
import io
import json
import sys

import aiohttp

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"


async def get_json(s, url, tries=5):
    for i in range(tries):
        try:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 429:
                    await asyncio.sleep(1.5 * (i + 1))
                    continue
                if r.status != 200:
                    return None
                return await r.json()
        except Exception:
            await asyncio.sleep(0.5 * (i + 1))
    return None


async def one_window(s, w, taker_only):
    ev = await get_json(s, f"{GAMMA}/events?slug=btc-updown-5m-{w}")
    if not ev:
        return [], f"{w}:no_event"
    m = ev[0]["markets"][0]
    cid = m["conditionId"]
    try:
        op = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
        resolved = "" if not m.get("closed") else (1 if float(op[0]) >= 0.5 else 0)
    except Exception:
        resolved = ""
    rows, seen, offset, pages = [], set(), 0, 0
    while offset <= 10000:
        page = await get_json(s, f"{DATA}/trades?market={cid}&limit=500&offset={offset}&takerOnly={'true' if taker_only else 'false'}")
        pages += 1
        if not page:
            break
        for t in page:
            key = (t.get("transactionHash"), t["timestamp"], t["price"], t["size"], t["proxyWallet"], t.get("outcomeIndex"))
            if key in seen:
                continue
            seen.add(key)
            rows.append([w, t["timestamp"], t["proxyWallet"][2:12], "B" if t["side"] == "BUY" else "S",
                         t.get("outcomeIndex"), t["price"], t["size"], resolved, (t.get("transactionHash") or "")[2:10]])
        if len(page) < 500 or min(t["timestamp"] for t in page) < w - 60:
            break
        offset += 500
        await asyncio.sleep(0.25)
    return rows, f"{w}:{len(rows)}rows/{pages}pages/res={resolved}"


async def main():
    windows = [int(x) for x in sys.argv[1].split(",") if x]
    taker_only = (sys.argv[2] != "0") if len(sys.argv) > 2 else True
    out, stats = [], []
    async with aiohttp.ClientSession() as s:
        for w in windows:
            rows, st = await one_window(s, w, taker_only)
            out.extend(rows)
            stats.append(st)
            await asyncio.sleep(0.25)
    buf = io.StringIO()
    csv.writer(buf).writerows(out)
    print("BEGIN")
    print(base64.b64encode(gzip.compress(buf.getvalue().encode())).decode())
    print("END")
    print("#STATS " + " ".join(stats))


asyncio.run(main())
