#!/usr/bin/env python3
"""measure_codex.py: measure the Codex CLI facts the multi-provider cockpit relies on.

This is phase 0 of docs/multi_provider/implementation_plan.md. Each step measures assumptions from
docs/multi_provider/requirements.md (section 17) and appends what it saw to a report. Nothing here
is installed. Run it from the repository with the interpreter the tools support:

    /usr/bin/python3 scripts/measure_codex.py <step>

Steps, in the order to run them:

    setup     The program and its version (A12), where its daemon socket lives (A13), what Codex
              writes into a home it is pointed at (A11), a keychain baseline. No sign-in.
    m1        An empty home, then the first sign-in, into home A (A11, A1, A2's default).
    m3        Account and usage reads through `codex app-server` in home A: three reads over ten
              minutes (A6, A7). No sign-in. Use --rounds and --interval to change that.
    m5        Which environment variables change the account or the server (A10). No sign-in.
    m4        Whether `codex login` honours BROWSER (A9). Cancelled before any sign-in completes.
    m6        Which shared links survive a real session (A8). Optional. Spends a few tokens.
    m8        A second sign-in to the same account, into home B, then renewals (A4).
              Run `m8 --later` about an hour afterwards.
    m7        Two app servers in home A: one renews, then the other (A5, A3). It can leave home A
              signed out. That is a result, not a failure.
    m2        Two homes in keychain mode: one keychain item per home, or one shared? (A2)
    status    Which steps have run, and which homes exist.
    report    Every result so far, without personal data, ready to copy into docs/gotchas.md.
    clean     Sign every measurement home out and delete it. The report is kept.

WHY THIS ORDER: steps that sign in to the same account again (m8, m2) come late. If a new sign-in
cancels older logins of that account (the open question A4), doing them early would break the
steps that still need home A.

SAFETY
  - Your own ~/.codex is never read, written or copied. Every home is a new folder under the
    measurement root, ~/.cockpit/measure/codex (mode 700), or COCKPIT_MEASURE_ROOT if set.
  - No token value is recorded or printed. The report holds file names, modes, sizes, key names,
    times, exit codes, percents and reset times. Tokens, email addresses and account ids appear
    only as short SHA-256 prefixes, enough to tell two of them apart.
  - Only read methods of the app server are called: `initialize`, `account/read` and
    `account/rateLimits/read` (ALLOWED_METHODS). Never `account/rateLimitResetCredit/consume`,
    which spends a reset credit, or `account/sendAddCreditsNudgeEmail`, which sends an email.
  - m8 and m2 sign in to the same account again. If a new sign-in cancels older ones (A4), Codex
    logins of that account elsewhere can be signed out too, on another computer included.
  - Every subprocess has a time limit; app servers run in their own process group, which is
    ended after each read.
  - The interactive session in m6 makes Codex start a background daemon for home A, which runs
    from a copy of Codex inside the home and outlives the session (gotcha #58). m6 stops it after
    the session, `clean` stops every home's daemon before deleting the home, and `status` lists any
    Codex process still running from a measurement home.
"""
import datetime
import hashlib
import json
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.parse

sys.dont_write_bytecode = True   # gotcha #13: keep __pycache__ out of the tree

ROOT = os.path.abspath(os.environ.get("COCKPIT_MEASURE_ROOT")
                       or os.path.expanduser("~/.cockpit/measure/codex"))
HOMES = os.path.join(ROOT, "homes")
LOGS = os.path.join(ROOT, "logs")
REPORT = os.path.join(ROOT, "report.jsonl")
STATE = os.path.join(ROOT, "state.json")
USER_HOME = os.path.expanduser("~")
REAL_CODEX_HOME = os.path.join(USER_HOME, ".codex")

# The only app-server methods this script may call. Anything else raises before it is sent.
ALLOWED_METHODS = {"initialize", "account/read", "account/rateLimits/read"}

# Environment variables that might change which credential or which server Codex uses, found in
# the Codex 0.162.1 binary (with `strings`) and in OpenAI's documentation. m5 measures each one.
# They are also removed from every child's environment, so that a variable exported in your shell
# cannot change what a measurement sees. CODEX_HOME is set explicitly instead.
OVERRIDES = ["OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_AUTH",
             "CODEX_URL", "CODEX_EXEC_SERVER_URL", "CODEX_APP_SERVER_CHATGPT_BASE_URL",
             "CODEX_AUTHAPI_BASE_URL", "CODEX_REVOKE_TOKEN_URL_OVERRIDE", "CODEX_CONNECTORS_TOKEN",
             "CODEX_OSS_BASE_URL", "OPENAI_IDENTITY_TOKEN_FILE", "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
             "OPENAI_FEDERATION_RULE_ID", "CODEX_SQLITE_HOME"]

KEYRING = ["-c", 'cli_auth_credentials_store="keyring"']

# A dummy URL for m5: port 9 (discard) on this Mac. Nothing listens there, so a token that Codex
# might send to it never leaves the machine.
DUMMY_URL = "http://127.0.0.1:9"
DUMMY_SECRET = "cockpit-measure-dummy-not-a-secret"


# --------------------------------------------------------------------------- small helpers

class Refused(Exception):
    pass


class RpcError(Exception):
    def __init__(self, err):
        super().__init__((err or {}).get("message", "error"))
        self.code = (err or {}).get("code")


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def tag(text):
    """A short, stable stand-in for an email address or an account id: enough to tell whether two
    readings belong to the same account, without recording which account it is."""
    if not text:
        return None
    return "h:" + hashlib.sha256(str(text).strip().lower().encode()).hexdigest()[:8]


