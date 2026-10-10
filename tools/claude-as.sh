# claude-as — run Claude Code as a specific account
# Sourced from ~/.zshrc. Undo: run uninstall.sh from the repo you installed from.

# WHY the absolute path: `claude` on PATH can resolve to a terminal app's shim, or to
# a stale install that predates `claude auth status` and the usage payload entirely. ~/.local/bin/claude is the symlink the native installer
# keeps pointed at the current version, so it survives auto-updates.
# Verified 2026-08-04: it moved 2.1.221 -> 2.1.222 mid-setup with no breakage.
# COCKPIT_CLAUDE_BIN is the override for installs that live somewhere else (npm,
# Homebrew). SYNC WARNING: same lookup as CLAUDE_BIN in claude-switch.py and verify.sh.
CLAUDE_AS_BIN="${COCKPIT_CLAUDE_BIN:-$HOME/.local/bin/claude}"
CLAUDE_AS_DIR="$HOME/.claude-profiles"

# ---------------------------------------------------------------------------
# NO PINNING. (The `.active` → export CLAUDE_CONFIG_DIR block that briefly
# lived here on 2026-08-17 was REMOVED the same day.)
# ---------------------------------------------------------------------------
# New terminals deliberately run with NO CLAUDE_CONFIG_DIR, so they read the
# SHARED keychain slot — the FLEET LANE that every running session and any
# launchd job also read. Switching accounts is one aimed
# browser login into that slot (`claude-switch <name>`), and everything —
# running sessions included — follows it. Pinning new terminals to a profile
# was safe but switched the wrong thing: it never touched the running fleet,
# and it made the user's own /login habit land in private slots, silently
# splitting the fleet. Full history: claude-switch.py's docstring and docs/how_it_works.md.
#
# Do NOT reintroduce an export here. A shell-level CLAUDE_CONFIG_DIR default
# recreates the split-fleet failure. Per-session pinning is what `claude-as`
# (below) is for — explicit, one session at a time.

# One clear line instead of "no such file or directory" when Claude Code is not
# where we look. WHY a function: claude-as and claude-login both launch the binary
# directly, and a new user on an npm or Homebrew install hits this first.
_cockpit_need_claude() {
  [ -x "$CLAUDE_AS_BIN" ] && return 0
  echo "claude-cockpit: Claude Code was not found at $CLAUDE_AS_BIN"
  echo "  If it is installed somewhere else, add this line to ~/.zshrc and open a new terminal:"
  echo "    export COCKPIT_CLAUDE_BIN=/full/path/to/claude"
  return 1
}

claude-as() {
  _cockpit_need_claude || return 1
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "usage: claude-as <account> [claude args...]"
    echo ""
    claude-profiles
    return 1
  fi
  local p="$CLAUDE_AS_DIR/$name"
  # Require the profile's OWN .claude.json, not just the directory: launching
  # claude at a bare dir initialises a fresh signed-out config there.
  if [ ! -f "$p/.claude.json" ]; then
    echo "no such profile: $name"
    echo "create it with: bash ~/.claude-profiles/new-profile.sh $name"
    return 1
  fi
  shift
  CLAUDE_CONFIG_DIR="$p" "$CLAUDE_AS_BIN" "$@"
}

