"""
Collector health check (analysis only). Reads the L2 files written by scripts/collect_l2.py, either a local
pulled copy or a directory on the Railway volume, and reports whether the data is continuous enough to
trust: hours covered, event counts, gaps longer than 60s, window coverage, and the collector's own stats.

Exit code 0 = healthy, 1 = problems found (for use in a scheduled task).

Usage: python scripts/health_check.py [--dir data/l2_pulled/l2] [--max-gap 60] [--min-hours 12]
"""
import argparse
import glob
import gzip
import json
import os
import sys
import time
from collections import Counter


def load_ts(pattern):
    """(sorted timestamps of all rows, Counter of row types, set of windows)."""
    ts, kinds, windows = [], Counter(), set()
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts.append(r["ts"])
                kinds[r.get("t")] += 1
                if "w" in r:
                    windows.add(r["w"])
    ts.sort()
    return ts, kinds, windows


def find_gaps(ts, max_gap):
    """[(start, end, length)] for consecutive timestamps more than max_gap seconds apart."""
    return [(a, b, b - a) for a, b in zip(ts, ts[1:]) if b - a > max_gap]


def summarize(directory, max_gap=60.0, min_hours=12.0, now=None):
    ts, kinds, windows = load_ts(os.path.join(directory, "l2_*.jsonl*"))
    problems = []
    out = {"rows": len(ts), "kinds": dict(kinds), "windows": len(windows)}
    if not ts:
        return dict(out, hours=0.0, gaps=[], problems=["no L2 rows found"])
    hours = (ts[-1] - ts[0]) / 3600.0
    gaps = find_gaps(ts, max_gap)
    out.update(hours=hours, first=ts[0], last=ts[-1], gaps=gaps)
    if hours < min_hours:
        problems.append(f"only {hours:.1f}h of data (need {min_hours:g}h)")
    if gaps:
        problems.append(f"{len(gaps)} gap(s) over {max_gap:g}s, longest {max(g[2] for g in gaps):.0f}s")
    age = (now or time.time()) - ts[-1]
    if age > 300:
        problems.append(f"newest row is {age / 60:.0f} min old (collector may be down)")
    stats_path = os.path.join(directory, "collector_stats.json")
    if os.path.exists(stats_path):
        try:
            st = json.load(open(stats_path))
            out["stats"] = {k: st.get(k) for k in ("connected", "reconnects", "trades", "snaps", "arb_episodes")}
            if st.get("connected") is False:
                problems.append("collector reports connected=false")
        except (OSError, json.JSONDecodeError):
            problems.append("collector_stats.json unreadable")
    out["problems"] = problems
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/l2_pulled/l2")
    ap.add_argument("--max-gap", type=float, default=60.0)
    ap.add_argument("--min-hours", type=float, default=12.0)
    args = ap.parse_args()
    s = summarize(args.dir, args.max_gap, args.min_hours)
    print(f"rows {s['rows']}  types {s['kinds']}  windows {s['windows']}  hours {s.get('hours', 0):.1f}")
    if s.get("stats"):
        print("collector:", s["stats"])
    for g in s.get("gaps", [])[:5]:
        print(f"  gap {g[2]:.0f}s starting {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(g[0]))} UTC")
    if s["problems"]:
        print("UNHEALTHY:", "; ".join(s["problems"]))
        sys.exit(1)
    print("HEALTHY")


if __name__ == "__main__":
    main()
