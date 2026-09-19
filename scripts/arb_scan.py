"""
Phase 1: complete-set parity feasibility from the collector's arb_log.jsonl (analysis only).

Kill rule (pre-registered in the plan): arb is dead if the median episode lasts under 1s, or if
there is fewer than one fee-clearing episode per day at size >= 5 shares.
`net` ignores gas and merge cost, so it is an upper bound.

Usage: python scripts/arb_scan.py [--log data/l2/arb_log.jsonl] [--min-shares 5]
"""
import argparse
import json
import statistics
import sys

BUCKETS = [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, float("inf"))]


def load(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def summarize(rows, min_shares=5.0):
    if not rows:
        return None
    span_days = max((max(r["end"] for r in rows) - min(r["start"] for r in rows)) / 86400.0, 1e-9)
    durs = [r["dur"] for r in rows]
    good = [r for r in rows if r["net"] > 0 and r["shares"] >= min_shares]
    hist = {f"{lo:g}-{hi:g}s": sum(1 for d in durs if lo <= d < hi) for lo, hi in BUCKETS}
    per_day = len(good) / span_days
    median = statistics.median(durs)
    return {"episodes": len(rows), "span_days": span_days, "median_dur": median, "hist": hist,
            "fee_clearing": len(good), "per_day": per_day,
            "max_net": max(r["net"] for r in rows), "kill": median < 1.0 or per_day < 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="data/l2/arb_log.jsonl")
    ap.add_argument("--min-shares", type=float, default=5.0)
    args = ap.parse_args()
    try:
        s = summarize(load(args.log), args.min_shares)
    except FileNotFoundError:
        sys.exit(f"no arb log at {args.log} yet (collector not running or no YES+NO<1 seen)")
    if s is None:
        sys.exit("arb log is empty: no YES+NO<1 episode observed. That is itself the answer if the collector has run for days.")
    print(f"episodes: {s['episodes']} over {s['span_days']:.2f} days   median duration {s['median_dur']:.2f}s")
    print("duration histogram:", s["hist"])
    print(f"fee-clearing (net>0, >= {args.min_shares:g} shares): {s['fee_clearing']}  = {s['per_day']:.2f}/day   best net/share {s['max_net']:.4f}")
    print("VERDICT:", "KILL arb (too brief or too rare)" if s["kill"] else "ARB CANDIDATE: check gas/merge cost next")


if __name__ == "__main__":
    main()
