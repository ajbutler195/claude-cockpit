#!/usr/bin/env python3
"""Render every claude-cockpit screen state as plain text, from fake accounts only.

    pip install pyte
    python3 tests/render_cockpit_states.py --out <folder> [--state NAME] [--cols N] [--rows N]
    python3 tests/render_cockpit_states.py --list        the states
    python3 tests/render_cockpit_states.py --stubs       every stubbed spawn, with file:line

Writes one file per state per width, <state>_<cols>.txt (<state>_<cols>x<rows>.txt when
--rows is not 40), each exactly the rows a terminal of that size would show: trailing
spaces stripped, nothing else edited. Without --state it renders every state, at 140,
100 and 80 columns. A state that exists only to show a difference (swap_on) writes no
file when its screen turns out identical to the state it is compared with.

WHY THIS EXISTS
  The cockpit is a curses screen whose layout changes with account state (fresh, stale,
  timed out, signed out, blocked by a per-model limit) and with terminal width. The only
  other way to look at those states is to wait for real accounts to fall into them, on a
  machine that holds real logins, and a screenshot taken there shows real addresses and
  real usage. This harness draws each state from invented data, so the screens can be
  reviewed, compared between versions and published, and no login is ever involved.

WHAT IS REAL AND WHAT IS FAKE
  Real: the cockpit. install.sh copies the tools into a throwaway home folder, and a
  child process loads claude-cockpit.py from there and starts it with the same three
  statements as that file's own `if __name__ == "__main__":` block.
  Fake: the accounts (alpha, beta, gamma, delta at test.invalid), their usage numbers,
  and every answer the `claude` binary, the keychain or the server would have given.

SAFETY DESIGN (four layers, so that no single mistake can reach a real login)
  1. Demo HOME. Every process this harness starts gets HOME set to a new temp folder
     and an environment built from nothing, so CLAUDE_CONFIG_DIR and the rest of the
     caller's variables never reach it. The tools find everything through `~`, and the
     macOS `security` command finds the login keychain through HOME, so the real
     profiles and the real keychain are not addressable from the child. The parent
     refuses to start a child whose HOME is the real one; the child refuses to run
     unless HOME carries this run's marker.
  2. Every spawn is stubbed. Each function in the cockpit and the two modules it loads
     (claude-switch.py, claude-swap.py) that would run `claude`, run `security` or open
     a connection is replaced by a stub that answers from the fixture. The list is the
     STUBBED table, and it is not taken on trust: on every launch the tools' source is
     parsed and a spawn found in a function without a stub fails the run, naming the
     file and line. `--stubs` prints the table with today's line numbers.
  3. Escape guard. Before any tool is loaded, subprocess.run/Popen/check_output (and
     the rest of that family), os.system/fork/exec/spawn, urllib.request.urlopen and
     socket connect are replaced by functions that write one line to a log and raise.
     A stub that was missed therefore cannot start a process: it fails, and the log
     says where. A capture with a non-empty log is rejected and no file is written.
     COCKPIT_CLAUDE_BIN points at /usr/bin/true so that even a spawn that got past
     both layers would run a program that does nothing.
  4. Only e, r and q are ever typed. Digits and u start an account switch, p sends real
     messages and a rewrites a settings file, so the one function that types a key
     refuses anything else.

LIMITS OF THE CAPTURE
  Plain text only: colour and bold are not recorded. Reset times and countdowns are
  computed from the clock, so they differ from run to run.
  pyte is not a complete terminal. It drops any control sequence it does not implement
  without a word, and the screen it then shows looks like a cockpit bug: two frames
  interleaved, text apparently written over other text. The cockpit's output uses two
  such sequences today (scroll up, scroll down); make_terminal() adds them and explains
  how that was found. Any OTHER sequence pyte does not implement voids the capture, so
  a wrong screen is never written as if it were right. Bytes that are not UTF-8 would
  show as U+FFFD; the summary line counts them. Measured on this code: rules and bars
  come out whole, with no U+FFFD.

Needs `pyte` in the interpreter that runs this file (the parent). The child that runs
the cockpit needs only the standard library and uses /usr/bin/python3 when it exists,
because Python 3.9 is the interpreter the tools support. This whole file therefore
stays Python 3.9 compatible.
"""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import pty
import pwd
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import traceback

sys.dont_write_bytecode = True   # a __pycache__ folder next to the tools looks like a profile to them

THIS_FILE = os.path.abspath(__file__)
REPO = os.path.dirname(os.path.dirname(THIS_FILE))

ALLOWED_KEYS = ("e", "r", "q")
WIDTHS = (140, 100, 80)
ROWS = 40
# Inside the demo home but OUTSIDE .claude-profiles: the tools treat sub-folders of
# .claude-profiles as candidate profiles, so nothing of ours may live there.
HARNESS_DIR = ".render_harness"
MARKER = "demo_home_marker"
TOKEN_ENV = "COCKPIT_RENDER_TOKEN"
QUIET_SEC = 1.0          # the screen must stop changing for this long before it is read
FIRST_WAIT_SEC = 2.0     # and never be read earlier than this after launch
SETTLE_LIMIT_SEC = 25.0
# The footer counts seconds since the last refresh ("updated 0m07s ago"), so the screen
# emits new bytes every second for ever. "Stable" has to mean stable apart from that
# counter, or no refreshed screen would ever be captured.
TICK = re.compile(r"updated \d+m\d\ds ago")
# One CSI control sequence: ESC [, parameter bytes, intermediate bytes, one final byte.
# The final byte names the command, which is all capture() needs to know.
CSI_FINAL = re.compile(rb"\x1b\[[0-?]*[ -/]*([@-~])")

FLEET_EMAIL = "alpha@test.invalid"
# What the server calls the per-model weekly budget today. The cockpit prints whatever
# name the payload carries, so this string becomes the third meter's column heading.
SCOPED_MODEL = "Fable"


# ---------------------------------------------------------------------------
# Fixtures: the fake accounts and the states built from them
# ---------------------------------------------------------------------------