def fp(token):
    """Fingerprint of a token, as claude-swap.py's fp(): a SHA-256 prefix, never the value."""
    if not token or not isinstance(token, str):
        return None
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def scrub(value):
    """Remove personal parts from anything that goes into the report: the measurement root, the
    home folder path, and email addresses."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        v = value.replace(ROOT, "<root>").replace(USER_HOME, "~")
        return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<email>", v)
    return value


def shape(text):
    """The structure of a string without its identifying parts: emails, paths, long hex and long
    opaque runs are replaced by placeholders."""
    t = scrub(text or "")
    t = re.sub(r"\b[0-9a-fA-F]{8,}\b", lambda m: "<hex%d>" % len(m.group()), t)
    return re.sub(r"[A-Za-z0-9_\-]{24,}", lambda m: "<id%d>" % len(m.group()), t)


def iso(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return "unparsed"


def mkdir_private(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(st):
    tmp = STATE + ".%d.tmp" % os.getpid()
    with open(os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE)


def record(step, data, conclusions):
    """Append one result to the report. Everything passes through scrub() on the way in."""
    rec = {"step": step, "at": now(), "codex_version": codex_version(),
           "data": scrub(data), "conclusions": scrub(conclusions)}
    with open(os.open(REPORT, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600), "a") as f:
        f.write(json.dumps(rec) + "\n")
    print("\nRecorded in the report:")
    for a, text in sorted(conclusions.items()):
        print("  %-4s %s" % (a, scrub(text)))


def say(text=""):
    print(text, flush=True)


def confirm(text, args):
    say(text)
    if "--yes" in args:
        return
    try:
        input("\nPress Enter to continue, or Ctrl-C to stop. ")
    except (KeyboardInterrupt, EOFError):
        say("\nStopped. Nothing more was done.")
        sys.exit(1)


# --------------------------------------------------------------------------- the program

_BIN = []
_VERSION = []


def codex_bin():
    """COCKPIT_CODEX_BIN, else Homebrew's path, else the absolute path found on PATH. Never the bare
    name (invariant #6); the report records which path was used."""
    if not _BIN:
        for p in (os.environ.get("COCKPIT_CODEX_BIN"), "/opt/homebrew/bin/codex", shutil.which("codex")):
            if p and os.path.isfile(p) and os.access(p, os.X_OK):
                _BIN.append(os.path.abspath(p))
                break
        else:
            sys.stderr.write("measure_codex: the Codex CLI was not found. Install it "
                             "(brew install --cask codex) or set COCKPIT_CODEX_BIN.\n")
            sys.exit(2)
    return _BIN[0]


def codex_version():
    if not _VERSION:
        try:
            mkdir_private(home_path("probe"))
            r = subprocess.run([codex_bin(), "--version"], capture_output=True, text=True, timeout=30,
                               env=env_for(home_path("probe")), cwd=ROOT)
            _VERSION.append((r.stdout or r.stderr).strip())
        except Exception as e:
            _VERSION.append("unknown (%s)" % type(e).__name__)
    return _VERSION[0]


def env_for(home, set_vars=None):
    """The environment of every Codex process this script starts: CODEX_HOME set explicitly to a
    measurement home, the variables in OVERRIDES removed, then `set_vars` added (m4, m5)."""
    if os.path.realpath(home) == os.path.realpath(REAL_CODEX_HOME):
        raise Refused("refusing to point Codex at your own ~/.codex")
    env = dict(os.environ)
    for k in OVERRIDES + ["CODEX_HOME"]:
        env.pop(k, None)
    env["CODEX_HOME"] = home
    env.update(set_vars or {})
    return env


def run(args, home, timeout=60, set_vars=None):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env=env_for(home, set_vars), cwd=ROOT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None


def home_path(name):
    return os.path.join(HOMES, name)


def new_home(name):
    """A new, empty home. Refuses to reuse one, so no step can inherit another step's login."""
    p = home_path(name)
    if os.path.exists(p):
        raise Refused("home %s already exists. Run `clean` first if you want to start over." % name)
    mkdir_private(p)
    return p


def need_home(name, signed_in=True):
    p = home_path(name)
    if not os.path.isdir(p):
        raise Refused("home %s does not exist yet. Run the earlier steps first (see --help)." % name)
    if signed_in and not os.path.exists(os.path.join(p, "auth.json")):
        raise Refused("home %s holds no auth.json, so it is not signed in in file mode." % name)
    return p


# --------------------------------------------------------------------------- what is on disk

def listing(path, depth=3):
    """Names, kinds, modes and sizes under `path`. Never file contents."""
    out = []
    if not os.path.isdir(path):
        return out
    for dirpath, dirnames, filenames in os.walk(path):
        rel = os.path.relpath(dirpath, path)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        if level >= depth:
            dirnames[:] = []
        for n in sorted(dirnames + filenames):
            full = os.path.join(dirpath, n)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            kind = ("link" if stat.S_ISLNK(st.st_mode) else "dir" if stat.S_ISDIR(st.st_mode)
                    else "socket" if stat.S_ISSOCK(st.st_mode) else "file")
            out.append({"path": os.path.normpath(os.path.join(rel, n)), "kind": kind,
                        "mode": oct(st.st_mode & 0o777), "size": st.st_size if kind == "file" else None})
    return out


def real_codex_snapshot():
    """Whether ~/.codex exists, and its entries' names and modification times. Never contents."""
    if not os.path.exists(REAL_CODEX_HOME):
        return {"exists": False}
    entries = {}
    for e in listing(REAL_CODEX_HOME, depth=1):
        try:
            entries[e["path"]] = os.lstat(os.path.join(REAL_CODEX_HOME, e["path"])).st_mtime
        except OSError:
            pass
    return {"exists": True, "entries": entries}


def auth_shape(home):
    """The structure of a home's auth.json: key names and value types, its mode and size, the
    login method, the last renewal time, and token fingerprints. Never a token."""
    p = os.path.join(home, "auth.json")
    if not os.path.exists(p):
        return None
    st = os.stat(p)
    out = {"mode": oct(st.st_mode & 0o777), "size": st.st_size}
    try:
        d = json.load(open(p))
    except Exception:
        out["parse"] = "failed"
        return out

    def keys(x, depth=0):
        if isinstance(x, dict) and depth < 2:
            return {k: keys(v, depth + 1) for k, v in x.items()}
        return type(x).__name__

    tokens = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
    mode = d.get("auth_mode")
    out.update({"keys": keys(d),
                "auth_mode": mode if isinstance(mode, str) and re.match(r"^[A-Za-z_]{1,30}$", mode) else None,
                "last_refresh": d.get("last_refresh") if isinstance(d.get("last_refresh"), str) else None,
                "refresh_fp": fp(tokens.get("refresh_token")),
                "access_fp": fp(tokens.get("access_token"))})
    return out


# --------------------------------------------------------------------------- the keychain (names only)

_SVCE = re.compile(r'"svce"<blob>="((?:[^"\\]|\\.)*)"')
_ACCT = re.compile(r'"acct"<blob>="((?:[^"\\]|\\.)*)"')


def keychain_items():
    """(service, account) attributes of every generic-password item in the keychains `security`
    searches. `dump-keychain` without -d prints no secret, and asks for no password."""
    r = subprocess.run(["security", "dump-keychain"], capture_output=True, text=True, timeout=180)
    items = []
    for block in (r.stdout or "").split("keychain: ")[1:]:
        if 'class: "genp"' not in block:
            continue
        s, a = _SVCE.search(block), _ACCT.search(block)
        items.append((s.group(1) if s else "", a.group(1) if a else ""))
    return items


def item_key(item):
    return hashlib.sha256((item[0] + "\0" + item[1]).encode()).hexdigest()[:16]


def new_items(before_keys, homes=()):
    """Items that were not in `before_keys`, by name, each checked for traces of a home's path."""
    out = []
    for svc, acct in keychain_items():
        if item_key((svc, acct)) in before_keys:
            continue
        traces = []
        for h in homes:
            digest = hashlib.sha256(h.encode()).hexdigest()
            for n in (8, 12, 16, 64):
                if digest[:n] in svc or digest[:n] in acct:
                    traces.append("contains sha256(%s)[:%d]" % (os.path.basename(h), n))
                    break
            if h in svc or h in acct:
                traces.append("contains the path of home %s" % os.path.basename(h))
        out.append({"service": shape(svc), "account": shape(acct), "traces": traces,
                    "codex_like": bool(re.search(r"codex|openai|chatgpt", svc + acct, re.I))})
    return out


# --------------------------------------------------------------------------- the app server

class AppServer:
    """One `codex app-server` over stdio: JSON-RPC 2.0 messages, one per line, without the
    "jsonrpc" field (A7). Started in its own process group and ended by close()."""

    def __init__(self, home, config=(), set_vars=None, name="server"):
        mkdir_private(LOGS)
        log_path = os.path.join(LOGS, "%s-%d.stderr" % (name, int(time.time() * 1000)))
        self.log = os.fdopen(os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "wb")
        self.p = subprocess.Popen([codex_bin()] + list(config) + ["app-server"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                  env=env_for(home, set_vars), cwd=ROOT, start_new_session=True)
        self.buf, self.next_id, self.notes = b"", 0, []

    def _send(self, msg):
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()

    def _line(self, deadline):
        while b"\n" not in self.buf:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("no answer from the app server in time")
            ready, _, _ = select.select([self.p.stdout], [], [], left)
            if not ready:
                raise TimeoutError("no answer from the app server in time")
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError("the app server closed its output (exit %s)" % self.p.poll())
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line

    def request(self, method, params=None, timeout=60):
        if method not in ALLOWED_METHODS:
            raise Refused("method %s is not on the read-only list" % method)
        self.next_id += 1
        rid = self.next_id
        msg = {"id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.time() + timeout
        while True:
            raw = self._line(deadline)
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if m.get("id") == rid and ("result" in m or "error" in m):
                if "error" in m:
                    raise RpcError(m["error"])
                return m["result"]
            if isinstance(m.get("method"), str):
                self.notes.append(m["method"])     # notifications, and server requests we ignore

    def start(self):
        res = self.request("initialize", {"clientInfo": {"name": "cockpit_measure",
                                                         "title": "claude-cockpit phase 0",
                                                         "version": "0.1"}}, timeout=60)
        self.init_keys = sorted(res.keys()) if isinstance(res, dict) else []
        self._send({"method": "initialized"})
        return self

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.p.pid, sig)
                    self.p.wait(timeout=5)
                    break
                except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
                    continue
        self.log.close()


def err(e):
    if isinstance(e, RpcError):
        return {"kind": "rpc_error", "code": e.code, "message": shape(str(e))[:240]}
    return {"kind": type(e).__name__, "message": shape(str(e))[:240]}


def account_summary(res):
    a = (res or {}).get("account")
    out = {"requires_openai_auth": (res or {}).get("requiresOpenaiAuth")}
    out["account"] = None if not a else {"type": a.get("type"), "plan": a.get("planType"),
                                         "email": tag(a.get("email"))}
    return out


def _window(w):
    if not w:
        return None
    return {"used_pct": w.get("usedPercent"), "window_mins": w.get("windowDurationMins"),
            "resets_at": iso(w.get("resetsAt"))}


def _snapshot(s):
    s = s or {}
    c = s.get("credits") or None
    return {"limit_id": s.get("limitId"), "limit_name": s.get("limitName"), "plan": s.get("planType"),
            "primary": _window(s.get("primary")), "secondary": _window(s.get("secondary")),
            "reached": s.get("rateLimitReachedType"),
            "credits": None if not c else {"has": c.get("hasCredits"), "unlimited": c.get("unlimited")}}


def limits_summary(res):
    res = res or {}
    return {"top_level_keys": sorted(res.keys()), "main": _snapshot(res.get("rateLimits")),
            "by_limit": {k: _snapshot(v) for k, v in (res.get("rateLimitsByLimitId") or {}).items()},
            "account": tag(res.get("accountId")), "ordinary_usage_allowed": res.get("ordinaryUsageAllowed"),
            "reset_credits_available": (res.get("rateLimitResetCredits") or {}).get("availableCount")}


def read_once(home, config=(), set_vars=None, refresh=False, limits=True, name="read"):
    """One app server: initialize, account/read and (optionally) account/rateLimits/read."""
    r = {"at": now(), "refresh_requested": refresh}
    s = None
    try:
        s = AppServer(home, config, set_vars, name).start()
        r["initialize_keys"] = s.init_keys
        r["account"] = account_summary(s.request("account/read", {"refreshToken": bool(refresh)}))
        if limits:
            try:
                r["limits"] = limits_summary(s.request("account/rateLimits/read",
                                                       {"excludeResetCreditDetails": True}))
            except (RpcError, TimeoutError, EOFError) as e:
                r["limits_error"] = err(e)
        r["notifications"] = sorted(set(s.notes))
    except (RpcError, TimeoutError, EOFError, OSError, Refused) as e:
        r["error"] = err(e)
    finally:
        if s:
            s.close()
    return r


def works(reading):
    """True when a reading shows a signed-in ChatGPT account and no error."""
    acct = ((reading or {}).get("account") or {}).get("account")
    return bool(acct) and acct.get("type") == "chatgpt" and "error" not in (reading or {})


def who(reading):
    acct = ((reading or {}).get("account") or {}).get("account") or {}
    return acct.get("email")


def sign_in(home, config=()):
    """The vendor's own sign-in, interactive, in this terminal."""
    say("\nStarting `codex login`. A browser page opens: sign in to OpenAI as you usually do\n"
        "(Continue with Google). Come back here when the page says you are signed in.\n")
    try:
        r = subprocess.run([codex_bin()] + list(config) + ["login"], env=env_for(home), cwd=ROOT, timeout=900)
        return r.returncode
    except subprocess.TimeoutExpired:
        return "timeout"
    except KeyboardInterrupt:
        return "interrupted"


def processes_under(path):
    """(pid, command) of every running process whose program lives under `path`. Found from `ps`;
    the daemon of gotcha #58 runs from a copy of Codex inside its home, so its command starts there."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:
        return []
    prefix = os.path.realpath(path) + os.sep
    found = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith(prefix):
            found.append((int(parts[0]), parts[1]))
    return found


def stop_daemon(home):
    """Stop a home's background daemon: Codex's own `app-server daemon stop` first, then TERM for any
    process still running from inside the home, the parent update loop first so that it cannot start
    the server again. Returns notes for the report."""
    notes = []
    r = run([codex_bin(), "app-server", "daemon", "stop"], home, timeout=30)
    notes.append("daemon stop: %s" % ("timed out" if r is None else "exit %s" % r.returncode))
    left = processes_under(home)
    for pid, cmd in sorted(left, key=lambda pc: "pid-update-loop" not in pc[1]):
        try:
            os.kill(pid, signal.SIGTERM)
            notes.append("TERM sent to %s" % shape(cmd)[:120])
        except (ProcessLookupError, PermissionError):
            pass
    if left:
        time.sleep(2)
    still = processes_under(home)
    if still:
        notes.append("STILL RUNNING: %s" % "; ".join(shape(c)[:120] for _, c in still))
    return notes


def elapsed_since(iso_text):
    """How long ago a timestamp written by now() was, in words."""
    try:
        then = datetime.datetime.fromisoformat(iso_text)
    except Exception:
        return "an unknown time"
    secs = int(max(0, (datetime.datetime.now().astimezone() - then).total_seconds()))
    days, rest = divmod(secs, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return "%d day%s %d hour%s" % (days, "" if days == 1 else "s", hours, "" if hours == 1 else "s")
    if hours:
        return "%d hour%s %d minutes" % (hours, "" if hours == 1 else "s", rest // 60)
    return "%d minutes" % (rest // 60)


def login_status(home, config=()):
    r = run([codex_bin()] + list(config) + ["login", "status"], home, timeout=60)
    if r is None:
        return {"timeout": True}
    return {"exit": r.returncode, "output": shape((r.stdout or "") + (r.stderr or "")).strip()[:300]}


# --------------------------------------------------------------------------- the steps

def step_setup(args):
    mkdir_private(ROOT)
    mkdir_private(HOMES)
    st = load_state()
    path = codex_bin()
    probe = home_path("probe")
    mkdir_private(probe)
    version = codex_version()
    # A13: the daemon's control socket. `daemon version` reports where it tried to connect.
    r = run([path, "app-server", "daemon", "version"], probe, timeout=30)
    text = ((r.stdout or "") + (r.stderr or "")) if r else ""
    m = re.search(r"(/\S+app-server-control\S*\.sock)", text)
    sock = m.group(1) if m else None
    in_home = bool(sock and os.path.realpath(sock).startswith(os.path.realpath(probe) + os.sep))
    probe_files = listing(probe)
    st["keychain_baseline"] = sorted({item_key(i) for i in keychain_items()})
    st["real_codex_at_setup"] = real_codex_snapshot()
    st["setup_at"] = now()
    save_state(st)
    data = {"program": path, "resolves_to": os.path.realpath(path), "version": version,
            "daemon_socket": sock, "probe_home_after_version_and_daemon_probe": probe_files,
            "real_codex_home": st["real_codex_at_setup"],
            "keychain_items_in_baseline": len(st["keychain_baseline"])}
    record("setup", data, {
        "A12": "program %s -> %s (%s)" % (path, os.path.realpath(path), version),
        "A13": ("daemon control socket is inside CODEX_HOME (%s), so each home has its own daemon"
                % (sock or "?")) if in_home else ("daemon control socket NOT inside CODEX_HOME: %s" % sock),
        "A11": "a probe home received: %s" % (", ".join(e["path"] for e in probe_files) or "nothing"),
    })
    say("\nNext: m1 (signs in once).")


def step_m1(args):
    need_setup()
    confirm("m1 creates home A, checks what Codex writes into an empty home, then signs in once\n"
            "into home A with Codex's default storage.", args)
    st = load_state()
    a = new_home("A")
    before_real = real_codex_snapshot()
    status_empty = login_status(a)
    empty_files = listing(a)
    k_before = {item_key(i) for i in keychain_items()}
    rc = sign_in(a)
    files = listing(a)
    shape_a = auth_shape(a)
    kc = new_items(k_before, homes=[a])
    after_real = real_codex_snapshot()
    reading = read_once(a, limits=False, name="m1")
    status_after = login_status(a)
    st.setdefault("homes", {})["A"] = {"mode": "file", "email": tag(who(reading))}
    save_state(st)
    stored = ("auth.json in home A" if shape_a else "no auth.json in home A")
    codex_items = [i for i in kc if i["codex_like"]]
    stored += (", plus new keychain item(s): %s" % "; ".join(i["service"] for i in codex_items)) if codex_items \
        else ", no new Codex keychain item"
    record("m1", {"login_status_in_empty_home": status_empty, "files_after_status": empty_files,
                  "sign_in_exit": rc, "files_after_sign_in": files, "auth_json": shape_a,
                  "new_keychain_items": kc, "real_codex_before": before_real,
                  "real_codex_after": after_real, "account_read": reading,
                  "login_status_after": status_after}, {
        "A11": "`codex login status` in an empty home created: %s"
               % (", ".join(e["path"] for e in empty_files) or "nothing"),
        "A1": "~/.codex %s by a sign-in with CODEX_HOME set"
              % ("unchanged" if before_real == after_real else "CHANGED"),
        "A2": "default storage: " + stored,
        "A6": "account/read after sign-in: %s" % (
            "type %s, plan %s" % (reading["account"]["account"]["type"], reading["account"]["account"]["plan"])
            if works(reading) else "not signed in (%s)" % reading.get("error", {}).get("message", "")),
    })
    say("\nNext: m3 (about ten minutes, no sign-in).")


def step_m3(args):
    a = need_home("A")
    rounds = int(arg_value(args, "--rounds", 3))
    interval = int(arg_value(args, "--interval", 300))
    say("m3 reads account and usage in home A %d time(s), %d seconds apart. Leave it running.\n"
        "Do not use Codex on this account meanwhile: the point is to see the reads change nothing."
        % (rounds, interval))
    readings = []
    try:
        for i in range(rounds):
            if i:
                say("  waiting %d s before read %d..." % (interval, i + 1))
                time.sleep(interval)
            r = read_once(a, name="m3")
            readings.append(r)
            main = (r.get("limits") or {}).get("main") or {}
            say("  read %d: account %s, primary %s, secondary %s%s" % (
                i + 1, "ok" if works(r) else "NOT OK", main.get("primary"), main.get("secondary"),
                ("  limits error: %s" % r["limits_error"]["message"]) if "limits_error" in r else ""))
    except KeyboardInterrupt:
        say("\nStopped early; recording the reads so far.")
    status = login_status(a)

    def mains(r):
        return ((r.get("limits") or {}).get("main") or {})
    def used(r):
        m = mains(r)
        return tuple((w or {}).get("used_pct") for w in (m.get("primary"), m.get("secondary")))
    # WHY the percent and not the whole window: on the free plan, a window at 0% reports a reset
    # time of "now plus the window length", which moves forward with the clock between reads
    # (seen 2026-10-13). A moving reset time with an unchanged percent is not usage.
    first, last = (mains(readings[0]), mains(readings[-1])) if readings else ({}, {})
    unchanged = bool(readings) and all(used(r) == used(readings[0]) for r in readings)
    record("m3", {"rounds": len(readings), "interval_s": interval, "readings": readings,
                  "login_status": status}, {
        "A6": "account/read gives type, plan and email (recorded as a hash): %s; `codex login status` says: %s"
              % ("yes" if readings and works(readings[0]) else "no", status.get("output", "?")),
        "A7": ("%d usage reads over %d s: percent used %s %s; primary reset time %s -> %s; first "
               "reading %s" % (len(readings), interval * max(0, len(readings) - 1),
                               "UNCHANGED" if unchanged else "CHANGED",
                               used(readings[0]) if readings else None,
                               (first.get("primary") or {}).get("resets_at"),
                               (last.get("primary") or {}).get("resets_at"), first)),
    })
    say("\nOptional: compare with Codex's own view. Run\n  CODEX_HOME=%s %s\n"
        "type /status, then /quit. That starts no model request." % (a, codex_bin()))
    say("\nNext: m5 (about a minute, no sign-in).")


def binary_candidates():
    """OVERRIDES, plus any name in the installed Codex binary that looks like it could change the
    credential or the server and is not on that list yet (a newer version may add some).

    WHY the strict filter: strings(1) glues neighbouring names together, for example
    CODEX_OSS_PORTCODEX_OSS_BASE_URL. A name with a second OPENAI_/CODEX_/CHATGPT_ inside it is
    such a join, and is skipped rather than guessed apart."""
    try:
        out = subprocess.run(["strings", "-a", os.path.realpath(codex_bin())], capture_output=True,
                             text=True, timeout=120).stdout
    except Exception:
        out = ""
    found = set()
    for n in set(re.findall(r"(?:OPENAI|CODEX|CHATGPT)_[A-Z0-9_]{3,40}", out)):
        if re.search(r".(?:OPENAI|CODEX|CHATGPT)_", n) or n.endswith("_"):
            continue
        parts = set(n.split("_"))
        if parts & {"KEY", "TOKEN", "AUTH", "URL", "SQLITE", "AUTHAPI", "IDENTITY", "FEDERATION"}:
            found.add(n)
    return sorted((found | set(OVERRIDES)) - {"CODEX_HOME", "CODEX_GITHUB_PERSONAL_ACCESS_TOKEN"})


def dummy_value(var):
    """A harmless value for m5: URLs point at port 9 on this Mac, where nothing listens; file paths
    point at a file that does not exist; everything else is an obviously fake string."""
    if var.endswith("SQLITE_HOME"):
        target = os.path.join(ROOT, "sqlite-probe")
        if os.path.exists(target):
            shutil.rmtree(target)
        mkdir_private(target)
        return target
    if "URL" in var or "BASE" in var:
        return DUMMY_URL
    if var.endswith("_FILE"):
        return os.path.join(ROOT, "no-such-file")
    return DUMMY_SECRET


def step_m5(args):
    a = need_home("A")
    say("m5 reads home A once normally, then once per candidate variable, each set to a dummy value\n"
        "(URLs point at port 9 on this Mac, where nothing listens).")
    base = read_once(a, name="m5-base")
    if not works(base):
        raise Refused("home A does not read as signed in, so there is no baseline: %s" % base.get("error"))
    results, conclusions = {}, {}
    auth0 = auth_shape(a)
    for var in binary_candidates():
        value = dummy_value(var)
        r = read_once(a, set_vars={var: value}, name="m5")
        if var.endswith("SQLITE_HOME"):
            r["files_created_there"] = listing(value)
        if "error" in r:
            verdict = "breaks the reads: %s" % r["error"]["message"]
        elif (r.get("account") or {}) != (base.get("account") or {}):
            verdict = "changes the account seen: %s" % (r.get("account"),)
        elif "limits_error" in r and "limits_error" not in base:
            verdict = "breaks the usage read: %s" % r["limits_error"]["message"]
        else:
            verdict = "no effect on account and usage reads (model requests not tested)"
        if r.get("files_created_there"):
            verdict += "; Codex wrote %s into it" % ", ".join(e["path"] for e in r["files_created_there"])
        auth1 = auth_shape(a)
        if auth1 is None or (auth1.get("keys"), auth1.get("auth_mode")) != (auth0.get("keys"), auth0.get("auth_mode")):
            verdict += "; it CHANGED home A's auth.json, so m5 stopped here"
        results[var] = {"reading": r, "verdict": verdict, "auth_after": auth1}
        say("  %-28s %s" % (var, verdict))
        if "CHANGED home A" in verdict:
            say("  Home A may need a new sign-in: run `clean`, then start again from m1.")
            break
    conclusions["A10"] = "; ".join("%s: %s" % (k, v["verdict"]) for k, v in sorted(results.items()))
    record("m5", {"baseline": base, "variables": results}, conclusions)
    say("\nNext: m4 (no sign-in completes).")


def step_m4(args):
    need_setup()
    confirm("m4 starts `codex login` in a new home C with BROWSER set to a small recorder script, then\n"
            "cancels it. If Codex ignores BROWSER, your normal browser opens a sign-in page: close it\n"
            "without signing in. The sign-in cannot complete either way, because the login is stopped.",
            args)
    c = new_home("C")
    bindir = os.path.join(ROOT, "bin")
    mkdir_private(bindir)
    recorder = os.path.join(bindir, "browser-recorder")
    out_file = os.path.join(bindir, "browser-arg")
    with open(os.open(recorder, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o700), "w") as f:
        f.write('#!/bin/sh\nprintf "%s\\n" "$1" > "$COCKPIT_MEASURE_BROWSER_OUT"\n')
    if os.path.exists(out_file):
        os.unlink(out_file)
    p = subprocess.Popen([codex_bin(), "login"], env=env_for(c, {"BROWSER": recorder,
                                                                 "COCKPIT_MEASURE_BROWSER_OUT": out_file}),
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.time() + 25
    while time.time() < deadline and not os.path.exists(out_file) and p.poll() is None:
        time.sleep(0.5)
    time.sleep(1)
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        printed = p.communicate(timeout=10)[0] or b""
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        printed = p.communicate()[0] or b""
    url = None
    if os.path.exists(out_file):
        raw = open(out_file).read().strip()
        parts = urllib.parse.urlsplit(raw)
        url = "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)   # never the query: it holds the login state
        os.unlink(out_file)
    printed_hosts = sorted(set(re.findall(r"https?://([A-Za-z0-9.-]+)", printed.decode(errors="replace"))))
    record("m4", {"recorder_called": url is not None, "recorded_url_without_query": url,
                  "hosts_codex_printed": printed_hosts, "auth_json_in_C": auth_shape(c) is not None}, {
        "A9": ("`codex login` honours BROWSER: it called the recorder with %s" % url) if url
              else "`codex login` did NOT call BROWSER within 25 s (it printed: %s)" % (", ".join(printed_hosts) or "no URL"),
    })
    say("\nNext: m6 (optional, spends a few tokens) or m8 (signs in once).")


SHARED_ITEMS = ["AGENTS.md", "prompts", "skills", "config.toml", "history.jsonl", "sessions"]


def step_m6(args):
    a = need_home("A")
    confirm("m6 links six stand-in items into home A, then starts an interactive Codex session there.\n"
            "In the session:\n"
            "  1. If Codex asks whether to trust the folder, trust it.\n"
            "  2. Type:  Reply with only the word ok   and wait for the answer.\n"
            "  3. Type /model, choose a different model or reasoning level, confirm.\n"
            "  4. Type /quit.\n"
            "This spends a few tokens.", args)
    standin = os.path.join(ROOT, "standin")
    if os.path.exists(standin):
        shutil.rmtree(standin)
    mkdir_private(standin)
    open(os.path.join(standin, "AGENTS.md"), "w").write("# Measurement stand-in. No instructions here.\n")
    os.makedirs(os.path.join(standin, "prompts"))
    open(os.path.join(standin, "prompts", "hello.md"), "w").write("Say hello.\n")
    os.makedirs(os.path.join(standin, "skills"))
    open(os.path.join(standin, "config.toml"), "w").write("# measurement stand-in\n")
    open(os.path.join(standin, "history.jsonl"), "w").close()
    os.makedirs(os.path.join(standin, "sessions"))
    moved = []
    for item in SHARED_ITEMS:
        dest = os.path.join(a, item)
        if os.path.lexists(dest):
            os.rename(dest, dest + ".before-m6")
            moved.append(item)
        os.symlink(os.path.join(standin, item), dest)
    before = {i: snapshot_item(os.path.join(standin, i)) for i in SHARED_ITEMS}
    project = os.path.join(ROOT, "project")
    mkdir_private(project)
    try:
        subprocess.run([codex_bin()], env=env_for(a), cwd=project, timeout=1800)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        pass
    daemons = [shape(c)[:160] for _, c in processes_under(a)]
    results = {}
    for item in SHARED_ITEMS:
        dest = os.path.join(a, item)
        if os.path.islink(dest):
            changed = snapshot_item(os.path.join(standin, item)) != before[item]
            results[item] = "stayed a link; " + ("written through it" if changed else "not written")
        elif os.path.lexists(dest):
            results[item] = "REPLACED by a real %s" % ("folder" if os.path.isdir(dest) else "file")
        else:
            results[item] = "link removed"
    for item in SHARED_ITEMS:                      # put home A back as it was
        dest = os.path.join(a, item)
        if os.path.islink(dest):
            os.unlink(dest)
        elif os.path.isdir(dest):
            shutil.rmtree(dest)
        elif os.path.lexists(dest):
            os.unlink(dest)
        if item in moved:
            os.rename(dest + ".before-m6", dest)
    stopped = stop_daemon(a)
    record("m6", {"results": results, "moved_aside_and_restored": moved,
                  "processes_left_by_the_session": daemons, "daemon_stop": stopped}, {
        "A8": "; ".join("%s: %s" % kv for kv in sorted(results.items())),
        "A13": "the interactive session left %d process(es) running from inside home A%s; %s"
               % (len(daemons), (": " + "; ".join(daemons)) if daemons else "", "; ".join(stopped))})
    say("\nNext: m8 (signs in once).")


def snapshot_item(path):
    if os.path.isdir(path):
        return sorted((e["path"], e["size"]) for e in listing(path))
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime)
    except OSError:
        return None


def step_m8(args):
    a = need_home("A")
    st = load_state()
    if "--later" in args:
        if "B" not in st.get("homes", {}):
            raise Refused("run `m8` (without --later) first")
        say("m8 --later: renewing and reading homes A and B again.")
        ra = read_once(a, refresh=True, limits=False, name="m8-later")
        rb = read_once(home_path("B"), refresh=True, limits=False, name="m8-later")
        ra2, rb2 = read_once(a, name="m8-later"), read_once(home_path("B"), name="m8-later")
        record("m8-later", {"since_m8": st.get("m8_at"), "renew_A": ra, "renew_B": rb,
                            "read_A": ra2, "read_B": rb2, "auth_A": auth_shape(a),
                            "auth_B": auth_shape(home_path("B"))}, {
            "A4": "%s after m8: A %s, B %s (after renewing each)"
                  % (elapsed_since(st.get("m8_at")), "works" if works(ra2) else "FAILS",
                     "works" if works(rb2) else "FAILS")})
        say("\nNext: m7.")
        return
    confirm("m8 signs in once more, into a new home B. Sign in to the SAME OpenAI account as in m1.\n"
            "Then it renews each home's login and checks that both still work.", args)
    b = new_home("B")
    rc = sign_in(b)
    a0, b0 = auth_shape(a), auth_shape(b)
    id_a, id_b = read_once(a, limits=False, name="m8"), read_once(b, limits=False, name="m8")
    same = bool(who(id_a)) and who(id_a) == who(id_b)
    renew_a = read_once(a, refresh=True, limits=False, name="m8")
    a1 = auth_shape(a)
    read_a, read_b = read_once(a, name="m8"), read_once(b, name="m8")
    renew_b = read_once(b, refresh=True, limits=False, name="m8")
    b1 = auth_shape(b)
    read_a2, read_b2 = read_once(a, name="m8"), read_once(b, name="m8")
    st.setdefault("homes", {})["B"] = {"mode": "file", "email": tag(who(id_b))}
    st["m8_at"] = now()
    save_state(st)

    def rotated(x, y):
        if not (x and y and x.get("refresh_fp") and y.get("refresh_fp")):
            return "unknown"
        return "yes" if x["refresh_fp"] != y["refresh_fp"] else "no"
    record("m8", {"sign_in_exit": rc, "same_account": same, "id_A": id_a, "id_B": id_b,
                  "auth_A_before": a0, "renew_A": renew_a, "auth_A_after": a1,
                  "read_A": read_a, "read_B": read_b, "auth_B_before": b0, "renew_B": renew_b,
                  "auth_B_after": b1, "read_A_end": read_a2, "read_B_end": read_b2}, {
        "A4": ("%s; right after B's sign-in A %s; after A renewed both %s/%s; after B renewed both %s/%s"
               % ("same account" if same else "DIFFERENT accounts, so this is not the test",
                  "works" if works(id_a) else "FAILS",
                  "works" if works(read_a) else "FAILS", "works" if works(read_b) else "FAILS",
                  "works" if works(read_a2) else "FAILS", "works" if works(read_b2) else "FAILS")),
        "A3": "a forced renewal changed the refresh token: A %s, B %s" % (rotated(a0, a1), rotated(b0, b1)),
    })
    later = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%H:%M")
    say("\nNext: `m8 --later` after %s, then m7." % later)


def step_m7(args):
    a = need_home("A")
    confirm("m7 starts two app servers in home A. The first renews the login; then the second reads\n"
            "and renews with what it loaded before. It can leave home A signed out: that is a result.", args)
    out = {"auth_before": auth_shape(a)}
    s1 = s2 = None
    try:
        s1 = AppServer(a, name="m7-s1").start()
        s2 = AppServer(a, name="m7-s2").start()
        for label, srv, method, params in (
                ("s1_read", s1, "account/read", {"refreshToken": False}),
                ("s2_read", s2, "account/read", {"refreshToken": False}),
                ("s1_renew", s1, "account/read", {"refreshToken": True}),
                ("s2_limits", s2, "account/rateLimits/read", {"excludeResetCreditDetails": True}),
                ("s2_renew", s2, "account/read", {"refreshToken": True})):
            try:
                res = srv.request(method, params)
                out[label] = account_summary(res) if method == "account/read" else limits_summary(res)
            except (RpcError, TimeoutError, EOFError) as e:
                out[label] = {"error": err(e)}
            out["auth_after_" + label] = auth_shape(a)
    except (RpcError, TimeoutError, EOFError, OSError) as e:
        out["error"] = err(e)
    finally:
        for s in (s1, s2):
            if s:
                s.close()
    fresh = read_once(a, name="m7-after")
    out["fresh_read_after"] = fresh

    def ok(label):
        v = out.get(label) or {}
        return "error" not in v and bool((v.get("account") or {}) or v.get("main"))
    fp0 = (out.get("auth_before") or {}).get("refresh_fp")
    fp1 = (out.get("auth_after_s1_renew") or {}).get("refresh_fp")
    fp2 = (out.get("auth_after_s2_renew") or {}).get("refresh_fp")
    record("m7", out, {
        "A5": ("second process after the first renewed: usage read %s, its own renewal %s; a fresh read "
               "afterwards %s" % ("ok" if ok("s2_limits") else "FAILED", "ok" if ok("s2_renew") else "FAILED",
                                  "works" if works(fresh) else "FAILS (home A is now signed out)")),
        "A3": "refresh token changed on renewal: first %s, second %s" % (
            "yes" if fp0 and fp1 and fp0 != fp1 else "no" if fp0 and fp1 else "unknown",
            "yes" if fp1 and fp2 and fp1 != fp2 else "no" if fp1 and fp2 else "unknown"),
    })
    say("\nNext: m2 (signs in twice).")


def step_m2(args):
    need_setup()
    confirm("m2 signs in twice more, into new homes D and E, with Codex told to keep logins in the\n"
            "macOS keychain. Use the same OpenAI account. If macOS asks whether codex may use the\n"
            "keychain, choose Allow.", args)
    st = load_state()
    d, e = new_home("D"), new_home("E")
    st.setdefault("homes", {}).update({"D": {"mode": "keyring"}, "E": {"mode": "keyring"}})
    save_state(st)
    k0 = {item_key(i) for i in keychain_items()}
    rc_d = sign_in(d, KEYRING)
    k1_items = keychain_items()
    new_d = new_items(k0, homes=[d, e])
    k1 = {item_key(i) for i in k1_items}
    rc_e = sign_in(e, KEYRING)
    new_e = new_items(k1, homes=[d, e])
    read_d = read_once(d, KEYRING, limits=False, name="m2")
    read_e = read_once(e, KEYRING, limits=False, name="m2")
    files_d, files_e = listing(d), listing(e)
    if new_d and new_e:
        verdict = "one keychain item per home (D added %d, E added %d more)" % (len(new_d), len(new_e))
    elif new_d and not new_e:
        verdict = "E added NO item: the homes share one keychain item"
    else:
        verdict = "no new keychain item after D's sign-in; see the data"
    traces = sorted({t for i in new_d + new_e for t in i["traces"]})
    record("m2", {"sign_in_exit_D": rc_d, "sign_in_exit_E": rc_e, "new_items_after_D": new_d,
                  "new_items_after_E": new_e, "auth_json_in_D": auth_shape(d) is not None,
                  "auth_json_in_E": auth_shape(e) is not None, "files_D": files_d, "files_E": files_e,
                  "read_D_after_E": read_d, "read_E": read_e}, {
        "A2": "keychain mode: %s; item names: %s; path traces: %s; D still works after E's sign-in: %s"
              % (verdict, "; ".join(sorted({i["service"] + " / " + i["account"] for i in new_d + new_e})) or "none",
                 ", ".join(traces) or "none", "yes" if works(read_d) else "NO"),
    })
    say("\nNext: report, then clean.")


def step_status(args):
    st = load_state()
    say("measurement root: %s" % ROOT)
    steps = []
    try:
        steps = [json.loads(l)["step"] for l in open(REPORT)]
    except Exception:
        pass
    say("steps recorded: %s" % (", ".join(steps) or "none"))
    for name in sorted(os.listdir(HOMES)) if os.path.isdir(HOMES) else []:
        h = home_path(name)
        mode = (st.get("homes", {}).get(name) or {}).get("mode", "file")
        say("  home %-6s %s" % (name, "auth.json present" if os.path.exists(os.path.join(h, "auth.json"))
                                else "keychain mode" if mode == "keyring" else "no login"))
    procs = processes_under(HOMES)
    say("processes running from a measurement home: %s" % (len(procs) or "none"))
    for pid, cmd in procs:
        say("  %d %s" % (pid, shape(cmd)[:150]))


def step_report(args):
    try:
        recs = [json.loads(l) for l in open(REPORT)]
    except FileNotFoundError:
        raise Refused("nothing recorded yet")
    say("Codex measurements, %s" % ", ".join(sorted({r["codex_version"] for r in recs})))
    by_a = {}
    for r in recs:
        for a, text in r["conclusions"].items():
            by_a.setdefault(a, []).append("%s (%s, %s)" % (text, r["step"], r["at"][:16]))
    for a in sorted(by_a, key=lambda x: int(re.sub(r"\D", "", x) or 0)):
        say("\n%s" % a)
        for line in by_a[a]:
            say("  - %s" % line)
    say("\nFull data: %s (one JSON record per step; no tokens, emails only as hashes)." % REPORT)


def step_clean(args):
    confirm("clean stops every measurement home's daemon, signs the home out (`codex logout`) and\n"
            "deletes it, with the logs and stand-ins. The report and your own ~/.codex are kept.", args)
    st = load_state()
    done = []
    for name in sorted(os.listdir(HOMES)) if os.path.isdir(HOMES) else []:
        h = home_path(name)
        config = KEYRING if (st.get("homes", {}).get(name) or {}).get("mode") == "keyring" else []
        done.extend("%s: %s" % (name, n) for n in stop_daemon(h))
        if config or os.path.exists(os.path.join(h, "auth.json")):
            r = run([codex_bin()] + config + ["logout"], h, timeout=60)
            done.append("%s: logout %s" % (name, "exit %s" % r.returncode if r else "timed out"))
        shutil.rmtree(h, ignore_errors=True)
    for extra in ("logs", "bin", "standin", "project", "sqlite-probe"):
        shutil.rmtree(os.path.join(ROOT, extra), ignore_errors=True)
    left = [i for i in new_items(set(st.get("keychain_baseline", []))) if i["codex_like"]]
    st.pop("homes", None)
    save_state(st)
    for line in done:
        say("  " + line)
    say("Codex keychain items left since setup: %s" % (
        "; ".join(i["service"] + " / " + i["account"] for i in left) or "none"))
    procs = processes_under(HOMES)
    say("Processes still running from a measurement home: %s" % (
        "; ".join(shape(c)[:120] for _, c in procs) or "none"))
    say("Report kept at %s" % REPORT)


def need_setup():
    if not load_state().get("setup_at"):
        raise Refused("run `setup` first")


def arg_value(args, name, default):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


STEPS = {"setup": step_setup, "m1": step_m1, "m3": step_m3, "m5": step_m5, "m4": step_m4,
         "m6": step_m6, "m8": step_m8, "m7": step_m7, "m2": step_m2, "status": step_status,
         "report": step_report, "clean": step_clean}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help") or argv[0] not in STEPS:
        say(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help", "help") else 1
    if os.path.realpath(ROOT).startswith(os.path.realpath(REAL_CODEX_HOME)):
        sys.stderr.write("measure_codex: the measurement root must not be inside ~/.codex\n")
        return 1
    try:
        STEPS[argv[0]](argv[1:])
    except Refused as e:
        sys.stderr.write("measure_codex: %s\n" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
