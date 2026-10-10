#!/bin/bash
# ============================================================================
# UNINSTALL: remove what install.sh (and install_watcher.sh) added
#
#   bash uninstall.sh          show what WOULD be removed. Changes nothing.
#   bash uninstall.sh --yes    do it.
#
# Removes: the watcher (if installed), the lines install.sh added to ~/.zshrc
# (after a backup), and the tool files in ~/.claude-profiles.
#
# LEAVES IN PLACE, on purpose:
#   - every account's profile folder, your settings and the logs
#   - every login in the keychain
# WHY logins are never removed here: an earlier "undo everything" script deleted
# every account's keychain item in one go, and a harmless problem elsewhere once
# routed its owner to that command. Removing a login signs that account out on
# this Mac, so it is always a separate, deliberate act. This script only PRINTS
# the commands that would do it.
# ============================================================================
set -euo pipefail

DEST="$HOME/.claude-profiles"
ZSHRC="$HOME/.zshrc"
LABEL="com.claude-cockpit.autoswitch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# SYNC WARNING: same list as install.sh. Only these files are ever removed.
TOOLS="claude-as.sh claude-usage.py claude-cockpit.py claude-switch.py claude-swap.py claude-autoswitch.py claude-meters.py claude-ping.py claude-open-in-chrome.sh new-profile.sh verify.sh"
# SYNC WARNING: the three texts install.sh writes into ~/.zshrc.
MARK='# claude-as (multi-account profiles)'
SRC_LINE='[ -f "$HOME/.claude-profiles/claude-as.sh" ] && source "$HOME/.claude-profiles/claude-as.sh"'
TAG='# added by claude-cockpit install.sh'

DO=0
case "${1:-}" in
  --yes) DO=1 ;;
  "") : ;;
  -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: $1   (try --help)"; exit 2 ;;
esac
if [ "$DO" -eq 1 ]; then ACT="removing"; else ACT="would remove"; fi

echo "claude-cockpit uninstall ($([ "$DO" -eq 1 ] && echo 'DOING IT' || echo 'preview only, nothing changes'))"
echo ""

# ------------------------------------------------------------------- watcher
if [ -f "$PLIST" ]; then
  echo "Watcher: $ACT $PLIST"
  if [ "$DO" -eq 1 ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
  fi
else
  echo "Watcher: not installed"
fi

# --------------------------------------------------------------------- shell
# Remove ONLY the exact lines install.sh wrote. Anything else that mentions the
# cockpit in ~/.zshrc was put there by the user and is theirs to remove.
if [ -f "$ZSHRC" ] && grep -qF -e "$MARK" -e "$SRC_LINE" -e "$TAG" "$ZSHRC"; then
  echo "Shell: $ACT these lines from ~/.zshrc:"
  grep -nF -e "$MARK" -e "$SRC_LINE" -e "$TAG" "$ZSHRC" | sed 's/^/    /'
  if [ "$DO" -eq 1 ]; then
    cp -p "$ZSHRC" "$ZSHRC.bak.claude-cockpit.$(date +%Y%m%d_%H%M%S)"
    TMP="$(mktemp)"
    grep -vF -e "$MARK" -e "$SRC_LINE" -e "$TAG" "$ZSHRC" > "$TMP" || true
    cat "$TMP" > "$ZSHRC"      # cat, not mv: keeps the file's owner, mode and any symlink
    rm -f "$TMP"
    echo "    (a backup of the old ~/.zshrc is next to it)"
  fi
else
  echo "Shell: ~/.zshrc has none of the lines install.sh adds"
fi

# --------------------------------------------------------------------- tools
FOUND=0
for f in $TOOLS; do
  if [ -e "$DEST/$f" ]; then
    FOUND=$((FOUND + 1))
    [ "$DO" -eq 1 ] && rm -f "$DEST/$f"
  fi
done
echo "Tools: $ACT $FOUND tool file(s) from $DEST"

# ------------------------------------------------- what is deliberately left
echo ""
echo "Left in place (delete by hand if you want them gone):"
if [ -d "$DEST" ]; then
  for d in "$DEST"/*/; do
    [ -f "${d}.claude.json" ] && echo "    profile folder   ${d%/}"
  done
  for f in autoswitch.json chrome-profiles.json fleet_config_dir autoswitch.log meters.jsonl forecast.json forecast_history.jsonl token_hours.json; do
    [ -e "$DEST/$f" ] && echo "    file             $DEST/$f"
  done
fi

echo ""
echo "Logins in the keychain were NOT touched. Each line below would sign one account"
echo "out on this Mac. Run one only if you mean it; this script never runs them:"
if [ -d "$DEST" ]; then
  for d in "$DEST"/*/; do
    [ -f "${d}.claude.json" ] || continue
    p="${d%/}"; n="$(basename "$p")"
    # Claude Code names a profile's keychain item after the first 8 hex digits of the
    # sha256 of the profile folder path STRING, so the path must be hashed exactly.
    echo "    security delete-generic-password -s \"Claude Code-credentials-$(printf '%s' "$p" | shasum -a 256 | cut -c1-8)\"    # $n"
    echo "    security delete-generic-password -s \"claude-cockpit-spare-$n\"    # $n's spare, if one exists"
  done
fi
echo ""
if [ "$DO" -eq 1 ]; then echo "Done. Open a new terminal so the commands disappear from your shell."
else echo "Nothing was changed. To do it:  bash uninstall.sh --yes"; fi
