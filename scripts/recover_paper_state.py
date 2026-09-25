"""Rebuild data/paper/state.json from data/paper/log.jsonl after corruption.

Replays every enter/exit event through the REAL enter()/exit_pos() functions from paper_bot.py,
in timestamp order, starting from a fresh $50 account. Since sizing and PnL are fully deterministic
given (sym, rule, px, ts), this reconstructs the exact same trajectory as the original run -- not
a guess. Any exit with no matching open position, or an unexpectedly-priced pnl mismatch against
the originally-logged value, is printed as a warning (both were seen once, from a brief window on
2026-09-23 when two bot instances ran concurrently against the same log file). Small (a few cents)
drift from that incident is expected and was verified against the last good data/paper/live.log
line before use.

Usage: python scripts/recover_paper_state.py [--apply]
  Without --apply: prints the reconstructed state and exits (dry run).
  With --apply: writes data/paper/state.json (atomic), after backing up the corrupted file to
  data/paper/state.json.corrupted-<timestamp>.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_bot import new_state, enter, exit_pos, OUT  # noqa: E402
from fast_backtest import RULES  # noqa: E402


def replay(log_path):
    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("action") in ("enter", "exit"):
                events.append(ev)
    events.sort(key=lambda e: e["ts"])

    state = new_state()
    for ev in events:
        if ev["action"] == "enter":
            hold = RULES[ev["rule"]][1]
            r = enter(state, ev["sym"], ev["rule"], hold, ev["px"], ev["ts"])
            if r is None:
                print(f"WARN: enter skipped (already in pos or notional<5): {ev}", file=sys.stderr)
        elif ev["action"] == "exit":
            if ev["sym"] not in state["pos"]:
                print(f"WARN: exit with no matching open pos, skipping: {ev}", file=sys.stderr)
                continue
            r = exit_pos(state, ev["sym"], ev["px"], ev["ts"])
            logged_pnl = ev.get("pnl")
            if logged_pnl is not None and abs(logged_pnl - round(r["pnl"], 4)) > 0.0005:
                print(f"MISMATCH pnl: logged={logged_pnl} replayed={round(r['pnl'], 4)} ev={ev}",
                      file=sys.stderr)

    for sym, p in state["pos"].items():
        state.setdefault("mark", {})[sym] = p.get("mid_in", p["cost_basis"] / p["qty"])
    return state, len(events)


def main():
    apply = "--apply" in sys.argv
    log_path = OUT / "log.jsonl"
    if not log_path.exists():
        print(f"No {log_path} found, cannot recover.", file=sys.stderr)
        return 1

    state, n = replay(log_path)
    print(json.dumps({k: v for k, v in state.items() if k not in ("pos", "mark")}, indent=2))
    print("open positions:", list(state["pos"].keys()))
    print("total events replayed:", n)

    if apply:
        sp = OUT / "state.json"
        if sp.exists():
            backup = OUT / f"state.json.corrupted-{int(time.time())}"
            sp.rename(backup)
            print(f"Backed up existing state.json to {backup}")
        tmp = OUT / "state.json.tmp"
        tmp.write_text(json.dumps(state))
        tmp.replace(sp)
        print(f"Wrote recovered state to {sp}")
    else:
        print("\nDry run only. Re-run with --apply to write data/paper/state.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
