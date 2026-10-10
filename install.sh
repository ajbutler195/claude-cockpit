#!/bin/bash
# ============================================================================
# INSTALL: put the cockpit tools in ~/.claude-profiles and hook them into zsh
#
#   bash install.sh              first install
#   bash install.sh --update     replace tools that already exist and differ
#   bash install.sh --no-shell   do not touch ~/.zshrc
#
# WHY the tools are COPIED to ~/.claude-profiles instead of run from this clone:
# every tool finds its siblings, its settings and each account's profile folder
# at that one fixed path (the profile folder path is even hashed into the name
# of the keychain item that holds the login). Running from the clone would mean
# two places that both think they are home.
#
# This script NEVER touches the keychain and never signs anything in or out.
# Signing in is always a separate, deliberate step: claude-login <name>.
# Safe to run twice: a second run with nothing changed changes nothing.
# ============================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude-profiles"
ZSHRC="$HOME/.zshrc"

# SYNC WARNING: uninstall.sh carries the same list and removes exactly these files.
TOOLS="claude-as.sh claude-usage.py claude-cockpit.py claude-switch.py claude-swap.py claude-autoswitch.py claude-meters.py claude-ping.py claude-open-in-chrome.sh new-profile.sh verify.sh"

# SYNC WARNING: three readers depend on this exact text.
#   verify.sh section E greps ~/.zshrc for MARK to say "the shell is hooked up".
#   uninstall.sh removes the lines by MARK, SRC_LINE and TAG.
MARK='# claude-as (multi-account profiles)'
SRC_LINE='[ -f "$HOME/.claude-profiles/claude-as.sh" ] && source "$HOME/.claude-profiles/claude-as.sh"'
TAG='# added by claude-cockpit install.sh'

UPDATE=0; SHELL_HOOK=1
for a in "$@"; do
  case "$a" in
    --update)   UPDATE=1 ;;
    --no-shell) SHELL_HOOK=0 ;;
    -h|--help)  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a   (try --help)"; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
