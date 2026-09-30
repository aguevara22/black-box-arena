#!/usr/bin/env bash
# Stop the launchd-supervised contestant runner (macOS).
set -euo pipefail
LABEL="com.research.arena-runner"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
if [[ -f "$PLIST" ]]; then
  launchctl unload "$PLIST" && echo "unloaded ${LABEL}"
else
  echo "no ${PLIST}; nothing to stop"
fi
