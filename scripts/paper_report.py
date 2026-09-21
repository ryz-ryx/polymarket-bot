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


def dedupe(events):
    """log.jsonl also holds events from an earlier run of the bot; keep one copy of any repeat (same sym/action/rule within 2 s, the later one)."""
    out = []
    for e in events:
        if e.get("action") in ("enter", "exit"):
            dup = next((i for i, o in enumerate(out) if (o.get("sym"), o.get("action"), o.get("rule")) ==
                        (e["sym"], e["action"], e["rule"]) and abs(o["ts"] - e["ts"]) < 2000), None)
            if dup is not None:
                out[dup] = max(out[dup], e, key=lambda x: x["ts"])
                continue
        out.append(e)
    return out


def verdict(trades, equity, prereg, n_boot=5000):
    """PASS / FAIL / INSUFFICIENT against docs/paper_prereg.json (95% bootstrap CI of mean net P&L per trade)."""
    n = len(trades)
    if equity <= prereg["stop_equity"]:
        return "FAIL", f"equity {equity:.2f} at or below stop {prereg['stop_equity']}"
    if n < prereg["min_closed_trades"]:
        return "INSUFFICIENT", f"{n} closed trades, need {prereg['min_closed_trades']}; this says almost nothing yet"
    import random
    rng = random.Random(1)
    means = sorted(sum(rng.choices(trades, k=n)) / n for _ in range(n_boot))
    lo, hi = means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]
    if lo > 0:
        return "PASS", f"mean net {sum(trades)/n:.4f}/trade, 95% CI [{lo:.4f}, {hi:.4f}] above 0 (paper fills are optimistic)"
    if hi < 0:
        return "FAIL", f"mean net {sum(trades)/n:.4f}/trade, 95% CI [{lo:.4f}, {hi:.4f}] below 0"
    return "INSUFFICIENT", f"95% CI [{lo:.4f}, {hi:.4f}] includes 0"


def cost_split(exits):
    """Per-rule split of net P&L into signal (mid to mid), spread and fees, from exits that carry mid prices."""
    rows = {}
    for e in exits:
        if "mid_in" not in e:
            continue
        gross = e["qty"] * (e["mid_out"] - e["mid_in"])
        fees = e["fee_in"] + e["fee_out"]
        r = rows.setdefault(e["rule"], [0, 0.0, 0.0, 0.0, 0.0])
        r[0] += 1
        r[1] += gross
        r[2] += gross - fees - e["pnl"]  # what remains is spread
        r[3] += fees
        r[4] += e["pnl"]
    return rows


def main():
    state = json.loads((OUT / "state.json").read_text())
    events = [json.loads(line) for line in (OUT / "log.jsonl").read_text().splitlines() if line.strip()]
    ticks = read_ticks()
    events = dedupe(events)
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
    prereg = json.loads((OUT.parent.parent / "docs" / "paper_prereg.json").read_text())
    from paper_bot import equity as _eq  # noqa: E402
    v, why = verdict([t["pnl"] for t in trades], _eq(state), prereg)
    print(f"PAPER ONLY (fills at real bid/ask, no queue or latency modelling: real results would be worse)")
    print(f"VERDICT: {v} - {why}")
    split = cost_split(trades)
    if split:
        print("per rule: trades | signal (mid to mid) | spread | fees | net")
        for rule, (n, g, s, f, net) in split.items():
            print(f"  {rule}: {n} | {g:+.4f} | -{s:.4f} | -{f:.4f} | {net:+.4f}")
    else:
        print("cost split: no closed trades with mid prices logged yet (older trades lack them)")
    if ticks:
        eqs = [t[4] for t in ticks]
        print(f"ticks: {len(ticks)} rows, equity min={min(eqs):.3f} max={max(eqs):.3f} last={eqs[-1]:.3f}")
        by = {}
        for ts, sym, bid, ask, _ in ticks:
            by.setdefault(sym, []).append((ts, bid, ask))
        for sym, r in by.items():
            hold = (r[-1][1] + r[-1][2]) / 2 / ((r[0][1] + r[0][2]) / 2) - 1
            print(f"  hold-only {sym} over the same period: {hold*100:+.2f}% (bot: {(eqs[-1]/eqs[0]-1)*100:+.2f}%)")
            spr = [(a - b) / ((a + b) / 2) * 1e4 for _, b, a in r]
            gaps = [(r[i + 1][0] - r[i][0]) / 1000 for i in range(len(r) - 1) if r[i + 1][0] - r[i][0] > 2000]
            print(f"  {sym}: mean spread {sum(spr)/len(spr):.3f} bps, gaps>2s: {len(gaps)} (max {max(gaps, default=0):.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
