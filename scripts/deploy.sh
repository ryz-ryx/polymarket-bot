#!/usr/bin/env bash
# Reliable Railway deploy for this project.
#
# `railway up` / `railway up --ci` have a platform bug where the build-diffing
# step ("no changes detected in watch paths") falsely skips rebuilds even
# against genuinely different, freshly-pushed content. The one path that
# reliably forces a real rebuild is redeploying the *specific* deployment
# entry (whether ACTIVE or SKIPPED) rather than asking Railway to diff a new
# upload -- that's what the dashboard's "Redeploy" button does, and
# `railway redeploy` (no `--from-source`) does the same thing from the CLI.
#
# Usage: scripts/deploy.sh [service-name]
set -euo pipefail

SERVICE="${1:-polymarket-bot}"
export MSYS_NO_PATHCONV=1

echo "==> Pushing latest commit (deploy trigger)..."
git push

echo "==> Waiting for Railway to register the new deployment..."
sleep 8

echo "==> Forcing rebuild via 'railway redeploy' (bypasses the watch-path diff bug)..."
npx @railway/cli@latest redeploy --service "$SERVICE" --yes

echo "==> Polling deployment status..."
for i in $(seq 1 30); do
  STATUS=$(npx @railway/cli@latest deployment list --service "$SERVICE" --json 2>/dev/null \
    | python -c "import json,sys; d=json.load(sys.stdin); print(d[0]['status'])" 2>/dev/null || echo "UNKNOWN")
  echo "  [$i/30] status=$STATUS"
  case "$STATUS" in
    SUCCESS) echo "==> Deploy succeeded."; exit 0 ;;
    FAILED|REMOVED) echo "==> Deploy failed (status=$STATUS)."; exit 1 ;;
  esac
  sleep 10
done

echo "==> Timed out waiting for deploy to finish. Check the Railway dashboard."
exit 1
