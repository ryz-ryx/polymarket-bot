"""Simulate the frozen live BTC strategy restricted to two 4-hour sessions/day, over the most
recent 30 days of real Polymarket history. Reuses walk_forward.py's fill logic (slip=True, the
realistic-fill variant) since that's the only one worth reporting per docs/CLOSING_MEMO_164.md.

NOT a new pre-registered statistical test -- this is a monitoring/exploratory simulation to match
the operator's actual 2x4h/day machine-on schedule, reported honestly as "backtest, known-optimistic"
per the structural limitation documented in walk_forward.py and docs/CLOSING_MEMO_164.md (no historical
order-book depth, so it cannot reproduce the live bot's real-time trade-rejection logic).

Usage: python scripts/session_simulate.py [--asset BTC] [--session-hours 8,12,20,24]
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from walk_forward import FROZEN_BTC, build_inputs, path_for, run, summarize, fmt
from replay_real_market import load_real_windows, load_binance_spot


def in_session(ts, session_hours):
    hour = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).hour
    for i in range(0, len(session_hours), 2):
        lo, hi = session_hours[i], session_hours[i + 1]
        if lo <= hour < hi:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--session-hours", default="8,12,20,24",
                     help="comma-separated UTC hour pairs, e.g. 8,12,20,24 = 08:00-12:00 and 20:00-00:00")
    args = ap.parse_args()
    asset = args.asset.upper()
    session_hours = [int(x) for x in args.session_hours.split(",")]

    real_path, spot_path = path_for(asset)
    inputs = build_inputs(load_real_windows(real_path), load_binance_spot(spot_path))
    if not inputs:
        print("No matched windows.", file=sys.stderr)
        return 1

    last_ts = inputs[-1]["window_ts"]
    cutoff = last_ts - args.days * 86_400
    month = [w for w in inputs if w["window_ts"] >= cutoff]
    sessioned = [w for w in month if in_session(w["window_ts"], session_hours)]

    d0 = datetime.datetime.fromtimestamp(cutoff, datetime.timezone.utc).date()
    d1 = datetime.datetime.fromtimestamp(last_ts, datetime.timezone.utc).date()
    print(f"[{asset}] last {args.days}d of data: {d0} to {d1} | {len(month)} windows total, "
          f"{len(sessioned)} inside sessions ({session_hours[0]:02d}:00-{session_hours[1]:02d}:00 and "
          f"{session_hours[2]:02d}:00-{session_hours[3]%24:02d}:00 UTC)")
    print("LABEL: backtest, known-optimistic (no historical order-book depth -- see docs/CLOSING_MEMO_164.md).\n")

    pnls_realistic = run(sessioned, slip=True, **FROZEN_BTC)
    pnls_optimistic = run(sessioned, slip=False, **FROZEN_BTC)
    print(fmt("Session sim, realistic fill (slip=True)", summarize(pnls_realistic)))
    print(fmt("Session sim, optimistic fill (slip=False)", summarize(pnls_optimistic)))

    strat_inputs_by_day = {}
    for w in sessioned:
        day = datetime.datetime.fromtimestamp(w["window_ts"], datetime.timezone.utc).date()
        strat_inputs_by_day.setdefault(day, []).append(w)
    print("\nPer-day (realistic fill):")
    for day in sorted(strat_inputs_by_day):
        day_pnls = run(strat_inputs_by_day[day], slip=True, **FROZEN_BTC)
        s = summarize(day_pnls)
        if s["n"]:
            print(f"  {day}  n={s['n']:>3}  win={s['win']*100:5.1f}%  rate={s['rate']*100:+7.2f}%")
        else:
            print(f"  {day}  n=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
