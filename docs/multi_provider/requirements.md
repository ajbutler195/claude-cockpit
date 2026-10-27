# Multi-provider cockpit: requirements

Status: draft of 2026-10-13. Nothing in this document is built yet. The plan for building it is in
[implementation_plan.md](implementation_plan.md).

This document specifies two things:

1. `cockpit`, one command that starts a session on any of your accounts, on any supported AI coding
   service, in the current terminal or in new ones.
2. The cockpit screen, extended to show every provider's accounts and usage.

It is written for whoever builds it, a person or a coding agent. Read the [README](../../README.md)
and [how_it_works.md](../how_it_works.md) first: words such as fleet, slot, anchor and borrowed are
defined there, and the safety rules this document extends are in [safety.md](../safety.md).

How to read the numbers. Requirements are numbered R1, R2 and so on; safety requirements S1 and on;
non-functional requirements N1 and on; assumptions about other vendors' tools A1 and on; decisions
D1 and on. The numbers are stable, like the gotcha numbers: a number is never reused, and a
requirement that is dropped keeps its number and is marked DROPPED. A new requirement takes the next
free number even when it is listed in an earlier section. Code comments may cite them
("requirement R5").

## 1. Background

Today every tool in this repository serves Claude Code. `claude-as <name>` starts a session on one
account by setting `CLAUDE_CONFIG_DIR` to that account's profile folder. The cockpit shows each Claude
account's usage, and `claude-switch` and `claude-swap` move the fleet between accounts.

Other AI coding CLIs work the same way at the level that matters here. Each keeps its login in a home
folder that one environment variable can move: Codex CLI reads `CODEX_HOME`, Grok Build reads
`GROK_HOME`. So "one profile folder per account, and a launcher that sets the variable" carries over
to them. What does not carry over is everything that moves logins between slots: that code rests on
measured facts about Claude Code that nobody has measured for the other CLIs.

## 2. Goals

1. One command starts a session on any account of any supported provider.
2. One command opens sessions on several accounts at once, each in a new terminal of its own.
3. One command lists every account on every provider, with its sign-in state and usage.
4. The cockpit screen shows every provider's accounts and usage.
5. Nothing about the Claude tools becomes less safe, and no existing command changes behaviour.

Typical use:

- You have two Claude accounts and two ChatGPT accounts. `cockpit ls` shows all four with their
  usage. `cockpit open claude/personal codex/work` opens a new terminal for each and starts both
  sessions side by side.
- You add a SuperGrok account: `cockpit add grok main`, then `cockpit login grok/main`, then
  `cockpit as grok/main`.
- One Codex account is nearly out for the week. `cockpit ls codex` shows which other one has room.

## 3. Out of scope for this version

- Coordination of any kind: routing work between agents, handing a task from one agent to another,
  running agents together. Deferred by the project owner on 2026-10-13.
- Fleet switching, swap, auto-swap, `claude-ping` and the run-out forecast for any provider other
  than Claude.
- Storing, rotating or creating API keys, and tracking API spend.
- Windows and Linux.
- Opening sessions in terminal apps other than tmux, iTerm2 and Terminal.app (`cockpit open` prints
  the commands instead), and arranging panes or window layouts.
- Editing any vendor CLI's own settings files.

## 4. Terms

| Term | Meaning |
|---|---|
| provider | A supported AI coding service and its CLI. The ids are `claude`, `codex`, `grok`, `antigravity`, `gemini` and `ollama` (D5). |
| profile | One account's private home folder for one provider. |
| address | `<provider>/<profile>`, for example `codex/work`. |
| home variable | The environment variable a provider's CLI reads to find its home folder: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GROK_HOME`. |
| default home | Where a provider's CLI keeps its state when the home variable is unset, for example `~/.codex`. For Claude this is the fleet. |
| profile root | `~/.cockpit/profiles`, where the profiles of every provider except Claude live (D4). Claude profiles stay in `~/.claude-profiles`. |
| marker | The file that makes a folder a profile. For Claude it is the profile's own `.claude.json`. For every other provider it is `.cockpit-profile.json`. |
| subscription profile | A profile signed in with the vendor's account sign-in (OAuth), as opposed to an API key. |
| override variable | An environment variable that makes a CLI use another credential, or another server, than the profile's own login. `ANTHROPIC_API_KEY` is one. |
| program path | The fixed path the tools use to start a provider's CLI. |
| enabled provider | A provider whose adapter has earned its proof date (S14). |

## 5. The command

**R1.** The command is `cockpit` (D1). Like the existing commands, it is a zsh function defined in
`claude-as.sh` that runs `~/.claude-profiles/cockpit.py`.

**R2.** Subcommands:

| Command | What it does | What it costs |
|---|---|---|
| `cockpit` | Opens the cockpit screen, as `claude-cockpit` does. | Free |
| `cockpit ls [<provider>] [--refresh] [--emails] [--json]` | Lists every profile with its sign-in state and usage (section 9). | Free (for Codex, once A7 confirms it) |
| `cockpit add <provider> <name>` | Creates a profile (section 6). | Free |
| `cockpit login <address> [--device-auth]` | Runs the vendor's own sign-in for that profile only (section 7). | A browser sign-in |
| `cockpit as <address> [--keep-env VAR]... [-- <args>...]` | Starts the provider's CLI on that account in this terminal (section 8). | Whatever the session uses |
| `cockpit open <address>... [--in <app>]` | Opens a new terminal for each address and starts that session in it (R83 to R92). | Whatever the sessions use |
| `cockpit providers` | Lists the supported providers, each one's program path, whether the program was found, and whether the provider is enabled. | Free |
| `cockpit verify` | Health check for every provider (section 12). | Free |
| `cockpit help` | Usage. | Free |

