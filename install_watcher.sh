#!/bin/bash
# ============================================================================
# INSTALL THE WATCHER (optional): a background job that keeps the run-out
# forecast fresh and can alert you when the account in use is nearly out
#
#   bash install_watcher.sh            install and start it
#   bash install_watcher.sh --print    show the launchd file it would write. Changes nothing.
#   bash install_watcher.sh --remove   stop and remove it
#
# Run install.sh first. The cockpit works without the watcher; the FORECAST panel
# just has nothing to show.
#
# WHAT IT DOES ONCE RUNNING
#   - every 5 minutes: records each account's usage and recomputes the forecast
#   - if "enabled" is true in ~/.claude-profiles/autoswitch.json (the cockpit's `a`
#     key flips it): alerts when the account in use reaches the threshold
#   - it swaps accounts by itself ONLY if you turned on "swap_enabled" AND armed it
#     with `claude-autoswitch --arm`, and then exactly once
# ============================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude-profiles"
LABEL="com.claude-cockpit.autoswitch"      # SYNC WARNING: same label in uninstall.sh and the template
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$SRC/launchd/autoswitch.plist.template"

MODE="install"
case "${1:-}" in
  "") : ;;
  --print)  MODE="print" ;;
  --remove) MODE="remove" ;;
  -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: $1   (try --help)"; exit 2 ;;
esac

# Fill the template. Python, not sed: a home folder path may contain characters
# that mean something to sed or to XML, and the values must be XML-escaped.
# launchd does not read the shell profile, so COCKPIT_CLAUDE_BIN (if the user set
# one) has to be written into the job's own environment or the watcher would look
# for Claude Code in the default place and not find it.
render() {
  /usr/bin/python3 - "$TEMPLATE" "$HOME" "$(id -un)" "${COCKPIT_CLAUDE_BIN:-}" <<'PY'
import sys
from xml.sax.saxutils import escape
template, home, user, claude_bin = sys.argv[1:5]
s = open(template, encoding="utf-8").read()
env = ("    <key>COCKPIT_CLAUDE_BIN</key><string>%s</string>" % escape(claude_bin)) if claude_bin else ""
s = s.replace("__CLAUDE_BIN_ENV__\n", (env + "\n") if env else "")
s = s.replace("__HOME__", escape(home)).replace("__USER__", escape(user))
sys.stdout.write(s)
PY
}

if [ "$MODE" = "print" ]; then render; exit 0; fi

if [ "$MODE" = "remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Watcher stopped and removed."
  exit 0
fi

[ -f "$DEST/claude-autoswitch.py" ] || { echo "Run install.sh first: $DEST/claude-autoswitch.py is missing."; exit 1; }
[ -f "$DEST/autoswitch.json" ]      || { echo "Run install.sh first: $DEST/autoswitch.json is missing."; exit 1; }
[ -x /usr/bin/python3 ] || { echo "/usr/bin/python3 is missing. Install the Xcode Command Line Tools: xcode-select --install"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"
render > "$PLIST"
plutil -lint "$PLIST" >/dev/null || { echo "The generated file is not a valid plist: $PLIST"; exit 1; }
# bootout first so a second run replaces the running job instead of failing on "already loaded".
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Watcher installed and started."
echo "  Is it running:   launchctl list | grep autoswitch"
echo "  Its log:         $DEST/autoswitch.log"
echo "  Alerts are $(/usr/bin/python3 -c "import json,sys; print('ON' if json.load(open(sys.argv[1])).get('enabled') is True else 'OFF (press a in claude-cockpit to turn them on)')" "$DEST/autoswitch.json" 2>/dev/null || echo 'unknown (autoswitch.json could not be read)')"
echo "  The forecast appears in the cockpit after the first few 5-minute readings."
