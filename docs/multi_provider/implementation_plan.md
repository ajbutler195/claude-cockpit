# Multi-provider cockpit: implementation plan

Status: draft of 2026-10-13. This plan builds what [requirements.md](requirements.md) specifies, and
the numbers R, S, N, A and D below refer to that document. Update the status table at the end as
phases finish.

It is written for whoever builds it, a person or a coding agent. Read the requirements first.

## 1. Approach

Three choices shape the plan.

1. **Wrap the Claude tools; do not restructure them.** They hold the safety-critical code. Three
   earlier designs of that code lost logins, and the current one is proven. The new command sits
   beside them and calls their functions and scripts. The only existing files that change are
   `claude-as.sh` (one function), `install.sh` and `uninstall.sh` (file lists), and, in phase 3,
   `claude-cockpit.py` (new sections below the Claude part).
2. **New code never writes a login (S1).** Every provider gets the vendor's own sign-in, read-only
   usage, and a launcher that sets one environment variable. That keeps each adapter small, and it
   makes the safety claim checkable by a code gate (R68) instead of by ceremony. The one ceremony
   kept is a proof date per provider (S14).
3. **Measure before building.** Each adapter is preceded by measurements of the vendor facts it relies
   on (requirements section 17). The facts go into `docs/gotchas.md` with a date and a version.

Order: measure Codex, then the command with Claude only, then Codex, the screen, Grok Build, Ollama,
Google, and optional follow-ups. Codex is the first new provider because its model is closest to
Claude Code's (a movable home folder; usage windows of 5 hours and a week), and because its usage can
be read through OpenAI's own client.

## 2. Files

New files:

| File | Phase | Purpose |
|---|---|---|
| `tools/cockpit.py` | 1 | The command: argument parsing, subcommands, help, completion candidates. Thin: the logic lives in the next file. |
| `tools/cockpit-core.py` | 1 | Code that is not about one provider: the provider registry and proof dates, address resolution, the profile root and markers, environment building and `exec`, the usage cache, the host allowlist, subprocess helpers, the code gate, and (phase 2) a small JSON-RPC client. |
| `tools/provider-claude.py` | 1 | The Claude adapter. Calls `claude-switch.py`, `claude-cockpit.py`, `claude-as.sh`, `new-profile.sh` and `verify.sh`. |
| `tools/cockpit-terminals.py` | 1 | `cockpit open`: choosing where to open, building the typed command, and driving tmux or AppleScript (R83 to R92). Kept apart from `cockpit-core.py` because its AppleScript half is macOS-only (N1). |
| `tools/provider-codex.py` | 2 | The Codex adapter. |
| `tools/provider-grok.py` | 4 | The Grok Build adapter. |
| `tools/provider-ollama.py` | 5 | The Ollama adapter. |
| `tools/provider-google.py` | 6 | The Antigravity and Gemini adapters. |
| `tests/fake_vendor.py` | 1 | A stand-in for any vendor program (section 5). |
| `tests/test_cockpit_cli.py` | 1 | The command, addresses, environment, launch, listing and gates. |
| `tests/test_provider_codex.py` | 2 | The Codex adapter against the stand-in program. |
| `scripts/measure_codex.py` | 0 | Measurement helper. Not installed. |
| `scripts/measure_grok.py` | 4 | The same for Grok. |

Changed files:

| File | Phase | Change |
|---|---|---|
| `tools/claude-as.sh` | 1 | Adds the `cockpit` function and its completion. Nothing else. |
| `install.sh`, `uninstall.sh` | 1, then each provider phase | The TOOLS lists, which must stay identical (SYNC WARNING); program-path recording (R73); the `cockpit` name check (R74); the printed clean-up commands (R75). |
| `tools/claude-cockpit.py` | 3 | Sections for other providers (R58 to R63). |
| `tests/render_cockpit_states.py` | 3 | Fixture providers, stubs and new states. |
| `README.md`, `docs/how_it_works.md`, `docs/safety.md`, `docs/gotchas.md`, `CHANGELOG.md` | every phase | N6 and N7. |

