#!/usr/bin/env bash
# Runs scripts/paper_bot.py in the background for RUN_MINUTES, committing a status snapshot
# (docs/status.json) every COMMIT_INTERVAL_S seconds so the GitHub Pages dashboard stays current.
# Never modifies trading/sizing parameters -- see docs/FREEZE.md.
set -uo pipefail

RUN_MINUTES=320
COMMIT_INTERVAL_S=300

mkdir -p docs

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

commit_and_push() {
  git add data/paper/state.json data/paper/live.log data/paper/log.jsonl docs/status.json 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -m "paper bot: status update ($(date -u +%FT%TZ))" -q
    for attempt in 1 2 3; do
      if git push -q; then
        break
      fi
      git pull --rebase -q
      sleep 2
    done
  fi
}

python3 scripts/paper_bot.py "$RUN_MINUTES" > /tmp/paper_bot_output.json &
BOT_PID=$!

while kill -0 "$BOT_PID" 2>/dev/null; do
  sleep "$COMMIT_INTERVAL_S"
  write_status
  commit_and_push
done

wait "$BOT_PID"
write_status
commit_and_push
cat /tmp/paper_bot_output.json 2>/dev/null || true
