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


def stop_reason(state, prereg):
    """Pre-registered stop rule (docs/paper_prereg.json). Returns a reason string or None."""
    eq = equity(state)
    if eq <= prereg["stop_equity"]:
        return f"equity {eq:.2f} <= stop {prereg['stop_equity']}"
    return None


def enter(state, sym, rule, hold, ask, ts, mid=None):
    if sym in state["pos"]:
        return None
    notional = min(state["cash"] / (1 + FEE), equity(state) * SIZE_FRAC)
    if notional < 5:
        return None
    fee = notional * FEE
    state["cash"] -= notional + fee
    state["fees"] += fee
    state["trades"] += 1
    mid = ask if mid is None else mid
    state["pos"][sym] = {"rule": rule, "qty": notional / ask, "cost_basis": notional + fee,
                         "exit_ts": ts + hold * 60_000, "mid_in": mid, "fee_in": fee}
    return {"ts": ts, "sym": sym, "action": "enter", "rule": rule, "px": ask, "notional": round(notional, 2)}


def exit_pos(state, sym, bid, ts, mid=None):
    p = state["pos"].pop(sym)
    gross = p["qty"] * bid
    fee = gross * FEE
    state["cash"] += gross - fee
    state["fees"] += fee
    pnl = gross - fee - p["cost_basis"]
    state["closed_pnl"] += pnl
    ev = {"ts": ts, "sym": sym, "action": "exit", "rule": p["rule"], "px": bid, "pnl": round(pnl, 4)}
    if "mid_in" in p:  # lets the report split P&L into signal (mid to mid), spread and fees
        ev.update(mid_in=p["mid_in"], mid_out=bid if mid is None else mid, qty=p["qty"],
                  fee_in=p["fee_in"], fee_out=fee)
    return ev


def on_tick(state, sym, bid, ask, ts):
    """Update the mark price and close the position if its hold time is over. Returns events."""
    state.setdefault("mark", {})[sym] = (bid + ask) / 2
    p = state["pos"].get(sym)
    if p and ts >= p["exit_ts"]:
        return [exit_pos(state, sym, bid, ts, mid=(bid + ask) / 2)]
    return []


def on_bar_close(state, sym, closes, ask, ts, bid=None):
    """A 1-minute bar just closed: evaluate the rules on closed bars and enter at the ask if one fires."""
    if sym in state["pos"] or len(closes) < BARS_NEEDED:
        return []
    c = np.asarray(closes, float)[-BARS_NEEDED:]
    for name, (kind, hold) in RULES.items():
        if signals(kind, c)[-1]:
            ev = enter(state, sym, name, hold, ask, ts, mid=None if bid is None else (bid + ask) / 2)
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
    msg_count = 0
    while time.time() < end_time:
        try:
            print(f"[consume] connecting to {WS}", file=sys.stderr, flush=True)
            async with websockets.connect(WS, open_timeout=15, ping_interval=20) as ws:
                print("[consume] connected, fetching initial closed bars", file=sys.stderr, flush=True)
                for s in SYMBOLS:  # re-sync bars in case we were disconnected
                    closes[s].clear()
                    cl, last_t[s] = fetch_closed(s)
                    closes[s].extend(cl)
                print(f"[consume] initial bars loaded for {SYMBOLS}, entering message loop", file=sys.stderr, flush=True)
                async for raw in ws:
                    if time.time() >= end_time:
                        return
                    msg_count += 1
                    if msg_count % 500 == 1:
                        print(f"[consume] {msg_count} ws messages received so far, quotes={list(quotes.keys())}",
                              file=sys.stderr, flush=True)
                    d = json.loads(raw)["data"]
                    if "k" in d:
                        k, s = d["k"], d["k"]["s"]
                        if k["x"] and int(k["t"]) > last_t[s]:
                            last_t[s] = int(k["t"])
                            closes[s].append(float(k["c"]))
                            if s in quotes:
                                for ev in on_bar_close(state, s, closes[s], quotes[s][1], int(time.time() * 1000),
                                                 bid=quotes[s][0]):
                                    log_event(ev)
                    else:
                        quotes[d["s"]] = (float(d["b"]), float(d["a"]))
        except Exception as e:  # network drop: log, wait, reconnect
            print(f"[consume] EXCEPTION: {repr(e)[:300]}", file=sys.stderr, flush=True)
            log_event({"action": "error", "msg": repr(e)[:150], "ts": int(time.time() * 1000)})
            await asyncio.sleep(3)


async def ticker(state, quotes, end_time, prereg):
    writer = TickWriter()
    last_save = last_beat = 0.0
    live = open(OUT / "live.log", "a", buffering=1)
    try:
        while time.time() < end_time:
            now = time.time()
            why = stop_reason(state, prereg)
            if why:  # pre-registered stop: halt and say why
                log_event({"action": "stop", "msg": why, "ts": int(now * 1000)})
                live.write(f"STOPPED by pre-registered rule: {why}\n")
                break
            ts = int(now * 1000)
            for s, (bid, ask) in list(quotes.items()):
                for ev in on_tick(state, s, bid, ask, ts):
                    log_event(ev)
                p = state["pos"].get(s, {}).get("rule", "flat")
                writer.write(ts, s, bid, ask, equity(state), p)
            if now - last_save >= 1:
                save_state(state)
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
        save_state(state)
        writer.close()
        live.close()


def save_state(state):
    """Atomic write: a crash or kill mid-write can never leave state.json truncated/corrupted,
    since the rename only happens after the full write succeeds (2026-09-25: recovered a
    corrupted all-null state.json this way was NOT possible after a non-atomic write got
    interrupted; see scripts/recover_paper_state.py, kept for reference)."""
    tmp = OUT / "state.json.tmp"
    tmp.write_text(json.dumps(state))
    tmp.replace(OUT / "state.json")


def load_state():
    sp = OUT / "state.json"
    if not sp.exists():
        return new_state()
    try:
        return json.loads(sp.read_text())
    except json.JSONDecodeError:
        print(f"WARNING: {sp} is corrupted (unreadable JSON) -- starting a fresh $50 state. "
              f"If log.jsonl exists, recover manually first with scripts/recover_paper_state.py.",
              file=sys.stderr)
        return new_state()


async def run(minutes):
    OUT.mkdir(parents=True, exist_ok=True)
    state = load_state()
    state.setdefault("mark", {})
    quotes, closes, last_t = {}, {s: deque(maxlen=BARS_NEEDED + 50) for s in SYMBOLS}, {}
    prereg = json.loads((ROOT / "docs" / "paper_prereg.json").read_text())
    end_time = time.time() + min(minutes, prereg["max_runtime_days"] * 1440) * 60
    tick = asyncio.ensure_future(ticker(state, quotes, end_time, prereg))
    cons = asyncio.ensure_future(consume(state, quotes, closes, last_t, end_time))
    await tick  # the ticker ends on stop or time; then end the websocket consumer too
    cons.cancel()
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in state.items() if k not in ("pos", "mark")}))


if __name__ == "__main__":
    asyncio.run(run(float(sys.argv[1]) if len(sys.argv) > 1 else 60))
