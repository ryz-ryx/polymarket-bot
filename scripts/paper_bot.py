"""Paper-only fast trading bot on Binance spot BTC/ETH, live websocket prices, 100 ms account refresh.

Runs the three pre-registered fast rules (docs/PREREG_fast_intraday.md) on 1-minute bars; entries fill at the live ASK and
exits at the live BID (real spread) plus a 0.10% fee per side, on a $50 paper account. NO keys, NO orders: it only reads
public market data. Everything is stored under data/paper/:
  ticks_YYYYMMDD_HH.csv(.gz)  100 ms snapshots (bid, ask, equity, position)
  log.jsonl                   every enter/exit/error event
  live.log                    human-readable status every 5 s
  state.json                  account state, saved every second
These rules had no measurable gross edge in backtests, so expect losses about equal to fees; this bot measures that live.
"""
import asyncio
import csv
import gzip
import json
import shutil
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fast_backtest import RULES, signals  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "paper"
FEE = 0.001  # Binance spot taker fee per side; the spread is paid explicitly via bid/ask fills
SIZE_FRAC = 0.45
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
BARS_NEEDED = 1601  # signals() needs a 1440-minute volatility window plus a 15-minute return
PAGES = 2  # 1000 bars per REST request
TICK_S = 0.1
REST = "https://data-api.binance.vision/api/v3/klines?symbol={s}&interval=1m&limit=1000"
WS = "wss://stream.binance.com:9443/stream?streams=" + "/".join(
    f"{s.lower()}@bookTicker/{s.lower()}@kline_1m" for s in SYMBOLS)


def new_state(cash=50.0):
    return {"cash": cash, "pos": {}, "trades": 0, "fees": 0.0, "closed_pnl": 0.0, "mark": {}}


def equity(state):
    """Cash plus every open position valued at ITS OWN coin's last mid price."""
    return state["cash"] + sum(q["qty"] * state["mark"].get(sym, q["cost_basis"] / q["qty"])
                               for sym, q in state["pos"].items())


def enter(state, sym, rule, hold, ask, ts):
    if sym in state["pos"]:
        return None
    notional = min(state["cash"] / (1 + FEE), equity(state) * SIZE_FRAC)
    if notional < 5:
        return None
    fee = notional * FEE
    state["cash"] -= notional + fee
    state["fees"] += fee
    state["trades"] += 1
    state["pos"][sym] = {"rule": rule, "qty": notional / ask, "cost_basis": notional + fee,
                         "exit_ts": ts + hold * 60_000}
    return {"ts": ts, "sym": sym, "action": "enter", "rule": rule, "px": ask, "notional": round(notional, 2)}


def exit_pos(state, sym, bid, ts):
    p = state["pos"].pop(sym)
    gross = p["qty"] * bid
    fee = gross * FEE
    state["cash"] += gross - fee
    state["fees"] += fee
    pnl = gross - fee - p["cost_basis"]
    state["closed_pnl"] += pnl
    return {"ts": ts, "sym": sym, "action": "exit", "rule": p["rule"], "px": bid, "pnl": round(pnl, 4)}


def on_tick(state, sym, bid, ask, ts):
    """Update the mark price and close the position if its hold time is over. Returns events."""
    state.setdefault("mark", {})[sym] = (bid + ask) / 2
    p = state["pos"].get(sym)
    if p and ts >= p["exit_ts"]:
        return [exit_pos(state, sym, bid, ts)]
    return []


def on_bar_close(state, sym, closes, ask, ts):
    """A 1-minute bar just closed: evaluate the rules on closed bars and enter at the ask if one fires."""
    if sym in state["pos"] or len(closes) < BARS_NEEDED:
        return []
    c = np.asarray(closes, float)[-BARS_NEEDED:]
    for name, (kind, hold) in RULES.items():
        if signals(kind, c)[-1]:
            ev = enter(state, sym, name, hold, ask, ts)
            return [ev] if ev else []
    return []


def fetch_closed(sym):
    """Last ~2000 CLOSED 1-minute closes from REST (used at start and after a reconnect)."""
    rows, end = [], ""
    for _ in range(PAGES):
        with urllib.request.urlopen(REST.format(s=sym) + end, timeout=20) as r:
            page = json.loads(r.read())
        rows = page + rows
        end = f"&endTime={int(page[0][0]) - 1}"
    rows = rows[:-1]  # drop the still-open candle
    return [float(b[4]) for b in rows], int(rows[-1][0])