**R3.** An address is `<provider>/<profile>` (D2). The provider part must be one of the provider ids,
exactly, in lower case. The profile part must pass the existing profile-name rule (ASCII letters,
digits, `-` and `_`), checked by the same function the other tools use, `valid_name()` in
`claude-switch.py`. There is never a second implementation of that rule (gotcha #26).

**R4.** Shorthand. A bare `<profile>` resolves when exactly one enabled provider has a profile of that
exact name. When none has, the command refuses and lists every address. When two or more have, it
refuses and lists the matching addresses. Either way nothing is started and the exit code is 3.
WHY: guessing between accounts is how the wrong account gets used. "The situation is ambiguous:
refuse" (gotcha #17).

**R5.** Exact case. The profile part must equal the folder name on disk, upper and lower case
included. If it matches a folder only when case is ignored, the command refuses and prints the
correct address. WHY: macOS disks are case-insensitive by default, so the folder `work` can be opened
as `Work`. But Claude Code names a profile's keychain item after the path string as it was given, so
`Work` and `work` name two unrelated slots. Checked 2026-10-13 on an APFS volume: a test for
`.../Work/.claude.json` succeeds for a folder named `work`, and the two path strings hash to
different slot names. A case variant therefore opens a session on an empty slot that looks signed
out. Other vendors may key stored logins by path too (A2). Today's `claude-as` has this hole (D7).

**R6.** Every address the command prints is the canonical one, with the exact provider id and the
on-disk profile name, so it can be pasted into the next command.

**R7.** Exit codes of `cockpit` itself:

| Code | Meaning |
|---|---|
| 0 | Done. |
| 1 | Refused, or the input is invalid. Nothing changed. |
| 2 | The provider's program was not found at its program path (the code `require_claude()` uses). |
| 3 | The address matches no profile, or more than one. |
| 4 | A sign-in completed, but it could not be confirmed which account it holds, or it holds a different account than the profile had before. |

After `cockpit as` hands over to the provider's program, the exit code is that program's.
`cockpit verify` exits with its failure count (R67).

**R8.** Refusals and errors go to stderr, say what to do next, and are never silenced by a quiet
option (gotcha #25).

**R9.** Tab completion completes subcommands, provider ids and addresses. The candidates come from
`cockpit.py` itself, so completion and resolution can never disagree. Producing them starts no other
program and makes no network request.

**R10.** Every existing command (`claude-as`, `claude-login`, `claude-profiles`, `claude-usage`,
`claude-cockpit`, `claude-switch`, `claude-swap`, `claude-ping`, `claude-autoswitch`,
`new-profile.sh`, `verify.sh`) keeps its behaviour and output. The one exception is R59: the screen
gains sections below the Claude part when profiles of other providers exist.

**R11.** `cockpit help` lists every subcommand with a one-line purpose, the address format, and which
subcommands open a browser or spend usage.

**R12.** `cockpit providers` shows, per provider: the id, the program path and whether a program is
there, its version where the program reports one, whether the provider is enabled (S14), and the
number of profiles.

## 6. Profiles

**R13.** Claude profiles do not change. They stay at `~/.claude-profiles/<name>/`, are created by
`new-profile.sh`, and are recognised by their own `.claude.json`. `cockpit add claude <name>` runs
`new-profile.sh <name>` and nothing else. WHY: Claude Code names a profile's keychain item after the
folder path, so moving a Claude profile signs it out.

**R14.** Profiles of every other provider live at `<profile root>/<provider>/<name>/`. The profile root
is `~/.cockpit/profiles` unless `COCKPIT_PROFILE_ROOT` is set. That value must be an absolute,
normalised path; any other value stops the tools instead of falling back, as
`COCKPIT_FLEET_CONFIG_DIR` does. The profile root is never inside `~/.claude-profiles`. WHY: tools
that walk `~/.claude-profiles` treat its sub-folders as candidate Claude profiles, and pointing
Claude Code at a folder writes a configuration into it (gotcha #30).

**R15.** A folder under the profile root is a profile only if it holds `.cockpit-profile.json` and
that file's `provider` and `name` match the folder's place. Every tool applies this test before it
starts any program in a folder or reads anything else from it: filter first, probe second
(gotcha #30). WHY: vendor CLIs create a fresh configuration in any folder they are pointed at (A11),
so without the test a typo becomes an account.

**R16.** The marker is JSON: `provider`, `name`, `created_at` and, once a sign-in has been confirmed,
`identity` with `email`, the vendor's account id where it gives one, and `confirmed_at`. Provider
settings that are not secrets go there too (an Ollama server address, the name of an API-key item).
It never holds a credential. The profile folder is mode 700 and the marker 600.

**R17.** `cockpit add <provider> <name>`:

- refuses when the provider is unknown or not enabled, the name fails R3, the folder already exists,
  or the provider allows one account per machine and already has a profile (R21);
- checks everything it can before it creates anything, then creates the folder, the shared links
  (R18) and the marker, in that order;
- if a step fails, removes what it created, so no half-made folder blocks a second attempt;
- never signs in, and ends by printing the next command, `cockpit login <address>`.

**R18.** Shared items. Like `new-profile.sh`, `cockpit add` may link items from the provider's
default home into the new profile, so instructions, prompts and similar settings stay shared. Each
provider's list holds only items measured to be safe to share (A8, A23). An item is safe when the
vendor's CLI keeps it a link after writing to it, and it holds no login. Items a vendor keeps logins
in (`auth.json`, `mcp_credentials.json` and the like) are never linked. An item missing from the
default home is skipped with a note.

**R19.** Profile folders are permanent. No command renames, moves or deletes one. The documentation
says how to remove a profile by hand, and that renaming or moving one can sign it out.

**R20.** `cockpit add` never copies a login, from the default home or from anywhere else. A new
profile starts signed out. WHY: copying a login puts one single-use refresh token in two places
(gotcha #1, and A3 for Codex). Codex and Grok keep their logins in plain files, where a copy is one
`cp` away.

**R21.** A provider whose CLI can hold only one login per machine gets at most one profile
(A30 for Antigravity, A41 for Ollama Cloud). `cockpit add` refuses a second and says why.

**R22.** Each provider's default home is shown read-only by `cockpit ls` and `cockpit verify`, as
`<provider>/(default)`, with its sign-in state and account. It is where the plain vendor command
goes, the counterpart of Claude's fleet, and knowing its account is what makes R29 possible.
`cockpit as` does not accept it: run the vendor command directly.

**R23.** API-key profiles (first for `gemini`, possibly later for others). The marker names a keychain
item, `cockpit-apikey-<provider>-<name>`. The user creates that item with a
`security add-generic-password ... -w` command that `cockpit login` prints. Ending the command with
`-w` makes `security` prompt for the key, so the key never lands in shell history. `cockpit as` reads
the item and puts the key in the child's environment only. No code in this project writes the
item (S1).

## 7. Sign-in

**R24.** `cockpit login <address>` runs the vendor's own sign-in in this terminal, with the profile's
home variable set (R33). The tools never see the tokens the sign-in produces.

**R25.** For Claude it runs the existing `claude-login <name>`, with its aiming and its check. There is
no second implementation.

**R26.** After every sign-in the command finds out which account the profile now holds, from the
vendor (A6, A21, A30), and reports one of: a first sign-in, confirmed as `<email>`; the same account
as before; a different account than before (exit 4); could not confirm (exit 4). A confirmed account
is recorded in the marker. WHY: the browser decides which account signs in, not the command line
(invariant #9).

**R27.** Aiming. Where the vendor's sign-in honours the `BROWSER` variable (A9, A24), the sign-in page
may be opened in the Chrome profile that matches the expected account, with the existing code in
`claude-switch.py` (`chrome_profile_for_email`, the opener script and its receipt). Aiming is
optional, fails soft, and never replaces the check in R26.

**R28.** `--device-auth` passes the vendor's device-code option where one exists, for signing in on a
machine whose browser is elsewhere: `codex login --device-auth` (a ChatGPT account must enable it
first in its security settings) and `grok login --device-auth`.

**R29.** Same account twice. When the confirmed account equals the account of another profile of the
same provider, or of its default home (R22), the command warns and names both. Until that provider's
"two logins for one account" measurement is done (A4, A22), the warning says the other login may stop
working. Once it is measured, the warning states the measured outcome.

**R30.** Local Ollama has no accounts. `cockpit login` refuses an `ollama` profile that points at a
local server and says so.

## 8. Starting a session

**R31.** `cockpit as <address> [-- <args>...]` resolves the address (R3 to R6), checks the marker
(R15), finds the program (R32), builds the environment (R33 to R35) and then replaces itself with the
provider's program (`exec`). The program therefore owns the terminal, receives signals directly and
returns its own exit code. Everything after `--` goes to the program unchanged.

**R32.** Program path: `COCKPIT_<PROVIDER>_BIN` if it is set, else the provider's default path. Never
a bare name looked up on `PATH` (invariant #6). For Claude these are `COCKPIT_CLAUDE_BIN` and
`~/.local/bin/claude`, as today. The default paths of the other programs are measured (A12, A26, A30,
A40).

**R33.** The home variable is set explicitly to the profile folder on every launch, never inherited
from the calling shell (invariant #4, generalised). For Claude the value is exactly the string
`claude-as` uses, `$HOME/.claude-profiles/<name>`. One character of difference names a different
keychain slot.

**R34.** Override variables are removed from the child's environment before a subscription profile
starts, and one line on stderr names each removed variable, never its value. `--keep-env VAR` keeps
one and may be repeated. The starting lists, to be confirmed by measurement (A10, A27, A50):

| Provider | Variables | Why |
|---|---|---|
| claude | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` | Each can replace the profile's login, or send requests to another service. |
| codex | `CODEX_ACCESS_TOKEN`, `OPENAI_FEDERATION_RULE_ID`, `OPENAI_IDENTITY_TOKEN_FILE`, `CODEX_SQLITE_HOME`; and, until model requests are tested, `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL` | The first replaces the login, the next two stop Codex from starting, and the fourth moves the profile's state elsewhere (gotcha #62). The last three are a precaution. |
| grok | `XAI_API_KEY` | Used when the profile holds no session, so a signed-out profile would quietly bill an API key instead of failing. |
| grok | `GROK_AUTH_PROVIDER_COMMAND`, `GROK_OIDC_ISSUER`, `GROK_OIDC_CLIENT_ID` | Each outranks the browser login. |
| grok | `GROK_CLI_CHAT_PROXY_BASE_URL` | Candidate. A27 decides. |
| gemini | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_APPLICATION_CREDENTIALS` | Replaced by the profile's own key (R23). |
| ollama | `OLLAMA_HOST` | Replaced by the profile's own server address. |

WHY: a leftover `export` changes which account or server a session uses, and how it is billed, while
the command line names a different profile. Settings kept in files are not covered: Claude Code's
`apiKeyHelper`, a per-model key in Grok's `config.toml`, Codex's `model_provider`. `cockpit verify`
reports the ones it can detect (R70).

**R35.** Nothing else in the environment changes. Variables that belong to other providers are left
alone.

**R36.** Before it hands over, `cockpit as` makes no network request and starts no vendor program. It
only reads local files, plus keychain items for R23 and R40. Target: under 300 ms from Enter to `exec`
on the Mac used for the proof runs. WHY: `claude-as` is instant, and starting a vendor program to
check something can renew a login as a side effect.

**R37.** Claude parity. With no override variables set, `cockpit as claude/<name> -- <args>` starts the
same program, with the same arguments and the same `CLAUDE_CONFIG_DIR`, as `claude-as <name> <args>`.
A test proves it with a stand-in program that records what it was given.

**R38.** Without an address, `cockpit as` prints its usage and the list of addresses, as `claude-as`
with no name prints `claude-profiles`, and exits 1.

**R39.** Two sessions on one profile are allowed, unless measurement shows that the provider's CLI
loses or cancels a login when two processes share one home (A5, A23). For such a provider,
`cockpit as` warns when another process already runs on that profile, found from process environments
the way `claude-switch` counts the sessions on each lane.

**R40.** A Claude profile without a login of its own. When the profile's own slot holds no Claude
login, `cockpit as claude/<name>` says so before starting: the profile never signed in, its login
reached its hard deadline, or `claude-swap` moved it to the fleet (borrowed). The session will start
signed out, and `cockpit login claude/<name>` fixes all three cases. In the borrowed case it gives the
account a fresh anchor, which ends the borrowed hazard (safety.md). The session still starts, as it
would with `claude-as`. The test is `has_claude_login()`, the one the other tools use.

### Opening sessions in new terminals

**R83.** `cockpit open <address>... [--in <app>]` opens one new terminal for each address, in the
order given, and starts `cockpit as <address>` in each, in the folder `cockpit open` was run from.
The terminal it was run from stays as it was, and `cockpit open` exits once the new terminals are
open. It takes no options for the sessions themselves: the vendors' CLIs take different options, so
a session that needs some is started with `cockpit as` in a terminal of its own.

**R84.** Every address is resolved (R3 to R6) and its marker checked (R15) before anything opens. If
any address fails, nothing opens, and the exit code is the first failure's (1 or 3). At most 10
addresses are accepted per call. WHY: a typo should not leave half the sessions open, and a script
that loops by mistake should not open a hundred windows.

**R85.** Where the terminals open, decided in this order:

| Condition | What opens |
|---|---|
| `--in tmux`, `--in iterm` or `--in terminal` is given; otherwise `COCKPIT_OPEN_IN` is set to one of those | That app, as in the rows below. `tmux` is refused when the command is not running inside tmux. |
| Running inside tmux (`TMUX` is set), in any terminal app, VS Code's included | A new tmux window per address, in the current tmux session |
| Running in iTerm2 (`TERM_PROGRAM` is `iTerm.app`) | A new tab per address, in the current window |
| Running in Terminal.app (`TERM_PROGRAM` is `Apple_Terminal`) | A new window per address. A script can open Terminal tabs only with extra Accessibility permission, so it opens windows. |
| Anywhere else, VS Code's integrated terminal included | Nothing opens. For each address it prints the command to type into a new terminal, says why, suggests `--in`, `COCKPIT_OPEN_IN` or tmux, and exits 1. VS Code gives a command no way to open its terminals. |

**R86.** In each new terminal the user's normal shell starts as it always does there, and the command
is typed into it (tmux `send-keys`, iTerm2 `write text`, Terminal.app `do script`), as if the user had
typed it. WHY: the session gets the user's full shell setup, including the `PATH` that agents need to
run tools. And when the session ends, the terminal stays open at a prompt, with the session's last
messages still on screen.

**R87.** The typed command is `cd <folder> && <python> <cockpit.py> as <address>`. `<python>` is the
interpreter that runs `cockpit open` and `<cockpit.py>` is the installed script, both as absolute
paths, so the command works in any shell whether or not the `cockpit` function is loaded there. The
folder and the paths are quoted for the shell with `shlex.quote`. Addresses are canonical (R6), so
they contain only ASCII letters, digits, `-`, `_` and `/`. A folder whose path contains a control
character, such as a newline, is refused: typed into a shell, a newline would run the line early.

**R88.** The cockpit's own settings that change how an address resolves, when they are set in the
calling shell (`COCKPIT_PROFILE_ROOT`, `COCKPIT_UNPROVEN`, `COCKPIT_FLEET_CONFIG_DIR` and every
`COCKPIT_<PROVIDER>_BIN`), are written into the typed command, quoted, so that the new terminal
resolves the address exactly as `cockpit open` checked it. No other variable is passed on. Override
variables in particular are not, and S9 holds either way, because `cockpit as` removes them.

**R89.** The typed command reaches tmux and AppleScript only as a separate argument, never pasted into
a command line or into a script: `tmux send-keys -l` takes it as one argument, and AppleScript
receives it through its `run` handler's argument list. So the shell quoting of R87 is the only
quoting there is. `claude-as.sh` follows the same rule for profile names. Checked 2026-10-13 (A72):
`osascript` returns such an argument byte for byte, with quotes, `$`, backticks, backslashes, tabs
and newlines intact.

**R90.** Where the app allows it, each new tab or window is titled with its address: the tmux window
name, the iTerm2 session name, the Terminal.app tab's custom title. This is best effort: a vendor CLI
may set its own title.

**R91.** If opening a terminal fails after others have opened, `cockpit open` stops, says which
addresses were opened and which were not, and exits 1. When macOS refuses because the user has not
allowed their terminal app to control iTerm2 or Terminal (AppleScript reports that it is not allowed
to send Apple events), the message says where to allow it: System Settings, Privacy & Security,
Automation.

**R92.** `cockpit open` itself starts no vendor program and makes no network request. It starts only
`tmux` or `osascript`. Each session starts in its own terminal through `cockpit as`, so every other
requirement of this section applies to it there.

## 9. Listing

**R41.** `cockpit ls` prints one line per profile, grouped by provider in a fixed order, Claude first.
Columns: address; account (email); sign-in state; usage; age of the numbers; a note when something
needs attention.

**R42.** By default `ls` prints saved numbers and refreshes nothing, like `claude-usage --fast`.
`--refresh` refreshes every profile in parallel, each within its provider's time limit, then prints.
A slow provider holds up the others by no more than its own limit.

**R43.** Email addresses are hidden behind the screen's fixed mask, `••••••@••••••`, unless `--emails`
is given (D6, gotcha #56).

**R44.** One vocabulary of sign-in states for every provider: `signed in`; `signed out`, only when the
vendor's own CLI said so and never inferred from a failed usage read (gotcha #21); `renew by <date>`
within 72 hours of a known deadline; `unknown` when it could not be checked.

**R45.** Usage is shown per window as `<label> <percent>%`, for example `5h 12% · wk 40%`, with `●` on
the window the vendor says is binding. Labels come from the vendor's data, not from constants
(gotcha #22). A provider with no usage source shows `no usage source`. A dash keeps its existing
meaning: no data for a window that exists.

**R46.** Freshness follows the screen: numbers older than 6 minutes show their age, numbers older than
15 minutes are marked stale, and a refresh that failed says why on that line: timed out, rate limited
by the server, signed out, or failed (invariant #8).

**R47.** The Claude fleet account is marked `▸`, as on the screen. Each provider's default home is
listed as `<provider>/(default)` (R22).

**R48.** `--json` prints the same data as JSON with a `schema` number, for scripts. It never contains
a token, and email addresses follow R43.

**R49.** `ls <provider>` limits the output to one provider.

## 10. Usage readings

**R50.** Every usage read is read-only. It sends no message to a model and opens no usage window. It
renews no login unless the vendor's own read does that, and where it does, the provider's section in
`safety.md` says so.

**R51.** A reading holds its windows and a status. Each window has a label, a kind, the percent used
(or an amount and a unit), the reset time as the server gives it (never computed), and whether it
binds. The reading also holds the time the server produced it. The status is one of `ok`, `timeout`,
`limited`, `signed_out`, `failed` and `unsupported`, and the statuses are never merged (gotcha #21).

**R52.** Readings are cached in one file per provider, `~/.cockpit/cache/<provider>.json`. A reading
younger than the caller's limit is reused. When the vendor reports rate limiting, reads of that
profile back off for at least 3 minutes and the last good reading is returned with its true age
(gotcha #52). Claude's `.usage-cache.json` and its behaviour do not change.

**R53.** Each adapter lists the hosts it may contact. A request to any other host fails before it
connects, and a test checks this. A token goes only to the service that issued it (safety.md).

**R54.** Numbers are never combined across providers: no total, no shared score, no shared pace. Each
provider's limits are counted in its own units.

**R55.** Codex usage comes from the Codex CLI's app server (`codex app-server`, method
`account/rateLimits/read`, A7), started with `CODEX_HOME` set to the profile and stopped after the
read, within a time limit. It calls only `account/read` and `account/rateLimits/read`, through an
allowlist, never `account/rateLimitResetCredit/consume`, which spends a reset credit (gotcha #61).
This project's code makes no request to OpenAI itself. WHY: the app server is OpenAI's own client and
handles sign-in, renewal and headers. The web endpoint behind it is private and comes with no
compatibility promise.

**R56.** Grok, Antigravity, Gemini and Ollama Cloud report `unsupported` until a usage source has been
found and measured (A25 for Grok). Local Ollama reports capacity, not quota: the models loaded and
the memory they use, from its local API on 127.0.0.1 (A40).

**R57.** Claude readings do not change in this version. Moving them to the direct read recorded in
gotcha #45 is a separate change (D10).

## 11. The screen

**R58.** With no profiles of other providers, the screen is identical to today's, character for
character, at every terminal width. The render harness proves it.

**R59.** With other providers, the Claude part stays as it is, and one section per provider follows the
FORECAST panel, in the fixed provider order. A section has:

- a header with the provider's name and its window labels;
- one row per profile, with the name, the masked email, the provider's windows (with bars where the
  width allows) and their reset times;
- a note line under any row whose numbers cannot be trusted, worded by the same rules as the Claude
  rows;
- a "most headroom" line for that provider, computed only from rows that can be trusted.

**R60.** The number keys, `u`, `p` and `a` act on Claude only, as today. Rows of other providers are
not numbered. `r` refreshes every provider and `e` shows or hides every email. No key is added, so the
footer text, and the width at which it wraps, stay the same (gotcha #56).

**R61.** Other providers are refreshed on the same schedule (on `r` and every 5 minutes), in the
background, each within its own time limit. Claude rows update as soon as Claude's refresh finishes,
whatever the other providers are doing.

**R62.** Layout follows the existing rules: no meter is dropped to fit, and a narrower terminal loses
bars, never numbers. When the sections do not fit the terminal's height, the last line shown says how
many rows are hidden. Nothing is cut off without a word.

**R63.** Every email on screen goes through `UI.em()`.

## 12. Health check

**R64.** `cockpit verify` runs `verify.sh` unchanged, then a section for each provider that has
profiles, then a summary.

**R65.** For each provider it checks:

- the program is at its path, and its version;
- the profile root and every profile folder are mode 700, and every marker is mode 600;
- every marker parses and matches its place;
- every shared link is still a symbolic link to an existing target. A link replaced by a real file
  has drifted; this is the check `verify.sh` makes for Claude's `settings.json`.
- each profile's sign-in state, and whether its recorded account still matches;
- a one-account provider has at most one profile.

**R66.** It reports the account of each provider's default home, and any profile that holds the same
account (R29).

**R67.** Exit code: the number of failures, capped at 100. WHY: an exit code is one byte, so 256
failures would read as success.

**R68.** Code gate. It parses every new tool file and fails when:

- a subprocess call carries a keychain-writing argument (the list `verify.sh` section D3 uses); or
- a file is opened for writing under a name a vendor keeps logins in (`auth.json`,
  `mcp_credentials.json`, `.credentials.json`).

This makes S1 checkable.

**R69.** It warns when the current shell exports an override variable (R34). `cockpit as` would remove
it, but plain vendor commands would use it.

**R70.** It reports file-based overrides that it can detect without starting a vendor program, for
example `apiKeyHelper` in Claude's `settings.json`.

**R71.** Every check has a test that makes it fail on a known-bad input (gotchas #27 and #33).

**R93.** Background processes. Some providers' CLIs leave a process running for each profile (for
Codex, gotcha #58). For those, `cockpit verify` lists the processes running for each profile, and
warns about any whose profile folder no longer exists. The documented way to remove such a profile
by hand starts by stopping its daemon: for Codex, `CODEX_HOME=<profile> codex app-server daemon stop`.

## 13. Install and uninstall

**R72.** `install.sh` installs the new tool files with the others (one TOOLS list, the same `--update`
and backup behaviour). `claude-as.sh` gains the `cockpit` function and its completion. Neither
creates a profile folder or the profile root: `cockpit add` creates those when first needed.

**R73.** When a supported provider's program is not at its default path but is found on `PATH`,
`install.sh` records its absolute path in `~/.zshrc` as `COCKPIT_<PROVIDER>_BIN`. The line is tagged
so that `uninstall.sh` can remove it, the way `install.sh` records `COCKPIT_CLAUDE_BIN` today.

**R74.** `install.sh` warns when a program named `cockpit` already exists on `PATH`, because the shell
function would hide it.

**R75.** `uninstall.sh` removes the new tool files and the lines `install.sh` added. It leaves
`~/.cockpit` in place and prints, but never runs, the commands that would delete each profile's
stored login (for example `rm ~/.cockpit/profiles/codex/work/auth.json`), as it does for Claude's
keychain items.

**R76.** The watcher and its `launchd` job do not change in this version.

## 14. Per provider

**R77.** claude. Wraps the existing tools. `as` follows R33 and R37, `login` runs `claude-login`, `ls`
reads through `claude-cockpit.py`, and `verify` runs `verify.sh`.

**R78.** codex. A profile is a `CODEX_HOME` folder.

- **Sign-in:** `codex login`, with `--device-auth` on request. There is no aiming, because
  `codex login` ignores `BROWSER` (gotcha #63).
- **Account and usage:** from `codex app-server` (gotcha #61).
- **Login storage:** Codex keeps each profile's login apart in both of its storage modes
  (gotcha #59), so the tools force neither. In keychain mode the login is named after the folder's
  path, so R5 and R19 apply to Codex as they do to Claude.
- **Shared items:** profiles share `AGENTS.md`, `prompts/`, `skills/`, `config.toml`,
  `history.jsonl` and `sessions/` with the default home (gotcha #64).
- **Sessions and accounts:** several sessions may share a profile, and several profiles may hold
  one account (gotcha #60).
- **Background daemon:** the interactive app leaves a daemon running for each profile, with
  analytics on by default (gotcha #58). R93 covers it, and the Codex section of `safety.md` says how
  to turn analytics off.

**R79.** grok. A profile is a `GROK_HOME` folder. Sign-in is `grok login`, with `--device-auth` on
request. The login is in `<home>/auth.json` and MCP sign-ins in `<home>/mcp_credentials.json` (A20);
neither is ever linked. Grok re-reads `auth.json` on its next request, so a new sign-in moves sessions
already running on that profile at once, and the documentation must say so. Account: A21. Usage:
`unsupported` until A25.

**R80.** antigravity. One profile per machine until A30 shows otherwise. The program is `agy` (A30).
Usage: `unsupported`.

**R81.** gemini. API-key profiles only (R23). Since 2026-06-18 the Gemini CLI no longer serves
individual Google accounts. Whether its home folder can be moved is A31; until then its profiles share
the default home and differ only in the key.

**R82.** ollama. A profile names a server address, local by default. `cockpit as ollama/<name> --
<args>` starts `ollama` with `OLLAMA_HOST` set to that address and passes the arguments through, for
example `-- launch claude` to run Claude Code on a local model. Ollama Cloud gets one profile (A41).

## 15. Safety requirements

**S1.** No code in this extension writes, copies, moves or deletes a login: no keychain write, and no
write to a file a vendor keeps logins in. Logins are written only by the vendors' own programs. R68
checks it.

**S2.** Fleet switching, swap, auto-swap and ping stay Claude-only. No new code calls them.

**S3.** Every vendor program the tools start gets its home variable set explicitly to the profile, or
explicitly removed when the tools mean the default home. It is never inherited (invariant #4,
generalised).

**S4.** A folder is checked for its marker before any program is pointed at it (invariant #5 and
gotcha #30, generalised).

**S5.** Programs are started from fixed paths (invariant #6, generalised).

**S6.** A number that cannot be shown to be fresh is not shown as fresh, for every provider
(invariant #8).

**S7.** Every sign-in is followed by a check of which account it holds (invariant #9).

**S8.** A token is sent only to the service that issued it. Each adapter has a list of allowed hosts
(R53).

**S9.** Override variables are removed from subscription launches and reported by name (R34).

**S10.** Addresses match folder names exactly, including case (R5).

**S11.** An ambiguous address is refused (R4).

**S12.** A login is never copied (R20).

**S13.** Privacy. Emails are hidden by default on every new screen and listing. No token is printed,
logged or cached. There is no telemetry, and no network destination beyond each provider's own
services and 127.0.0.1 for Ollama.

**S14.** Proof dates. A provider is enabled only when its proof date is set in the code. A date is
earned by one recorded run, by hand on a real account, of `add`, `login`, `as` and `ls --refresh`.
Blanking the date disables the provider everywhere, as `SWITCH_PROVEN_ON` does for the switch keys
(gotcha #19). Setting `COCKPIT_UNPROVEN=1` enables unproven providers for development, with a warning
on every command.

**S15.** Text typed into a shell is built only from validated, quoted parts (R87), and it reaches tmux
and AppleScript as an argument, never inside the text of a command or a script (R89).

## 16. Non-functional requirements

**N1.** macOS only in this version. Code that is not about one provider avoids macOS-only calls, so
that the file-based providers could later run on Linux.

**N2.** Python 3.9 and the standard library only, as now. `/usr/bin/python3` is what the watcher runs.
Every module sets `sys.dont_write_bytecode = True` (gotcha #13).

**N3.** New tool files follow the existing pattern: hyphenated file names, loaded by path, siblings
found through `os.path.expanduser("~/.claude-profiles/...")`, so that `tests/run_dev.py` can point the
tests at the copy in `tools/`.

**N4.** Speed. `cockpit as` reaches `exec` within 300 ms (R36). `cockpit ls` without `--refresh` starts
at most one vendor program in total (Claude's `auth status`, for the fleet marker), plus keychain
reads, and finishes within a few seconds for ten profiles. Every refresh is bounded by its provider's
time limit.

**N5.** Every subprocess has a time limit and is reaped. A program started for a reading, such as
`codex app-server`, runs in its own process group, which is ended after the read, so that no child
outlives the command (gotcha #55).

**N6.** Every fact about a vendor's tool that the code relies on is recorded in `docs/gotchas.md` as a
numbered entry with its date and the vendor program's version, and code comments cite it.

**N7.** The documentation is updated in each phase: the README command table, a section of
`how_it_works.md`, a section per provider in `safety.md` that lists what each command can change, and
`CHANGELOG.md` in its existing format.

## 17. Assumptions to measure

These are facts about other vendors' tools, as found on 2026-10-13 in vendor documentation and
third-party sources. None has been measured on a real account in this project. Each one is measured
before the requirements that need it are built. The result goes into `docs/gotchas.md`, and the
status column here is updated.

| ID | Assumption | Source | Needed by | Status |
|---|---|---|---|---|
| A1 | `CODEX_HOME` moves all of Codex's per-user files, `auth.json` included. | OpenAI docs (for file storage) | R14, R78 | Measured 2026-10-13: a sign-in with `CODEX_HOME` set left `~/.codex` untouched (gotcha #57). |
| A2 | Where Codex keeps a login when `cli_auth_credentials_store` is not set (its values are `file`, `keyring`, `auto` and `ephemeral`), and how its keychain entries are named: one per `CODEX_HOME`, or one per machine. | OpenAI docs (the values only) | R78 | Measured: by default in `auth.json`, with no keychain item (2026-10-13). In keychain mode, one item per home, named after a hash of its path (2026-10-22). Gotcha #59. |
| A3 | Codex refresh tokens are single-use. | OpenAI's CI/CD guide implies rotation; third-party sources say single-use | R20 | Measured 2026-10-13 and 2026-10-22: every renewal changed the refresh token (gotcha #60). |
| A4 | Two logins for the same ChatGPT account, in two homes, both keep working, including after either renews. One third-party source says a new sign-in revokes the older login. | third-party | R29 | Measured 2026-10-13 to 2026-10-22: both kept working, through renewals and nine days later (gotcha #60). |
| A5 | Two Codex processes in one home both survive a renewal. | OpenAI warns against sharing `auth.json` between concurrent jobs | R39 | Measured 2026-10-22: both survived (gotcha #60). |
| A6 | The app-server method `account/read`, or `codex login status`, tells which account is signed in without spending usage. | app-server README (a mirror of OpenAI's repository) | R26, R78 | Measured 2026-10-13: `account/read` gives the type, plan and email; `codex login status` gives only the method (gotcha #61). |
| A7 | `account/rateLimits/read` returns the 5-hour and weekly windows with percent used and reset times, spends nothing and opens no window. Also: the handshake it needs. | OpenAI pull request #5302 | R55 | Measured 2026-10-13 on the free plan: one 30-day window, and reads spend nothing. Paid plans' 5-hour and weekly windows are unmeasured (gotcha #61). |
| A8 | Which items of `~/.codex` stay links after Codex writes to them: `AGENTS.md`, `prompts/`, `skills/`, `config.toml`, `sessions/`, `history.jsonl`. | none | R18 | Measured 2026-10-13: all six stay links (gotcha #64). |
| A9 | `codex login` honours `BROWSER`. | none | R27 | Measured 2026-10-13: it does not (gotcha #63). |
| A10 | Which environment variables change the credential or the server Codex uses. The Codex 0.162.1 binary holds nineteen candidate names (`scripts/measure_codex.py` lists them). Two of them, `OPENAI_FEDERATION_RULE_ID` and `OPENAI_IDENTITY_TOKEN_FILE`, stop `codex app-server` from starting at all. | the Codex binary, OpenAI docs | R34 | Measured 2026-10-13 (gotcha #62). |
| A11 | Codex, Grok and Antigravity create files in any home folder they are pointed at. | measured for Claude Code (gotcha #30) | R15 | Codex measured 2026-10-13 (gotcha #57). Grok and Antigravity unmeasured. |
| A12 | Where the official Codex installers put the program. | none | R32 | Measured 2026-10-13: the Homebrew cask links `/opt/homebrew/bin/codex` to `/opt/homebrew/Caskroom/codex/<version>/bin/codex`, a native arm64 program. The npm package is unmeasured. Homebrew upgraded it to 0.163.0 on 2026-10-20. |
| A13 | Codex keeps a separate app-server daemon for each `CODEX_HOME`, so sessions of two profiles never share a daemon or its login. | measured | R33, R39, R93 | Measured 2026-10-13 to 2026-10-23: the interactive app starts a background daemon per home, which outlives the session and updates itself; its socket is named after the home's path (gotcha #58). |
| A20 | `GROK_HOME` moves `auth.json` and `mcp_credentials.json` with the rest of `~/.grok`. | xAI docs (the config file only) | R79 | unmeasured |
| A21 | How to read which account a Grok profile holds without spending usage. No command is documented. | none | R26, R79 | unmeasured |
| A22 | Two Grok logins for one account coexist; whether Grok's refresh tokens are single-use. | none | R29 | unmeasured |
| A23 | Two Grok processes in one home survive a renewal; which items of `~/.grok` can be shared. | xAI docs: changes to `auth.json` apply on the next request | R18, R39 | unmeasured |
| A24 | `grok login` honours `BROWSER`. | none | R27 | unmeasured |
| A25 | A usage source for Grok subscriptions exists. Third-party tools read an undocumented endpoint. | third-party | R56 | unmeasured |
| A26 | Where the official installer puts `grok`. | install guides | R32 | unmeasured |
| A27 | Which environment variables change the credential or the server Grok uses. xAI documents the precedence: a per-model key, then the session, then `XAI_API_KEY`. | xAI docs | R34 | unmeasured |
| A30 | Antigravity CLI: the program's name and path, where it keeps its login, and whether more than one account is possible. Reportedly one keychain entry per machine. | third-party | R21, R80 | unmeasured |
| A31 | The Gemini CLI still accepts `GEMINI_API_KEY` for paid API use; whether its home folder can be moved. | Google's transition announcement | R81 | unmeasured |
| A40 | Local Ollama's API reports loaded models and memory use without side effects (`/api/ps`, `/api/version`); where the installer puts `ollama`. | Ollama docs | R32, R56 | unmeasured |
| A41 | An Ollama Cloud sign-in belongs to the machine's Ollama server, so there is one account per machine. | none | R21, R82 | unmeasured |
| A50 | The override variable list for the installed Claude Code version. | none | R34 | unmeasured |
| A60 | Each vendor's terms allow keeping several of your own accounts signed in on one machine, and starting the vendor's CLI from a wrapper. Google has reportedly said that using the Gemini CLI's sign-in from third-party tools breaks its policy. | third-party | S14 | not reviewed |
| A70 | iTerm2's AppleScript (`create tab with default profile`, `write text`, a session's `name`) and Terminal.app's (`do script`, a tab's `custom title`) work as R85 to R90 need, on the installed versions. Also: when macOS asks for Automation permission, and the exact error when it is refused. | the apps' scripting dictionaries | R85, R86, R90, R91 | unmeasured |
| A71 | tmux's `new-window -P -F '#{pane_id}' -c <folder> -n <name>` and `send-keys -l` work as R85, R86 and R89 need, on the installed tmux version. | tmux manual | R85, R86, R89 | unmeasured |
| A72 | `osascript` passes the arguments that follow the script to its `run` handler unchanged. | measured | R89 | Measured 2026-10-13 on macOS 26.5.2: identical bytes for quotes, `$`, backticks, backslashes, a tab and a newline, with the script given by `-e` and on stdin. |

## 18. Decisions

| ID | Decision | Status |
|---|---|---|
| D1 | The command is `cockpit`. | Decided 2026-10-13 |
| D2 | Addresses are `<provider>/<profile>`, and a bare `<profile>` is accepted when it is unambiguous. | Decided 2026-10-13 |
| D3 | `cockpit as` starts a session in the current terminal. `cockpit open` opens a new terminal for each address and starts the sessions there (R83 to R92). | Decided 2026-10-13 |
| D4 | The profile root is `~/.cockpit/profiles`. | Decided 2026-10-13 |
| D5 | Provider ids: `claude`, `codex`, `grok`, `antigravity`, `gemini`, `ollama`. | Decided 2026-10-13 |
| D6 | `cockpit ls` hides emails unless `--emails` is given. | Decided 2026-10-13 |
| D7 | Close the case hole of R5 in `claude-as` too. It changes an existing command, so it is a separate change. | Proposed |
| D8 | Whether `claude-as` should also remove override variables (R34). | Open |
| D9 | Whether to rename the project. Not needed for this version. | Open |
| D10 | Move Claude usage refreshes to the direct read of gotcha #45. Independent of this work. | Optional |

## 19. Done means

- Every requirement of an enabled provider is met and covered by a test. Where a test cannot cover it
  (a real sign-in, a real launch), the recorded hand run checks it.
- Every enabled provider has its proof date (S14).
- The existing test files pass unchanged, and every existing screen state renders exactly as before.
- The documentation listed in N7 is updated.