Not changed: `claude-switch.py`, `claude-swap.py`, `claude-autoswitch.py`, `claude-meters.py`,
`claude-ping.py`, `claude-usage.py`, `claude-open-in-chrome.sh`, `new-profile.sh`, `verify.sh`, the
`launchd` template, and every existing test file.

## 3. Design

### 3.1 The provider interface

Each `provider-*.py` file defines one class with this shape (two classes for Google). Python 3.9,
standard library only.

```python
class Provider:
    id = "codex"                     # provider id (D5)
    title = "Codex"
    home_var = "CODEX_HOME"          # None for Claude, which builds its own path (R33)
    bin_var = "COCKPIT_CODEX_BIN"
    default_bin = ""                 # measured (A12)
    one_account = False              # R21
    override_vars = ()               # R34, measured (A10)
    hosts = ()                       # R53. Codex: none, the vendor program makes the requests

    def profiles(self): ...          # folder names that pass the marker test (R15); starts no program
    def profile_dir(self, name): ...
    def create(self, name): ...      # R17, R18
    def login_command(self, name, device_auth): ...   # (argv, env) of the vendor sign-in (R24)
    def account(self, name): ...     # {"email", "account_id"} or None; read-only (R26)
    def signed_in(self, name): ...   # True, False, or None when it cannot tell (R44)
    def launch_command(self, name, args, keep): ...   # (program, argv, env, removed_names) (R31 to R35)
    def read_usage(self, name, max_age): ...          # a Reading (R50 to R52)
    def checks(self): ...            # [(level, text)] for cockpit verify (R65)
```

`Reading` and `Window` are small `dataclasses` with the fields of R51. Every method that starts a
vendor program takes its runner as an argument with a real default, the pattern `Swapper` and
`Watcher` already use, so that tests can replace it.

### 3.2 Registry and proof dates

`cockpit-core.py` holds the proof dates (S14):

```python
PROVEN_ON = {"claude": "", "codex": "", "grok": "", "antigravity": "", "gemini": "", "ollama": ""}
```

A provider is enabled when its date is set, or when `COCKPIT_UNPROVEN=1`. Adapters are loaded by path,
the way the existing tools load each other, and only when needed: `cockpit as codex/work` loads
`provider-codex.py` and no other adapter. If an adapter fails to load, commands about the other
providers still work, and `cockpit ls` shows the error on that provider's line.

One deliberate exception: the Claude adapter imports `claude-switch.py`, which stops with a clear
message when `COCKPIT_FLEET_CONFIG_DIR` is invalid. `cockpit` then stops the same way for Claude
addresses, as every other Python tool here does ("an invalid value stops the tools").

### 3.3 Resolving an address (R3 to R6)

1. Split on the first `/`.
2. With two parts, the provider must be enabled and the name must pass `valid_name()`. List that
   provider's profiles: a directory listing plus the marker test, starting no program.
   - An exact match resolves.
   - A match that ignores case is refused, with the correct address (R5).
   - No match exits 3 and lists that provider's addresses.
3. With one part, check the name, then collect exact matches across every enabled provider. One match
   resolves. None or several exit 3 with the list (R4). Matches that differ only in case are offered
   as suggestions.

### 3.4 Building the environment and handing over (R31 to R36)

```python
def launch(provider, name, args, keep, exec_fn=os.execve):
    program, argv, env, removed = provider.launch_command(name, args, keep)   # reads only, starts nothing
    if removed:
        sys.stderr.write("cockpit: removed from this session's environment: %s\n" % ", ".join(removed))
    sys.stdout.flush()
    sys.stderr.flush()
    exec_fn(program, argv, env)
```

