"""
Run-once harness for pre-registered test L on the FRESH sample. Analysis only; touches no trading code.

Guards (all fixed in advance):
  1. Refuses to start unless every expected chunk is present (default 336) and no file is still being written.
  2. Refuses to start if data/L_RAN.lock exists (the fresh sample may be evaluated exactly once).
  3. Writes the lock BEFORE running, so a crash or a disliked result can not be followed by a second look.
  4. Saves stdout, the git commit and the FREEZE.md / late_lock_test.py hashes to data/L_result_<utc>.txt.

Usage: python scripts/run_test_l_once.py [--expected 336]
"""
import argparse
import datetime as dt
import glob
import hashlib
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "data", "L_RAN.lock")
FRESH = "data/trades2/chunk_*.csv.gz"
OLD = "data/trades/chunk_*.csv.gz"
QUIET_SEC = 120


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def preflight(expected):
    """Return an error string, or None when the run may proceed."""
    if os.path.exists(LOCK):
        return f"REFUSED: {LOCK} exists. The fresh sample was already evaluated once."
    files = glob.glob(os.path.join(ROOT, FRESH))
    if len(files) < expected:
        return f"REFUSED: {len(files)}/{expected} chunks present. Wait for the download to finish."
    newest = max(os.path.getmtime(f) for f in files)
    if time.time() - newest < QUIET_SEC:
        return "REFUSED: a chunk was written in the last 2 minutes; the download may still be running."
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", type=int, default=336)
    args = ap.parse_args()
    err = preflight(args.expected)
    if err:
        print(err)
        return 2
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open(LOCK, "w") as f:
        f.write(f"started {stamp}\n")
    cmd = [sys.executable, "scripts/late_lock_test.py", "--fresh", "--trades", FRESH, "--exclude", OLD]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = os.path.join(ROOT, "data", f"L_result_{stamp}.txt")
    with open(out, "w") as f:
        f.write(f"commit {git_head()}\nFREEZE.md {sha(os.path.join(ROOT, 'FREEZE.md'))}\n")
        f.write(f"late_lock_test.py {sha(os.path.join(ROOT, 'scripts', 'late_lock_test.py'))}\n")
        f.write(f"exit {proc.returncode}\n\n{proc.stdout}\n{proc.stderr}")
    print(proc.stdout)
    print(f"saved {out}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
