#!/usr/bin/env bash
set -euo pipefail

CHALLENGE="${1:-smoke_min}"
LABEL="com.research.arena-daemon"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$HERE/../.." && pwd)"
DAEMON_URL="${ORACLE_DAEMON_URL:-http://127.0.0.1:8787}"
STATE_ROOT="${ORACLE_STATE_ROOT:-$PROJECT/state}"

echo "== launchd =="
launchctl list | grep -F "$LABEL" || echo "  (not loaded)"

echo
echo "== process =="
pgrep -fl "daemon.py.*--challenge ${CHALLENGE}" || pgrep -fl "arena/daemon.py" || echo "  (no arena daemon process running)"

echo
echo "== daemon health =="
python3 "$PROJECT/arena/client.py" health --daemon "$DAEMON_URL" || true

echo
echo "== recent log lines =="
for d in "$STATE_ROOT/$CHALLENGE/daemon.log" "$STATE_ROOT/daemon.launchd.err.log" "$STATE_ROOT/daemon.launchd.out.log"; do
  [[ -f "$d" ]] || continue
  echo "-- $d (tail 20) --"
  tail -n 20 "$d"
done
