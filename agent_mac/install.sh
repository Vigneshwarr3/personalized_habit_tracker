#!/usr/bin/env bash
# Install / update the Mac agent + nightly backup copy as launchd jobs (start at login, restart if they crash).
#   ./agent_mac/install.sh                      # uses the token in an existing config.yaml
#   HT_DEVICE_TOKEN=ht_... ./agent_mac/install.sh
#
# The agent is copied to ~/Library/Application Support/HabitTracker (launchd can't read ~/Downloads without
# extra Files & Folders permission). Re-run after pulling changes to update it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$HOME/Library/Application Support/HabitTracker"
APP_DIR="$HOME_DIR/app"
VENV="$HOME_DIR/venv"
LOGS="$HOME/Library/Logs/HabitTracker"
LA="$HOME/Library/LaunchAgents"
PY="${HT_PYTHON:-/usr/local/bin/python3.11}"
SERVER_URL="${HT_SERVER_URL:-https://your-server.your-tailnet.ts.net:8765}"
DEVICE="${HT_DEVICE_NAME:-$(scutil --get LocalHostName 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo macbook)}"
BACKUP_SRC="${HT_BACKUP_SRC:-user@100.x.y.z:habit-tracker/data/backups/}"
BACKUP_DST="${HT_BACKUP_DST:-$HOME/HabitTracker-Backups}"

mkdir -p "$APP_DIR" "$LOGS" "$LA" "$BACKUP_DST"
chmod 700 "$HOME_DIR"

echo "==> copying agent code"
rsync -a --exclude '__pycache__' --exclude 'tests' "$ROOT/agent_mac" "$APP_DIR/"

echo "==> python venv"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements-agent.txt"

CFG="$HOME_DIR/config.yaml"
if [ ! -f "$CFG" ]; then
  if [ -z "${HT_DEVICE_TOKEN:-}" ]; then
    echo "No $CFG yet. Re-run with HT_DEVICE_TOKEN=<token> (create one on the dashboard: Privacy & devices)." >&2
    exit 1
  fi
  umask 077
  cat > "$CFG" <<EOF
# Habit Tracker Mac agent. Edit, then: launchctl kickstart -k gui/$(id -u)/com.habittracker.agent
server_url: $SERVER_URL
token: $HT_DEVICE_TOKEN
device: $DEVICE
sample_seconds_ac: 5
sample_seconds_battery: 15
idle_threshold_seconds: 120
upload_every_seconds: 60
incognito: skip
ignore_assertion_processes: []   # add apps that keep the display awake while you're away
EOF
  echo "    wrote $CFG"
fi

echo "==> launchd jobs"
cat > "$LA/com.habittracker.agent.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.habittracker.agent</string>
  <key>ProgramArguments</key><array>
    <string>$VENV/bin/python</string><string>-m</string><string>agent_mac</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/agent.log</string>
  <key>StandardErrorPath</key><string>$LOGS/agent.log</string>
</dict></plist>
EOF

# Copies the server's nightly backups to this Mac every 6 h. rsync without --delete: nothing is ever removed.
cat > "$LA/com.habittracker.backup-pull.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.habittracker.backup-pull</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/rsync</string><string>-a</string><string>--ignore-existing</string>
    <string>-e</string><string>/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=15</string>
    <string>$BACKUP_SRC</string><string>$BACKUP_DST/</string>
  </array>
  <key>StartInterval</key><integer>21600</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>$LOGS/backup-pull.log</string>
  <key>StandardErrorPath</key><string>$LOGS/backup-pull.log</string>
</dict></plist>
EOF

for job in com.habittracker.agent com.habittracker.backup-pull; do
  launchctl bootout "gui/$(id -u)/$job" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do   # wait until launchd has really unloaded it (avoids "Bootstrap failed: 5")
    launchctl print "gui/$(id -u)/$job" >/dev/null 2>&1 || break; sleep 1
  done
  launchctl bootstrap "gui/$(id -u)" "$LA/$job.plist"
done

echo "==> done. Check it with:"
echo "   cd \"$APP_DIR\" && \"$VENV/bin/python\" -m agent_mac status"
echo "   tail -f \"$LOGS/agent.log\""
