#!/usr/bin/env bash
# Install (or reinstall) the weekday 06:30 launchd job on macOS.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.macrodash.daily.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
sed "s|__REPO__|$REPO|g" "$REPO/scheduling/com.macrodash.daily.plist" > "$DEST"
launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed $DEST"
echo "Mac time zone: $(readlink /etc/localtime | sed 's|.*/zoneinfo/||')  (job fires at 06:30 local)"
echo "Test now with: launchctl kickstart -k gui/$(id -u)/com.macrodash.daily"
