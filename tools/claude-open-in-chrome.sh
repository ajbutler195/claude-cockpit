#!/bin/bash
# BROWSER opener for an AIMED Claude login. Claude Code spawns this as
#   claude-open-in-chrome.sh <url>
# with exactly ONE argv. Verified 2026-08-19 against 2.1.236's zwp():
#   let r = vE()?.browser ?? V.BROWSER;  return MdS(await Vn(r || "open", [e]));
# No shell, no extra arguments, full env inherited (measured: 102 vars).
#
# WHY aiming is safe at all: the auto-open URL carries
# redirect_uri=http://localhost:<port>/callback, a listener inside the WAITING
# CLI process. Any browser or profile completes the flow, so choosing one
# changes WHO is offered the consent screen, never WHETHER the login finishes.
#
# WHY `open -n` and not plain `open -a`: without -n, LaunchServices merely
# ACTIVATES the already-running Chrome and DROPS --args entirely, so neither the
# profile flag nor the URL is delivered. Tested live 2026-08-19: -n forwards
# through Chrome's process singleton (root PID identical before/after), lands a
# new session in the aimed profile, and starts no second instance.
#
# WHY the var is COCKPIT_ and not CLAUDE_: 2.1.236's PTY-host env builder deletes
# every CLAUDE_* var except three whitelisted names. That is not this code path
# today, but the binary auto-updates daily and we do not own that namespace.
#
# FAIL SOFT ON EVERYTHING. A cosmetic aiming feature must never be able to block
# a credential login, so every unknown falls through to plain `open`.
set -u
url="${1:-}"
[ -z "$url" ] && exit 0

prof="${COCKPIT_CHROME_PROFILE_DIR:-}"
app="/Applications/Google Chrome.app"
receipt="$HOME/.claude-profiles/.last-aim"

# The receipt exists because the switch announces its intended profile BEFORE
# this script runs. Without proof of what actually happened, a silently-failed
# aim (vendor update, renamed profile) would be reported as an achieved one
# forever — the exact shape invariant #8 forbids. claude-switch reads this back
# after the login returns. A plain FILE here can never be mistaken for a profile:
# profiles() requires a .claude.json inside a DIRECTORY.
say() { printf '%s %s %s\n' "$(date +%s)" "$1" "${2:-}" > "$receipt" 2>/dev/null || true; }

if [ -n "$prof" ] && [ -d "$app" ] \
   && [ -d "$HOME/Library/Application Support/Google/Chrome/$prof" ]; then
  say aimed "$prof"
  # Absolute path: PATH here can resolve to a shim or a stale install.
  exec /usr/bin/open -na "Google Chrome" --args --profile-directory="$prof" "$url"
fi
say fellthrough "${prof:-none}"
exec /usr/bin/open "$url"