def _account(name, five, seven, scoped, five_in_min, week_in_min, binding=None):
    """One signed-in fake account. Percentages are what the screen should show;
    *_in_min are how far in the future each window resets (285 and 80, not 300 and 90:
    the countdown is rounded to whole hours and must not flip while a run is going)."""
    return {"name": name, "email": name + "@test.invalid", "five": five, "seven": seven,
            "scoped": scoped, "five_in_min": five_in_min, "week_in_min": week_in_min,
            "binding": binding, "age_min": 1, "has_login": True, "signed_in": True,
            "refresh": "ok", "login_days": 25, "live": None}


FOUR = [
    _account("alpha", 62, 48, 35, 130, 4560),                            # the account in use
    _account("beta", 4, 12, 9, 285, 7320),                               # lots of room
    _account("gamma", 8, 22, 100, 80, 1800, binding="weekly_scoped"),    # per-model limit hit
    _account("delta", 97, 71, 64, 50, 3060),                             # session nearly gone
]


def _four(**changes):
    accounts = [dict(a) for a in FOUR]
    for a in accounts:
        a.update(changes.get(a["name"], {}))
    return accounts


def _never_signed_in(name):
    """What new-profile.sh leaves behind: a profile folder whose .claude.json has no
    identity and no usage cache, and no login in its keychain slot."""
    return {"name": name, "email": None, "has_login": False, "signed_in": False,
            "refresh": "failed", "login_days": 25, "live": None}


# The pace and accuracy figures a week of meter history would have produced. They are
# the only invented parts of forecast.json; see child_forecast().
FORECAST_PACES = {"7d": {"v": 1.9, "src": "est"},
                  "48h": {"v": 2.8, "src": "meters"},
                  "24h": {"v": 4.3, "src": "meters"}}
FORECAST_ACTUAL_POINTS = 58.0

# How long a stubbed refresh takes. Not zero on purpose: a real refresh takes seconds,
# so a real launch always paints the numbers from disk first and repaints when the
# refresh comes back. With an instant stub the first paint is sometimes skipped, and
# the capture would exercise a different path from one run to the next.
REFRESH_SEC = 0.4

# A "borrowed" account is one whose own login was moved into the shared fleet slot by
# claude-swap. Its profile slot is then empty and the cockpit reads its numbers live
# from the server instead of from disk. On disk it keeps the numbers from the moment
# of the move, hence the old age.
_BORROWED = {"has_login": False, "age_min": 38}

STATES = {
    "empty": {
        "about": "no profile folders at all: the first launch after install.sh",
        "accounts": []},
    "one_new": {
        "about": "one profile (alpha) made by new-profile.sh and never signed in",
        "accounts": [_never_signed_in("alpha")]},
    "populated": {
        "about": "four signed-in accounts with fresh numbers, emails hidden",
        "accounts": _four()},
    "populated_emails_shown": {
        "about": "the populated screen after pressing e",
        "accounts": _four(), "keys": "e"},
    "stale": {
        "about": "beta's numbers are 40 minutes old, gamma's refresh timed out",
        "accounts": _four(beta={"age_min": 40}, gamma={"refresh": "timeout"})},
    "signed_out": {
        "about": "delta is signed out: its refresh fails and its login check says no",
        "accounts": _four(delta={"signed_in": False, "refresh": "failed"})},
    "forecast": {
        "about": "the populated screen with a filled-in forecast panel",
        "accounts": _four(), "forecast": "week_of_history"},
    "swap_on": {
        "about": "the populated screen with swap_enabled true in autoswitch.json",
        "accounts": _four(), "autoswitch": {"swap_enabled": True}, "same_as": "populated"},

    # ----- beyond the eight above: states a user meets that the list did not name -----
    "refreshing": {
        "extra": True,
        "about": "the `stale` accounts as first drawn from disk, start-up refresh still running",
        "accounts": _four(beta={"age_min": 40}, gamma={"refresh": "timeout"}), "refresh_delay": 6},
    "auto_on_no_watcher": {
        "extra": True,
        "about": "populated, after `a` turned auto-switch on with no watcher installed",
        "accounts": _four(), "autoswitch": {"enabled": True}},
    "login_expiring": {
        "extra": True,
        "about": "populated, but alpha's login reaches its renewal deadline in 36 hours",
        "accounts": _four(alpha={"login_days": 1.5})},
    "borrowed": {
        "extra": True,
        "about": "alpha is on the fleet with its own login (after a claude-swap), live numbers",
        "accounts": _four(alpha=dict(_BORROWED, live="numbers"))},
    "rate_limited": {
        "extra": True,
        "about": "as `borrowed`, but the server is rate-limiting usage reads for alpha",
        "accounts": _four(alpha=dict(_BORROWED, live="limited"))},
    "forecast_first_day": {
        "extra": True,
        "about": "populated, with the forecast the watcher writes before it has any history",
        "accounts": _four(), "forecast": "no_history"},
}


