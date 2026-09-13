#!/bin/bash
# Install claude-stats-exporter.py as a per-user launchd agent on a Mac.
# Serves :9101/metrics for the `claude-code-stats` Prometheus job — after
# installing, add this Mac as a target in 02-config-prometheus.yaml.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/claude-stats-exporter.py"
DEST_DIR="$HOME/.local/bin"
DEST="$DEST_DIR/claude-stats-exporter.py"
LABEL="com.claude-stats-exporter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
PORT="${PORT:-9101}"
INTERVAL="${INTERVAL:-300}"

mkdir -p "$DEST_DIR" "$(dirname "$PLIST")" "$LOG_DIR"
install -m 0755 "$SRC" "$DEST"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$DEST</string>
    <string>--port</string><string>$PORT</string>
    <string>--interval</string><string>$INTERVAL</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/claude-stats-exporter.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/claude-stats-exporter.log</string>
</dict>
</plist>
PL

UID_NUM="$(id -u)"
if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$UID_NUM/$LABEL"
  sleep 2                            # let launchd finish tearing the job down
fi
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

for _ in $(seq 1 30); do            # first scan takes a few seconds
  if curl -fsS "http://127.0.0.1:$PORT/metrics" 2>/dev/null | grep -q '^ai_total_sessions'; then
    echo "claude-stats-exporter running on :$PORT ($(hostname))"
    exit 0
  fi
  sleep 1
done
echo "exporter did not come up; see $LOG_DIR/claude-stats-exporter.log" >&2
exit 1
