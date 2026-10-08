#!/usr/bin/env bash
# Wrapper used by launchd/cron: runs the pipeline from the repo root with the
# project's virtualenv (if present) and appends output to logs/scheduler.log.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
mkdir -p logs
if [ -x "$REPO/.venv/bin/python" ]; then PY="$REPO/.venv/bin/python"; else PY="$(command -v python3)"; fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') run start" >> logs/scheduler.log
"$PY" run.py >> logs/scheduler.log 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') run end (exit $?)" >> logs/scheduler.log
