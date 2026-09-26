#!/usr/bin/env bash
# Runs scripts/paper_bot.py in the background for RUN_MINUTES, committing a status snapshot
# (docs/status.json) every COMMIT_INTERVAL_S seconds so the GitHub Pages dashboard stays current.
# Never modifies trading/sizing parameters -- see docs/FREEZE.md.
#
# 2026-09-26 fix: actions/checkout@v4 leaves the repo in DETACHED HEAD, which silently broke every
# push/pull-rebase in the old version of this script (every attempt failed with "You are not
# currently on a branch" / "unstaged changes"). That left the repo in a stuck partial-rebase state
# repeatedly, which appears to have also reverted the bot's own local state.json between commit
# attempts -- three full ~5-7h runs on 2026-09-25/26 show trades/equity frozen at the exact same
# values the entire time. Fix: (1) check out a real local branch before doing anything else, so
# push/pull have something to reference; (2) the 5-minute loop now ONLY touches docs/status.json
# (small, low-conflict-risk) instead of the trading-critical files -- those commit once, at the
# very end of the run, when there's nothing left to lose from a failed retry.
set -uo pipefail

RUN_MINUTES=320
COMMIT_INTERVAL_S=300

mkdir -p docs

# Fix detached HEAD: checkout@v4 leaves us on a commit, not a branch. Without this, every
# push/pull below fails immediately and silently (the old bug).
git checkout -B main
git branch --set-upstream-to=origin/main main 2>/dev/null || true

write_status() {
  python3 - <<'PYEOF'
import json
from pathlib import Path
from datetime import datetime, timezone

root = Path.cwd()
state_path = root / "data" / "paper" / "state.json"
log_path = root / "data" / "paper" / "live.log"

state = json.loads(state_path.read_text()) if state_path.exists() else {}
equity = state.get("cash", 0.0) + sum(
    q.get("qty", 0.0) * state.get("mark", {}).get(sym, (q.get("cost_basis", 0.0) / q["qty"]) if q.get("qty") else 0.0)
    for sym, q in state.get("pos", {}).items()
)
last_lines = []
if log_path.exists():
    with open(log_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4000))
        last_lines = f.read().decode("utf-8", errors="replace").splitlines()[-10:]

status = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "cash": round(state.get("cash", 0.0), 4),
    "equity": round(equity, 4),
    "trades": state.get("trades", 0),
    "fees": round(state.get("fees", 0.0), 4),
    "closed_pnl": round(state.get("closed_pnl", 0.0), 4),
    "open_positions": list(state.get("pos", {}).keys()),
    "target_trades": 200,
    "recent_log": last_lines,
}
Path("docs/status.json").write_text(json.dumps(status, indent=2))
PYEOF
}

# Periodic: status.json ONLY. One small file, effectively never conflicts with itself across
# runs (concurrency group serializes runs anyway), so a simple checkout-ours on conflict is safe.
commit_status_only() {
  git add docs/status.json 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -m "paper bot: status update ($(date -u +%FT%TZ))" -q
    for attempt in 1 2 3; do
      if git push -q origin main; then
        return 0
      fi
      git fetch -q origin main
      git rebase -q -X ours origin/main || { git rebase --abort 2>/dev/null; git reset -q --hard origin/main; }
      sleep 2
    done
  fi
}

# Final: the real trading records. Runs once, when the bot process has already exited, so a
# failed push here just means retrying against whatever's now on origin -- never data loss,
# since state.json/live.log/log.jsonl are files on disk regardless of git state.
commit_final() {
  git add data/paper/state.json data/paper/live.log data/paper/log.jsonl docs/status.json 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -m "paper bot: final status ($(date -u +%FT%TZ))" -q
    for attempt in 1 2 3; do
      if git push -q origin main; then
        return 0
      fi
      git fetch -q origin main
      git rebase -q origin/main && continue
      echo "WARNING: final push failed after rebase, retrying" >&2
      sleep 3
    done
  fi
}

python3 scripts/paper_bot.py "$RUN_MINUTES" > /tmp/paper_bot_output.json &
BOT_PID=$!

while kill -0 "$BOT_PID" 2>/dev/null; do
  sleep "$COMMIT_INTERVAL_S"
  write_status
  commit_status_only
done

wait "$BOT_PID"
write_status
commit_final
cat /tmp/paper_bot_output.json 2>/dev/null || true
