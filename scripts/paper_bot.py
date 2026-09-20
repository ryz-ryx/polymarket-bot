"""Paper-only fast trading bot on Binance spot BTC/ETH 1-minute bars.

Runs the three pre-registered fast rules (docs/PREREG_fast_intraday.md) against live bars with a $50 paper account,
0.12% fee+slippage per side. NO keys, NO orders: it only reads public klines and writes data/paper/.
These rules had no measurable gross edge in backtests, so expect losses equal to fees; this bot measures that live.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fast_backtest import RULES, signals  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "paper"
SIDE_COST = 0.0012
SIZE_FRAC = 0.45
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
URL = "https://data-api.binance.vision/api/v3/klines?symbol={s}&interval=1m&limit=1000"
BARS_NEEDED = 1601  # signals() needs a 1440-minute volatility window plus 15-minute return
PAGES = 2  # 1000 bars per request


def new_state(cash=50.0):
    return {"cash": cash, "pos": {}, "trades": 0, "fees": 0.0, "closed_pnl": 0.0}


def step(state, sym, bars):
    """bars: array [open_time_ms, open, close]; last row = still-open candle. Returns list of log events."""
    events = []
    closed = bars[:-1]
    now_ts, now_px = int(bars[-1, 0]), float(bars[-1, 1])  # current candle open = best available price
    p = state["pos"].get(sym)
    if p and now_ts >= p["exit_ts"]:
        gross = p["qty"] * now_px
        fee = gross * SIDE_COST
        state["cash"] += gross - fee
        state["fees"] += fee
        pnl = gross - fee - p["cost_basis"]
        state["closed_pnl"] += pnl
        events.append({"ts": now_ts, "sym": sym, "action": "exit", "rule": p["rule"], "px": now_px, "pnl": round(pnl, 4)})
        del state["pos"][sym]
    if sym not in state["pos"] and len(closed) >= BARS_NEEDED:
        c = closed[-BARS_NEEDED:, 2]
        for name, (kind, hold) in RULES.items():
            if signals(kind, c)[-1]:
                equity = state["cash"] + sum(q["qty"] * now_px for q in state["pos"].values())
                notional = min(state["cash"] / (1 + SIDE_COST), equity * SIZE_FRAC)
                if notional < 5:
                    break
                fee = notional * SIDE_COST
                state["cash"] -= notional + fee
                state["fees"] += fee
                state["trades"] += 1
                state["pos"][sym] = {"rule": name, "qty": notional / now_px, "cost_basis": notional + fee,
                                     "exit_ts": now_ts + hold * 60_000}
                events.append({"ts": now_ts, "sym": sym, "action": "enter", "rule": name, "px": now_px,
                               "notional": round(notional, 2)})
                break
    return events


def fetch(sym):
    rows, end = [], ""
    for _ in range(PAGES):
        with urllib.request.urlopen(URL.format(s=sym) + end, timeout=20) as r:
            page = json.loads(r.read())
        rows = page + rows
        end = f"&endTime={int(page[0][0]) - 1}"
    return np.array([[int(b[0]), float(b[1]), float(b[4])] for b in rows])


def heartbeat(state, sym, bars):
    """One human-readable status line: price, what each rule sees right now, account."""
    c = bars[:-1, 2]
    flags = " ".join(f"{n[:2]}={'FIRE' if signals(k, c[-BARS_NEEDED:])[-1] else '-'}" for n, (k, h) in RULES.items())
    equity = state["cash"] + sum(q["qty"] * bars[-1, 1] for q in state["pos"].values())
    pos = state["pos"].get(sym, {}).get("rule", "flat")
    return (f"{time.strftime('%H:%M:%S')} {sym} px={bars[-1, 1]:.2f} rules[{flags}] pos={pos} "
            f"cash={state['cash']:.2f} equity={equity:.2f} trades={state['trades']} fees={state['fees']:.3f}")


def main(minutes=55):
    OUT.mkdir(parents=True, exist_ok=True)
    sp = OUT / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    end = time.time() + minutes * 60
    while time.time() < end:
        for sym in SYMBOLS:
            try:
                bars = fetch(sym)
                ev = step(state, sym, bars)
                print(heartbeat(state, sym, bars), flush=True)
            except Exception as e:  # network hiccup: log and continue
                ev = [{"action": "error", "sym": sym, "msg": str(e)[:100]}]
            with open(OUT / "log.jsonl", "a") as f:
                for e in ev:
                    f.write(json.dumps(e) + "\n")
        sp.write_text(json.dumps(state))
        time.sleep(30)
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in state.items() if k != "pos"}))


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 55)
