# Gotchas

A gotcha is something about Claude Code, the macOS keychain or these tools that behaved in a way
nobody would guess from reading the code, and that cost real time or a real login to find out.
Each one has a symptom, the reason, and what to do.

How to read this file:

- **The numbers are stable.** Code comments say things like "gotcha #45", and those references
  must keep resolving. A number is never reused. Nine entries were about the original author's own
  machine setup and are left as one-line stubs so the numbering holds.
- **Several entries describe designs that no longer exist.** They are marked HISTORICAL. They are
  kept because they explain why the current design is the way it is. The short version of that
  story is in [how_it_works.md](how_it_works.md#design-history).
- **Terms** such as fleet, anchor, slot, spare, borrowed and pinned are defined in
  [how_it_works.md](how_it_works.md#words-used-here).
- **Profile names are examples.** `personal`, `work`, `side`, `ops`, `team` and `lab`
  stand for the accounts that were in use when a measurement was taken. They match the names used
  in the code comments.
- **Facts about Claude Code are dated.** Where a version is named, that is the version the fact
  was measured on. Where only a date is given, the version was not recorded. Claude Code was
  updating about daily; the nearest recorded versions are in this table. Any of these facts can
  change with an update.

| Date | Claude Code version recorded |
|---|---|
| 2026-08-04 | 2.1.221, then 2.1.222 the same day |
| 2026-08-17 | 2.1.234 |
| 2026-08-19 | 2.1.236 |
| 2026-08-24 | 2.1.241 |
| 2026-10-01 | 2.1.287 |
| 2026-10-09 | 2.1.296 (the version this release was tested with) |

---

## 1. Putting one login in two slots cancels both. This is the worst one.

**Symptom:** Several unrelated running sessions all show `Login expired · Please run /login`
within the same few minutes. Or a profile that "has a keychain entry" reports `loggedIn: false`.
Or an account works all day and fails the moment its token renews.

**Why it happens:** A stored login is an access token plus a **single-use refresh token**. Put a
copy in two keychain slots and two independent processes hold the same one-shot ticket. Whichever
renews first spends it, and the server rejects everyone else. Claude Code's renewal lock is a file
inside the settings folder (`.oauth_refresh.lock`), so two folders have two different locks and no
mutual exclusion at all. Claude Code coordinates concurrent renewals within one settings folder.
It cannot coordinate across folders.

Measured 2026-08-04: a scratch profile holding a copy of another slot's login got a 401, renewed
(correctly), and so spent the original's refresh token. 58 seconds later, seven sessions running
on the original began to fail.

**What to do:** One login, exactly one slot, always.

- Never copy a login. If a login has to change slots, move it: write the destination, read it
  back, then remove the source. An interruption then leaves a temporary duplicate, which is
  recoverable, never a loss. This is what `claude-swap` does.
- Signing the same account in twice is **not** this problem. An earlier version of this entry said
  a second sign-in cancels the first. That was measured false on 2026-08-17 (Claude Code 2.1.234):
  an anchor authenticated more than four hours after a newer login for the same account was made,
  and an anchor and a newer fleet login for one account authenticated at the same moment. Being
  signed in on several devices is the same fact. The current design depends on it. See the
  addendum after #31.
- What remains true: overwriting a slot destroys the local copy of whatever was there, and signing
  in again to the same settings folder replaces that folder's login.
- `claude-swap --check` and `verify.sh` both look for a login held twice.

---

## 2. A keychain slot can exist and hold no login. "Present" is not "signed in".

**Symptom:** `security find-generic-password -s "<slot>"` succeeds, an audit says the item is
present, and the profile still cannot authenticate.

**Why it happens:** The item can survive with empty-string tokens:

```
SLOT         accessTok  refreshTok
side                 0           0   <- the item exists, the tokens are blank
ops                108         108
```

A check for existence passes. Authentication does not. Since the swap was added there is a second
way to get here: an item can hold MCP server sign-ins and no Claude login (#44).

**What to do:** Never test presence. Test authentication:

```bash
CLAUDE_CONFIG_DIR=~/.claude-profiles/<name> ~/.local/bin/claude auth status
# the field that matters: "loggedIn": true
```

In the code, `has_claude_login()` tests for a refresh token inside the item, and `signed_in()`
asks Claude Code.

---

## 3. A failed usage refresh exits 0 and prints a cost summary

**Symptom:** The cockpit shows numbers that never change, with nothing wrong on screen. The real
account has moved 40 percentage points.

**Why it happens:** For a signed-out profile, `claude -p "/usage"` exits 0 and prints the cost
footer of print mode instead of the usage report:

```
Total cost:            $0.0000
Total duration (API):  0s
```

The exit code and stderr are both clean. A bare `except: pass` catches nothing, because nothing
was thrown.

**What to do:** Judge success on the content, never on the exit code:

```python
ok = "Current session" in (r.stdout or "")
```

And show the failure on the row. Measured 2026-08-04: one account's row sat 46 minutes old
showing 40% and 8% while the account was really at 80% and 16%.

---

## 4. Never call bare `claude` from a script

**Symptom:** A script behaves as if Claude Code lacked a command such as `claude auth status`, or
the usage data it expects is missing.

**Why it happens:** A `PATH` lookup does not always find the Claude Code you think it does. It can
hit a shim (some terminal apps put a wrapper named `claude` first on `PATH`) or a stale install
left behind by another install method. An old version may lack commands these tools rely on. A
`launchd` job has no shell `PATH` at all.

**What to do:** Scripts use one fixed path: the environment variable `COCKPIT_CLAUDE_BIN` if it is
set, otherwise `~/.local/bin/claude`, the symlink that Anthropic's native installer keeps pointed
at the current version. That symlink carried on working across an automatic update from 2.1.221
to 2.1.222 in the middle of a session (2026-08-04). Never hard-code a path that contains a version
number.

---

## 5. Identity lives in `.claude.json`, not in the keychain

**Symptom:** You change the login in a slot and `claude auth status` still reports the old email
address. Requests are billed to the new account while the display insists on the old one.

**Why it happens:** The keychain holds only tokens. `email`, `orgId` and `orgName` come from
`oauthAccount` in `<settings folder>/.claude.json`. Proven: a fresh settings folder that was given
a valid login returned `loggedIn: true` with `"email": null, "orgId": null`.

**What to do:** Anything that changes a login by itself must update **both**, or Claude Code writes
the usage cache under the wrong `accountUuid` and the numbers on screen belong to another account.
Reproduced 2026-08-04: `~/.claude.json` held one account's 8% and 15% filed under a different
account's id. Claude Code's own sign-in updates both. `claude-swap` patches `oauthAccount` and
clears the per-account caches as part of every swap.

The corollary matters more: never decide where a login belongs by reading this file (#17).

---

## 6. A session stopped on a usage limit and a new login (corrected by #47)

**Symptom:** You switch accounts expecting the sessions that were stopped on a limit to continue
on the fresh account. On the version this was first studied on, they sat there until the original
account's window reset.

**Why it happens:** As read in the Claude Code binary in August 2026 (2.1.234 on 2026-08-17), the
code path that reads the keychain again is triggered by an HTTP 401 and by a 403 for a revoked
token. A subscription usage limit arrives as a 429, which is not routed to that path.

| Event | Reads the keychain again? |
|---|---|
| 401, or 403 for a revoked token | Yes, at once. |
| 429 usage limit | Not by that path. |
| Access token expiry (about 8 hours), or a helper call that may renew | Yes, as a side effect. |

An even earlier version of this entry claimed the opposite. It came from reading the 401 handler
and assuming that a usage limit reached it.

**Correction (#47, Claude Code 2.1.287, 2026-10-01):** in two measurements, running sessions were
on the new login within about a minute, and sessions stopped on a limit continued on the new
account at their next message. Treat the table above as the worst case, not the usual case.

**What to do:** After a fleet switch, send one message to a session that is stopped on a limit.
Do not build anything on either timing without measuring it on your version.

**Related trap:** a session whose requests are succeeding and a session that is blocked take
different code paths. Testing one tells you nothing about the other.

---

## 7. `CLAUDE_CONFIG_DIR` is fixed when a process starts

**Symptom:** You expect a running session to follow a change of profile. It never does.

**Why it happens:** A process holds a private copy of its environment from the moment it starts.
There is no way to change another process's environment afterwards. Verified with `ps eww` on a
live process.

**What to do:** Treat it as a safety property. Nothing you do in one terminal can pull the account
out from under a pinned session in another. Only the login behind the shared slot can change for
running sessions, and only for sessions that read the shared slot.

---

## 8. `cachedUsageUtilization` is cleared on account change and written at most every 5 minutes

**Symptom:** A profile that has just signed in shows no usage at all. Or a refresh appears to do
nothing.

**Why it happens:** Two separate mechanisms in Claude Code (constants seen in the binary in
2026-08):

- the cache is cleared whenever its `accountUuid` stops matching the signed-in account;
- writes are limited to once per 5 minutes (300000 ms), and reads treat the value as valid for
  1 hour (3600000 ms).

**What to do:** Expect "no data" right after a sign-in and refresh once. Never conclude that a
refresh failed because `fetchedAtMs` did not change inside the 5-minute window. Check the content
of the report instead (#3). Always show the age of `fetchedAtMs` to the person reading: a 1-hour
validity is far too generous for a meter that was seen to move 40 points in 45 minutes.

---

## 9. Usage belongs to the account, not to the profile

**Symptom:** Two profiles show identical usage and it looks like a bug.

**Why it happens:** The allowance belongs to the account. Two profiles signed in to the same
account report the same numbers, correctly.

**What to do:** Do not "fix" it. Do check whether it is #1 in disguise: two slots for one account
is fine when they hold two separate logins, and fatal when they hold the same one.
`claude-swap --check` tells you which.

---

## 10. Many sessions on one login have a contention ceiling

**Symptom:** Several sessions fail authentication in the same second. The signature is always a
cluster, never a single session.

**Why it happens:** Dozens of concurrent processes can share one keychain slot. Claude Code does
coordinate their renewals, but its lock gives up after 5 retries at 1 to 2 seconds each, about 7
seconds of contention in all (as read in the binary in 2026-08). If the renewal round trip is slow
while many processes wake together, some run out of retries.

**What to do:** Nothing local fixes this. It comes with running many sessions on one subscription,
and it was rare at the load where it was measured (a few events over three weeks). Do not blame
the switch tools for it, and do not blame it for a cluster the switch tools caused. Tell them
apart by size and by whether a login had just been written.

---

## 11. Two messages look like auth failures and only one is

**Symptom:** A simple text search of the session logs reports hundreds of auth failures.

**Why it happens:** `You've hit your session limit · resets 3:30pm` is ordinary usage exhaustion,
not an auth failure, and it is far more common than a real one. Searching transcripts for words
like "session has expired" also matches those words inside your own source files and tool output.

**What to do:** Look only at assistant messages whose model is `<synthetic>`, and match only:
`run /login`, `Not logged in`, `OAuth session expired and could not be refreshed`,
`token has expired`. Everything else is noise.

---

## 12. (omitted: specific to the original author's machine setup)

---

## 13. Any folder in `~/.claude-profiles/` looks like a profile

**Symptom:** `verify.sh` reports ten failures for a profile named `__pycache__`.

**Why it happens:** Importing the Python tools creates a `__pycache__/` folder, and a simple scan
treats every subfolder as a profile.

**What to do:** A profile is a folder that **contains its own `.claude.json`**. Every tool and
`verify.sh` test for that file. The Python tools also set `sys.dont_write_bytecode` so the folder
is not created in the first place. Keep both when adding a tool. This was not enough by itself:
see #30.

---

## 14. (omitted: specific to the original author's machine setup)

---

## 15. (omitted: specific to the original author's machine setup)

---

## 16. (omitted: specific to the original author's machine setup)

---

## 17. [HISTORICAL: the move-based switch was deleted 2026-08-17] Never derive where a login goes from `.claude.json`

**Symptom:** A switch writes one account's login into a different account's keychain slot and
destroys the login that was there. The abort message says "Nothing was deleted."

**Why it happens:** `oauthAccount` in `~/.claude.json` can disagree with the login that is really
in the shared slot (#5). The switch of that time created the disagreement itself: when its update
of the file ran out of retries it returned a generic failure, the caller logged "left as is", and
90 seconds later it tried again on the stale identity. On 2026-08-05 that destroyed one account's
login and left two slots holding the same token. One key press.

**What to do:** At the time, the fix was to derive the destination from the layout of the slots
with no identity string consulted (and that failed in its own way: #23, #24). The lasting rules:

- Never read a cached identity to decide where a login goes. `claude-swap` asks the server who a
  token belongs to.
- The shared slot holds a login that matches no profile: refuse.
- The situation is ambiguous: refuse.
- A change that half completed must return its own distinct "state is inconsistent, stop" result.
  Never a generic failure that a caller will retry.

---

## 18. [HISTORICAL: the move-based switch was deleted 2026-08-17] Verify a credential write by fingerprint, not by `auth status`

**Symptom:** The switch always aborts with "written but does not authenticate as X", although the
write succeeded.

**Why it happens:** `claude auth status` reports the email address from `.claude.json`, not from
the keychain (#5). A check that asks for an email address right after writing a login runs before
the file has been updated, so it can never pass. This bug shipped and broke the first live switch
of that design.

**What to do:** Write, read the slot back, and compare. That is the only comparison available
locally that is true to the login itself. `claude-swap` now compares the whole item after every
write (#43), and asks the server for the identity.

---

## 19. A key that changes logins needs a proof date

**Symptom:** A defect that was already written down as blocking fires in real use, because the key
that triggers it was never disarmed.

**Why it happens:** Writing a hazard down and removing its trigger are different acts. The defect
in #17 was documented hours before the key press that fired it.

**What to do:** `claude-cockpit.py` carries two constants. `SWITCH_PROVEN_ON` gates the number
keys: blank it and they stop working. `SWAP_PROVEN_ON` gates the no-browser swap behind those
keys: blank it and the keys use the browser sign-in only. (The user's `swap_enabled` setting is a
separate, second gate on the swap.)

- If a keychain write ever appears in `claude-switch.py` (the `verify.sh` D3 check fails), blank
  `SWITCH_PROVEN_ON` the same day.
- Before changing the move or restore code in `claude-swap.py`, blank `SWAP_PROVEN_ON`. Restore it
  after `tests/test_claude_swap.py` passes and one swap run by hand has worked.

An older ritual (switch, verify, undo, verify, with identical token fingerprints either side) was
deleted. Under the current design every browser switch makes a new login, so the fingerprints can
never match and the ritual could never be satisfied.

---

## 20. [HISTORICAL: the move-based switch was deleted 2026-08-17] The active account is signed out of its own profile

**Symptom:** The one account you are actively using, and are plainly signed in to, is the row
that reads "NOT REFRESHING, signed out?". Every other row is fine.

**Why it happens:** This is the fix for #1 seen from the other side. A move-based switch put the
login into the shared slot, so the active account's own slot was empty by design. A refresh that
set `CLAUDE_CONFIG_DIR` to that profile hit an empty slot, exited 0, printed the cost footer
instead of the usage report (#3) and was scored as a failure. The profile's `.claude.json` also
stopped being updated, so the row froze on the numbers it had when it became active.

Measured 2026-08-14: the account on the shared slot showed a frozen 0% and 0% while it was really
at 100% of its session window, and it was the only account with any room left on the per-model
limit. The screen recommended a different account whose binding limit was the per-model one at
100%.

**What to do:** Under the browser-switch design nothing leaves its own slot, so every row is
refreshed through its own profile and this special case was deleted. It came back with the swap:
a borrowed account's own slot is empty again. See #50 for how the cockpit handles that now.

---

## 21. Never state a cause you did not check. "Signed out?" on a timeout is dangerous advice

**Symptom:** A row says the account is not refreshing, asks "signed out?" and tells you to sign
in. The account is signed in perfectly well.

**Why it happens:** The failure branch merged "failed" and "timed out" into one message that named
a cause it had never tested. A function that checks sign-in state existed in the file and was
never called. On a machine running many sessions with plugins and MCP servers, a 90-second timeout
of the refresh is an ordinary false alarm.

The advice was worse than unhelpful. It sent the user to sign in on a lane that was not the one
with the problem, and a sign-in is never the fix for a timeout.

**What to do:** Keep the states apart, and mention signing in only for the one that was verified:

| Refresh result | What the row says |
|---|---|
| Timed out | The numbers are old, press `r`, do not `/login`. |
| Borrowed row, server rate limiting | Numbers return in a few minutes. Not signed out. |
| Failed, and `claude auth status` says logged in | Signed in, but `/usage` returned no report. Transient. |
| Failed, and `claude auth status` says not logged in | `SIGNED OUT`, fix: `claude-login <name>`. |

Run the sign-in check when rows are loaded, never in the drawing loop. It is a subprocess that
can take 20 seconds, and the screen is redrawn several times a second.

---

## 22. The per-model weekly limit is not in `utilization.seven_day_<model>`. It is in `limits[]`

**Symptom:** You add a column for the per-model limit, read `utilization.seven_day_opus` or
`seven_day_sonnet`, and every account shows a dash for ever. The keys exist, so nothing looks
wrong.

**Why it happens:** Those named keys are all `null` (seen 2026-08-14). The real per-model figure
appears only as an entry in `utilization.limits[]`:

```json
{ "kind": "weekly_scoped", "group": "weekly", "percent": 22,
  "severity": "normal", "resets_at": "...",
  "scope": { "model": { "id": null, "display_name": "Fable" } },
  "is_active": false }
```

The payload carries other entries with opaque names as well. They are not the per-model limit.

`is_active` is the most useful flag in the payload and was being thrown away. It is the server
naming the limit that actually binds the account. Measured 2026-08-14, when three of four accounts
showed a session at 0% while their binding limit was the per-model one:

| Account | session | weekly_all | per-model | binding |
|---|---|---|---|---|
| personal | 0% | 80% | **100%** | weekly_scoped |
| work | 0% | 97% | **100%** | weekly_scoped |
| ops | 0% | 88% | 89% | weekly_scoped |
| side | **100%** | 20% | 22% | session |

**What to do:**

- Walk `limits[]`, select `kind == "weekly_scoped"`, and read the name from
  `scope.model.display_name`. Handle zero, one or several entries.
- Never hard-code the model name as a lookup key or a column heading. Show the server's string,
  so that a rename or a second per-model limit does not mislabel the column.
- Show `is_active` (the cockpit draws `●`) and do **not** fold it into one headroom score. A
  per-model meter at 100% does not block the other models, so a single ranking would steer you
  away from an account that is perfectly usable. Give it its own recommendation line.

---

## 23. [HISTORICAL: superseded by the browser-switch design the same day] A plain `/login` destroyed the on-duty account's only login

Under the current design a plain `/login` in an unpinned session **is** a fleet switch, minus the
aiming and the check. It is allowed. This entry is what ended the move-based designs, where the
shared slot held an account's only login. The one case of that shape left today is a borrowed
login (#48).

**Symptom:** An account you used yesterday is signed out today with no login in its own slot, and
no log says why. Or every switch refuses with a message that the shared slot holds a login that
lives in no profile.

**Why it happens:** `/login` (or `claude auth login`) in a shell with no `CLAUDE_CONFIG_DIR` makes
a new login and writes it to the shared slot. It knows nothing about profiles. It empties nobody's
slot and it overwrites whatever was there.

Under the move-based design that slot held the on-duty account's only copy. A plain `/login`
therefore destroyed a login with no recovery short of signing in again.

Measured 2026-08-17: three plain logins in one afternoon. The login that had been in the shared
slot no longer existed in any slot. Nothing broke, but only because the design's invariant was
already broken: every account happened to still hold another login in its own slot.

**What to do:** This is why the default switch no longer parks anything. Every account keeps its
anchor, and the shared slot only ever holds a login that can be replaced.

- `claude-login <name>` names its target before acting and writes that profile's slot only.
- `claude-login --fleet` signs in to the shared slot on purpose.
- `/login` inside a session started with `claude-as` writes that profile's slot, which is the
  right one.

---

## 24. [HISTORICAL: the move-based switch was deleted 2026-08-17] A profile that never signed in looked like the fleet account's home

**Symptom:** You create a new profile and the switch immediately names **it** as the place to
park a live login.

**Why it happens:** The move-based switch found the active account's home by structure: "the one
profile whose keychain slot is empty". That is correct while exactly one slot is empty. A new
profile also has an empty slot. Creating one turned the switch from safely refusing into
confidently wrong.

Measured 2026-08-17, right after `new-profile.sh team`:

```
park_target() -> ('team', 'the one profile with no slot of its own')
```

That would have written another account's live login into the new profile's slot. It did not
happen because the cockpit's number keys had been disarmed before the profile was created.

The second half of the same trap: `new-profile.sh` copied `~/.claude.json` whole, so a new profile
inherited the previous account's `oauthAccount` and reported itself as an existing account. That
had been described as harmless. A profile that lies about its identity gives every lookup from
account to profile two candidates.

**What to do:**

- `new-profile.sh` removes `oauthAccount` and every per-account cache from the copied file. It
  keeps only the MCP server configuration and the list of trusted folders.
- Nothing infers ownership from an empty slot any more.
- `claude-switch` refuses a target that has no login in its own slot.
- Sign a new profile in straight after creating it.

---

## 25. A quiet flag that also silences errors produces a refusal with no reason

**Symptom:** The cockpit shows "refused (nothing changed):" with nothing after the colon. The tool
looks broken, when the state was merely something you could fix.

**Why it happens:** The switch routed every message through one print function, and its quiet
mode silenced that function, refusal reasons included. The cockpit of that time ran the switch
quietly and showed the last line of its output, so it had nothing to show.

**What to do:** A reason that cannot be read is worse than no message. Two things follow in the
current code:

- The cockpit no longer runs the switch quietly. It hands the terminal to the switch, which
  prints everything in full, and then shows a one-line result of its own for each exit code.
- `claude-switch <name> --quiet` still silences every message, refusals included. Use it only
  where the exit code (0 to 4) is all you need.

If you add a quiet mode to anything, send refusals to stderr and never silence them.

---

## 26. Python `str.isalnum()` accepts Cyrillic letters. The shell pattern `[a-zA-Z0-9_-]` does not

**Symptom:** The cockpit says you are on one account and every new terminal is on another, with
nothing reporting an error.

**Why it happens:** A profile name was validated both in Python and in shell. `str.isalnum()` is
Unicode-aware and returns true for Cyrillic, Greek and full-width letters. The shell pattern
`*[!a-zA-Z0-9_-]*` is ASCII only. A name such as `good` typed with a Cyrillic letter o was
accepted by the Python tool and rejected by every shell, which silently fell back to the default.

It was caught 2026-08-17 by a parity check, not by a hand-written list of cases. The hand-written
list is exactly what had missed it.

**What to do:** Validate with an explicit ASCII character set, never `isalnum()`, and keep the
rule identical in `claude-switch.py` (`valid_name`) and in `new-profile.sh`. Resolve anything that
depends on a profile name in one place: the shell asks `claude-switch.py` rather than repeating
the rule. `verify.sh` D3 runs the parity check on every run: every printable ASCII character plus
some hostile names, through both validators.

---

## 27. A text search cannot tell code from prose. Check the syntax tree

**Symptom:** A safety check reports a keychain write in a file whose only mention of one is a
comment explaining that there are none. Or, worse, it passes a file that builds the dangerous
argument by joining strings.

**Why it happens:** `grep -c 'add-generic-password'` matched the docstring of `claude-switch.py`,
which says that the file contains no such call. The tempting reaction is to delete the
documentation so the check passes, which makes both worse.

**What to do:** Parse the file and inspect the arguments of every subprocess call. That is what
`verify.sh` D3 does. And **prove the check can fail**: run it against a file that is known to be
bad. The previous version of `claude-switch.py` failed it on two calls, and the same check run on
`claude-swap.py` reports that file's keychain calls. That is what makes the pass on
`claude-switch.py` mean something.

---

## 28. The browser picks the account, not the command line. Always verify after a sign-in

**Symptom:** You ran `claude-switch work` (or typed `/login` meaning one account) and the whole
fleet is quietly on a different account. Nothing reported an error.

**Why it happens:** `claude auth login` cannot force an account. The consent screen offers
whichever claude.ai session the browser holds, and a browser can hold several. One wrong click,
or one stale browser session, signs the wrong account in to the shared slot, and every session,
terminal and background job follows it.

**What to do:** Never trust the sign-in's exit code or your memory of what you clicked.
`claude-switch` records the shared slot's fingerprint first, confirms that it changed, reads back
which account the slot now holds, and compares that with the account requested. A mismatch is
reported loudly with the exact command to run again (exit code 3). If you signed in by hand, run
`claude-switch` with no arguments afterwards and read the `FLEET` line.

---

## 29. A verifier that inherits its environment audits the wrong thing

**Symptom:** `verify.sh` reports that the shared slot authenticates as account A, while a probe of
the same slot with a clean environment says account B.

**Why it happens:** Run from a pinned shell, `CLAUDE_CONFIG_DIR` is in the environment, and every
plain `claude auth status` inside the script inherits it. The "shared slot" checks then examine
the pinned profile instead. Caught 2026-08-17: `verify.sh` said the shared slot was `work` when it
was really `personal`.

**What to do:** `verify.sh` unsets `CLAUDE_CONFIG_DIR` on its first line, and the Python tools
remove it for each fleet probe. Any new tool that asks a question about the fleet must do the
same. The environment is part of the input.

---

## 30. Claude Code plants a `.claude.json` in any folder it is pointed at

**Symptom:** `__pycache__` appears in the cockpit as one more account, holding a fresh signed-out
configuration, a `projects/` folder and a `backups/` folder.

**Why it happens:** Pointing `CLAUDE_CONFIG_DIR` at a bare folder makes Claude Code create a
configuration there. So the rule "a profile is a folder containing `.claude.json`" (#13) stops
being enough the moment any tool walks raw folders and probes them. An early shell loop did
exactly that: it probed every subfolder, Claude Code planted a configuration in `__pycache__`, and
every scanner then accepted it as a profile.

**What to do:** Three doors were closed on 2026-08-17. Profile scanners exclude `__pycache__` by
name as well as by the file test. The shell tools refuse to probe or launch into a folder that
has no `.claude.json` already. The Python tools set `sys.dont_write_bytecode`. If you write a tool
that walks `~/.claude-profiles/`, filter first and probe second. Here, probing is a write.

---

## 31. WATCH ITEM: a renewal race after a switch (unproven, bounded, observable)

**Symptom:** None observed. The scenario: a fleet session that still holds the old login in memory
reaches its renewal after the fleet was switched to a new login. If the renewal renewed the old
login from memory and wrote the result back, the fleet would silently go back to the old account
and the new login's local copy would be destroyed.

**Why it happens (or does not):** It is believed not to happen. The Claude Code binary (2.1.234)
carries machinery for "someone else changed the slot under me", which implies the slot is
consulted and not blindly written, and months of daily switching by `/login` produced no observed
flip-back. But the order of operations on the write path was not proven from the binary.

**What to do:** After a fleet switch from A to B, the shared slot must still belong to B hours
later (renewals are fine; the identity must stay B). One observed return to A turns this from a
watch item into a bug: report it, and pin sessions with `claude-as` until it is fixed. The damage
is bounded either way to one sign-in, because anchors are untouched by definition.

---

## ADDENDUM: the claim-2 seal test, "two logins for one account coexist" (2026-08-17, Claude Code 2.1.234): PASS

("Claim-2 seal test" is the name this test had in the original notes.) The question that decided
the browser-switch design: **does an account's older login survive its renewal after newer logins
exist for the same account?** If renewing had the effect of cancelling sibling logins, making a
fleet login would eventually kill the anchor, anchors would be a fiction and a switch by sign-in
would be unsafe.

Measured on one account's anchor, made three days earlier. By the time of the test, two newer
logins for the same account had been made that day, and one of those had been overwritten.

1. The anchor reached its expiry and renewed. The server accepted the old login's refresh token
   and issued a new pair.
2. The renewed anchor authenticated and returned a real usage report (judged on content, never on
   the exit code: #3).
3. The newer fleet login for the same account was alive at the same time.
4. A second account's pair, an anchor and a fleet login, were both alive at the same time.

**Conclusion:** logins are independent. On the server side, a new sign-in does not cancel an
account's existing logins: not at once (measured at +2 h 44 min and +4 h 11 min earlier the same
day) and not at renewal (this test). The original claim in #1 that a second sign-in cancels the
first is dead. What cancelled sessions in the 2026-08-04 incident was the same login in two
slots, never the second sign-in.

---

## 32. The sign-in page opens in Chrome's last-used profile. Set `BROWSER` to aim it

**Symptom:** `claude-switch personal` opens the consent screen in whichever Chrome window you
touched last, not in the Chrome profile that holds that account. On a machine with many Chrome
profiles that is almost never the right one. Every switch then costs a manual hop between
profiles and raises the odds of signing the wrong account in (#28).

**Why it happens:** `claude-switch` never opens a browser. It runs `claude auth login`, and Claude
Code opens the URL. As read in Claude Code 2.1.236: if the environment variable `BROWSER` is set,
Claude Code runs that program with the URL as its single argument. Otherwise it runs `open <url>`,
which goes to the default browser, and Chrome delivers the page to the window of its last-used
profile. There is no settings key for this. The environment variable is the only lever a user has.

**What to do:** For the one sign-in subprocess, `claude-switch.py` sets `BROWSER` to
`~/.claude-profiles/claude-open-in-chrome.sh` and `COCKPIT_CHROME_PROFILE_DIR` to the Chrome
profile folder. The script runs
`open -na "Google Chrome" --args --profile-directory=<folder> <url>`.

Details that each cost a measurement:

- **`open -n` is required.** Without `-n`, macOS only activates the Chrome that is already running
  and drops the `--args`, so neither the profile flag nor the URL arrives. With `-n` the request
  is passed on through Chrome's single running instance. Tested 2026-08-19: the same root process
  before and after, and no second instance.
- **The variable is named `COCKPIT_...` and not `CLAUDE_...`.** One code path in 2.1.236 removes
  every `CLAUDE_*` variable except a short allow-list from a child's environment. It is not the
  path used here, but that namespace belongs to the vendor.
- **Never export `BROWSER` from your shell startup file.** It would send every URL that Claude
  Code ever opens into one Chrome profile. Both places that set it limit it to one subprocess.
- **The fleet's sign-in environment removes `CLAUDE_CONFIG_DIR`. An anchor sign-in must not use
  it.** If `claude-login <name>` went through the same function, an intended anchor sign-in would
  write the shared slot while its own check looked at the untouched anchor. `claude-as.sh` builds
  its own environment for that reason. Do not merge the two.

**Aiming is not verification, and the aim can be right while the account is wrong.** The lookup
joins the Claude account to Chrome's **Google** sign-in (`Local State`,
`profile.info_cache[<folder>].user_name`). The claude.ai session in that profile's cookies is a
third identity that nothing here can see. A correctly aimed profile can still show a consent
screen for another account, and aiming removes the friction where you would have noticed. That is
why the switch prints a line asking you to confirm the address on the consent screen, and why the
check in #28 is still what makes a switch correct. Aiming changes who is offered, never who is
granted.

A rejected alternative, recorded so it is not tried again: Chrome has a `--profile-email` switch
(found by string search in Chrome 151). It loses to `--profile-directory` because what it does on
no match, or on several matches, is undocumented, and a black box cannot report honestly which
profile it opened.

### 32a. Measurement trap: Chrome's session files and `last_used` both mislead

**Symptom:** You try to prove which Chrome profile received a window, and the evidence
contradicts what you saw on screen.

**Why it happens:**

- `Local State -> profile.last_used` is written lazily. Measured twice: a window opened in one
  profile and then in another, while `last_used` stayed on a third.
- A new `Sessions/Session_*` file appears only if that profile had no window open. An idle profile
  produced one at once. A profile that was already open produced none, which reads as a failure
  and is not one. The modification time of `Preferences` is no use either: Chrome rewrites it
  constantly for every loaded profile.

**What to do:** The profile's History database is the proof. Open a URL with a unique query
string, then ask each candidate profile:

```bash
cp "$HOME/Library/Application Support/Google/Chrome/Profile 3/History" /tmp/h.db
sqlite3 /tmp/h.db "SELECT count(*) FROM urls WHERE url LIKE '%probe=<unique>%';"
```

Measured 2026-08-20: 1 for the aimed profile and 0 for every other, while a deliberately unaimed
control landed in the last-used profile. `about:blank` is a poor test URL: no application claims
it, so the plain `open` fallback exits 1.

### 32b. The aim receipt exists because an announced aim is not an achieved one

**Symptom:** (The one this prevents.) The switch says it is aiming at a Chrome profile, every
time, while the page has in fact been opening somewhere else since an update or since the Chrome
profile was renamed.

**Why it happens:** The switch prints the profile it intends to use before the opener runs.

**What to do:** The opener writes one line to `~/.claude-profiles/.last-aim`, in the form
`<time> aimed|fellthrough <profile>`. `claude-switch` reads it back after the sign-in and says so
if the aim did not happen. A plain file in that folder is safe: a profile must be a folder that
contains a `.claude.json`.

### 32c. An account whose email address changed silently loses its aim

**Symptom:** One account, and only one, opens its consent screen in an unrelated Chrome profile.

**Why it happens:** The lookup joins the Claude account's email address to Chrome's Google
sign-in. Those are two strings with two owners, and nothing keeps them equal. In the measured case
(2026-09-16) a Claude account had been moved to a new email address while its Chrome profile
stayed signed in to the old Google address. Zero matches meant no `BROWSER` was set, so Claude
Code ran plain `open`, and Chrome used its last-used window, which belonged to a different
account.

Nothing reported an error, because falling through is the designed behaviour. **The failure of a
self-correcting lookup is not that it breaks loudly. It keeps working against the wrong target.**
And the wrong window was signed in to a different Claude account: that is #28, one click away,
with the whole fleet following.

Two near misses made the lookup look as if it should have worked: a Chrome profile whose display
name matched the account but whose Google sign-in was another address, and the Chrome profile that
did hold the right claude.ai session but under the old Google address. Neither matches, correctly.

**What to do:** `~/.claude-profiles/chrome-profiles.json` is read before the derived lookup. Map
the Claude account's email address to the Chrome profile folder there. Where you can, prefer
signing the Chrome profile in to the new Google address. The override is a hand-kept map, and it
will never announce that it has gone stale.

Find the right Chrome profile by evidence, not by guessing, with the History check in #32a.

Run this whenever an account is added, renamed or moved, with your own profile names:

```bash
cd ~/.claude-profiles
for p in personal work side; do
  printf '%-10s -> [%s]\n' "$p" "$(python3 claude-switch.py --chrome-profile-for "$p")"
done
```

A blank is the bug. The check takes a second and is the only thing that makes this failure
visible.

---

## 33. `verify.sh`'s shared-token check could never fail, for months

**Symptom:** `ALL CHECKS PASSED`, including `PASS  no shared tokens across slots`, on every run,
whatever the keychain actually held.

**Why it happens:** The script built its list of token hashes correctly near the top. A later
line then reset that variable to empty before the check read it. The check's "no duplicates"
branch therefore ran every time. That check exists to catch #1, and it was enforcing nothing.

**What to do:** The resetting line was deleted on 2026-08-20 and replaced by a comment saying why
nothing may be assigned there. Then, as #27 requires, the detector was proved able to fail by
feeding it a synthetic duplicate:

```
TOKHASHES='personal aaaa1111 / side aaaa1111 / team bbbb2222'
```

It reported a shared token held by `personal` and `side`. A green run is only evidence if the
checks inside it can go red. `verify.sh --simulate-missing <profile>` exists for the same reason:
it pretends one profile has no login, without touching the keychain, so you can watch the check
fail.

---

## 34. Every `/usage` probe is a full Claude Code session. An expensive SessionStart hook turns the refresh loop into a leak

**Symptom:** The disk fills up. The user temp folder holds tens of thousands of orphaned
`pip-unpack-*` folders, arriving in regular bursts every 5 minutes, and gigabytes of package
downloads per day that produce nothing.

**Why it happens:** A refresh runs `claude -p "/usage"` under each profile's `CLAUDE_CONFIG_DIR`.
That is a **full** session: it loads that settings folder's hooks, its plugins and its MCP
servers. In the measured case (found and fixed 2026-08-24) a plugin's SessionStart hook looked for
a Python environment under the settings folder and, not finding a complete one, rebuilt it with
pip, downloading about 100 MB each time. A probe session lives for seconds. The hook's pip was
killed with it, its temp folders were orphaned, the environment never completed, and the next
refresh tried again, for ever. The main settings folder never leaked, because interactive sessions
live long enough for one build to finish.

**What to do:** Every probe runs as a minimal session:

```
claude -p "/usage" --settings '{"disableAllHooks":true}' --strict-mcp-config --max-budget-usd 0.02
```

(with `--no-session-persistence` added later, #55). Verified on Claude Code 2.1.241: the report
still parses, the probe dropped to about 6 seconds, and a probe with the flags produced no hook
side effects. `--strict-mcp-config` with no `--mcp-config` starts zero MCP servers.

The `/usage` probe arguments live in exactly three places that must change together:
`claude-cockpit.py` (`refresh_one`), `claude-usage.py` (`refresh`) and `claude-ping.py`
(`PROBE_FLAGS`). The watcher does not run `/usage`.

Traps:

- `--bare` reads as if it were made for this. It also skips the keychain and limits
  authentication to an API key. OAuth is exactly what `/usage` needs. `--bare` breaks the probe.
- `disableAllHooks` is undocumented (absent from `--help` on 2.1.241). If a later version drops
  it, the sign is those temp folders coming back.
- A running cockpit or watcher keeps the old code in memory. Editing a file fixes nothing until
  they are restarted.

---

## 35. `--max-budget-usd` is a pre-flight estimate, depends on the account, and refuses with exit 0 and a silent stderr

**Symptom:** A feature that runs `claude -p` works on some accounts and does nothing on others,
with no error anywhere. The exit code is 0, stderr is empty, and the thing you were trying to
cause did not happen.

**Why it happens:** The flag does not cap spending. It rejects the call up front against an
estimate, and the estimate varies by account. Measured 2026-08-28 with identical flags and prompt:

| Account | `--max-budget-usd` | Result |
|---|---|---|
| work | 0.05 | Rejected |
| work | 0.50 | Accepted |
| work | 3.00 | Accepted |
| ops | 0.50 | Rejected |

The rejection prints `Error: Exceeded USD budget (0.5)` on **stdout** and exits **0**. A caller
that checks the exit code sees success, and a caller that checks stderr sees nothing.

**What to do:** Do not use it as a safety measure on a call that sends a message. It caps nothing
real and it refuses at thresholds you cannot predict. Bound the call by its structure instead:
replace the system prompt so there is no context to spend, keep it to a single `-p` call, start
zero MCP servers, and put a hard timeout on the subprocess. **Keep** it on `/usage` probes (at
`0.02`), where it has never fired because that call spends nothing.

If you must keep it on a real call, capture stdout and stderr and search for
`Exceeded USD budget`. That string is the only evidence you will get.

---

## 36. A bare `claude -p "hi"` bills about 116,000 cache-creation tokens. The system prompt is the cost, not the prompt

**Symptom:** A "cheap one-word ping" turns out to be one of the most expensive things in a loop.
The token accounting shows a six-figure `cache_creation_input_tokens` for a two-token exchange.

**Why it happens:** The default system prompt carries the global `CLAUDE.md`, the project
`CLAUDE.md` of whatever folder you happen to be in, and the description of every skill. Measured
2026-08-28 on one `-p "Reply with only the word: ok"`:

```
cache_creation_input_tokens: 116456      # default system prompt
cache_creation_input_tokens: 0           # with --system-prompt and --setting-sources ""
```

`--setting-sources user` is not enough: the global `CLAUDE.md` still loads. The sign was in the
reply itself, which came back following instructions from that file. With `""` the reply is a
bare `Ok`.

**What to do:** For any call whose purpose is the side effect of making a request, not the answer:

```
--system-prompt "Reply with one word." --setting-sources "" \
--settings '{"disableAllHooks":true}' --strict-mcp-config --no-session-persistence
```

and set the working folder to `$HOME`, so that no repository's `CLAUDE.md` is picked up from
wherever the caller was standing. Read the reply as your proof: if it shows any sign of your own
instructions, the context is not being suppressed.

---

## 37. You cannot verify a usage change by re-reading `.claude.json`. The write is limited to once per 300 seconds

**Symptom:** A tool does something that really moves an account's meter, re-reads the profile's
cached usage to confirm, finds it unchanged and reports its own success as a failure. Running
`/usage` again does not help.

**Why it happens:** This is the write limit from #8 seen from another side.
`cachedUsageUtilization` is rewritten at most once per five minutes, so any read inside that
window returns the previous value however many times `/usage` runs. Measured 2026-08-28: after a
ping opened an account's 5-hour window, the profile file still showed no reset time, with a
`fetchedAtMs` 250 seconds old, while the live report already said
`Current session: 1% used · resets Aug 28 at 3:30pm`.

The first version of `claude-ping` scored a working ping as "no window after ping".

**What to do:** Verify against the **stdout** of `/usage`, which is live server data, never
against the cached file:

```python
SESSION_LINE = re.compile(r"Current session:\s*(.+)")
m = SESSION_LINE.search(usage_proc.stdout or "")
```

The cached file is fine for display (up to 5 minutes behind) and for a rough decision such as
"which accounts look idle". It is useless as immediate proof of something you just did.

---

## 38. `/usage` does not open the 5-hour window. Only a real message does

**Symptom:** You assume the cockpit's own refresh loop keeps every account's clock running, and
build (or skip building) on that assumption.

**Why it happens:** `claude -p "/usage"` reads `/api/oauth/usage`. It costs no usage (verified
2026-08-04 by running it four times against an idle account that stayed at 0%). The stronger fact,
proven 2026-08-27: it also does not **start** a window. The cockpit had been refreshing every
profile every 300 seconds for two days, and one idle account's `five_hour.resets_at` was still
`null`.

The 5-hour window opens on an account's first message and ends exactly five hours later whatever
you do. It does not extend with activity. An idle account has no clock running, and the moment you
need it you start a fresh five hours from zero.

**What to do:** To warm an account on purpose, send a real message. `claude-ping` (or `p` in the
cockpit) does this for every account with no live window, using the minimal flags from #35 and
#36.

For the test of "is it idle", use `five_hour.resets_at` (null, or in the past), never
`utilization == 0`. An account reads 0% for the first minutes of an open window, so a percentage
test pings accounts whose clock is already running and spends tokens for nothing. Measured
2026-08-27: one account read 0% with a closed window and, 25 minutes later, 2% with an open one.
Only the timestamp told them apart.

---

## 39. (omitted: specific to the original author's machine setup)

---

## 40. (omitted: specific to the original author's machine setup)

---

## 41. (omitted: specific to the original author's machine setup)

---

## 42. (omitted: specific to the original author's machine setup)

---

## 43. `security -i` silently cuts long commands short. A "failed" keychain write is not an untouched slot

**Symptom:** The first live `claude-swap` (2026-10-01) reported that it could not write the fleet
slot, had rolled back, and had left the fleet untouched. The fleet slot actually held about 2,000
bytes of JSON that did not parse. The fleet login and the MCP server sign-ins stored in that item
were gone.

**Why it happens:** `security -i` reads each command into a line buffer of about 4 KB. The shared
item was about 40 KB, because it also carries `mcpOAuth` (#44). The hex value was cut at roughly
4,000 characters, the cut value **was written** (the command updates in place), and `security`
exited with an error. The caller treated the error as "nothing changed" and then undid its own
earlier step, which deleted the only other copy of the outgoing login. The unit tests had passed
because their items were a few hundred bytes. An earlier move-based design used the same write and
never hit this, because the shared item held no MCP sign-ins then.

Claude Code itself (2.1.287, as read in the binary) uses `security -i` only when the command fits,
and otherwise passes the value as a command-line argument.

**What to do** (all of it is in `claude-swap.py`):

- Use the same two paths as Claude Code: stdin when the command is at most 3,800 characters,
  otherwise `security add-generic-password ... -X <hex>` as arguments.
- After **every** write, read the item back and compare the whole value. A fingerprint of one key
  would pass a cut inside `mcpOAuth`.
- On any failure, write the original value (held in memory) back and prove it. Never assume a
  failed write left the item as it was.
- An item that does not parse raises `CorruptSlot`. It must never read as "empty", because an
  empty slot is a valid destination for a move.
- Tests must use a realistic 40 KB item. `tests/test_claude_swap.py` case T13 reproduces the
  truncation. It was proved able to fail: with the original stdin-only write most of the suite
  goes red, and without the restore step T13 goes red.

The recovery used that night: another account's own login was moved (not copied) into the shared
slot with an argument write, read back and parsed, then removed from its own slot. No session
reported an auth error. The MCP sign-ins could not be recovered, and those servers asked for a
sign-in again on next use.

---

## 44. A Claude keychain item holds MCP sign-ins too. "The item exists" does not mean "signed in". Move only `claudeAiOauth`

**Symptom:** A guard that should refuse lets a browser switch through while an account's login is
borrowed. Or MCP sign-ins turn up in the wrong slot after a login was moved.

**Why it happens:** Every `Claude Code-credentials*` item is JSON with up to two top-level keys:
`claudeAiOauth` (the Claude login) and `mcpOAuth` (tokens for that settings folder's remote MCP
servers). On 2026-10-01 the shared item held many MCP sign-ins and the profile items held a few.

**What to do:**

- Moving or copying the whole item carries MCP sign-ins into the wrong slot, and those have
  single-use refresh tokens too. Move only `claudeAiOauth` and keep every other key, as
  `claude-swap.py` does in `_put()` and `_take()`.
- When a profile's login is borrowed, its item keeps its `mcpOAuth`, so the item still exists.
  "The slot has a fingerprint" therefore stops meaning "signed in". `claude-switch.py` uses
  `has_claude_login()` for that question. The first version of the borrowed-login guard used the
  old test and passed when it had to refuse. A stubbed sign-in caught it, and no browser opened.
  Test any new guard with `fleet_switch(name, runner=stub)`, never live.
- `refreshTokenExpiresAt` is inside `claudeAiOauth` as well. See #46.

---

## 45. On Claude Code 2.1.287, `/usage` reads every local transcript. A `/usage` probe can take minutes

**Symptom:** Cockpit rows show that the refresh timed out. A swap's verification times out and
rolls back a swap that was in fact good.

**Why it happens:** On 2.1.287, `claude -p "/usage"` appends a report on what is contributing to
your usage (requests, sessions and top skills over the last 24 hours and 7 days). It builds that
report by reading every transcript under `projects/`. Measured 2026-10-01 on a history of several
hundred sessions: one fleet `/usage` probe ran for more than 7 minutes, so the swap's 90-second
verification timed out three times and rolled back a good swap. The same command had answered in under 2 minutes earlier that evening, so the time varies
with how busy the disk is.

**What to do:** For anything that only needs to know "can Claude Code authenticate with this
slot", send one word with everything stripped (the flags from #36, working folder `$HOME`). It
takes 6 to 18 seconds, costs a few tokens, and proves the real binary can read and renew the slot:

```
claude -p "Reply with only the word: ok" --system-prompt "Reply with one word." \
  --setting-sources "" --settings '{"disableAllHooks":true}' --strict-mcp-config \
  --no-session-persistence
```

For the numbers alone, `GET https://api.anthropic.com/api/oauth/usage` (with the account's access
token as a bearer token and the header `anthropic-beta: oauth-2025-04-20`) returns `five_hour` and
`seven_day` at once. The watcher and the swap already read it that way.

**Still open:** the cockpit, `claude-usage` and `claude-ping` still refresh with `/usage` and a
90-second timeout. They no longer add to the problem (#55), but on 2026-10-08 every cockpit row on
a large history read as timed out, and a probe that was allowed to finish took 4 minutes 27
seconds. Moving those three places to the direct read is the recorded follow-up and has not been
done.

---

## 46. Every login has a hard expiry (`refreshTokenExpiresAt`), and renewing does not move it

**Symptom:** An account that has been fine for weeks shows `SIGNED OUT`. A session pinned to it
says `Login expired · Please run /login`.

**Why it happens:** Each `claudeAiOauth` carries `refreshTokenExpiresAt` next to `expiresAt`. It
is an absolute deadline. Measured 2026-10-01 (Claude Code 2.1.287): a login made that day carried
a deadline 29 days later. One anchor renewed about 7 hours before its deadline, and the date did
not move. At the deadline its slot was left with no access token, the cockpit showed
`SIGNED OUT`, and a session pinned to that profile failed about an hour later.

**What to do:** Every login (anchors, spares and the fleet's) needs one browser sign-in before
its deadline, roughly monthly: `claude-login <name>`.

- The cockpit warns on the row 72 hours ahead. For a borrowed account it reads the deadline from
  the shared slot.
- `claude-swap` refuses to move a login with less than 6 hours left, and skips a spare that is
  past its deadline.
- `claude auth status` is not proof that a login can still renew (#5). The one-word message in
  #45 is.

---

## 47. Correction to #6: running sessions move to a new fleet login within about a minute

**Symptom:** Documentation, and the text `claude-switch` prints, say that healthy and
limit-stopped sessions follow a switch only at their next token renewal (up to 8 hours). In
practice they follow much sooner.

**Why it happens:** Two measurements on 2026-10-01 (Claude Code 2.1.287):

1. **Sessions stopped on a limit.** The fleet was switched from `lab` to `personal` by
   browser sign-in. Five sessions that were stopped on `lab`'s limit ran on `personal` at
   their next message, 3.7 to 6.1 minutes later. One of them was woken by a background task
   finishing, with nobody typing. Messages sent 1 to 6 seconds after the switch still failed.
2. **Healthy sessions.** A no-browser swap moved the fleet from `team` to `ops`. Over the next
   47 seconds the server's meters showed `ops` rising from 17% to 19% and `team` flat at 30%.
   The running sessions were billing the new account within a minute.

**What to do:** Expect a fleet switch to move everything quickly on this version, and treat 8
hours as the worst case. A session parked on "continuing automatically at <time>" still needs one
message to try again. This is Claude Code's behaviour: measure again after a major update.

---

## 48. The swap (the no-browser switch) and its one new hazard

**Symptom:** After a swap, a plain `/login` or a browser fleet switch signs an account out
completely, and it needs a browser sign-in to come back.

**Why it happens:** `claude-swap <name>` **moves** an existing login into the fleet slot instead
of making a new one. The source is the account's spare (a keychain item named
`claude-cockpit-spare-<name>` that no Claude Code process reads), or else its own anchor
("borrowed"). The outgoing login goes back to its own slot if that slot is empty, otherwise it
becomes that account's spare.

While an account is on the fleet with a **borrowed** login, that login exists in one place only.
A plain `/login`, or any browser fleet switch, overwrites it.

**What to do:**

- `claude-switch` refuses in this state and points to `claude-swap`. With the swap on, the
  cockpit's number keys try the swap first and fall back to the browser only when the swap exits
  2 ("no login that can be moved").
- If the borrowed login is overwritten anyway: `claude-login <name>`, one browser sign-in.
- Spares remove the hazard: an account that leaves the fleet while its anchor is intact parks a
  spare, and the next swap to it uses the spare and borrows nothing.

How the swap protects itself: identity is asked of the server (`GET /api/oauth/profile`); both
logins must have at least 30 minutes of access-token life, so nothing that remembers a moved login
renews it before it has read the slot again; each slot is re-read just before it is written; the
move is verified by the one-word message (#45) plus the server's identity answer; and any failure
is rolled back. The full design is in the docstring of `claude-swap.py`. Tests:
`tests/test_claude_swap.py`.

The swap ships off. It runs only when `"swap_enabled": true` is set in `autoswitch.json`.

---

## 49. Auto-swap must read live usage and disarm before it swaps

**Symptom:** (The failures these rules prevent.) An automatic swap fires late, after the account
is already blocked. Or it fires twice.

**Why it happens:**

- The cached numbers lag. `cachedUsageUtilization` is written at most every 5 minutes (#8), and
  `/usage` can take minutes (#45). A busy fleet was measured using about 1 to 2% of a 5-hour
  window per minute, so a cached reading can be 7 to 10 points low. That is the whole margin of a
  97% trigger.
- `launchd` restarts a dead watcher at once. A crash in the middle of a swap, with the arming flag
  still set, would fire a second swap.

**What to do:**

- **Live numbers only.** The watcher calls `GET /api/oauth/usage` with each account's own token.
  For an account that is not on the fleet it reads through that account's own login, renewed
  first by a one-word message if its access token has expired.
- **Disarm first.** It writes `auto_swap: false` to `autoswitch.json` **before** it runs the swap.
  Test A3 in `tests/test_autoswap.py` pins the order.
- **Stay on Python 3.9.** The watcher runs under `/usr/bin/python3` (3.9.6 when tested), not
  whichever `python3` is first on your `PATH`. It imports `claude-swap.py` and `claude-meters.py`,
  so those must stay compatible with 3.9 as well.

---

## 50. A borrowed account's cockpit row freezes unless it is read through the shared slot (#20 came back with the swap)

**Symptom:** Measured 2026-10-01: the row of the account the fleet was running on read as stale,
38 minutes old, while every session was using it. Only that row was stale. Every other row was
one minute old.

**Why it happens:** The cockpit refreshes each row by probing that profile's **own** slot. The
browser-switch design deleted the special case for the active row on purpose, because under it no
login ever left its own slot. The swap broke that assumption. A borrowed account's own slot has no
Claude login, so its probe cannot refresh, and the row froze at the moment of the swap.

**What to do:** When a profile's slot has no Claude login, the cockpit reads that row live from
the server through the **shared** slot, but only after `/api/oauth/profile` confirms that the
shared login is that account. Otherwise the row really is signed out and the normal diagnosis
applies. The live payload has exactly the shape of `cachedUsageUtilization["utilization"]`, so the
same parser draws it. The row is labelled as being on the fleet with its own login, with a warning
not to `/login`. Measured: 0.7 seconds for a borrowed-row refresh.

A related advice trap: with no spares, **every** swap leaves the fleet on a borrowed login, so
"swap to another account first, then switch by browser" loops for ever. The way onto the fleet
for an account whose own login is missing or near its deadline is `claude-login <name>` (it renews
that account's own slot only and is never blocked) and then `claude-swap <name>`. The refusal
text of `claude-switch` says this.

---

## 51. The run-out forecast: what it trusts and the traps it avoids

**Symptom:** A simple forecast ("room left divided by pace") says you will last much longer than
you do. Or a pace taken from local logs misses most of the usage.

**Why it happens:**

- **Unused weekly room is lost** at each account's own reset, and each 5-hour window caps how fast
  one account can be drained (one full window was measured at roughly 26 to 33% of a week). A
  division ignores both.
- **Local session logs see only this Mac.** The server's meter counts claude.ai, other devices and
  pinned sessions. On 2026-10-01 one account climbed from 44% to 73% of its 5-hour window while it
  was not on the fleet. Session logs alone would have missed that.
- **Session logs repeat a message's usage** on every content-block line. Counting lines counts two
  or three times too much.

**What to do** (all in `claude-meters.py`, refreshed by the watcher every 5 minutes):

- **Unit.** One point is 1% of one account's weekly limit. The forecast assumes every account is
  on the same plan, so a point is the same size everywhere and N accounts give N times 100 points
  a week.
- **Simulate.** Step through 7 days in 3-minute steps, spending from the account whose refill
  comes soonest, within each account's 5-hour cap, and refilling at the reset times the server
  reports. Tests M6 to M10 pin the arithmetic against hand-worked answers.
- **Take the pace from the meters** once `meters.jsonl` covers at least 90% of a window. Until
  then, estimate it from the token cost in local logs, multiplied by a ratio fitted where both
  sources exist, and label it `est.`.
- **Count each message once**, by `message.id` (test M3).
- **Never send a message and never renew a token.** An account whose access token has expired is
  skipped until something uses it, since its meter is not moving. Renewing it with a one-word
  message would open that account's 5-hour window as a side effect.
- **Check yourself.** Every forecast records its 24-hour prediction per pace in
  `forecast_history.jsonl`. From the second day the panel shows yesterday's miss.

Storage: about 1 KB per 5-minute snapshot, trimmed to 35 days. The first scan of local logs reads
the last 8 days once. After that it reads only new bytes, every 15 minutes, behind a lock file.

`claude-meters.py forecast` also prints a "sustainable pace": 100 points a week for each account
it knows about, spread over the 168 hours of a week. The cockpit panel shows the simulated
"never runs dry" pace instead, which also accounts for when each account refills.

---

## 52. `/api/oauth/usage` is rate limited per account. Share one cached reading and back off on a 429

**Symptom:** Measured 2026-10-01: the cockpit showed the fleet account, which was on a borrowed
login, as `SIGNED OUT` with the advice to sign in, while every session was running on it. The
watcher logged that it could not read the fleet's live usage.

**Why it happens:** The usage endpoint returned HTTP 429 (with `retry-after: 0`) for that
account. It was being asked too often: the watcher every 45 seconds (alerts had just been turned
on), the 5-minute snapshots, test calls and the running sessions' own polls. The profile endpoint
still answered and sending messages was unaffected. Only usage **reads** failed. The cockpit
treated a failed read as a signed-out account.

**What to do** (in `claude-swap.py`, under "USAGE RATE LIMIT"):

- Every caller reads through one per-account cache, `.usage-cache.json`. A reading younger than
  the caller's limit is reused: 4 minutes normally, 1 minute once the fleet is at 85% or more,
  where freshness matters.
- A 429 starts a backoff of at least 3 minutes for that account. The last good reading is
  returned and its true age travels with it. `meters.jsonl` records when each reading was really
  fetched, so a reused reading is never counted as new.
- The fleet has a free second source: `cachedUsageUtilization` in `~/.claude.json`, kept by its
  own sessions. It is used when it is fresher, and only if it is filed under the same
  `accountUuid` (#5).
- Identity (`/api/oauth/profile`) is cached per token, since a token's owner cannot change.
- A borrowed row with no reading at all says that the server is rate limiting usage reads and
  that the account is not signed out. It never says `SIGNED OUT` for this reason.
- Tests U1 to U3 in `tests/test_meters.py`.

---

## 53. The alert cooldown must not pause the usage history

**Symptom:** Gaps of 20 minutes in `meters.jsonl` and a stale forecast, all night.

**Why it happens:** After an alert the watcher used to sleep for the whole cooldown in one go.
That also stopped the 5-minute snapshots. On 2026-10-01 the fleet sat at 97% or more for hours
(not armed, so alerts only), and the watcher alerted and slept every 20 minutes.

**What to do:** The cooldown sleeps in steps of `poll_sec` and records the meters in between. A
cooldown silences alerts, not data.

---

## 54. (omitted: specific to the original author's machine setup)

---

## 55. Every `/usage` probe must pass `--no-session-persistence`

**Symptom:** The `projects/` folder fills with tiny sessions whose only content is a `/usage`
command. Any tool that indexes session history lists them as sessions. And `/usage` itself slows
down, because on 2.1.287 and later it reads every transcript (#45).

**Why it happens:** `claude -p "/usage"` is a full session, and a `-p` session is **saved** unless
told otherwise. The probe arguments in three places lacked the flag: `claude-cockpit.py`
(`refresh_one`), `claude-usage.py` (`refresh`) and `claude-ping.py` (`PROBE_FLAGS`). A cockpit
refreshes every profile every 5 minutes, and one cockpit process had been left running for days
with no window. Where this was found (2026-10-08) it had added up to tens of thousands of probe
transcripts in under three months. The watcher and `claude-meters.py` run no `/usage`, so they
were never a source.

**What to do:** All three places pass `--no-session-persistence` (since 2026-10-08). Proven: with
the flag the report still prints and no `.jsonl` file is written; without it, exactly one is. The
one-word probe in `claude-swap.py` and the ping in `claude-ping.py` already carried the flag. The
`SYNC WARNING` comment in each file names exactly these three places.

If you maintain a tool that indexes session history, it is worth skipping sessions that contain
only a local command, as a second line of defence.

Trap when restarting a cockpit: a `/usage` child that was already running when the cockpit was
stopped keeps running, because the interrupt does not reach it. With no parent to enforce the
90-second timeout it finishes minutes later. Expect that trailing probe, or stop the whole process
group.

---

## 56. The cockpit hides email addresses, and the message row was hidden under the footer rule

**Symptom:** Two things. Email addresses in the cockpit are drawn as `••••••@••••••`. And, before
the fix described here, on a terminal narrower than about 130 columns the result of a switch and
the reason for a refusal did not appear at all.

**Why it happens:**

*The masking* is a design rule (2026-10-08): the cockpit can sit on a shared or recorded screen
without showing which addresses own the subscriptions. Every launch starts hidden. `e` reveals the
addresses for that process only, and the message row says so for as long as they are shown.

*The hidden message* was a layout bug found while adding the masking. The message was pinned to
the third row from the bottom. The two-row footer, used whenever the key hints and the refresh
stamp do not fit on one line, draws its rule on that same row, after the message, and overwrote
it. Adding one more key to the hint text raised the width needed for a one-row footer from 121 to
132 columns, which is how it surfaced.

**What to do:**

- **Mask at draw time only**, through one function (`UI.em()`). The stored values keep the real
  address, because the code falls back to matching the fleet row by email address when the live
  check cannot answer. Masking the stored value would silently break that match. Any new place
  that shows an address on screen must go through the same function.
- **Use one fixed mask string.** A mask that keeps the first letter or the domain leaks the
  length, the first letter and the domain. The profile-name column is what tells rows apart, and
  a switch is still verified by the server (#28).
- **Do not mask the switch output.** During a switch or an undo the cockpit is suspended and
  `claude-swap` or `claude-switch` print to the plain terminal with real addresses. The line that
  asks you to confirm the address on the consent screen is a safety check (#28, #32). Someone
  recording the screen during a switch will see addresses.
- **Draw the message one row above the rule, wherever the rule lands.** Any change to the hint
  text moves the width at which the footer wraps, so re-check the screen at a width just under it.

How it was tested: the real key loop was run in a pseudo-terminal with the refresh stubbed out, so
no probe ran, and the screen was read with a terminal emulator at four widths, pressing only `e`
and never a digit. A running cockpit keeps the old code until it is restarted.