def _iso(t0, minutes):
    # An explicit +00:00 offset: the cockpit parses these with datetime.fromisoformat,
    # which on Python 3.9 rejects a trailing "Z".
    moment = datetime.datetime.fromtimestamp(t0, datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    return moment.isoformat()


def _usage_cache(account, t0, now):
    """cachedUsageUtilization in the shape claude-cockpit.py parse_usage() reads.
    SYNC WARNING: parse_usage reads utilization.five_hour / seven_day (utilization,
    resets_at) and utilization.limits[] (kind, percent, severity, resets_at, is_active,
    scope.model.display_name), plus fetchedAtMs. Change this when that function changes."""
    five_at, week_at = _iso(t0, account["five_in_min"]), _iso(t0, account["week_in_min"])

    def limit(kind, percent, resets_at, **extra):
        # `severity` is stored by parse_usage but never drawn, so these words are
        # placeholders, not a claim about the server's vocabulary.
        entry = {"kind": kind, "percent": percent, "resets_at": resets_at,
                 "severity": "exceeded" if percent >= 100 else "warning" if percent >= 70 else "normal",
                 "is_active": account["binding"] == kind}
        entry.update(extra)
        return entry

    return {
        "fetchedAtMs": int((now - account["age_min"] * 60) * 1000),
        "accountUuid": "uuid-" + account["name"],
        "utilization": {
            "five_hour": {"utilization": account["five"], "resets_at": five_at},
            "seven_day": {"utilization": account["seven"], "resets_at": week_at},
            "limits": [
                limit("session", account["five"], five_at),
                limit("weekly_all", account["seven"], week_at),
                limit("weekly_scoped", account["scoped"], week_at,
                      scope={"model": {"display_name": SCOPED_MODEL}}),
            ],
        },
    }


def _write_json(path, value):
    with open(path, "w") as fh:
        json.dump(value, fh, indent=2)


def write_state(home, spec, t0):
    """Put the demo home into one state. Everything a previous state wrote is removed
    first, so no state can inherit a row, a forecast or a setting from the one before."""
    base = os.path.join(home, ".claude-profiles")
    hdir = os.path.join(home, HARNESS_DIR)
    now = time.time()
    for name in os.listdir(base):
        if os.path.isdir(os.path.join(base, name)):
            shutil.rmtree(os.path.join(base, name))
    for name in ("forecast.json", "forecast_history.jsonl", "autoswitch.log", ".usage-cache.json"):
        _remove(os.path.join(base, name))
    for name in ("escapes.log", "stubs.log", "crash.txt", "scenario.json"):
        _remove(os.path.join(hdir, name))

    # Start from the settings file exactly as install.sh wrote it, then apply the
    # state's changes, so a default state shows the shipped defaults.
    settings = json.load(open(os.path.join(hdir, "autoswitch.as_installed.json")))
    settings.update(spec.get("autoswitch") or {})
    _write_json(os.path.join(base, "autoswitch.json"), settings)

    # The fleet's own config file: the header's "FLEET:" address is read from here.
    _write_json(os.path.join(home, ".claude.json"),
                {"hasCompletedOnboarding": True,
                 "oauthAccount": {"emailAddress": FLEET_EMAIL, "accountUuid": "uuid-alpha"}})

    profiles, live_usage = {}, {}
    for account in spec["accounts"]:
        record = {"hasCompletedOnboarding": True}
        if account["email"]:
            record["oauthAccount"] = {"emailAddress": account["email"],
                                      "accountUuid": "uuid-" + account["name"]}
            record["cachedUsageUtilization"] = _usage_cache(account, t0, now)
            if account["live"] == "numbers":
                # What the server would return for this account right now: the same
                # payload as the disk cache holds, which is the shape the real
                # endpoint has (claude-swap.py live_usage_raw docstring).
                live_usage[account["name"]] = record["cachedUsageUtilization"]["utilization"]
        os.makedirs(os.path.join(base, account["name"]))
        _write_json(os.path.join(base, account["name"], ".claude.json"), record)
        profiles[account["name"]] = {k: account[k] for k in
                                     ("email", "has_login", "signed_in", "refresh", "login_days")}

    scenario = {"fleet_email": FLEET_EMAIL, "profiles": profiles, "live_usage": live_usage,
                "refresh_delay": spec.get("refresh_delay", REFRESH_SEC)}
    if spec.get("forecast") == "week_of_history":
        scenario["forecast"] = {
            "paces": FORECAST_PACES,
            "accuracy": {
                "made_at": now - 26 * 3600, "actual_points": FORECAST_ACTUAL_POINTS,
                "by_pace": {n: {"predicted": p["v"] * 24,
                                "miss_pct": (p["v"] * 24 - FORECAST_ACTUAL_POINTS) / FORECAST_ACTUAL_POINTS * 100}
                            for n, p in FORECAST_PACES.items()}}}
    _write_json(os.path.join(hdir, "scenario.json"), scenario)


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _append(path, text):
    # O_APPEND and one write per line: the cockpit refreshes on worker threads, and two
    # of them logging at once must not interleave.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, text.encode("utf-8", "replace"))
    finally:
        os.close(fd)


def _read_lines(path):
    try:
        return [line for line in open(path, encoding="utf-8").read().splitlines() if line]
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# CHILD: runs under the demo HOME. Guard first, then the tool, then the stubs.
# ---------------------------------------------------------------------------

class EscapedCall(RuntimeError):
    """A process spawn or a network call reached the real primitive."""


def install_escape_guard(log_path):
    """Make every way of starting a process or opening a connection log and raise.

    WHY it raises instead of only logging: the point is that nothing real can happen
    even when a stub is missing. The cockpit swallows most exceptions, so the log file
    is the evidence the parent reads; the raise is what stops the call."""
    import http.client
    import socket
    import urllib.request

    def deny(label, skip=0):
        def denied(*args, **kwargs):
            what = repr(args[skip])[:200] if len(args) > skip else ""
            _append(log_path, "ESCAPED %s %s\n" % (label, what))
            raise EscapedCall("%s was called: %s" % (label, what))
        return denied

    for name in ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
        setattr(subprocess, name, deny("subprocess." + name))
    try:
        # The C function every subprocess call ends in. Guarded as well so that code
        # holding its own reference to a subprocess function still cannot fork.
        import _posixsubprocess
        _posixsubprocess.fork_exec = deny("_posixsubprocess.fork_exec")
    except ImportError:
        pass
    for name in ("system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
                 "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
                 "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe"):
        if hasattr(os, name):
            setattr(os, name, deny("os." + name))
    urllib.request.urlopen = deny("urllib.request.urlopen")
    urllib.request.OpenerDirector.open = deny("urllib.request.OpenerDirector.open", skip=1)
    http.client.HTTPConnection.connect = deny("http.client.HTTPConnection.connect")
    socket.create_connection = deny("socket.create_connection")
    socket.socket.connect = deny("socket.socket.connect", skip=1)
    socket.socket.connect_ex = deny("socket.socket.connect_ex", skip=1)


def _load_tool(modname, filename):
    """Load one tool from the demo home the way the tests and the tools themselves do:
    the file names are hyphenated, so a normal import cannot reach them."""
    path = os.path.join(os.path.expanduser("~/.claude-profiles"), filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Every function in the three files the cockpit process loads that starts a process or
# opens a connection, and is therefore replaced by install_stubs(). Dotted names are
# attributes of the module (UI.confirm is the method confirm of class UI).
# SYNC WARNING: this table is checked against the tools' own source on every launch by
# audit_stubs(): a spawn inside a function that is not listed here fails the capture
# and names the file and line. So when a tool gains a new spawn, add the function here
# AND give it a stub in install_stubs(); the audit says which one is missing.
STUBBED = {
    "claude-cockpit.py": ("refresh_one", "signed_in", "UI.bg_ping", "UI.confirm"),
    "claude-switch.py": ("read_cred_hex", "authed_email", "_login_supports_email", "_run_login",
                         "_process_lanes"),
    "claude-swap.py": ("Keychain.read", "Keychain.write", "Keychain.delete", "_whoami_call",
                       "live_usage_raw", "probe"),
}
# Spawns written inline in a function that cannot be replaced without replacing the
# program: UI.run is the cockpit's key loop, and the `u` key's undo is coded inside it.
# Three other things keep that line from running: the parent never types `u`, the
# function it would call first (undo_fleet) is blocked, and the escape guard.
INLINE_SPAWNS = {("claude-cockpit.py", "UI.run")}

SPAWN_CALL = re.compile(r"(^|\.)(subprocess\.(run|Popen|call|check_call|check_output|getoutput|getstatusoutput)"
                        r"|urlopen|os\.(system|popen|fork\w*|exec\w*|spawn\w*|posix_spawn\w*))$")


def spawn_sites(path):
    """[(line, enclosing function, call text)] for every spawn or network call in a file.
    Read from the syntax tree, not by grep: a grep also matches the many comments in
    these tools that mention subprocess, and cannot say which function a call is in.

    It recognises the calls as these tools spell them (subprocess.run, os.system,
    urllib.request.urlopen). A call made through an alias (`from subprocess import run`)
    would not be listed here. That gap is why this audit is a second check and not the
    safety mechanism: the escape guard replaces the functions themselves, so it stops a
    call however it is spelled."""
    import ast
    source = open(path, encoding="utf-8").read()
    tree = ast.parse(source)
    spans = []

    def collect(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + [child.name]
                spans.append((child.lineno, child.end_lineno, ".".join(name)))
                collect(child, name)
            else:
                collect(child, prefix)

    def dotted(node):
        # The called name rebuilt from the tree (subprocess.run, urllib.request.urlopen).
        # Not ast.get_source_segment here: it re-splits the whole file on every call,
        # and asking it about every call in three long files took 5 seconds per launch.
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        return ".".join(reversed(parts + [node.id])) if isinstance(node, ast.Name) else ""

    collect(tree, [])
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = dotted(node.func)
        if not SPAWN_CALL.search(called):
            continue
        inside = [s for s in spans if s[0] <= node.lineno <= s[1]]
        owner = min(inside, key=lambda s: s[1] - s[0])[2] if inside else "<module level>"
        first = " ".join((ast.get_source_segment(source, node.args[0]) or "").split()) if node.args else ""
        sites.append((node.lineno, owner, "%s(%s" % (called, first[:60])))
    return sorted(sites)


def audit_stubs(tools_dir):
    """Compare STUBBED with what the tools' source really contains.
    Returns (rows, missing): one row per spawn site, and one sentence per site that
    sits in a function with no stub."""
    rows, missing = [], []
    for filename, stubbed in STUBBED.items():
        for line, owner, call in spawn_sites(os.path.join(tools_dir, filename)):
            # A spawn in a helper nested inside a stubbed function (UI.bg_ping.work)
            # is gone with its parent.
            covered = next((s for s in stubbed if owner == s or owner.startswith(s + ".")), None)
            if covered:
                rows.append((filename, line, covered, call, "stubbed"))
            elif (filename, owner) in INLINE_SPAWNS:
                rows.append((filename, line, owner, call, "inline, see INLINE_SPAWNS"))
            else:
                missing.append("%s:%d  %s  calls %s  and has no stub" % (filename, line, owner, call))
    return rows, missing


def _attribute(module, dotted):
    target = module
    for part in dotted.split("."):
        target = getattr(target, part)
    return target


def install_stubs(ck, scenario, hdir):
    """Replace every function listed in STUBBED, in every loaded copy of its module.

    Two kinds of stub. An ANSWERING stub stands in for something the screen needs
    (who is signed in, did the refresh work) and replies from the scenario. A BLOCKED
    stub stands in for an action this harness must never take (a login, a keychain
    write, a ping); reaching one is recorded as an escape, because only a forbidden key
    could have led there."""
    profiles = scenario["profiles"]
    fleet_email = scenario.get("fleet_email")
    delay = scenario.get("refresh_delay", 0)
    stub_log = os.path.join(hdir, "stubs.log")
    escape_log = os.path.join(hdir, "escapes.log")
    now_ms = time.time() * 1000
    slot_owner = {ck.cswitch.slot_for(os.path.join(ck.BASE, name)): name for name in profiles}
    # The cockpit loads claude-switch.py once itself and claude-swap.py loads it again,
    # so there are two separate module objects of it and both must be stubbed.
    loaded = {"claude-cockpit.py": [ck], "claude-switch.py": [ck.cswitch, ck.cswap.cswitch],
              "claude-swap.py": [ck.cswap]}
    originals = {(filename, i, name): _attribute(module, name)
                 for filename, modules in loaded.items()
                 for i, module in enumerate(modules) for name in STUBBED[filename]}

    def said(text):
        _append(stub_log, text + "\n")

    def blocked(label, result):
        def stub(*args, **kwargs):
            _append(escape_log, "BLOCKED-ACTION %s\n" % label)
            return result
        return stub

    def login_for(email, days):
        # The record shape the tools read from a keychain slot. The token strings are
        # labels, never credentials: nothing is sent anywhere.
        return {"claudeAiOauth": {"accessToken": "demo-access:" + email,
                                  "refreshToken": "demo-refresh:" + email,
                                  "expiresAt": int(now_ms + 6 * 3600 * 1000),
                                  "refreshTokenExpiresAt": int(now_ms + days * 86400 * 1000)}}

    def slot_contents(slot):
        if slot == ck.cswap.SHARED_SLOT:
            return login_for(fleet_email, 25) if fleet_email else None
        profile = profiles.get(slot_owner.get(slot) or "")
        if profile and profile.get("has_login"):
            return login_for(profile["email"], profile.get("login_days", 25))
        return None

    # --- claude-switch.py -------------------------------------------------------
    def read_cred_hex(slot):                       # real one runs `security find-generic-password`
        blob = slot_contents(slot)
        return json.dumps(blob).encode().hex() if blob else None

    def authed_email(config_dir=None):             # real one runs `claude auth status`
        if not config_dir:
            said("cswitch.authed_email fleet -> %s" % fleet_email)
            return fleet_email
        profile = profiles.get(os.path.basename(config_dir)) or {}
        return profile.get("email") if profile.get("signed_in") else None

    for sw in loaded["claude-switch.py"]:
        sw.read_cred_hex = read_cred_hex
        sw.authed_email = authed_email
        sw._login_supports_email = blocked("cswitch._login_supports_email", False)   # `claude auth login --help`
        sw._run_login = blocked("cswitch._run_login", 2)                              # `claude auth login`
        sw._process_lanes = blocked("cswitch._process_lanes", None)                   # `bash -c "ps ..."`
        # These two spawn nothing themselves; they drive the login above. Blocked as
        # well so that a digit or `u` could not get as far as printing a login prompt.
        sw.fleet_switch = blocked("cswitch.fleet_switch", 1)
        sw.undo_fleet = blocked("cswitch.undo_fleet", 1)

    # --- claude-swap.py ---------------------------------------------------------
    def keychain_read(self, slot):                 # real one runs `security find-generic-password`
        return slot_contents(slot)

    def whoami_call(token):                        # real one opens the server's profile URL
        if not token.startswith("demo-access:"):
            return None
        email = token.split(":", 1)[1]
        return (email, "uuid-" + email.split("@")[0])

    def live_usage_raw(oauth, key=None, max_age=0):   # real one opens the server's usage URL
        # None is what the real function returns while the server is rate-limiting
        # and no earlier reading is cached; the cockpit then shows the row as limited.
        payload = (scenario.get("live_usage") or {}).get(key)
        said("cswap.live_usage_raw %s -> %s" % (key, "numbers" if payload else "none"))
        return payload

    ck.cswap.Keychain.read = keychain_read
    ck.cswap.Keychain.write = blocked("cswap.Keychain.write", False)      # `security -i` / `security add-generic-password`
    ck.cswap.Keychain.delete = blocked("cswap.Keychain.delete", False)    # `security delete-generic-password`
    ck.cswap._whoami_call = whoami_call
    ck.cswap.live_usage_raw = live_usage_raw
    ck.cswap.probe = blocked("cswap.probe", False)                        # sends one real message through `claude -p`

    # --- claude-cockpit.py ------------------------------------------------------
    def refresh_one(name):                         # real one runs `claude -p "/usage"`
        if delay:
            time.sleep(delay)
        # SYNC WARNING: this branch repeats the opening statements of the real
        # refresh_one in claude-cockpit.py (the has_claude_login test). It is kept so
        # that a profile with no login of its own still goes through the real
        # refresh_borrowed(), which is what turns a never-signed-in profile into the
        # SIGNED OUT row a new user sees, and a swapped-in account into a borrowed row.
        if not ck.cswitch.has_claude_login(ck.cswitch.slot_for(os.path.join(ck.BASE, name))):
            ck.REFRESH_STATUS[name] = ck.refresh_borrowed(name)
        else:
            ck.LIVE.pop(name, None)
            ck.REFRESH_STATUS[name] = (profiles.get(name) or {}).get("refresh", "failed")
        said("cockpit.refresh_one %s -> %s" % (name, ck.REFRESH_STATUS[name]))
        return ck.REFRESH_STATUS[name]

    def signed_in(name):                           # real one runs `claude auth status`
        answer = bool((profiles.get(name) or {}).get("signed_in"))
        said("cockpit.signed_in %s -> %s" % (name, answer))
        return answer

    ck.refresh_one = refresh_one
    ck.signed_in = signed_in
    ck.UI.bg_ping = blocked("cockpit.UI.bg_ping", None)     # runs claude-ping.py, which sends real messages
    ck.UI.confirm = blocked("cockpit.UI.confirm", None)     # runs claude-swap.py, then a browser login

    # The table says these are stubbed; make sure each one really was replaced, in
    # every loaded copy, so the table and this function cannot drift apart.
    still_real = ["%s %s" % (filename, name) for (filename, i, name), original in originals.items()
                  if _attribute(loaded[filename][i], name) is original]
    if still_real:
        raise RuntimeError("listed in STUBBED but not replaced by install_stubs(): " + ", ".join(still_real))


def child_ui(scenario, hdir):
    import curses
    # Before the tool is loaded at all: a spawn this harness has no stub for stops the
    # run here, with its file and line, instead of surfacing later as a guard hit.
    missing = audit_stubs(os.path.expanduser("~/.claude-profiles"))[1]
    if missing:
        raise RuntimeError("a tool starts a process or opens a connection in a function "
                           "render_cockpit_states.py does not stub (add it to STUBBED and to "
                           "install_stubs):\n  " + "\n  ".join(missing))
    ck = _load_tool("ckpit", "claude-cockpit.py")
    install_stubs(ck, scenario, hdir)
    # SYNC WARNING: the same statements as the `if __name__ == "__main__":` block at
    # the end of claude-cockpit.py. If that block changes, change this with it, or the
    # capture stops showing what a real launch shows.
    ck.cswitch.require_claude()
    try:
        curses.wrapper(lambda scr: ck.UI(scr).run())
    except KeyboardInterrupt:
        pass


def child_forecast(scenario, hdir):
    """Write forecast.json with the real writer, claude-meters.py forecast().

    WHY not a hand-written file: the cockpit's forecast panel reads whatever shape
    forecast() writes, and a hand-made copy of that shape would go stale silently the
    day the writer changes. Here the real function builds the file from the fixture
    accounts (account state, the 7-day simulation, the never-runs-dry pace, the key
    names). Only the two inputs that need days of recorded history are supplied:
    paces() and accuracy(), each replaced by a value of the shape it returns.
    SYNC WARNING: FORECAST_PACES and the accuracy record built in write_state() copy
    the return shapes of paces() and accuracy() in claude-meters.py. If either of
    those changes shape, change the fixture with it.

    With no fixture in the scenario nothing is replaced at all, and the file is what
    the watcher writes on its first run, before any meter history exists."""
    meters = _load_tool("cmeters", "claude-meters.py")
    fixture = scenario.get("forecast")
    if fixture:
        meters.paces = lambda rows, hours, cal, at=None: fixture["paces"]
        meters.accuracy = lambda rows: fixture["accuracy"]
    meters.forecast(write=True)


def child_main(mode):
    home = os.environ.get("HOME", "")
    hdir = os.path.join(home, HARNESS_DIR)
    # pwd, not ~: the account's real home folder as the system records it, which
    # setting HOME cannot change. This is the comparison that makes the refusal real.
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    try:
        marker = open(os.path.join(hdir, MARKER)).read().strip()
    except OSError:
        marker = None
    if (not home or os.path.realpath(home) == os.path.realpath(real_home)
            or not marker or marker != os.environ.get(TOKEN_ENV)
            or "CLAUDE_CONFIG_DIR" in os.environ or "COCKPIT_FLEET_CONFIG_DIR" in os.environ):
        sys.stderr.write("render_cockpit_states: refusing to run the cockpit: HOME is not a demo home "
                         "made by this run, or CLAUDE_CONFIG_DIR is set.\n")
        return 3
    install_escape_guard(os.path.join(hdir, "escapes.log"))
    scenario = json.load(open(os.path.join(hdir, "scenario.json")))
    try:
        (child_ui if mode == "ui" else child_forecast)(scenario, hdir)
    except SystemExit:
        raise
    except BaseException:
        # curses has already restored the terminal by now, so a traceback printed to
        # it would land on top of the last screen. A file keeps the two apart.
        _append(os.path.join(hdir, "crash.txt"), traceback.format_exc())
        return 1
    return 0


# ---------------------------------------------------------------------------
# PARENT: build the demo home, run the child on a pseudo-terminal, read the screen
# ---------------------------------------------------------------------------

class HarnessError(Exception):
    pass


def _real_homes():
    homes = {os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)}
    if os.environ.get("HOME"):
        homes.add(os.path.realpath(os.environ["HOME"]))
    return homes


def spawn_in_demo_home(home, token, argv, **popen_args):
    """The ONLY place this harness starts a process. Refuses the real HOME.

    The environment is built from nothing instead of copied and edited: a copied
    environment keeps whatever the caller's shell exported, and CLAUDE_CONFIG_DIR or
    COCKPIT_FLEET_CONFIG_DIR arriving that way would point the tools back at a real
    login folder no matter what HOME says."""
    candidate = os.path.realpath(home)
    marker = os.path.join(candidate, HARNESS_DIR, MARKER)
    is_real = any(candidate == real or real.startswith(candidate + os.sep) for real in _real_homes())
    if is_real or not os.path.isfile(marker) or open(marker).read().strip() != token:
        raise HarnessError("refusing to start a child process: its HOME would not be a demo home made by this run")
    env = {
        "HOME": home,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "xterm-256color",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        # The keychain commands in the tools look up items by $USER. A name that is
        # not the real account's means an escaped lookup would also match nothing.
        "USER": "cockpit-demo",
        "LOGNAME": "cockpit-demo",
        # Satisfies the tools' "is Claude Code installed" check without naming a real
        # install. It is never executed: every spawn is stubbed and guarded.
        "COCKPIT_CLAUDE_BIN": "/usr/bin/true" if os.path.exists("/usr/bin/true") else "/bin/true",
        TOKEN_ENV: token,
    }
    return subprocess.Popen(argv, env=env, cwd=home, **popen_args)


def make_demo_home(tmp_root):
    home = tempfile.mkdtemp(prefix="cockpit_demo_home-temp.", dir=tmp_root)
    hdir = os.path.join(home, HARNESS_DIR)
    os.mkdir(hdir)
    token = os.urandom(8).hex()
    with open(os.path.join(hdir, MARKER), "w") as fh:
        fh.write(token + "\n")
    # install.sh, not a copy loop of our own: the first-launch screen should come from
    # exactly what a new user's install leaves on disk, settings file included.
    # --no-shell keeps it from writing a .zshrc; it never runs claude or security.
    proc = spawn_in_demo_home(home, token, ["/bin/bash", os.path.join(REPO, "install.sh"), "--no-shell"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.communicate(timeout=120)[0].decode("utf-8", "replace")
    base = os.path.join(home, ".claude-profiles")
    if proc.returncode != 0 or not os.path.isfile(os.path.join(base, "claude-cockpit.py")):
        raise HarnessError("install.sh did not install the tools into the demo home:\n" + out)
    shutil.copyfile(os.path.join(base, "autoswitch.json"), os.path.join(hdir, "autoswitch.as_installed.json"))
    return home, token


def make_terminal(cols, rows):
    """pyte, plus the two scroll sequences it does not implement. Returns the screen,
    the byte stream to feed, and the set of CSI final bytes the pair understands.

    WHY the two additions: when rows move between two frames (a warning line appears
    under an account and everything below it shifts down), curses does not redraw the
    moved rows. It narrows the scroll region, sends "scroll up n lines" (ESC [ n S) or
    "scroll down" (ESC [ n T), and then repaints only the cells that still differ.
    pyte silently drops S and T, so the repaint landed on rows that had never moved and
    the capture showed two frames interleaved: half a line from before the refresh
    joined to half a line from after it. That reads exactly like the cockpit writing
    over its own text, and it is not: every real terminal honours S and T. Measured in
    the `stale` state at 80 columns: ESC[4;26r ESC[26;1H ESC[5S ESC[1;40r.

    ByteStream, not Stream: the bytes arrive in arbitrary chunks, and a multi-byte
    character split across two reads must be joined before it is decoded."""
    import pyte

    class Screen(pyte.Screen):
        def _scroll(self, count, edge, one_line):
            # pyte already scrolls the region by one line when the cursor sits on its
            # edge and is told to step past it, so S and T are built from that: park
            # the cursor on the edge, step n times, put the cursor back (S and T do
            # not move it).
            top, bottom = self.margins or (0, self.lines - 1)
            x, y = self.cursor.x, self.cursor.y
            self.cursor.y = bottom if edge == "bottom" else top
            for _ in range(max(1, count or 1)):
                one_line()
            self.cursor.x, self.cursor.y = x, y

        def scroll_up(self, count=None, **_private):
            self._scroll(count, "bottom", self.index)

        def scroll_down(self, count=None, **_private):
            self._scroll(count, "top", self.reverse_index)

    class Stream(pyte.ByteStream):
        csi = dict(pyte.ByteStream.csi, S="scroll_up", T="scroll_down")

    screen = Screen(cols, rows)
    return screen, Stream(screen), frozenset(Stream.csi)


def capture(home, token, python, cols, rows, keys, exit_wait):
    """Run the cockpit once on a cols x rows pseudo-terminal and return what it showed."""
    hdir = os.path.join(home, HARNESS_DIR)
    master, slave = pty.openpty()
    try:
        # Size first, launch second: curses reads the size once at start-up, and a
        # resize sent afterwards would need a signal the child may not get in time.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        # -I: the child's module search path must not include this tests folder, and
        # no PYTHON* variable or user site folder may change what the tools import.
        proc = spawn_in_demo_home(home, token, [python, "-I", "-B", THIS_FILE, "--child", "ui"],
                                  stdin=slave, stdout=slave, stderr=slave,
                                  start_new_session=True, close_fds=True)
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)

    screen, stream, understood = make_terminal(cols, rows)
    term = {"open": True}
    raw = bytearray()
    started = time.time()

    def pump(timeout):
        if not term["open"]:
            time.sleep(timeout)
            return
        try:
            ready = select.select([master], [], [], timeout)[0]
            data = os.read(master, 65536) if ready else None
        except OSError:
            data = b""          # the child closed the terminal: it has exited
        if data == b"":
            term["open"] = False
        elif data:
            raw.extend(data)
            stream.feed(data)

    def settle(min_wait):
        """True once the screen has stopped changing; False if it never did."""
        began, last_text, last_change = time.time(), None, time.time()
        while time.time() - began < SETTLE_LIMIT_SEC:
            pump(0.05)
            text = TICK.sub("", "\n".join(screen.display))
            now = time.time()
            if text != last_text:
                last_text, last_change = text, now
            # `raw`: a screen nothing has been drawn on yet is not a settled screen.
            # Without this a child that is slow to start would be captured blank.
            if raw and now - began >= min_wait and now - last_change >= QUIET_SEC:
                return True
            if not term["open"]:
                return False
        return False

    def press(key):
        if key not in ALLOWED_KEYS:
            raise HarnessError("refusing to type %r into the cockpit: only e, r and q are allowed" % key)
        try:
            os.write(master, key.encode())
        except OSError:
            pass                # already exited (the empty screen quits by itself)

    try:
        stable = settle(FIRST_WAIT_SEC)
        for key in keys:
            press(key)
            stable = settle(QUIET_SEC) and stable
        lines = [line.rstrip() for line in screen.display]
        seconds = time.time() - started
        press("q")
        asked_to_quit = time.time()
        deadline = asked_to_quit + exit_wait
        while proc.poll() is None and time.time() < deadline:
            pump(0.05)
        forced = proc.poll() is None
        if forced:
            proc.kill()
        code = proc.wait()
        quit_seconds = time.time() - asked_to_quit
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    try:
        crash = open(os.path.join(hdir, "crash.txt"), encoding="utf-8").read()
    except FileNotFoundError:
        crash = None
    # The emulator drops a control sequence it does not know without a word, and the
    # screen it then shows is wrong in a way that looks like a cockpit bug (see
    # make_terminal). So every sequence the child sent is checked against the ones the
    # emulator implements, and an unknown one voids the capture instead of shipping it.
    ignored = sorted({final.decode() for final in CSI_FINAL.findall(bytes(raw))} - understood)
    return {"lines": lines, "stable": stable, "seconds": seconds, "exit": code, "forced": forced,
            "quit_seconds": quit_seconds,
            "escapes": _read_lines(os.path.join(hdir, "escapes.log")),
            "stubs": _read_lines(os.path.join(hdir, "stubs.log")), "crash": crash, "ignored": ignored}


def build_forecast(home, token, python):
    proc = spawn_in_demo_home(home, token, [python, "-I", "-B", THIS_FILE, "--child", "forecast"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.communicate(timeout=120)[0].decode("utf-8", "replace")
    hdir = os.path.join(home, HARNESS_DIR)
    if proc.returncode != 0 or not os.path.isfile(os.path.join(home, ".claude-profiles", "forecast.json")):
        crash = "".join(open(p).read() for p in [os.path.join(hdir, "crash.txt")] if os.path.isfile(p))
        raise HarnessError("claude-meters.py forecast() did not write forecast.json:\n" + out + crash)


def render(home, token, python, state, cols, rows, t0):
    spec = STATES[state]
    write_state(home, spec, t0)
    if spec.get("forecast"):
        build_forecast(home, token, python)
    # The cockpit's refresh threads are joined when the interpreter exits, so after q
    # the child stays alive until a refresh in flight has finished. The wait for the
    # exit therefore has to cover the longest stubbed refresh.
    result = capture(home, token, python, cols, rows, spec.get("keys", ""),
                     exit_wait=8 + spec.get("refresh_delay", REFRESH_SEC))
    problems = []
    if result["escapes"]:
        problems.append("%d call(s) escaped the stubs: %s" % (len(result["escapes"]), "; ".join(result["escapes"])))
    if result["crash"]:
        problems.append("the child stopped with an error:\n" + result["crash"])
    if result["ignored"]:
        problems.append("the output used terminal sequence(s) ESC [ ... %s that the emulator does not implement, "
                        "so the captured screen cannot be trusted (see make_terminal)" % " ".join(result["ignored"]))
    if not result["stable"]:
        problems.append("the screen never settled")
    if result["forced"]:
        problems.append("the cockpit did not exit after q")
    elif result["exit"] != 0:
        problems.append("the cockpit exited with status %s" % result["exit"])
    result["problems"] = problems
    return result


def main(argv):
    if len(argv) >= 2 and argv[0] == "--child":
        return child_main(argv[1])

    ap = argparse.ArgumentParser(description="Render every claude-cockpit screen state as plain text, "
                                             "from fake accounts in a throwaway home folder.")
    ap.add_argument("--out", help="folder to write <state>_<cols>.txt files into")
    ap.add_argument("--state", action="append", choices=list(STATES), help="only this state (repeatable)")
    ap.add_argument("--cols", action="append", type=int, help="only this width (repeatable; default 140 100 80)")
    ap.add_argument("--rows", type=int, default=ROWS, help="terminal height (default %d)" % ROWS)
    ap.add_argument("--tmp-root", help="where to create the throwaway home (default: the system temp folder)")
    ap.add_argument("--python", help="interpreter for the cockpit child (default /usr/bin/python3)")
    ap.add_argument("--list", action="store_true", help="list the states and exit")
    ap.add_argument("--verbose", action="store_true", help="also print what each answering stub was asked and said")
    ap.add_argument("--stubs", action="store_true",
                    help="list every spawn or network call in the tools and the stub that covers it, then exit")
    args = ap.parse_args(argv)

    if args.list:
        for extra, heading in ((False, "the eight named states"), (True, "further states")):
            print(heading + ":")
            for name, spec in STATES.items():
                if bool(spec.get("extra")) == extra:
                    print("  %-24s %s" % (name, spec["about"]))
        return 0
    if args.stubs:
        # Reads the tools' source as text; nothing from tools/ is executed here.
        rows, missing = audit_stubs(os.path.join(REPO, "tools"))
        for filename, line, name, call, how in rows:
            print("%-18s:%-5d %-22s %-26s %s" % (filename, line, name, how, call))
        for sentence in missing:
            print("NOT STUBBED  " + sentence)
        return 1 if missing else 0
    if not args.out:
        ap.error("--out is required")
    try:
        import pyte  # noqa: F401  (imported for real in make_terminal)
    except ImportError:
        sys.exit("render_cockpit_states: the pyte package is missing. Install it with:  pip install pyte")

    python = args.python or ("/usr/bin/python3" if os.access("/usr/bin/python3", os.X_OK) else sys.executable)
    states = args.state or list(STATES)
    widths = args.cols or list(WIDTHS)
    os.makedirs(args.out, exist_ok=True)
    # Reset times are measured from one moment for the whole run, rounded to five
    # minutes, so every capture of a run shows the same clock times and two states can
    # be compared line by line.
    t0 = int(time.time() // 300 * 300)

    home = None
    failures = captures = escapes = 0
    try:
        home, token = make_demo_home(args.tmp_root)
        print("demo home: %s" % home)
        # The captures are only evidence about the code they were made from, so name it.
        for filename in STUBBED:
            digest = hashlib.sha256(open(os.path.join(home, ".claude-profiles", filename), "rb").read()).hexdigest()
            print("installed for this run: %-18s sha256 %s" % (filename, digest[:16]))
        kept = {}
        for state in states:
            base_state = STATES[state].get("same_as")
            for cols in widths:
                result = render(home, token, python, state, cols, args.rows, t0)
                captures += 1
                escapes += len(result["escapes"])
                suffix = "%d" % cols if args.rows == ROWS else "%dx%d" % (cols, args.rows)
                path = os.path.join(args.out, "%s_%s.txt" % (state, suffix))
                text = "\n".join(result["lines"]) + "\n"
                kept[(state, cols)] = text
                verdict = "ok"
                if result["problems"]:
                    failures += 1
                    verdict = "FAILED, no file written: " + " | ".join(result["problems"])
                elif base_state:
                    if (base_state, cols) not in kept:
                        other = render(home, token, python, base_state, cols, args.rows, t0)
                        captures += 1
                        escapes += len(other["escapes"])
                        kept[(base_state, cols)] = "\n".join(other["lines"]) + "\n"
                    if TICK.sub("", kept[(base_state, cols)]) == TICK.sub("", text):
                        # Identical apart from the seconds counter: a second copy of
                        # the same screen would only invite a reader to hunt for a
                        # difference that is not there.
                        _remove(path)
                        verdict = "identical to %s, no file written" % base_state
                        path = None
                if path and not result["problems"]:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
                undecodable = text.count(chr(0xFFFD))   # chr(): a literal U+FFFD here would read as a damaged file
                print("%-24s %3dx%-3d exit=%s escaped=%d stub_calls=%-3d read_at=%.1fs quit_took=%.1fs  %s%s%s" % (
                    state, cols, args.rows, result["exit"], len(result["escapes"]), len(result["stubs"]),
                    result["seconds"], result["quit_seconds"], verdict,
                    ("  -> " + path) if path and not result["problems"] else "",
                    ("  NOTE: %d U+FFFD on screen, see LIMITS OF THE CAPTURE" % undecodable) if undecodable else ""))
                if args.verbose:
                    for call in result["stubs"]:
                        print("      stub: " + call)
    except HarnessError as e:
        print("render_cockpit_states: %s" % e, file=sys.stderr)
        failures += 1
    finally:
        if home:
            shutil.rmtree(home, ignore_errors=True)
            print("demo home removed: %s" % ("yes" if not os.path.exists(home) else "NO, delete it by hand: " + home))
    print("escape guard: %d call(s) reached a real spawn or network primitive across %d capture(s)" % (escapes, captures))
    return 1 if failures or escapes else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
