#!/usr/bin/env bash
set -euo pipefail

CHALLENGE="${1:-smoke_min}"
LABEL="com.research.arena-daemon"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$HERE/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="$PROJECT/.venv/bin/python"
STATE_ROOT="${ORACLE_STATE_ROOT:-$PROJECT/state}"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$STATE_ROOT"

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
    <string>${PROJECT}/arena/daemon.py</string>
    <string>--challenge</string>
    <string>${CHALLENGE}</string>
  </array>

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
  <integer>30</integer>

  <key>StandardOutPath</key>
  <string>${STATE_ROOT}/daemon.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_ROOT}/daemon.launchd.err.log</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PROJECT}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>ORACLE_STATE_ROOT</key>
    <string>${STATE_ROOT}</string>
  </dict>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "loaded ${LABEL} for challenge ${CHALLENGE}"
launchctl list | grep -F "$LABEL" || true