class TickWriter:
    """100 ms snapshots to an hourly CSV; the previous hour is gzipped when the hour rolls over."""

    def __init__(self):
        self.hour, self.f, self.w = None, None, None

    def write(self, ts_ms, sym, bid, ask, eq, pos):
        hour = time.strftime("%Y%m%d_%H", time.gmtime(ts_ms / 1000))
        if hour != self.hour:
            self.close()
            self.hour = hour
            path = OUT / f"ticks_{hour}.csv"
            new = not path.exists()
            self.f = open(path, "a", newline="")
            self.w = csv.writer(self.f)
            if new:
                self.w.writerow(["ts_ms", "sym", "bid", "ask", "equity", "pos"])
        self.w.writerow([ts_ms, sym, bid, ask, round(eq, 4), pos])

    def flush(self):
        if self.f:
            self.f.flush()

    def close(self):
        if self.f:
            self.f.close()
            p = OUT / f"ticks_{self.hour}.csv"
            with open(p, "rb") as src, gzip.open(str(p) + ".gz", "wb") as dst:
                shutil.copyfileobj(src, dst)
            p.unlink()
            self.f = None


def log_event(ev):
    with open(OUT / "log.jsonl", "a") as f:
        f.write(json.dumps(ev) + "\n")


async def consume(state, quotes, closes, last_t, end_time):
    import websockets
    while time.time() < end_time:
        try:
            async with websockets.connect(WS, open_timeout=15, ping_interval=20) as ws:
                for s in SYMBOLS:  # re-sync bars in case we were disconnected
                    closes[s].clear()
                    cl, last_t[s] = fetch_closed(s)
                    closes[s].extend(cl)
                async for raw in ws:
                    if time.time() >= end_time:
                        return
                    d = json.loads(raw)["data"]
                    if "k" in d:
                        k, s = d["k"], d["k"]["s"]
                        if k["x"] and int(k["t"]) > last_t[s]:
                            last_t[s] = int(k["t"])
                            closes[s].append(float(k["c"]))
                            if s in quotes:
                                for ev in on_bar_close(state, s, closes[s], quotes[s][1], int(time.time() * 1000)):
                                    log_event(ev)
                    else:
                        quotes[d["s"]] = (float(d["b"]), float(d["a"]))
        except Exception as e:  # network drop: log, wait, reconnect
            log_event({"action": "error", "msg": repr(e)[:150], "ts": int(time.time() * 1000)})
            await asyncio.sleep(3)


async def ticker(state, quotes, end_time):
    writer = TickWriter()
    last_save = last_beat = 0.0
    live = open(OUT / "live.log", "a", buffering=1)
    try:
        while time.time() < end_time:
            now = time.time()
            ts = int(now * 1000)
            for s, (bid, ask) in list(quotes.items()):
                for ev in on_tick(state, s, bid, ask, ts):
                    log_event(ev)
                p = state["pos"].get(s, {}).get("rule", "flat")
                writer.write(ts, s, bid, ask, equity(state), p)
            if now - last_save >= 1:
                (OUT / "state.json").write_text(json.dumps(state))
                writer.flush()
                last_save = now
            if now - last_beat >= 5 and quotes:
                q = " ".join(f"{s}={v[0]:.2f}/{v[1]:.2f}" for s, v in quotes.items())
                pos = ",".join(f"{s}:{p['rule']}" for s, p in state["pos"].items()) or "flat"
                live.write(f"{time.strftime('%H:%M:%S')} bid/ask {q} pos={pos} cash={state['cash']:.2f} "
                           f"equity={equity(state):.2f} trades={state['trades']} fees={state['fees']:.3f} "
                           f"pnl={state['closed_pnl']:.3f}\n")
                last_beat = now
            await asyncio.sleep(TICK_S)
    finally:
        (OUT / "state.json").write_text(json.dumps(state))
        writer.close()
        live.close()


async def run(minutes):
    OUT.mkdir(parents=True, exist_ok=True)
    sp = OUT / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    state.setdefault("mark", {})
    quotes, closes, last_t = {}, {s: deque(maxlen=BARS_NEEDED + 50) for s in SYMBOLS}, {}
    end_time = time.time() + minutes * 60
    await asyncio.gather(consume(state, quotes, closes, last_t, end_time), ticker(state, quotes, end_time))
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in state.items() if k not in ("pos", "mark")}))


if __name__ == "__main__":
    asyncio.run(run(float(sys.argv[1]) if len(sys.argv) > 1 else 60))
