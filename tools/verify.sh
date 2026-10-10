#!/bin/bash

# Never trust the inherited environment (added 2026-08-17). Run from a
# claude-as-pinned shell, an inherited CLAUDE_CONFIG_DIR makes every "shared
# slot" probe below audit the PINNED profile instead and report it as the
# fleet — this script did exactly that during an adversarial review (reported
# work as the shared slot while it was really personal). A verifier that lies from
# certain shells is worse than none. Scrub once, here, for the whole run.
unset CLAUDE_CONFIG_DIR

# ============================================================================
# VERIFY — did the multi-account profile setup break anything?
#
#   bash ~/.claude-profiles/verify.sh
#
# Read-only. Changes nothing. Run it any time you suspect something is off.
# Every line prints PASS, FAIL, or INFO. Every FAIL names its own fix.
# ============================================================================
set -uo pipefail

# SYNC WARNING: same lookup order as claude-as.sh (CLAUDE_AS_BIN) and the CLAUDE
# constant in every python tool: COCKPIT_CLAUDE_BIN, else the native installer's
# symlink. Never bare `claude` (a PATH lookup can hit a shim or a stale install).
CLAUDE_BIN="${COCKPIT_CLAUDE_BIN:-$HOME/.local/bin/claude}"
PROFILES_DIR="$HOME/.claude-profiles"

fails=0
# Dump the keychain ONCE. Calling `security dump-keychain` repeatedly inside the
# per-profile loop proved unreliable (2026-08-04: it reported "no keychain login"
# for all four profiles while the entries demonstrably existed). One dump, then
# grep the string.
KCDUMP=$(security dump-keychain 2>/dev/null | grep -oE '"svce"<blob>="Claude Code-credentials[-0-9a-f]*"' | sed -E 's/.*="(.*)"/\1/' | sort -u)

# --simulate-missing <profile>: pretend that profile's slot is absent, WITHOUT
# touching the keychain. Exists because a safety net nobody has seen fail is not a
# safety net — this file printed ALL CHECKS PASSED through an incident that
# destroyed an account (2026-08-05). Read-only: it filters the in-memory slot list.
SIMULATE_MISSING=""
if [ "${1:-}" = "--simulate-missing" ] && [ -n "${2:-}" ]; then
  SIMULATE_MISSING="$2"
  simslot="Claude Code-credentials-$(printf '%s' "$HOME/.claude-profiles/$SIMULATE_MISSING" | shasum -a 256 | cut -c1-8)"
  KCDUMP=$(printf '%s\n' "$KCDUMP" | grep -vx "$simslot")
  printf '\n*** SIMULATION: pretending %s has no keychain slot (%s). Nothing was changed. ***\n' \
         "$SIMULATE_MISSING" "$simslot"
fi

# Hash every slot's access token so D2 can detect the FATAL case: one credential
# copied into two slots = one shared single-use refresh token. Only hashes are
# handled — the plaintext never leaves the python process.
TOKHASHES=$(python3 - <<'PY'
import subprocess, json, os, hashlib
u = os.environ.get("USER", "")
def slot(p): return "Claude Code-credentials-" + hashlib.sha256(p.encode()).hexdigest()[:8]
rows = [("default", "Claude Code-credentials")]
base = os.path.expanduser("~/.claude-profiles")
if os.path.isdir(base):
    for n in sorted(os.listdir(base)):
        if os.path.isfile(os.path.join(base, n, ".claude.json")):
            rows.append((n, slot(os.path.join(base, n))))
for name, s in rows:
    raw = subprocess.run(["security", "find-generic-password", "-a", u, "-s", s, "-w"],
                         capture_output=True, text=True).stdout.strip()
    if not raw:
        continue
    try:
        tok = (json.loads(raw).get("claudeAiOauth") or {}).get("accessToken") or ""
    except Exception:
        continue
    if tok:
        print(name, hashlib.sha256(tok.encode()).hexdigest()[:12])
PY
)

# Accumulators for the D2 account-level invariant.
SLOT_STATE=""     # "<profile> has-slot|no-slot <authenticated email>" per line
# TOKHASHES ("<slot label> <sha256(accessToken) prefix>" per line) is built at
# the top of this file. It is NOT reset here: an assignment on this line used to
# clobber it before D2 could read it, so "no shared tokens across slots" printed
# unconditionally and the check for the gotcha #1 class — one credential in two
# slots, which killed seven live sessions on 2026-08-04 — was VACUOUS. Restored
# 2026-08-20. Do not re-add an assignment here (gotcha #27: prove a gate can fail).
warns=0