`launch_command` starts from a copy of the caller's environment, deletes the provider's override
variables that are not in `keep`, then sets the home variable to the canonical folder.

For Claude, the folder string is built from `$HOME` exactly as `claude-as.sh` builds it,
`$HOME/.claude-profiles/<name>`. Do not build it with `os.path.expanduser`, which drops a trailing
slash from `HOME`, so the string, and with it the keychain slot, would differ (R33). The program is
`claude-switch.py`'s `CLAUDE_BIN`, the same two-step lookup `claude-as.sh` makes.

### 3.5 Markers and the profile root

- Root: `COCKPIT_PROFILE_ROOT`, else `~/.cockpit/profiles`. It is checked like `_fleet_config_dir()`
  checks its value: an absolute, normalised path, or the tools stop.
- Writing the marker: write a temporary file in the same folder, `fsync` it, then `os.replace` it,
  with mode 600.
- `create()` makes every check first. Then:
  - it makes the profile folder with `os.mkdir(path, 0o700)`, not `makedirs`, so an existing folder
    fails instead of being reused;
  - it adds the shared links, then the marker;
  - on failure it removes only the links and the folder it made.

### 3.6 The usage cache (R52)

`~/.cockpit/cache/<provider>.json` has the shape of `.usage-cache.json`:
`{name: {"at": t, "data": ..., "backoff_until": t}}`. It is written through a temporary file whose
name holds the process id, then `os.replace`. When two processes write at once, the lost update costs
one extra read, the same trade-off the Claude cache makes. Claude's cache file is not touched.

### 3.7 The JSON-RPC client (phase 2)

