"""Summarise the paper bot's stored logs (data/paper/): trades, fees, spread paid, equity range, data gaps."""
import csv
import glob
import gzip
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "paper"


def read_ticks():
    rows = []
    for f in sorted(glob.glob(str(OUT / "ticks_*.csv*"))):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append((int(r["ts_ms"]), r["sym"], float(r["bid"]), float(r["ask"]), float(r["equity"])))
    return rows


def main():
    state = json.loads((OUT / "state.json").read_text())
    events = [json.loads(line) for line in (OUT / "log.jsonl").read_text().splitlines() if line.strip()]
    ticks = read_ticks()
    trades = [e for e in events if e.get("action") == "exit"]
    errors = [e for e in events if e.get("action") == "error"]
    print(f"account: cash={state['cash']:.3f} trades_opened={state['trades']} fees={state['fees']:.4f} "
          f"closed_pnl={state['closed_pnl']:.4f} open={list(state['pos'])}")
    print(f"closed trades: {len(trades)}", end="")
    if trades:
        pnls = [t["pnl"] for t in trades]
        print(f"  wins={sum(p > 0 for p in pnls)}  sum={sum(pnls):.4f}  mean={sum(pnls)/len(pnls):.4f}")
    else:
        print()
    print(f"errors logged: {len(errors)}", [e["msg"] for e in errors][:3])
    if ticks:
        eqs = [t[4] for t in ticks]
        print(f"ticks: {len(ticks)} rows, equity min={min(eqs):.3f} max={max(eqs):.3f} last={eqs[-1]:.3f}")
        by = {}
        for ts, sym, bid, ask, _ in ticks:
            by.setdefault(sym, []).append((ts, bid, ask))
        for sym, r in by.items():
            spr = [(a - b) / ((a + b) / 2) * 1e4 for _, b, a in r]
            gaps = [(r[i + 1][0] - r[i][0]) / 1000 for i in range(len(r) - 1) if r[i + 1][0] - r[i][0] > 2000]
            print(f"  {sym}: mean spread {sum(spr)/len(spr):.3f} bps, gaps>2s: {len(gaps)} (max {max(gaps, default=0):.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