# Sign in an account's ANCHOR — its own private keychain slot.
#   claude-login team      sign in team's own slot (its anchor)
#   claude-login            inside a claude-as shell: re-auth that profile
#   claude-login --fleet    unaimed fleet login (same as typing /login anywhere
#                           unpinned; prefer `claude-switch <name>`, which aims
#                           the browser at an account and VERIFIES the result)
#
# WHY anchors matter: every account keeps a permanent login in its own slot, so
# the fleet slot's grant is always expendable — overwriting it during a switch
# can never destroy an account's only credential. new-profile.sh + claude-login
# is the anchoring step claude-switch requires before it will fleet-switch to a
# new account.
claude-login() {
  _cockpit_need_claude || return 1
  local name="${1:-}" p who
  if [ "$name" = "--fleet" ] || [ "$name" = "--headless" ]; then   # --headless: older name for --fleet, kept working
    echo "Signing in the FLEET lane (the shared slot every session and scheduled job follows)."
    echo "Unaimed: whatever account you pick in the browser becomes the fleet."
    echo "Prefer:  claude-switch <name>   — same login, aimed and verified."
    # Fleet-dir mode: "the fleet" is one named config dir, not the shared default slot
    # (another Claude process owns that one). Same source order as claude-switch.py's
    # _fleet_config_dir: env, then ~/.claude-profiles/fleet_config_dir. SYNC WARNING:
    # keep this in step with that function; ordinary machine = neither set = unchanged.
    local _fleet="${COCKPIT_FLEET_CONFIG_DIR:-$(cat "$HOME/.claude-profiles/fleet_config_dir" 2>/dev/null)}"
    if [ -n "$_fleet" ]; then
      ( export CLAUDE_CONFIG_DIR="$_fleet"; "$CLAUDE_AS_BIN" auth login )
    else
      ( unset CLAUDE_CONFIG_DIR; "$CLAUDE_AS_BIN" auth login )
    fi
    return $?
  fi
  if [ -z "$name" ]; then
    p="${CLAUDE_CONFIG_DIR:-}"
    if [ -z "$p" ]; then
      echo "Name a target:"
      echo "   claude-login <profile>   sign in that account's own anchor slot"
      echo "   claude-login --fleet     sign in the shared fleet slot (unaimed)"
      echo "   claude-switch <name>     fleet switch, aimed and verified (preferred)"
      claude-profiles
      return 1
    fi
    name=$(basename "$p")
  else
    p="$CLAUDE_AS_DIR/$name"
  fi
  if [ ! -f "$p/.claude.json" ]; then
    echo "no such profile: $name"
    echo "create it with: bash ~/.claude-profiles/new-profile.sh $name"
    return 1
  fi
  echo "Signing in profile: $name"
  echo "   its keychain slot only. No other account can be affected."
  # Aim the consent screen at this account's own Chrome profile. The resolver
  # lives in claude-switch.py and is called, never reimplemented here: two copies
  # of one rule drifted on a Cyrillic homoglyph once and split the fleet silently
  # (gotcha #26). Name travels by argv, never interpolated into source.
  # NOTE: this lane deliberately does NOT use claude-switch's login_env(), which
  # POPS CLAUDE_CONFIG_DIR for the fleet. Here it must stay SET, or an intended
  # anchor login would rewrite the shared fleet slot instead.
  local cprof cmail
  cprof=$(python3 "$CLAUDE_AS_DIR/claude-switch.py" --chrome-profile-for "$name" 2>/dev/null)
  cmail=$(python3 "$CLAUDE_AS_DIR/claude-switch.py" --email-for "$name" 2>/dev/null)
  if [ -n "$cprof" ]; then
    echo "   aiming at Chrome profile: $cprof"
    echo "   confirm the consent screen shows ${cmail:-this account} before authorizing."
  fi
  # A first-ever login has no email yet, so nothing to match on and no aiming —
  # by design, it happens once per account. Subshell so BROWSER never leaks into
  # the interactive shell, where it would redirect every URL claude ever opens.
  (
    CLAUDE_CONFIG_DIR="$p"
    export CLAUDE_CONFIG_DIR
    if [ -n "$cprof" ] && [ -x "$CLAUDE_AS_DIR/claude-open-in-chrome.sh" ]; then
      BROWSER="$CLAUDE_AS_DIR/claude-open-in-chrome.sh"
      COCKPIT_CHROME_PROFILE_DIR="$cprof"
      export BROWSER COCKPIT_CHROME_PROFILE_DIR
    fi
    if [ -n "$cmail" ]; then
      "$CLAUDE_AS_BIN" auth login --email "$cmail"
    else
      "$CLAUDE_AS_BIN" auth login
    fi
  ) || return $?
  who=$(CLAUDE_CONFIG_DIR="$p" "$CLAUDE_AS_BIN" auth status 2>/dev/null \
        | grep '"email"' | sed -E 's/.*: "(.*)",?/\1/')
  # Confirm by AUTHENTICATION, never by "the command exited 0" — a signed-out
  # profile exits 0 from several claude subcommands and prints a cost footer.
  if [ -n "$who" ]; then echo "OK: $name is signed in as $who"
  else echo "WARNING: $name still does not authenticate. Re-run claude-login $name"; return 1; fi
}

