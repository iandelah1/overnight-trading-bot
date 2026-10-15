#!/bin/bash
# Stop and remove the scheduled bot job.
LABEL="com.overnightbot.tick"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed $LABEL"