For `codex app-server`: JSON-RPC 2.0 messages, one per line, over the child's stdin and stdout,
without the `"jsonrpc"` field. The handshake is `initialize` with `clientInfo`, then the notification
`initialized`, then the requests (gotcha #61). Run this way, over stdio, the app server starts no
background daemon (gotcha #58).

- Start the program with `start_new_session=True`, so that it leads its own process group.
- Send `initialize` and wait for its answer. Send the notification the protocol requires after it.
  Send the request, then read lines until the answer with the matching id arrives, ignoring
  notifications.
- One overall deadline (20 seconds to start with), enforced with `select` on stdout. On the deadline,
  or on any error, end the process group, reap it, and return `timeout` or `failed`.
- Log nothing from the replies. They are not expected to hold tokens, but the rule costs nothing.

### 3.8 The Claude adapter

| Method | Implementation |
|---|---|
| `profiles` | `cswitch.profiles()` |
| `create` | `bash ~/.claude-profiles/new-profile.sh <name>` |
| `login_command` | `zsh -c 'source "$1"; shift; claude-login "$@"' cockpit <path of claude-as.sh> <name>`. The name travels as an argument and is never pasted into the command text. |
| `account` | `oauthAccount` in the profile's `.claude.json`, which Claude Code's own sign-in writes. Used only for display and for the before-and-after comparison of R26, never to decide where a login goes. |
| `signed_in` | `ckpit.signed_in(name)`, which runs `claude auth status`. |
| `launch_command` | `CLAUDE_CONFIG_DIR` as in section 3.4. Before starting, the check of R40 with `cswitch.has_claude_login(cswitch.slot_for(...))`. |
| `read_usage` | Saved numbers: `ckpit.read(name)`. Refresh: `ckpit.refresh_one(name)`, then `ckpit.read(name)`. Converted to a `Reading`. |
| `checks` | Runs `verify.sh` and passes its output and failure count through. |

Importing `claude-cockpit.py` has no side effects (`claude-usage.py` and `claude-ping.py` rely on
that), but it is slow enough that only `ls`, `verify` and the screen do it. `as`, `open` and `login`
do not.

### 3.9 Opening terminals (R83 to R92)

The app is chosen as R85 says: `--in`, then `COCKPIT_OPEN_IN`, then `TMUX`, then `TERM_PROGRAM`.

One function builds the typed command and does all of its quoting (R87, R88):

```python
def typed_command(folder, address, settings):    # settings: the COCKPIT_* variables of R88 that are set
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in folder):
        raise Refused("the folder path contains a control character")
    assign = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in sorted(settings.items()))
    run = "%s %s as %s" % (shlex.quote(sys.executable), shlex.quote(COCKPIT_PY), address)
    return "cd %s && %s" % (shlex.quote(folder), (assign + " " + run) if assign else run)
```

This function was tried on 2026-10-13. Nine hostile folder names were used: quotes, `$`, backticks,
`; && echo`, non-ASCII letters, a leading space, glob characters and a leading dash. Each ran with
and without a hostile setting value, in zsh and in bash. Every command landed in the right folder
with the right address and setting, and nothing extra ran. Test O3 repeats this.

How each app receives it. In every case the typed command arrives as a separate argument, never as
part of a command or script text (R89):

| App | Calls |
|---|---|
| tmux | `tmux new-window -P -F '#{pane_id}' -c <folder> -n <address>` prints the new pane's id. Then `tmux send-keys -t <pane> -l <typed command>`, then `tmux send-keys -t <pane> Enter`. |
| iTerm2 | `osascript - <typed command> <address>`, with the first script below on stdin. |
| Terminal | The same, with the second script. |

```applescript
-- iTerm2. To be confirmed by A70.
on run argv
  tell application "iTerm2"
    activate
    if (count of windows) is 0 then
      set s to current session of (create window with default profile)
    else
      tell current window to set s to current session of (create tab with default profile)
    end if
    tell s
      set name to item 2 of argv
      write text (item 1 of argv)
    end tell
  end tell
end run
```

```applescript
-- Terminal.app. To be confirmed by A70.
on run argv
  tell application "Terminal"
    activate
    set t to do script (item 1 of argv)
    set custom title of t to item 2 of argv
  end tell
end run
```

The addresses open one at a time, each call within a 10-second limit. The first failure stops the
rest (R91).

## 4. Phases

Sizes are relative: S, M, L.

### Phase 0: measure Codex

**Done 2026-10-22.** Every step ran. The results are gotchas #57 to #64, and requirements section 17
holds their status. Only the free plan was measured: paid plans' 5-hour and weekly windows are still
open.

Size S. Needs a ChatGPT account on a plan that includes Codex. One account is enough if it holds no
other Codex login you care about. m8 and m2 sign in to it again, and if a new sign-in cancels older
ones (A4), they can sign out its other Codex logins, on other computers too. Otherwise use a
secondary account.

Before you start:

- Install the Codex CLI, for example with `brew install --cask codex`. Any way of signing in to OpenAI
  works, Google included: `codex login` opens OpenAI's own sign-in page, and Codex then stores a login
  of its own. When the browser offers several Google accounts, pick the one tied to the OpenAI
  account. m1 confirms afterwards which account Codex got.
- Never copy `auth.json`, not even for a test (gotcha #1). Every home in this phase is new and empty,
  sits under `~/.cockpit/measure/codex`, and signs in fresh.
- Record no token values: only file names, modes, sizes, key names, times, exit codes, percents and
  reset times. Tokens, email addresses and account ids appear only as short SHA-256 prefixes.

`scripts/measure_codex.py` runs one step per call. It prints what to do and what it saw, and appends
a record to `~/.cockpit/measure/codex/report.jsonl`. Run the steps in this order. The steps that sign
in to the same account again come late: if a new sign-in cancels older logins (A4), running them
early would break the steps that still need home A.

| Order | Step | Measures | What it does | Sign-ins |
|---|---|---|---|---|
| 1 | `setup` | A12, A13, A11 | Records the program and its version, where the daemon's control socket lives, what Codex writes into a home it is pointed at, and a keychain baseline (item names only). | none |
| 2 | `m1` | A11, A1, A2 | Runs `codex login status` in an empty home A and lists what appeared. Then signs in there with the default storage, and records where the login went (`auth.json` or a new keychain item) and whether `~/.codex` changed. | 1 |
| 3 | `m3` | A6, A7 | Through `codex app-server` in home A: the handshake, `account/read` and `account/rateLimits/read`, three times over ten minutes. Confirms that the reads move no usage window. | none |
| 4 | `m5` | A10 | Sets each candidate variable to a dummy value in turn (URLs point at port 9 on the same Mac) and reads again. Stops if one makes Codex rewrite home A's `auth.json`. | none |
| 5 | `m4` | A9 | Starts `codex login` in a new home C with `BROWSER` set to a recorder script, then stops it before any sign-in can complete. | started, never completed |
| 6 | `m6` | A8 | Optional. Links six stand-in items into home A and runs a short interactive session that trusts a folder and changes a setting. Which links survive? Then stops the background daemon the session started (gotcha #58). Spends a few tokens. | none |
| 7 | `m8` | A4, A3 | Signs a new home B in to the same account, renews each home's login, and reads both. `m8 --later` repeats this an hour afterwards. | 1 |
| 8 | `m7` | A5, A3 | Two app servers in home A: the first renews, then the second reads and renews with what it loaded. Can leave home A signed out. | none |
| 9 | `m2` | A2 | Signs two new homes, D and E, in keychain mode. One keychain item per home, or one shared? | 2 |
| 10 | `report` | | Every result, without personal data. | none |
| 11 | `clean` | | Stops each measurement home's daemon, signs the home out and deletes it. Then checks that no Codex keychain item or process is left. Keeps the report. | none |

Measured while the script was written (2026-10-13, Codex 0.162.1): A12 and A13, part of A11, and the
candidate list for A10 (requirements section 17).

Results: one `docs/gotchas.md` entry per fact, with the date and the Codex version, written from the
output of `report`. Numbering continues at #57. Update the status column of requirements section 17.

Exit gate (met 2026-10-22):

- A1, A2, A6, A7, A10, A11 and A12 are measured, because phase 2 needs them.
- A4, A5, A8 and A9 are measured, or explicitly left at the conservative behaviour: warn about a
  second login for an account (R29), share nothing (R18), no aiming (R27).

### Phase 1: `cockpit` with Claude only

Size M. Needs no account until the hand run in the last step.

1. `cockpit-core.py`: the registry and `PROVEN_ON`, address resolution, exit codes, stderr messages,
   the profile root, markers, environment building and `exec`, and the code gate of R68.
2. `provider-claude.py`, as in section 3.8.
3. `cockpit-terminals.py`, as in section 3.9. Measure A70 and A71 first. They need no account, only
   iTerm2 or Terminal.app, and tmux.
4. `cockpit.py`, with these subcommands:
   - `as`, `open`, `login`, `add`;
   - `ls` with `--refresh`, `--emails`, `--json` and a provider filter;
   - `verify`, which runs `verify.sh`, adds the code gate and caps the exit code;
   - `providers` and `help`;
   - no arguments, which starts `claude-cockpit.py`;
   - `--complete`, for the shell.
5. `claude-as.sh`: the `cockpit` function, and a zsh completion that asks `cockpit.py --complete`.
6. `install.sh` and `uninstall.sh`: the new files in both TOOLS lists, and the warning of R74.
7. `tests/fake_vendor.py` and `tests/test_cockpit_cli.py` (cases in section 5).
8. Documentation: a `cockpit` block in the README's command table, a short section in
   `how_it_works.md`, and `CHANGELOG.md`.
9. Hand run on a Mac with real Claude profiles:
   - `cockpit ls`;
   - `cockpit as claude/<name>`, then `/status` inside the session shows the expected account;
   - `cockpit open` with two Claude profiles, once inside tmux and once from iTerm2 or Terminal.app:
     both sessions start in the right folder, on the right accounts. Then once from VS Code's
     terminal without `--in`, where it prints the commands instead;
   - one `cockpit login claude/<name>`;
   - `cockpit verify`.

   Record the run and set `PROVEN_ON["claude"]`.

Exit gate:

- The new tests and every existing test file pass, including `test_claude_swap.py`, which the
  maintainer runs.
- Every existing screen state renders identically. This phase does not touch the screen.
- A clean install into a throwaway home works (`install.sh`, then `cockpit help`). That kind of
  install is how the `set -e` bug in `new-profile.sh` was found.

### Phase 2: Codex

Size M. Needs phase 0's exit gate and phase 1.

1. `provider-codex.py`: everything in R78 and R93, using phase 0's facts (gotchas #57 to #64) on the
   storage modes, shared items, override variables, default program path and background daemon.
2. The JSON-RPC client in `cockpit-core.py` (section 3.7).
3. `ls` shows the Codex profiles and `codex/(default)`. Window labels come from the window lengths the
   server reports.
4. `cockpit login codex/<name>`, which does this in order:
   1. runs the sign-in;
   2. reads `account/read`;
   3. makes the comparison of R26 and records the account in the marker;
   4. runs the check of R29 against the other Codex profiles and the default home.
5. `tests/test_provider_codex.py` (section 5).
6. Documentation:
   - a Codex section in `how_it_works.md`;
   - what each Codex command can change, in `safety.md`. That includes the background daemon with
     its analytics, and how to turn analytics off (gotcha #58);
   - how to remove a Codex profile by hand, starting with stopping its daemon (R93);
   - the README.
7. Hand run of `cockpit add codex <name>`, `login`, `as`, `ls --refresh` and `verify` on a real
   account. Also list every file under the profile with its modification time before and after a
   launch: the marker is the only file this project's code may have written. Set
   `PROVEN_ON["codex"]`.

### Phase 3: the screen

Size M.

1. Before changing anything, render every existing state at 140, 100 and 80 columns and keep the
   files.
2. `claude-cockpit.py`: when the profile root holds any profile (a directory listing; otherwise no
   adapter is loaded), load the registry. Refresh the other providers in a second background worker
   with its own flags, so that Claude rows never wait (R61). Merge the results in `load()`, never from
   the worker thread: curses is not thread-safe, as the comment in `run()` explains.
3. `draw_providers(y)`, called after `draw_forecast()`, draws the sections of R59 in the existing
   width tiers (bars from 118 columns, numbers from 96, stacked below that), plus the hidden-rows line
   of R62.
4. `render_cockpit_states.py` gets:
   - a fixture Codex provider with invented accounts;
   - a stub for every new function that starts a program (the harness's audit fails the run for any
     it misses);
   - new states: `providers_codex`, `providers_stale`, `providers_no_usage_source`,
     `providers_too_tall`.
5. Exit gate: every existing state renders to exactly the files kept in step 1, and the new states
   have been read at every width.

### Phase 4: Grok Build

Size S to M.

- `scripts/measure_grok.py` measures A20 to A27 the way phase 0 does.
- Then come `provider-grok.py` (R79), tests that mirror phase 2, documentation, the hand run and the
  proof date.
- The documentation must say that Grok re-reads `auth.json` on its next request, so
  `cockpit login grok/<name>` moves sessions already running on that profile to the new login at
  once.

### Phase 5: Ollama

Size S. Measure A40 and A41. Then `provider-ollama.py` (R82), with 127.0.0.1 as the only allowed host
for local profiles, plus tests, documentation, the hand run and the proof date.

### Phase 6: Google

Size S to M. Measure A30 and A31, and review A60 first. Antigravity may end up with one profile, and
Gemini with API-key profiles only (R23). Then `provider-google.py`, tests, documentation, hand runs
and proof dates.

### Phase 7: optional follow-ups

- D10: Claude usage refreshes through the direct read (gotcha #45).
- D7: the case check of R5 in `claude-as`.
- D8: removing override variables in `claude-as`.
- A forecast per provider, starting with Codex. Its 5-hour and weekly windows fit the existing
  simulator once the "point" is counted per provider.
- Watcher alerts for other providers.
- D3: opening sessions in a new tab or a tmux pane.
- Linux for the file-based providers (N1).

## 5. Tests

The conventions are those of the existing test files: a `case` decorator that collects PASS and FAIL,
run through `tests/run_dev.py` with `/usr/bin/python3`.

Isolation:

- `COCKPIT_PROFILE_ROOT` points at a temporary folder. The test files refuse to run when it resolves to
  the real `~/.cockpit/profiles`.
- No test touches a real login, the real keychain, a real profile or the network.
  `test_claude_swap.py` keeps its throwaway keychain items, and no new test joins it.
- Vendor programs are replaced by `tests/fake_vendor.py`. It is installed as an executable in a
  temporary folder and selected with `COCKPIT_<PROVIDER>_BIN`.
  - A scenario file tells it what to print, how to answer JSON-RPC requests, whether to hang, and
    which exit code to return.
  - It appends its arguments, and selected environment variables, to a log for the test to check. It
    logs names only, plus the values of home variables.

Cases for `test_cockpit_cli.py`:

| Case | Checks |
|---|---|
| C1 | Address forms: `p/n` and a bare `n` resolve. Bad provider ids and bad names are refused (exit 1). |
| C2 | Name rule: the character set `verify.sh` D3 uses (every printable ASCII character, plus names with look-alike letters) gets the same verdict as from `valid_name()`. |
| C3 | Shorthand: one match resolves. None, or two, exit 3, list the addresses, and start nothing. |
| C4 | Case: `claude/Alpha` with a folder `alpha` is refused, with the correct address, and nothing starts. |
| C5 | Marker: a folder with no marker, or a mismatched one, is not a profile, and nothing starts in it. |
| C6 | Environment: override variables are removed and named on stderr, and values are never printed. `--keep-env` keeps one. An inherited home variable that names another profile is replaced. |
| C7 | Claude parity (R37): `claude-as alpha --x` (through `zsh` with `claude-as.sh` sourced) and `cockpit.py as claude/alpha -- --x`, both with the stand-in program, record the same program, arguments and `CLAUDE_CONFIG_DIR`. Repeat with `HOME` ending in `/`. |
| C8 | Every refusal ends before `exec`. The `exec` function is replaced by one that records calls. |
| C9 | `ls --json`: schema number; emails masked by default; `--emails` shows them; no field holds a value shaped like a token. |
| C10 | `verify` caps its exit code at 100. |
| C11 | The code gate fails on a planted keychain write and on a planted write to `auth.json`, and passes on the real files. That proves it can fail (gotcha #27). |
| C12 | `--complete` starts no program. The subprocess functions are replaced by ones that fail the test. |
| C13 | An unproven provider is hidden. With `COCKPIT_UNPROVEN=1` it appears, with a warning. |
| C14 | `add` leaves nothing behind when a step fails, and refuses an existing folder. |

Cases for `test_provider_codex.py`:

| Case | Checks |
|---|---|
| X1 | `add` creates the folder (700), the marker (600) and only the links phase 0 approved. |
| X2 | `add` never copies `auth.json` from a planted default home. |
| X3 | `login`: arguments and `CODEX_HOME`, each outcome of R26 (first, same, different, unknown) with its exit code, and the marker records only confirmed accounts. |
| X4 | The same account as another profile or the default home gives the warning of R29. |
| X5 | `as`: arguments, `CODEX_HOME`, removed variables, and the storage option if phase 0 requires it. |
| X6 | A normal app-server answer gives a `Reading` with the server's window labels and reset times. |
| X7 | A hanging app server gives `timeout`, and its process group is gone afterwards. |
| X8 | Malformed JSON or an error answer gives `failed`, never numbers. |
| X9 | Signed out only when the vendor says so. A failed read alone is `failed`. |
| X10 | The cache is reused within the age limit, with at least 3 minutes of backoff after a rate-limit error. |
| X11 | The adapter makes no network request of its own: `urlopen` and socket connections are replaced by ones that fail the test. |
| X12 | Each `verify` check fails on its bad input: wrong modes, a mismatched marker, a link replaced by a file, a missing program. |

Cases for `cockpit open`, also in `test_cockpit_cli.py`. The tmux and `osascript` calls go to a
recording runner, except in O5:

| Case | Checks |
|---|---|
| O1 | Choosing the app (R85): `--in` beats `COCKPIT_OPEN_IN`, which beats `TMUX`, which beats `TERM_PROGRAM`. VS Code and unknown apps print the commands and exit 1. `--in tmux` outside tmux is refused. |
| O2 | All or nothing (R84): with one bad address among good ones, no tmux or `osascript` call is made. More than 10 addresses are refused. |
| O3 | Quoting (R87): the typed command, run by `zsh -c` and by `bash -c` with a stand-in script in place of `cockpit.py`, lands in the right folder and passes the right address. Folder names include spaces, quotes, `$`, backticks, `;`, `&&` and non-ASCII letters. |
| O4 | A folder path with a newline or another control character is refused. |
| O5 | R89 with the real `osascript`: a script that only returns its argument gets back exactly the typed command for every folder name of O3. It controls no app. |
| O6 | In the recorded calls, the typed command appears only as a separate argument, never inside a script or command text. |
| O7 | R88: the `COCKPIT_*` settings that are set appear in the typed command, quoted. Override variables and other variables never do. |
| O8 | R91: when the second of three opens fails, the output names the opened and unopened addresses, and the exit code is 1. The "not allowed to send Apple events" error produces the System Settings path. |

The render harness covers the screen states (phase 3).

## 6. Risks

| Risk | Mitigation |
|---|---|
| A vendor changes its CLI: options, the app-server protocol, file names. | Facts carry the version they were measured on. Adapters check the shape of what they read and report `failed` rather than wrong numbers. `cockpit providers` and `verify` show versions. After a vendor's major update, measure again before trusting the proof date. |
| A measurement signs out a real login (M8). | Throwaway homes, a secondary account, and the warning at the top of phase 0. |
| A shared link is replaced by a real file and stops being shared. | Share only what phase 0 approved. `verify` reports the drift (R65). |
| The Codex logins of different profiles collide in the OS keychain. | A2 is measured before phase 2. If needed, file storage is forced (R78). |
| The screen grows taller than the terminal. | Claude stays first, and the hidden-rows line says what is missing (R62). |
| The scope grows back into coordination. | It is out of scope (requirements section 3). The interfaces do not rule it out later. |
| A vendor's terms restrict several accounts, or starting its CLI from a wrapper. | A60 is reviewed before each provider is enabled, and the findings go into `safety.md`. |
| macOS refuses the Automation permission, or iTerm2 or Terminal changes its scripting. | R91 says where to allow it. A70 is measured again on new versions. tmux and the printed commands keep working either way. |

## 7. Status

| Phase | Status | Proof date | Notes |
|---|---|---|---|
| 0. Measure Codex | done 2026-10-22 | | Gotchas #57 to #64. Free plan only; paid plans' windows are not measured. |
| 1. `cockpit`, Claude only | not started | | Needs no account until its hand run. |
| 2. Codex | not started | | Needs phases 0 and 1. |
| 3. The screen | not started | | |
| 4. Grok Build | not started | | |
| 5. Ollama | not started | | |
| 6. Google | not started | | |
| 7. Optional follow-ups | not started | | |