# List every profile and which account it is signed into.
claude-profiles() {
  if [ ! -d "$CLAUDE_AS_DIR" ]; then echo "no profiles yet"; return 0; fi
  printf '%-14s %s\n' "PROFILE" "SIGNED IN AS"
  local p name email
  for p in "$CLAUDE_AS_DIR"/*/; do
    [ -d "$p" ] || continue
    name=$(basename "$p")
    # Only probe REAL profiles. Pointing claude at a bare directory INITIALISES
    # a fresh config inside it — that is how __pycache__ grew a .claude.json and
    # appeared as a sixth account on 2026-08-17 (gotcha #13's second bite).
    [ "$name" = "__pycache__" ] && continue
    [ -f "${p%/}/.claude.json" ] || continue
    email=$(CLAUDE_CONFIG_DIR="${p%/}" "$CLAUDE_AS_BIN" auth status 2>/dev/null \
            | grep '"email"' | sed -E 's/.*: "(.*)",?/\1/')
    [ -z "$email" ] && email="(not signed in — run: claude-as $name  then /login)"
    printf '%-14s %s\n' "$name" "$email"
  done
  printf '%-14s %s\n' "(default)" "$("$CLAUDE_AS_BIN" auth status 2>/dev/null \
            | grep '"email"' | sed -E 's/.*: "(.*)",?/\1/')"
}

# tab-complete profile names
if [ -n "${ZSH_VERSION:-}" ]; then
  _claude_as_complete() { compadd $(ls -1 "$CLAUDE_AS_DIR" 2>/dev/null | grep -vE '\.(sh|py)$'); }
  compdef _claude_as_complete claude-as 2>/dev/null
fi

# How much subscription is left on every account.
#   claude-usage          refresh all, then show (~5s, costs no quota)
#   claude-usage --fast   last known numbers off disk, instant
claude-usage() { python3 "$HOME/.claude-profiles/claude-usage.py" "$@"; }

# Open the 5-hour session window on accounts that do not have one.
#   claude-ping              ping every account with NO live 5h window
#   claude-ping personal team    ping these regardless of state
#   claude-ping --dry-run    show what would be pinged, spawn nothing
#   claude-ping --fast       trust the cached meters, skip the free pre-check
#
# WHY: the 5h window opens on an account's FIRST inference call and expires
# exactly 5h later no matter what you do in between. An idle account has no
# window, so its clock is not running — the moment you need it you start a fresh
# 5h countdown from zero. Warming cold accounts early means those windows burn
# down in wall-clock time while you work on a different one. `/usage` does NOT
# do this (it is a local GET, no window, no quota); only a real message does.
# Also available as `p` in claude-cockpit.
claude-ping() { python3 "$HOME/.claude-profiles/claude-ping.py" "$@"; }

# Fleet switch — put EVERYTHING on the account you pick, with one browser login.
#   claude-switch              show who the fleet is on, change nothing
#   claude-switch work       aimed browser login; sessions, terminals and
#                              scheduled jobs all follow; result VERIFIED and reported
#   claude-switch --undo       aimed login back to the previous account
# The script itself contains no keychain write (AST-gated by verify.sh D3); the
# only credential writer is the official `claude auth login` it launches with
# the env scrubbed, so it aims at the fleet slot even from a claude-as shell.
claude-switch() { python3 "$HOME/.claude-profiles/claude-switch.py" "$@"; }

# Fleet switch WITHOUT a browser (cockpit v2, 2026-10-01). Moves an existing
# login into the shared slot (the account's spare if it has one, else its own
# private login — "borrowed") and moves the outgoing one back home. Verified with
# the server after every move; rolls back if the check fails.
#   claude-swap              who the fleet is on + where every login is
#   claude-swap lab      put everything on lab, no browser (~20s)
#   claude-swap --undo       swap back
#   claude-swap --check      prove no login is held in two slots
# While an account is on the fleet with its OWN login, a bare /login or a
# browser claude-switch would erase it — claude-switch refuses in that state.
claude-swap() { python3 "$HOME/.claude-profiles/claude-swap.py" "$@"; }

# Live cockpit: usage for every account + one-key account switching.
claude-cockpit() { python3 "$HOME/.claude-profiles/claude-cockpit.py" "$@"; }

# Auto-swap watcher (launchd com.claude-cockpit.autoswitch, always running).
# When ARMED it swaps the fleet ONCE (no browser, via claude-swap) the next time
# the fleet account's 5-hour or weekly usage reaches 97%, then disarms itself.
# Never auto-picks a profile listed in exclude_from_auto (empty on a fresh install).
#   claude-autoswitch --arm       arm one swap
#   claude-autoswitch --off       turn off swapping and alerts
#   claude-autoswitch --status    live numbers + which account it would pick
#   claude-autoswitch --once --dry-run   one evaluation, changes nothing
claude-autoswitch() { python3 "$HOME/.claude-profiles/claude-autoswitch.py" "$@"; }