die()  { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
say "Checking this Mac..."
[ "$(uname -s)" = "Darwin" ] || die "macOS only. The tools use the macOS keychain (security), launchd and open."
command -v security >/dev/null 2>&1 || die "the macOS 'security' command was not found."
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 was not found. Install the Xcode Command Line Tools: xcode-select --install"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 is older than 3.9 ($("$PY" -V 2>&1)). The tools need 3.9 or newer."
say "  ok  macOS, security, $("$PY" -V 2>&1)"

for f in $TOOLS; do [ -f "$SRC/tools/$f" ] || die "this clone is incomplete: tools/$f is missing."; done

# Where is Claude Code? Default = the symlink Anthropic's native installer maintains.
# WHY not just "claude" from PATH at run time: a PATH lookup can land on a shim or a
# stale install, and the launchd watcher has no shell PATH at all. So the tools use
# one fixed path, and an install that lives elsewhere is recorded ONCE, here, as
# COCKPIT_CLAUDE_BIN.
BIN="${COCKPIT_CLAUDE_BIN:-$HOME/.local/bin/claude}"
EXPORT_LINE=""
if [ -x "$BIN" ]; then
  say "  ok  Claude Code at $BIN"
else
  FOUND="$(command -v claude 2>/dev/null || true)"
  case "$FOUND" in
    /*) if [ -x "$FOUND" ]; then
          EXPORT_LINE="export COCKPIT_CLAUDE_BIN=\"$FOUND\"   $TAG"
          warn "Claude Code is not at $BIN, but was found at $FOUND."
          warn "That path will be recorded in ~/.zshrc as COCKPIT_CLAUDE_BIN."
        fi ;;
  esac
  if [ -z "$EXPORT_LINE" ]; then
    warn "Claude Code was not found at $BIN."
    warn "Install it, or add this to ~/.zshrc:  export COCKPIT_CLAUDE_BIN=/full/path/to/claude"
    warn "Continuing: the tools will be installed and will tell you the same thing when run."
  fi
fi

# ------------------------------------------------- never overwrite silently
# WHY this refuses by default: ~/.claude-profiles may already hold a copy of these
# tools that someone edited. Replacing a file that differs is only done on request,
# and a copy of what was there is kept outside the folder. (Outside on purpose: the
# tools treat sub-folders of ~/.claude-profiles as candidate profiles.)
DIFFER=""
for f in $TOOLS; do
  if [ -e "$DEST/$f" ] && ! cmp -s "$SRC/tools/$f" "$DEST/$f"; then DIFFER="$DIFFER $f"; fi
done
if [ -n "$DIFFER" ] && [ "$UPDATE" -ne 1 ]; then
  say ""
  say "These tools already exist in $DEST and are DIFFERENT from this version:"
  for f in $DIFFER; do say "    $f"; done
  say ""
  say "Nothing was changed. To replace them (a copy of each is kept first), run:"
  say "    bash install.sh --update"
  exit 1
fi
if [ -n "$DIFFER" ]; then
  BK="$HOME/.claude-profiles-backups/$(date +%Y%m%d_%H%M%S)"
  mkdir -p "$BK"
  for f in $DIFFER; do cp -p "$DEST/$f" "$BK/$f"; done
  say "Kept a copy of the replaced files in $BK"
fi

# ------------------------------------------------------------------- install
if [ ! -d "$DEST" ]; then mkdir -p "$DEST"; chmod 700 "$DEST"; fi
COPIED=0
for f in $TOOLS; do
  if ! cmp -s "$SRC/tools/$f" "$DEST/$f" 2>/dev/null; then
    cp "$SRC/tools/$f" "$DEST/$f"; COPIED=$((COPIED + 1))
  fi
  chmod 755 "$DEST/$f"
done
say "Tools: $COPIED copied, $(( $(echo $TOOLS | wc -w) - COPIED )) already up to date  ->  $DEST"

# Settings are created once and never overwritten: they are the user's from then on.
if [ -e "$DEST/autoswitch.json" ]; then
  say "Settings: kept your existing $DEST/autoswitch.json"
else
  cp "$SRC/config/autoswitch.example.json" "$DEST/autoswitch.json"
  say "Settings: created $DEST/autoswitch.json  (watcher alerts off, no-browser swap off)"
fi

# ------------------------------------------------------------------ shell hook
if [ "$SHELL_HOOK" -eq 1 ]; then
  if [ -f "$ZSHRC" ] && grep -qF '.claude-profiles/claude-as.sh' "$ZSHRC"; then
    say "Shell: ~/.zshrc already loads the commands"
  else
    if [ -f "$ZSHRC" ]; then cp -p "$ZSHRC" "$ZSHRC.bak.claude-cockpit.$(date +%Y%m%d_%H%M%S)"; fi
    {
      printf '\n%s\n%s\n' "$MARK" "$SRC_LINE"
      if [ -n "$EXPORT_LINE" ]; then printf '%s\n' "$EXPORT_LINE"; fi
    } >> "$ZSHRC"
    say "Shell: added the commands to ~/.zshrc (a backup of the old file is next to it)"
  fi
  case "${SHELL:-}" in
    */zsh) : ;;
    *) warn "Your login shell is not zsh. Add this line to your shell's startup file yourself:"
       warn "  $SRC_LINE" ;;
  esac
else
  say "Shell: skipped (--no-shell). To load the commands: source $DEST/claude-as.sh"
fi

say ""
say "Installed. Next:"
say "  1. Open a NEW terminal (so the commands load)."
say "  2. Add an account:      bash ~/.claude-profiles/new-profile.sh personal"
say "  3. Sign it in:          claude-login personal"
say "  4. Repeat 2 and 3 for each account, then run:   claude-cockpit"
say "  Check the setup any time:   bash ~/.claude-profiles/verify.sh"
