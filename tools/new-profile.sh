#!/bin/bash
# ============================================================================
# NEW PROFILE — add one more Claude account
#
#   bash ~/.claude-profiles/new-profile.sh <shortname>
#   e.g. bash ~/.claude-profiles/new-profile.sh work
#
# Then:  claude-as <shortname>   ->  /login   ->  sign in as that account.
#
# Creates ~/.claude-profiles/<shortname>/ which SHARES everything that matters
# with ~/.claude (skills, commands, agents, hooks, plugins, settings, CLAUDE.md,
# and all session history) and keeps ONLY the login private.
# Safe to re-run: it refuses to overwrite an existing profile.
# ============================================================================
set -euo pipefail

NAME="${1:-}"
if [ -z "$NAME" ]; then echo "usage: $0 <shortname>   (e.g. personal, work, client)"; exit 1; fi
case "$NAME" in *[!a-zA-Z0-9_-]*) echo "shortname must be letters, numbers, - or _ only"; exit 1;; esac

# Fleet-dir mode: shared skills/hooks/CLAUDE.md and the MCP/trust template come from the
# FLEET config dir, never from ~/.claude, which belongs to the other Claude process on such
# a machine. Ordinary machine: neither is set,
# so SRC_DIR=$HOME/.claude and SRC_JSON=$HOME/.claude.json exactly as before.
# SYNC WARNING: same source order as claude-switch.py _fleet_config_dir.
# `|| true` is load-bearing. Under `set -e` an assignment takes the exit status of its
# command substitution, so on every ordinary machine (no fleet_config_dir file) the
# failing `cat` ended this script right here: exit 1, no message, no profile created.
# Found by installing into a clean home folder.
FLEET="${COCKPIT_FLEET_CONFIG_DIR:-$(cat "$HOME/.claude-profiles/fleet_config_dir" 2>/dev/null || true)}"
SRC_DIR="${FLEET:-$HOME/.claude}"
SRC_JSON="${FLEET:+$FLEET/.claude.json}"; SRC_JSON="${SRC_JSON:-$HOME/.claude.json}"

P="$HOME/.claude-profiles/$NAME"
if [ -e "$P" ]; then echo "!! $P already exists. Delete it first if you meant to recreate it."; exit 1; fi
# Checked BEFORE the folder is created. WHY: the copy of this file further down is what
# makes the folder a profile. If it failed after mkdir, a half-made folder would be left
# behind that is not a profile yet blocks the next attempt ("already exists").
if [ ! -f "$SRC_JSON" ]; then
  echo "!! $SRC_JSON does not exist. Run Claude Code once and sign in first, so it creates its"
  echo "   own config; a new profile starts as a copy of that file (MCP servers, trusted folders)."
  exit 1
fi

# SYNC WARNING: this list is the contract with verify.sh. If you add or remove an
# entry here, update SHARED in ~/.claude-profiles/verify.sh too,
# or the verifier will report a false FAIL.
#
# WHY each one is shared rather than copied:
#   skills/commands/agents  - your skills and commands; copying would drift
#   hooks                   - the hook SCRIPTS
#   settings.json           - the hook WIRING, plus model/effort/permission mode
#                             and enabledPlugins. Without it a profile silently
#                             falls back to plain opus, default effort, and
#                             permission prompts. Verified 2026-08-04.
#   plugins                 - the enabled plugins' actual files; settings.json
#                             names them but they load from here
#   CLAUDE.md               - global instructions
#   projects                - ALL session history. Load-bearing: Claude Code
#                             writes transcripts to $CLAUDE_CONFIG_DIR/projects,
#                             and tools that read session history look only in
#                             ~/.claude/projects. Without this symlink, work done
#                             under another account becomes invisible to them.
SHARED="skills commands agents hooks plugins CLAUDE.md settings.json projects"

echo "Creating profile: $NAME"
mkdir -p "$P"
chmod 700 "$P"

for x in $SHARED; do
  if [ ! -e "$SRC_DIR/$x" ]; then echo "   !! $SRC_DIR/$x does not exist — skipping"; continue; fi
  ln -s "$SRC_DIR/$x" "$P/$x"
  echo "   shared:  $x"
done

# Private per profile. Carries your MCP servers and the folder trust list so you
# do not re-approve every folder. It also carries the PREVIOUS account's identity
# block until you sign in; that is harmless (auth status correctly reports logged
# out, because logged-in comes from the keychain, not this file) and signing in
# replaces it.
cp "$SRC_JSON" "$P/.claude.json"
chmod 600 "$P/.claude.json"

# STRIP the inherited identity and every account-scoped cache (2026-08-17).
#
# WHY: the copy carries the PREVIOUS account's oauthAccount block. That used to be
# called harmless, on the grounds that logged-in status comes from the keychain
# and signing in replaces it. It is not harmless — it makes a brand-new profile
# claim to BE an existing account. Measured the day this was added: a fresh
# `team` profile reported emailAddress personal@example.com, so every tool that
# maps an account to its home profile saw two candidates for one account. Under
# the credential-moving switch that mattered enormously (it could send a live
# credential to the wrong slot); it is merely confusing now, but a profile that
# lies about its identity has no upside in either design.
#
# The account-scoped keys are the same set Claude Code itself clears on logout.
# Leaving them means the new profile inherits the old account's model access,
# pricing and usage numbers, and the cockpit shows a filled-in row for an account
# that has never signed in.
#
# KEPT: mcpServers and the folder trust list, which is the entire reason this
# file is copied instead of created empty.
python3 - "$P/.claude.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.pop("oauthAccount", None)
for k in ["cachedUsageUtilization", "modelAccessCache", "orgModelDefaultCache",
          "clientDataCache", "clientDataCacheSlots", "additionalModelOptionsCache",
          "additionalModelCostsCache", "autoCompactWindowsCache",
          "lastSeenOrgDefaultUpdatedAt", "cachedExtraUsageDisabledReason"]:
    d.pop(k, None)
json.dump(d, open(p, "w"), indent=2)
PY
echo "   private: .claude.json  (MCP servers + folder trust list kept; identity and usage caches stripped)"

echo ""
echo "Done. Its keychain login will be:"
echo "   Claude Code-credentials-$(printf '%s' "$P" | shasum -a 256 | cut -c1-8)"
echo ""
echo "Next:"
echo "   claude-login $NAME     # sign in its ANCHOR (own keychain slot)"
echo "   claude-switch $NAME    # optional: put the whole fleet on it"
