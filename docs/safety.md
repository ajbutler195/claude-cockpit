# Safety: what these tools can do to your logins

This page is for someone deciding whether to trust claude-cockpit with their Claude logins. It
says what the tools are built never to do, what each command can change, what the opt-in swap
writes, and what to do when something looks wrong. Terms such as fleet, anchor, slot, spare and
borrowed are defined in [how_it_works.md](how_it_works.md#words-used-here).

## The short version

- A "login" here is what Claude Code stores in your macOS keychain after you sign in. Losing one
  means that account must sign in again in the browser on this Mac. It does not affect the account
  itself or your subscription.
- With the default settings, no tool in this project writes a keychain item. Sign-ins are done by
  Claude Code's own `claude auth login`, which the tools start and then check.
- One tool, `claude-swap.py`, writes keychain items itself. It is off until you set
  `"swap_enabled": true`.
- Things did go wrong while this was being built. Three earlier designs cancelled or lost logins
  and were replaced (see the [design history](how_it_works.md#design-history)). The swap's first
  live run damaged a keychain item (see [The swap opt-in](#the-swap-opt-in)). In every case the
  cost was signing in again.

## The invariants

These are the rules the code is built around. Code comments refer to them by number (for example
"invariant #4" and "invariant #8"), so the numbers are stable.

1. **Every account keeps its own permanent login.** Each account's anchor stays in its own
   profile's slot. That is why the login in the shared slot is always expendable: replacing it
   cannot take an account's last login away. `claude-switch` refuses to switch to an account that
   has no anchor. The one exception is an account on the fleet with a borrowed login, which only
   the opt-in swap can create. See [The one hazard](#the-one-hazard-a-borrowed-login).
2. **One login lives in exactly one slot and is never copied.** A login contains a single-use
   refresh token, and Claude Code coordinates renewals only inside one settings folder. The same
   login in two slots means the first renewal cancels the other holder. The swap only ever moves:
   write the destination, read it back, then remove the source. `claude-swap --check` and
   `verify.sh` both look for a login held twice.
3. **`claude-switch.py` contains no keychain write or delete.** The only thing that writes a login
   during a browser switch is Claude Code's own sign-in. `verify.sh` section D3 parses the file
   and fails if any subprocess call in it carries a keychain-writing argument. All code that
   writes keychain items lives in `claude-swap.py`, on purpose, so that this check stays
   meaningful.
4. **A tool that asks about the fleet must not inherit `CLAUDE_CONFIG_DIR`.** Run from a pinned
   shell, an inherited value would make every "shared slot" question quietly examine one profile
   instead. Fleet probes remove the variable from their environment, and `verify.sh` unsets it on
   its first line.
5. **Never point Claude Code at a folder that has no `.claude.json` of its own.** Claude Code
   creates a fresh, signed-out configuration in any folder it is pointed at, and that folder then
   looks like an account. The tools test for the file before they probe or launch into a folder.
6. **Never call bare `claude` from a script.** A `PATH` lookup can land on a shim or an old
   install. Every tool uses `COCKPIT_CLAUDE_BIN` if set, otherwise `~/.local/bin/claude`.
7. **A profile shares everything except `.claude.json`.** Skills, commands, agents, hooks,
   plugins, `CLAUDE.md`, `settings.json` and `projects` are links back to `~/.claude`.
   `verify.sh` checks every link.
8. **A number that cannot be shown to be fresh is not displayed as fresh.** A row whose refresh
   failed, timed out or is old says so on its own line, sinks to the bottom and is left out of the
   recommendation. The same rule covers an aim: the switch announces a Chrome profile before the
   browser opens, so it reads back a receipt afterwards and says so if the aim did not happen.
9. **The browser picks the account, so every sign-in is verified afterwards.** The tools cannot
   force which account signs in. Aiming the page at a browser profile is a convenience that must
   never block a sign-in and never stand in for the check. After each fleet sign-in the tool asks
   which account the slot now holds and compares it with the one you asked for.

## What each command can and cannot change

There are three levels.

**Level 1: reads only, as far as logins are concerned.** These never write a keychain item and
never start a sign-in.

| Command | What it does change |
|---|---|
| `claude-cockpit` (viewing, `r`, `e`) | Nothing. |
| `claude-cockpit`, `a` key | The `enabled` value in `autoswitch.json`. |
| `claude-cockpit`, `p` key, and `claude-ping` | Sends one word to each idle account. That opens its 5-hour window and uses a few tokens. |
| `claude-usage`, `claude-profiles` | Nothing. |
| `claude-switch` with no name, `claude-switch --active` | Nothing. |
| `claude-swap` with no name, `claude-swap --check`, `claude-swap --dry-run <name>` | Nothing. May send one word so that Claude Code renews a login close to expiry. |
| `claude-autoswitch --status`, `claude-autoswitch --once --dry-run` | Nothing but log lines. May send one word to renew another account's expired access token. |
| `claude-autoswitch --off` | Sets `enabled` and `auto_swap` to `false` in `autoswitch.json`. |
| `claude-meters.py` | Its own history and forecast files. |
| `verify.sh` | Nothing. |
| `claude-as <name>` | Starts Claude Code on that profile. |
| `new-profile.sh` | Creates one profile folder. |
| `install.sh`, `uninstall.sh`, `install_watcher.sh` | Tool files, two or three lines in `~/.zshrc`, one `launchd` file. Never the keychain. |

Many of these read logins from the keychain: to see whether a slot holds one, to read its expiry
dates, or to call the two read endpoints described [below](#what-the-tools-send-over-the-network).

**Level 2: runs Claude Code's own sign-in.** The tool starts `claude auth login`. Claude Code
writes the new login to exactly one slot.

| Command | Slot that Claude Code writes |
|---|---|
| `claude-login <name>` | That profile's slot. No other account can be affected. |
| `claude-login --fleet` | The shared slot. Not aimed, not checked. |
| `claude-switch <name>`, `claude-switch --undo` | The shared slot. Aimed, then checked. |
| Cockpit keys `1` to `9` and `u`, while the swap is off | The shared slot, through the same code as `claude-switch`. |

**Level 3: writes keychain items itself.** Only `claude-swap.py` does this, and only while
`"swap_enabled"` is `true`.

| Command | What it writes |
|---|---|
| `claude-swap <name>`, `claude-swap --undo` | See [Exactly what a swap writes](#exactly-what-a-swap-writes). |
| Cockpit keys `1` to `9` and `u`, while the swap is on | The same, by running `claude-swap`. |
| The watcher, while the swap is on **and** you armed it with `claude-autoswitch --arm` | The same, once. It disarms itself before it swaps. |

One thing applies at every level. Whenever a tool starts Claude Code (a `/usage` refresh,
`claude auth status`, a one-word message), Claude Code may renew that slot's login and store the
renewed tokens back in the same slot. That is Claude Code doing what it does in any session.

## The swap opt-in

### Why it is off

A swap moves login tokens between keychain items. It has checks before, during and after, it
rolls back on failure, and it has a test file. Its first live run still went wrong.

The shared keychain item was about 40 KB, because it also held MCP server sign-ins. The tool wrote
through `security -i`, which reads each command into a buffer of about 4 KB. The command was cut
short, the cut value was written, and `security` returned an error. The tool took the error to
mean "nothing was written" and undid its earlier step, which removed the only other copy of the
outgoing login. The shared item was left unreadable. The fleet login and the MCP server sign-ins
in that item were lost and had to be made again. The unit tests had passed because their test
items were a few hundred bytes.

What changed afterwards:

- Long values are written as a command-line argument instead, which is what Claude Code itself
  does (see the note on `ps` below).
- After every write the whole item is read back and compared, not a fingerprint of one part.
- A failed write is never assumed to have left the item unchanged. The original value, held in
  memory, is written back and checked.
- An item that does not parse stops everything. It is never treated as empty, because an empty
  slot is a valid place to move a login to.
- The tests use a 40 KB item and reproduce the truncation (`tests/test_claude_swap.py`, cases T13
  to T15).

Since the fix the swap has had a small number of live runs, all on the author's Mac. That is the
honest size of the evidence, and it is why a new user chooses the swap knowingly.

### How to turn it on

In `~/.claude-profiles/autoswitch.json`:

```json
"swap_enabled": true
```

It must be JSON `true`. A missing file, a file that does not parse, a missing key, the text
`"true"` and the number `1` all mean off. Three places enforce the setting and a test checks that
all three still ask: `claude-swap.py` itself, the cockpit's number and `u` keys, and the watcher.

While it is off, `claude-swap <name>` and `claude-swap --undo` exit 1 and change nothing. Status,
`--check` and `--dry-run` keep working.

### Exactly what a swap writes

For a swap from account A (on the fleet) to account B:

| Place | Change |
|---|---|
| A's own slot, **or** the item `claude-cockpit-spare-A` | Receives A's outgoing login. A's own slot is used if it is empty (A was borrowed). Otherwise a spare is created. An older spare for A is replaced. |
| The shared slot | Its `claudeAiOauth` key is replaced with B's login. Every other key in the item is kept. |
| Where B's login came from | If it was B's spare, that item is deleted. If it was B's own slot, the `claudeAiOauth` key is removed and the rest of the item is kept. |
| `~/.claude.json` | `oauthAccount` is set to B's identity and ten per-account cache keys are removed, so Claude Code displays B. |
| `.swap-ledger.jsonl`, `.last-swap.json` | One log line and the undo pointer. Names and short hash fingerprints only. |

It also sends one word through Claude Code to verify the result, which uses a few tokens of B's
allowance, and it asks `api.anthropic.com` who each token belongs to.

What a swap never does: leave a copy behind, move MCP sign-ins, touch the slot of an account that
is not part of the swap, or print or log a token.

One exposure to know about: when the value is too long for `security -i`, it is passed to
`security` as a command-line argument in hex. For the roughly 50 milliseconds that process lives,
another process running as you could see it with `ps`. Claude Code makes the same trade-off for
the same reason.

### The checks around a swap

Before: the server must confirm who the fleet login belongs to and that it matches a profile. The
incoming login must have at least 6 hours before its hard deadline. Both logins must have at least
30 minutes of access-token life. The server must confirm the incoming login belongs to the target.
Each slot is re-read immediately before it is written, and the swap stops if anything changed.

After: one real message through the fleet, the server's answer on who the shared login belongs
to, and a scan for any login held twice. If the message or the identity check fails, every move is
reversed.

## The one hazard: a borrowed login

When an account has no spare, a swap moves its anchor into the shared slot. That account is now
on the fleet with a **borrowed** login, and its own slot is empty. Invariant 1 does not hold for
it: the login in the shared slot is its only one.

While that is true, anything that overwrites the shared slot erases that login:

- a plain `/login` in any unpinned session,
- `claude-login --fleet`,
- a browser fleet switch.

`claude-switch` checks for this state and refuses. A `/login` typed by hand cannot be stopped by
these tools. If it happens, the account is not harmed. It needs one browser sign-in:
`claude-login <name>`.

How to see it: the cockpit row says the account is on the fleet with its own login, and
`claude-swap` with no arguments shows `BORROWED/empty` in that account's `PRIVATE LOGIN` column.
`claude-as <name>` does not work for that account while its login is away.

How to leave the state:

- `claude-swap <other>` moves the borrowed login back home as it brings the other one in.
- Or `claude-login <name>` gives the borrowed account a fresh anchor of its own. After that the
  shared login is a second, independent one and the hazard is gone.

Do one of these before you set `swap_enabled` back to `false`. With the swap off and a borrowed
login on the fleet, `claude-switch` refuses and `claude-swap` refuses.

Spares remove the hazard over time. An account that leaves the fleet while its anchor is intact
parks a spare, and the next swap to it uses the spare and borrows nothing.

## What the tools send over the network

The tools' own network requests go to one host, `api.anthropic.com`, and to two read-only
endpoints:

| Request | Why | Sent with |
|---|---|---|
| `GET /api/oauth/usage` | An account's live usage numbers. | That account's own access token. |
| `GET /api/oauth/profile` | Which account a token belongs to. | That token. |

They are made by `claude-swap.py` and by the tools that use its functions: the cockpit (for a
borrowed row), the watcher and `claude-meters.py`. A token is only ever sent to the service that
issued it.

Everything else goes through Claude Code itself, which the tools start as a subprocess:
`claude auth status`, `claude auth login`, `claude -p "/usage"` and a one-word message. Claude
Code then talks to Anthropic as it does in normal use.

Nothing is sent anywhere else. There is no telemetry, no update check and no third-party service.
On your own Mac the tools also start `open` (to show the sign-in page) and `osascript` (for
notifications).

## What the tools read and keep on your disk

- **Tokens** are read from the keychain into memory to check a slot, read expiry dates, compute a
  fingerprint or make the two requests above. The tools do not print them or write them to a
  file. The one exception is the `ps` exposure during a long keychain write, described above.
- **Fingerprints.** `.swap-ledger.jsonl` holds 12-character prefixes of SHA-256 hashes of refresh
  tokens. They identify a login without revealing it.
- **Email addresses** appear in `.last-switch.json`, in `autoswitch.log` and in each profile's
  `.claude.json`. The cockpit hides them on screen until you press `e`. During a switch the tools
  print them in full, because confirming the address is part of the check.
- **Session logs.** The watcher reads the transcripts under `~/.claude/projects` to count tokens
  for the forecast. It stores hourly totals and the path of each log file it has read in
  `token_hours.json`. It does not store their content.
- **Profile folders** are created with mode 700 and their `.claude.json` with mode 600. A profile
  folder holds an identity, your MCP server configuration and your list of trusted folders. Keep
  `~/.claude-profiles` out of any repository or shared backup. Claude Code's own code, as read in
  2026-08, falls back to a plain-text `.credentials.json` inside the settings folder if a keychain
  write fails. That has not been re-checked on the current version, but it is one more reason to
  keep those folders private.

## If something goes wrong

Start with the read-only checks. They change nothing.

```bash
bash ~/.claude-profiles/verify.sh
claude-swap --check
claude-switch
```

### A row says `SIGNED OUT`

The cockpit prints this only after `claude auth status` reported that the profile is not logged
in. Usual causes: the login reached its hard expiry (roughly monthly), the profile never signed
in, or a borrowed login was overwritten.

Fix: `claude-login <name>`. It writes that profile's slot and no other.

These are **not** signed-out states, and signing in is not the fix: a refresh that timed out, a
row that is only old, and a row that says the server is rate limiting usage reads.

If the top line shows the fleet itself as signed out, run `claude-switch <name>`.

### The switch says the browser granted a different account

The fleet is now on the account the browser chose. No login was lost: every anchor is where it
was. Run `claude-switch <name>` again and pick the right account on the consent screen. If the
account it landed on has no profile on this Mac, give it one (`new-profile.sh`, then
`claude-login`) or switch away; the tool prints which applies.

### `DUPLICATE LOGIN DETECTED`, or `verify.sh` reports `SHARED TOKEN`

One login is held in two slots. Both copies work until one of them renews, which can be hours
away, and then the other holder fails. So fix it soon, and look before you act.

1. Run `claude-swap --check`. It names the two holders, for example `FLEET` and `work private`,
   or `work private` and `work spare`.
2. Replace one of the two copies with a fresh login, or remove the copy that is a spare:
   - if one holder is a profile's own slot: `claude-login <name>` signs that slot in again, which
     replaces its copy;
   - if one holder is a spare: delete the spare. Nothing reads it.

     ```bash
     security delete-generic-password -s "claude-cockpit-spare-<name>"
     ```

3. Run `claude-swap --check` again. It should print `none`.

The tool's message says not to `/login`. It means: do not sign in before you know which slots are
involved.

### `SLOT DAMAGED`

A keychain item holds a value that is not valid JSON. `claude-swap` names the item and stops.
Claude Code cannot use that item either.

1. Do not run another swap.
2. Note the item name from the message.
3. Nothing can read a damaged item, so the repair is to remove that one item and sign in to that
   slot again.

   ```bash
   security delete-generic-password -s "<item name from the message>"
   ```

   - A profile's slot (`Claude Code-credentials-` plus 8 hex digits): then `claude-login <name>`.
   - A spare (`claude-cockpit-spare-<name>`): nothing more to do.
   - The shared slot (`Claude Code-credentials`): the fleet is signed out. Run `claude-profiles`
     to see which profiles still hold a login, then `claude-switch <name>`. If that account's own
     login was the one in the shared slot (it was borrowed), run `claude-login <name>` first.
4. MCP server sign-ins that were stored in the removed item are gone. Those servers ask you to
   sign in again the next time you use them.
5. Run `claude-swap --check` and `verify.sh`.

### Several sessions lose their login at the same moment

First rule out a duplicate with `claude-swap --check`. If there is none, this is most likely many
sessions contending for one login's renewal, which Claude Code occasionally loses
([gotchas.md](gotchas.md) #10). Those sessions recover with `/login` or a restart.

## What has not been proven

- **Another Mac.** No real sign-in has been tested on any machine other than the author's. The
  tests exercise the logic with fake logins.
- **The swap in volume.** It has had a small number of live runs on one machine.
- **The repairs for a duplicate or a damaged slot** on this page follow from how the pieces work.
  They have not been rehearsed end to end since the current design shipped.
- **The cockpit's hand-over to the browser sign-in.** When a number key starts a browser sign-in,
  the cockpit gives the terminal to the sign-in and takes it back afterwards. A note in the code
  records that this had not been tried by a real key press when it was written. Treat your first
  press as its test. `claude-switch <name>` in a plain terminal is the same switch without that
  step.
- **A renewal race after a switch.** A fleet session that still holds the old login in memory
  could, in theory, renew it after a switch and write it back over the new one. Claude Code has
  machinery that suggests it re-reads the slot instead, and no such flip-back has been observed,
  but the order of operations was not proven. The cost would be one more sign-in
  ([gotchas.md](gotchas.md) #31).
- **The alternative fleet slot** (`COCKPIT_FLEET_CONFIG_DIR`) has unit tests and has not been used
  with real logins.
