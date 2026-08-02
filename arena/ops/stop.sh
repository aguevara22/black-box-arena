#!/usr/bin/env bash
set -euo pipefail

LABEL="com.research.arena-daemon"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "$PLIST" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
fi

echo "unloaded ${LABEL}"
