#!/bin/bash
# Install a macOS launchd job that runs `run_bot.py tick` every 5 minutes.
# The bot itself decides whether there's anything to do at that moment.
# Usage: [PROFILE=all] scripts/install_schedule.sh [--live]
#   PROFILE=all runs every profiles/*.env; default runs just .env
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.overnightbot.tick"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
EXTRA_ARG=""
PROFILE="${PROFILE:-default}"
[[ "${1:-}" == "--live" ]] && EXTRA_ARG="<string>--live</string>"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/run_bot.py</string>
    <string>tick</string>
    <string>--profile</string>
    <string>$PROFILE</string>
    $EXTRA_ARG
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.err</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL for profile '$PROFILE' (runs every 5 minutes). Logs: $DIR/logs/bot.log"
