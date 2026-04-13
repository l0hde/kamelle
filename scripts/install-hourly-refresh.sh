#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.kamelle.hourly-refresh.plist"
LOG="$HOME/Library/Logs/kamelle-hourly-refresh.log"
PY="$ROOT/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo '⚠️ Kamelle venv not found. Run scripts/install-local.sh first.'
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.kamelle.hourly-refresh</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>kamelle</string>
    <string>refresh</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"

echo "✨ Installed hourly Kamelle refresh."
echo "Plist: $PLIST"
echo "Log:   $LOG"
