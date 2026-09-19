"""
Phase 0 data collector: compact Polymarket order-book + trade log for BTC 5-minute markets.

Observation only. It never trades and shares nothing with the bot except the Railway volume.
Runs as its own process (launched by railway_entrypoint.py when RUN_L2_COLLECTOR != 0).

The market channel is very heavy (~650 events/s), so raw deltas are NOT stored. The book is
rebuilt in memory and written as:
  {"t":"s","ts":..,"w":window_ts,"k":"Y|N","b":[[p,s]..3],"a":[[p,s]..3]}   top-3 snapshot, on change, >=1s apart, forced every 10s
  {"t":"t","ts":..,"st":server_ms,"w":..,"k":"Y|N","p":..,"s":..,"d":"B|S"} every trade (d = taker side)
Files rotate hourly to data/l2/l2_YYYYMMDDHH.jsonl and are gzipped when the hour ends; total size
is capped (L2_MAX_MB, default 120) by deleting the oldest. YES+NO<1 episodes go to
data/l2/arb_log.jsonl. Health counters go to data/l2/collector_stats.json.
"""
import asyncio
import gzip
import json
import os
import random
import shutil
import sys
import time

import aiohttp
from aiohttp.resolver import AsyncResolver

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.mm.arb import ArbTracker  # noqa: E402
from src.mm.book import LocalBook  # noqa: E402
from src.mm.venues import VENUES, Throttle  # noqa: E402

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com"
SLUG = "btc-updown-5m-{}"
OUT_DIR = os.getenv("L2_DIR", "data/l2")
MAX_MB = float(os.getenv("L2_MAX_MB", "120"))
WINDOW = 300
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
           "Origin": "https://polymarket.com"}