pass() { printf '  PASS  %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; warns=$((warns+1)); }
fail() { printf '  FAIL  %s\n' "$*"; fails=$((fails+1)); }
info() { printf '  info  %s\n' "$*"; }
hdr()  { printf '\n== %s\n' "$*"; }

printf '============================================================\n'
printf ' VERIFY  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf '============================================================\n'

# ------------------------------------------------- A. the default setup works
hdr "A. Your normal Claude Code (the default profile)"

[ -x "$CLAUDE_BIN" ] && pass "native binary present: $CLAUDE_BIN -> $(readlink "$CLAUDE_BIN" | xargs basename)" \
                     || fail "native binary missing at $CLAUDE_BIN"

AUTH=$("$CLAUDE_BIN" auth status 2>/dev/null)
if printf '%s' "$AUTH" | grep -q '"loggedIn": true'; then
  pass "default profile logged in as $(printf '%s' "$AUTH" | grep '"email"' | sed -E 's/.*: "(.*)",?/\1/')"
else
  fail "default profile is NOT logged in"
fi

DEF_KC=$(security dump-keychain 2>/dev/null | grep -c '"svce"<blob>="Claude Code-credentials"')
[ "$DEF_KC" = "1" ] && pass "default keychain login intact (exactly 1)" \
                    || fail "default keychain login count is $DEF_KC, expected 1"

# settings.json and CLAUDE.md are OPTIONAL in Claude Code: plenty of working setups have
# neither. WHY that matters here: this script was first written on a machine that had
# both, and treated their absence as a failure. On a minimal setup that produced
# failures for nothing being wrong. Absent = INFO. Present but a symlink = FAIL, because
# the profiles link TO these files, so the originals must be real files.
for f in "$HOME/.claude/settings.json" "$HOME/.claude/CLAUDE.md"; do
  if [ -L "$f" ]; then fail "$(basename "$f") in ~/.claude is a symlink (profiles link to it, so it must be a real file)"
  elif [ -f "$f" ]; then pass "$(basename "$f") is a real file in ~/.claude"
  else info "$(basename "$f") not present in ~/.claude (optional)"; fi
done
[ -f "$HOME/.claude.json" ] && [ ! -L "$HOME/.claude.json" ] && pass ".claude.json is a real file" \
                              || fail ".claude.json missing or unexpectedly a symlink (run Claude Code once and sign in)"
if [ -f "$HOME/.claude/settings.json" ]; then
  python3 -c "import json;json.load(open('$HOME/.claude/settings.json'))" 2>/dev/null \
    && pass "settings.json is valid JSON" || fail "settings.json is not valid JSON"
fi
python3 -c "import json;json.load(open('$HOME/.claude.json'))" 2>/dev/null \
  && pass ".claude.json is valid JSON" || fail ".claude.json is not valid JSON"

# ------------------------------------------------- B. what the profiles share
# INFO only, never PASS/FAIL. WHY: the original of this script compared these counts
# against a baseline captured on one machine before its migration. A fresh install has
# no baseline, so a threshold here could only produce false failures. The numbers are
# printed so you can compare two runs by eye (a count that drops is worth a look).
hdr "B. What every profile shares from ~/.claude (counts, for your own comparison)"
show_count() { # name path
  info "$1: $(ls -1 "$2" 2>/dev/null | wc -l | tr -d ' ')"
}
show_count "skills"   "$HOME/.claude/skills"
show_count "commands" "$HOME/.claude/commands"
show_count "agents"   "$HOME/.claude/agents"
show_count "hooks"    "$HOME/.claude/hooks"

T=$(find "$HOME/.claude/projects" -maxdepth 2 -name '*.jsonl' -not -path '*/subagents/*' 2>/dev/null | wc -l | tr -d ' ')
info "session transcripts: $T"
info "history size: $(du -sh "$HOME/.claude/projects" 2>/dev/null | cut -f1)"

# --------------------------------------------------------- D. profiles wiring
hdr "D. Each account profile is wired correctly"
if [ ! -d "$PROFILES_DIR" ]; then
  info "no profiles exist yet (nothing to check)"
else
  # every one of these MUST be a symlink into ~/.claude, or the profile is degraded
  SHARED="skills commands agents hooks plugins CLAUDE.md settings.json projects"
  for p in "$PROFILES_DIR"/*/; do
    [ -d "$p" ] || continue
    name=$(basename "$p")
    # A profile is a dir with its own .claude.json. Skip anything else that lands
    # here (e.g. __pycache__ from running the python helpers) so it is not
    # reported as a degraded profile.
    [ -f "$p.claude.json" ] || { info "skipping $name (not a profile)"; continue; }
    printf '\n  --- profile: %s\n' "$name"
    for x in $SHARED; do
      t="$p$x"
      if [ -L "$t" ]; then
        dest=$(readlink "$t")
        if [ -e "$t" ]; then pass "$name/$x -> $dest"
        else fail "$name/$x is a symlink to $dest but the target does not exist"; fi
      elif [ -e "$t" ]; then
        # THE GOTCHA: Claude Code writing settings.json can replace the symlink
        # with a real file, silently un-sharing it. This catches that.
        fail "$name/$x is a REAL FILE, not a symlink — it has drifted out of sync with ~/.claude"
      elif [ ! -e "$HOME/.claude/$x" ]; then
        # Nothing to link to: new-profile.sh skips an item that ~/.claude does not have,
        # so its absence from the profile is expected, not a degraded profile.
        info "$name/$x not linked (~/.claude/$x does not exist, nothing to share)"
      else
        fail "$name/$x is MISSING — this profile is degraded. Fix: ln -s ~/.claude/$x $p$x"
      fi
    done
    [ -f "$p.claude.json" ] && [ ! -L "$p.claude.json" ] \
      && pass "$name/.claude.json is its own private copy (correct)" \
      || fail "$name/.claude.json missing or symlinked (must be private)"

    perms=$(stat -f '%Lp' "$p" 2>/dev/null)
    [ "$perms" = "700" ] && pass "$name folder is private (700)" || fail "$name folder perms are $perms, expected 700"

    # what account is this profile signed into?
    st=$(CLAUDE_CONFIG_DIR="${p%/}" "$CLAUDE_BIN" auth status 2>/dev/null)
    if printf '%s' "$st" | grep -q '"loggedIn": true'; then
      pemail=$(printf '%s' "$st" | grep '"email"' | sed -E 's/.*: "(.*)",?/\1/')
      info "$name signed in as: $pemail"
    else
      pemail=""
      info "$name not signed in yet — run:  claude-as $name  then  /login"
    fi

    exp="Claude Code-credentials-$(printf '%s' "${p%/}" | shasum -a 256 | cut -c1-8)"
    # Record for the account-level invariant in section D2. Slot presence alone
    # cannot distinguish "parked in DEFAULT" from "credential destroyed".
    if printf '%s\n' "$KCDUMP" | grep -qx "$exp"; then
      pass "$name has its own keychain login ($exp)"
      SLOT_STATE="$SLOT_STATE$name has-slot $pemail
"
    else
      # A profile with no slot cannot authenticate, so `auth status` returns no
      # email for it. Its .claude.json identity is the ONLY way to know whose home
      # this directory is — and that is legitimate here: we are asking "who does
      # this empty directory belong to", not "what is this credential". Without
      # this fallback the check false-FAILs on the correctly-parked active account.
      [ -z "$pemail" ] && pemail=$(python3 -c "
import json,sys
try: print((json.load(open('$p.claude.json')).get('oauthAccount') or {}).get('emailAddress') or '')
except Exception: print('')" 2>/dev/null)
      SLOT_STATE="$SLOT_STATE$name no-slot $pemail
"
    fi
  done

  # ---------------------------------------------------------- D2. the invariant
  # REWRITTEN 2026-08-17 with the switch mechanism. The invariant it used to
  # enforce was "EXACTLY ONE profile has no slot of its own — the active one,
  # whose credential is parked in the shared DEFAULT slot." That was correct for
  # a switch that MOVED credentials, and it is now exactly backwards.
  #
  # The invariant today: EVERY profile permanently owns its own credential in its
  # own slot. Nothing is parked, nothing is moved, so a profile with no slot is
  # never "expected" — it is signed out, full stop. The DEFAULT slot is no longer
  # part of the rotation at all; it is the lane any launchd jobs
  # run on, because launchd does not source ~/.zshrc.
  hdr "D2. Account-level invariant (every account owns its own credential)"

  DEF_EMAIL=$("$CLAUDE_BIN" auth status 2>/dev/null | grep '"email"' | sed -E 's/.*: "(.*)",?/\1/')
  [ -n "$DEF_EMAIL" ] && info "FLEET lane (shared slot — sessions, new terminals, scheduled jobs): $DEF_EMAIL" \
                      || fail "shared slot does not authenticate — the FLEET (sessions + crons) is signed out. Fix: claude-switch <name>"

  # DEFAULT stays IN the shared-token scan below (that check is about identical
  # tokens and applies everywhere), but it is deliberately EXCLUDED from the
  # same-account scan: it is EXPECTED to hold a second, independent login for an
  # account that also has a profile. Two different token pairs for one account is
  # safe — the fatal case is the SAME token in two slots.

  # NOT a pipe: `cmd | while read` runs the loop in a SUBSHELL, so fails=$((fails+1))
  # would be discarded and this check would silently never fail. Here-string keeps
  # the loop in this shell.
  while read -r n state email; do
    [ -z "$n" ] && continue
    if [ "$state" = "no-slot" ]; then
      fail "$n has NO CREDENTIAL of its own — signed out. Fix: claude-login $n"
    fi
  done <<< "$SLOT_STATE"

  # FATAL: the same token in two slots is ONE shared single-use refresh token.
  # Whichever process refreshes first spends it; every other holder dies. This
  # killed seven live sessions on 2026-08-04 and is the reason the credential-
  # moving design existed at all. Nothing writes credentials any more, so this
  # should now be structurally impossible — which is exactly why it is still
  # checked. A regression here means something started copying again.
  DUPTOK=$(printf '%s' "$TOKHASHES" | awk '{print $2}' | sort | uniq -d)
  if [ -n "$DUPTOK" ]; then
    for h in $DUPTOK; do
      who=$(printf '%s' "$TOKHASHES" | awk -v h="$h" '$2==h{printf "%s ", $1}')
      fail "SHARED TOKEN — $who hold the identical credential (one single-use refresh token)"
    done
  else
    pass "no shared tokens across slots"
  fi

  # Same account in two PROFILE slots is still worth flagging (two profiles for
  # one subscription is usually a mistake), but DEFAULT is excluded per above.
  DUPACC=$(printf '%s' "$SLOT_STATE" | awk '$2=="has-slot" && $3!=""{print $3}' | sort | uniq -d)
  if [ -n "$DUPACC" ]; then
    for e in $DUPACC; do
      who=$(printf '%s' "$SLOT_STATE" | awk -v e="$e" '$3==e{printf "%s ", $1}')
      warn "SAME ACCOUNT in two profiles — $who both authenticate as $e"
    done
  else
    pass "every profile holds a distinct account"
  fi

  # ------------------------------------------------- D3. the switch mechanism
  # These three replace the old gotcha #19 ritual (switch -> verify -> undo ->
  # verify, comparing fingerprints by hand before re-arming the cockpit key).
  # That ritual was needed because switching mutated live credentials. It cannot
  # now, and THESE checks are what prove it, on every run rather than once.
  hdr "D3. Switch mechanism (must be incapable of touching a credential)"

  SWITCH_PY="$PROFILES_DIR/claude-switch.py"
  if [ -f "$SWITCH_PY" ]; then
    # AST gate, not grep: a text search for `add-generic-password` matches the
    # DOCSTRING that explains its absence, and would miss a call built from
    # concatenated strings. This parses the file and inspects the argv of every
    # subprocess call. Verified non-vacuous by running it against the pre-rebuild
    # file, which correctly FAILS on its two mutations.
    if python3 - "$SWITCH_PY" <<'PYGATE'
import ast, sys
MUT = {"add-generic-password","delete-generic-password","set-generic-password",
       "add-internet-password","delete-internet-password","unlock-keychain","-i","-U"}
tree = ast.parse(open(sys.argv[1]).read(), sys.argv[1])
bad = []
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and (getattr(n.func,"attr",None) or getattr(n.func,"id",None)) in \
       ("run","Popen","call","check_call","check_output","system"):
        for c in ast.walk(n):
            if isinstance(c, ast.Constant) and isinstance(c.value,str) and c.value in MUT:
                bad.append(f"line {n.lineno}: {c.value!r}")
sys.exit(1 if bad else 0)
PYGATE
    then pass "claude-switch.py cannot write or delete a credential (AST gate)"
    else fail "claude-switch.py CONTAINS A CREDENTIAL MUTATION — blank SWITCH_PROVEN_ON in claude-cockpit.py today"
    fi

    # The python validator and the shell validator must agree on every name, or
    # the cockpit and your terminals silently disagree about which account is
    # live. A Unicode homoglyph split them once (python str.isalnum() accepts
    # Cyrillic; the shell's [a-zA-Z0-9_-] does not), caught 2026-08-17.
    if python3 - "$SWITCH_PY" "$PROFILES_DIR/claude-as.sh" <<'PYGATE'
import importlib.util, subprocess, sys
s = importlib.util.spec_from_file_location("cs", sys.argv[1])
cs = importlib.util.module_from_spec(s); s.loader.exec_module(cs)
names = ["good","a-b","a_b","A1"] + [chr(c) for c in range(32,127)] + ["gооd","αβ","a b","../x"]
script = 'while IFS= read -r n; do case "$n" in "") echo 0;; *[!a-zA-Z0-9_-]*) echo 0;; *) echo 1;; esac; done'
r = subprocess.run(["zsh","-c",script], input="\n".join(n for n in names if "\n" not in n),
                   capture_output=True, text=True)
shell = [x=="1" for x in r.stdout.strip().split("\n")]
flat = [n for n in names if "\n" not in n]
bad = [(n,cs.valid_name(n),sh) for n,sh in zip(flat,shell) if cs.valid_name(n)!=sh]
sys.exit(1 if bad else 0)
PYGATE
    then pass "profile-name rule identical in claude-switch.py and the shell tools (new-profile.sh's case rule)"
    else fail "NAME RULE MISMATCH — python and shell will disagree about which dirs are profiles"
    fi
  else
    fail "claude-switch.py missing"
  fi

  # v4 (2026-08-17 evening): there is no .active file — the fleet IS whoever
  # the shared slot authenticates as. If the file reappears, something is
  # running the retired v3.5 pinning design.
  if [ -f "$PROFILES_DIR/.active" ]; then
    warn ".active exists but pinning was retired 2026-08-17 — rm ~/.claude-profiles/.active"
  else
    pass "no .active file (v3.5 pinning fully retired)"
  fi

  # Fleet-anchor integrity: the fleet grant is only EXPENDABLE because the same
  # account keeps a permanent anchor in its own profile slot. An unanchored
  # fleet account is the one destroy-shaped state left in the v4 design — the
  # next switch would overwrite its only local grant (recovery: re-login).
  if [ -n "$DEF_EMAIL" ]; then
    ANCHOR=""
    for p in "$PROFILES_DIR"/*/; do
      pn=$(basename "$p")
      [ "$pn" = "__pycache__" ] && continue
      [ -f "$PROFILES_DIR/$pn/.claude.json" ] || continue
      PE=$(python3 - "$PROFILES_DIR/$pn/.claude.json" <<'PYEOF'
import json, sys
print((json.load(open(sys.argv[1])).get("oauthAccount") or {}).get("emailAddress") or "")
PYEOF
)
      [ "$PE" = "$DEF_EMAIL" ] && ANCHOR="$pn" && break
    done
    if [ -n "$ANCHOR" ]; then
      pass "fleet account is anchored by profile '$ANCHOR' (fleet grant is expendable)"
    else
      warn "fleet account $DEF_EMAIL has NO anchor profile — its only local grant lives in the fleet slot and the next switch overwrites it. Anchor it: new-profile.sh <name> then claude-login <name>"
    fi
  fi
fi

# ---------------------------------------------------------------- E. switcher
hdr "E. The claude-as switcher"
[ -f "$PROFILES_DIR/claude-as.sh" ] && pass "switcher script present" || info "switcher script not installed"
grep -qF 'claude-as (multi-account profiles)' "$HOME/.zshrc" 2>/dev/null \
  && pass "~/.zshrc sources the switcher" || info "~/.zshrc does not source it yet"

printf '\n============================================================\n'
if [ "$fails" -eq 0 ] && [ "$warns" -eq 0 ]; then
  printf ' ALL CHECKS PASSED — nothing is broken.\n'
elif [ "$fails" -eq 0 ]; then
  printf ' %d WARNING(S), no failures. Nothing is broken; see above.\n' "$warns"
else
  printf ' %d CHECK(S) FAILED%s.\n' "$fails" \
    "$( [ "$warns" -gt 0 ] && printf ', %d warning(s)' "$warns" )"
  printf '\n'
  # WHY there is no blanket "undo everything" suggestion here: an earlier footer
  # printed one teardown command for ANY failure, and that command deleted every
  # per-profile keychain slot. A harmless symlink drift therefore routed the operator
  # to a command that signs out every account. Remedies are per-check, and removing
  # a login is always a deliberate, separate act (see uninstall.sh, which only
  # PRINTS the keychain commands and never runs them).
  printf ' Fix the SPECIFIC failure above. Common ones:\n'
  printf '   symlink drifted to a real file  ->  rm <path> && ln -s ~/.claude/<x> <path>\n'
  printf '   a profile has NO CREDENTIAL     ->  claude-login <name>\n'
  printf '   SHARED TOKEN across two slots   ->  see docs/safety.md\n'
fi
printf '============================================================\n'
exit "$fails"
