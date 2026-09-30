#!/usr/bin/env bash
# Start the contestant runner under launchd (macOS) so it survives your
# terminal and is restarted if it crashes. Usage:
#   arena/ops/runner-start.sh <challenge> [runner arguments...]
# Example:
#   arena/ops/runner-start.sh ising_lift --seat claude=claude --seat codex=codex
# The daemon must already be running (arena/ops/start.sh <challenge>).
set -euo pipefail

CHALLENGE="${1:?usage: runner-start.sh <challenge> [runner args]}"
shift || true
LABEL="com.research.arena-runner"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$HERE/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="$PROJECT/.venv/bin/python"
STATE_ROOT="${ORACLE_STATE_ROOT:-$PROJECT/state}"
DAEMON_URL="${ORACLE_DAEMON_URL:-http://127.0.0.1:8787}"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$STATE_ROOT"

EXTRA_ARGS=""
for arg in "$@"; do
  EXTRA_ARGS+="    <string>${arg}</string>
"
done

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${PROJECT}/arena/runner.py</string>
    <string>--challenge</string>
    <string>${CHALLENGE}</string>
    <string>--daemon</string>
    <string>${DAEMON_URL}</string>
${EXTRA_ARGS}  </array>

  <key>WorkingDirectory</key>
  <string>${PROJECT}</string>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
    <key>Crashed</key>
    <true/>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>60</integer>

  <key>StandardOutPath</key>
  <string>${STATE_ROOT}/runner.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_ROOT}/runner.launchd.err.log</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PROJECT}/.venv/bin:${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>ORACLE_STATE_ROOT</key>
    <string>${STATE_ROOT}</string>
    <key>ORACLE_DAEMON_URL</key>
    <string>${DAEMON_URL}</string>
  </dict>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "loaded ${LABEL} for challenge ${CHALLENGE}; gauges under ${STATE_ROOT}/${CHALLENGE}/runner/"
launchctl list | grep -F "$LABEL" || true