class Recorder:
    """Hourly jsonl files, gzip on rotation, oldest-first deletion above the size cap."""

    def __init__(self, out_dir: str, max_mb: float) -> None:
        self.dir, self.max_bytes = out_dir, int(max_mb * 1024 * 1024)
        self.key = None
        self.f = None
        self.bytes_written = 0
        os.makedirs(out_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.dir, f"l2_{key}.jsonl")

    def write(self, rec: dict) -> None:
        key = time.strftime("%Y%m%d%H", time.gmtime(rec["ts"]))
        if key != self.key:
            self._rotate(key)
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        self.f.write(line)
        self.bytes_written += len(line)

    def _rotate(self, key: str) -> None:
        prev = self.key
        if self.f:
            self.f.close()
        if prev:
            self._compress(self._path(prev))
        self.key = key
        self.f = open(self._path(key), "a", encoding="utf-8")
        self._enforce_cap()

    @staticmethod
    def _compress(path: str) -> None:
        try:
            with open(path, "rb") as src, gzip.open(path + ".gz", "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
            os.remove(path)
        except Exception as e:
            print(f"collect_l2: compress failed for {path}: {type(e).__name__}: {e}", flush=True)

    def _enforce_cap(self) -> None:
        files = sorted(f for f in os.listdir(self.dir) if f.startswith("l2_"))
        sizes = {f: os.path.getsize(os.path.join(self.dir, f)) for f in files}
        total = sum(sizes.values())
        for f in files[:-1]:  # never delete the newest (current) file
            if total <= self.max_bytes:
                break
            os.remove(os.path.join(self.dir, f))
            total -= sizes[f]

    def flush(self) -> None:
        if self.f:
            self.f.flush()


class Collector:
    def __init__(self) -> None:
        self.rec = Recorder(OUT_DIR, MAX_MB)
        self.arb = ArbTracker()
        self.windows = {}      # window_ts -> {"Y": token, "N": token}
        self.tokens = {}       # token -> (window_ts, "Y"|"N")
        self.books = {}        # token -> LocalBook
        self.last_snap = {}    # token -> (signature, ts)
        self.subscribed = set()
        self.next_try = {}     # window_ts -> next gamma attempt time
        self.stats = {"events": {}, "reconnects": 0, "trades": 0, "snaps": 0, "arb_episodes": 0,
                      "started": time.time(), "last_event_ts": None, "venue_rows": {}}
        self.ws = None
        self.arb_path = os.path.join(OUT_DIR, "arb_log.jsonl")
        self.stats_path = os.path.join(OUT_DIR, "collector_stats.json")

    # ---------- event handling ----------
    def handle(self, ev: dict, now: float) -> None:
        et = ev.get("event_type")
        self.stats["events"][et] = self.stats["events"].get(et, 0) + 1
        self.stats["last_event_ts"] = now
        if et == "book":
            aid = ev.get("asset_id")
            if aid in self.books:
                self.books[aid].apply_snapshot(ev.get("bids"), ev.get("asks"))
                self._after_update(aid, now)
        elif et == "price_change":
            touched = set()
            for ch in ev.get("price_changes", []) or []:
                aid = ch.get("asset_id")
                if aid in self.books:
                    self.books[aid].apply_change(ch.get("side"), ch.get("price"), ch.get("size"))
                    touched.add(aid)
            for aid in touched:
                self._after_update(aid, now)
        elif et == "last_trade_price":
            aid = ev.get("asset_id")
            if aid in self.tokens:
                w, k = self.tokens[aid]
                try:
                    self.rec.write({"t": "t", "ts": round(now, 3), "st": int(ev.get("timestamp", 0)),
                                    "w": w, "k": k, "p": float(ev["price"]), "s": float(ev["size"]),
                                    "d": "B" if str(ev.get("side", "")).upper() == "BUY" else "S"})
                    self.stats["trades"] += 1
                except (KeyError, ValueError, TypeError):
                    pass

    def _after_update(self, aid: str, now: float) -> None:
        w, _ = self.tokens[aid]
        toks = self.windows.get(w)
        if not toks or "Y" not in toks or "N" not in toks:
            return
        ep = self.arb.update(w, now, self.books[toks["Y"]].best_ask(), self.books[toks["N"]].best_ask())
        if ep:
            self._write_arb(ep)

    def _write_arb(self, ep: dict) -> None:
        self.stats["arb_episodes"] += 1
        with open(self.arb_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ep, separators=(",", ":")) + "\n")

    # ---------- windows / subscriptions ----------
    async def _fetch_tokens(self, session: aiohttp.ClientSession, w: int):
        try:
            async with session.get(f"{GAMMA}/events?slug={SLUG.format(w)}", timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return None
                ev = (await r.json())[0]
            toks = ev["markets"][0]["clobTokenIds"]
            toks = json.loads(toks) if isinstance(toks, str) else toks
            return (toks[0], toks[1]) if len(toks) >= 2 else None
        except Exception:
            return None

    async def housekeep(self, session: aiohttp.ClientSession, now: float) -> None:
        cur = int(now // WINDOW) * WINDOW
        add, drop = set(), set()
        for w in (cur, cur + WINDOW):
            if w not in self.windows and now >= self.next_try.get(w, 0):
                got = await self._fetch_tokens(session, w)
                if got:
                    self.windows[w] = {"Y": got[0], "N": got[1]}
                    for k, t in (("Y", got[0]), ("N", got[1])):
                        self.tokens[t] = (w, k)
                        self.books[t] = LocalBook()
                        add.add(t)
                else:
                    self.next_try[w] = now + 3.0
        for w in [w for w in self.windows if w + WINDOW + 60 < now]:
            for ep in self.arb.flush(now):
                self._write_arb(ep)
            for t in self.windows.pop(w).values():
                self.tokens.pop(t, None)
                self.books.pop(t, None)
                self.last_snap.pop(t, None)
                drop.add(t)
        if self.ws is not None and not self.ws.closed:
            if add:
                await self.ws.send_str(json.dumps({"assets_ids": list(add), "operation": "subscribe"}))
                self.subscribed |= add
            if drop & self.subscribed:
                await self.ws.send_str(json.dumps({"assets_ids": list(drop & self.subscribed), "operation": "unsubscribe"}))
                self.subscribed -= drop

    def snapshots(self, now: float) -> None:
        for t, book in self.books.items():
            b, a = book.top(3)
            if not b and not a:
                continue
            sig = (tuple(map(tuple, b)), tuple(map(tuple, a)))
            prev = self.last_snap.get(t)
            if prev and prev[0] == sig and now - prev[1] < 10.0:
                continue
            if prev and now - prev[1] < 1.0:
                continue
            w, k = self.tokens[t]
            self.rec.write({"t": "s", "ts": round(now, 3), "w": w, "k": k, "b": b, "a": a})
            self.last_snap[t] = (sig, now)
            self.stats["snaps"] += 1

    def write_stats(self) -> None:
        tmp = self.stats_path + ".tmp"
        out = dict(self.stats, bytes_written=self.rec.bytes_written, windows=sorted(self.windows),
                   connected=self.ws is not None and not self.ws.closed, updated=time.time())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        os.replace(tmp, self.stats_path)

    # ---------- main loop ----------
    async def _housekeeper(self, session: aiohttp.ClientSession) -> None:
        last_stats = 0.0
        while True:
            now = time.time()
            try:
                await self.housekeep(session, now)
                self.snapshots(now)
                self.rec.flush()
                if now - last_stats >= 30:
                    self.write_stats()
                    last_stats = now
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"collect_l2: housekeeping error: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(1.0)

    async def run_once(self) -> None:
        connector = aiohttp.TCPConnector(resolver=AsyncResolver(nameservers=["1.1.1.1", "8.8.8.8"]))
        async with aiohttp.ClientSession(connector=connector) as session:
            while not self.windows:  # need at least the current window's tokens to subscribe
                await self.housekeep(session, time.time())
                await asyncio.sleep(1.0)
            async with session.ws_connect(WS_URL, headers=HEADERS,
                                          timeout=aiohttp.ClientWSTimeout(ws_receive=25, ws_close=10)) as ws:
                self.ws = ws
                self.subscribed = set(self.tokens)
                await ws.send_str(json.dumps({"assets_ids": list(self.subscribed), "type": "market"}))
                print(f"collect_l2: connected, {len(self.subscribed)} tokens", flush=True)
                hk = asyncio.create_task(self._housekeeper(session))
                hb = asyncio.create_task(self._heartbeat(ws))
                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if msg.data in ("PONG", "PING"):
                                continue
                            try:
                                data = json.loads(msg.data)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            now = time.time()
                            for ev in (data if isinstance(data, list) else [data]):
                                if isinstance(ev, dict):
                                    self.handle(ev, now)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                finally:
                    hk.cancel()
                    hb.cancel()
                    print(f"collect_l2: polymarket socket ended code={ws.close_code}", flush=True)
                    self.ws = None

    @staticmethod
    async def _heartbeat(ws) -> None:
        try:
            while not ws.closed:
                await asyncio.sleep(10)
                await ws.send_str("PING")
        except Exception:
            return

    async def _venue_feed(self, name: str) -> None:
        """Record throttled top-of-book quotes from one exchange as {"t":"x","v":name,...} rows, on the same
        local receive clock as the Polymarket rows, so cross-venue lead-lag can be measured. Reconnects forever."""
        cfg, throttle, delay = VENUES[name], Throttle(0.25), 3.0
        while True:
            started = time.time()
            try:
                connector = aiohttp.TCPConnector(resolver=AsyncResolver(nameservers=["1.1.1.1", "8.8.8.8"]))
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(cfg["url"], timeout=aiohttp.ClientWSTimeout(ws_receive=30, ws_close=10)) as ws:
                        if cfg["sub"]:
                            await ws.send_str(json.dumps(cfg["sub"]))
                        async for msg in ws:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue
                            try:
                                q = cfg["parse"](json.loads(msg.data))
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not q:
                                continue
                            now = time.time()
                            if throttle.allow(now, q[0], q[1]):
                                self.rec.write({"t": "x", "ts": round(now, 3), "v": name, "b": q[0], "a": q[1],
                                                "st": None if q[2] is None else round(q[2], 3)})
                                self.stats["venue_rows"][name] = self.stats["venue_rows"].get(name, 0) + 1
                        print(f"collect_l2: {name} socket ended code={ws.close_code}", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"collect_l2: {name} feed error: {type(e).__name__}: {e}", flush=True)
            delay = 3.0 if time.time() - started > 60 else min(delay * 2, 60.0)
            await asyncio.sleep(delay + random.uniform(0.1, 1.0))

    async def run(self) -> None:
        self._venue_tasks = [asyncio.create_task(self._venue_feed(n)) for n in VENUES]
        fails = 0
        while True:
            t0 = time.time()
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"collect_l2: connection error: {type(e).__name__}: {e}", flush=True)
            self.stats["reconnects"] += 1
            fails = 0 if time.time() - t0 > 60 else fails + 1
            self.windows.clear(); self.tokens.clear(); self.books.clear(); self.last_snap.clear()
            await asyncio.sleep(min(30.0, 3.0 * (2 ** min(fails, 4))) + random.uniform(0.1, 1.0))


if __name__ == "__main__":
    try:
        asyncio.run(Collector().run())
    except KeyboardInterrupt:
        pass
