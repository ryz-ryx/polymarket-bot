"""Replay test: feed a real stored tick file through on_tick twice and assert the outcome is identical.
Catches any hidden nondeterminism (wall-clock reads, dict ordering, float drift) in the position/exit logic.
"""
import csv
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import paper_bot as pb  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "paper" / "ticks_20260920_13.csv.gz"


def _load_rows(n=3000):
    with gzip.open(FIXTURE, "rt", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[:n]


def _replay(rows, open_rule, hold_min=1):
    """Open one synthetic position at the first row, then feed every row through on_tick."""
    st = pb.new_state()
    sym = rows[0]["sym"]
    st["pos"][sym] = {"rule": open_rule, "qty": 1.0, "cost_basis": 10.0,
                       "exit_ts": int(rows[0]["ts_ms"]) + hold_min * 60_000}
    events = []
    for r in rows:
        events += pb.on_tick(st, r["sym"], float(r["bid"]), float(r["ask"]), int(r["ts_ms"]))
    return st, events


def test_replay_is_deterministic():
    rows = _load_rows()
    assert rows, "fixture tick file is empty or missing"
    st1, ev1 = _replay(rows, "R1_momentum")
    st2, ev2 = _replay(rows, "R1_momentum")
    assert ev1 == ev2
    assert st1["cash"] == st2["cash"] and st1["closed_pnl"] == st2["closed_pnl"]


def test_replay_exit_uses_real_bid_not_synthetic_price():
    rows = _load_rows()
    st, ev = _replay(rows, "R2_reversion")
    exits = [e for e in ev if e["action"] == "exit"]
    assert exits, "fixture window too short to reach the hold time; increase _load_rows(n)"
    first_row_after_exit = next(r for r in rows if int(r["ts_ms"]) == exits[0]["ts"])
    assert exits[0]["px"] == float(first_row_after_exit["bid"])
