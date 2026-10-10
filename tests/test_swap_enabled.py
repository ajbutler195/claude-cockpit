#!/usr/bin/env python3
"""Tests for the swap opt-in: "swap_enabled" in autoswitch.json.

Run:  /usr/bin/python3 run_dev.py test_swap_enabled.py

WHY THESE TESTS EXIST: a swap moves login tokens between keychain items. On a fresh
install it must do nothing until the user turns it on. Three separate places enforce
that (claude-swap.py main, the cockpit number keys, claude-autoswitch.py cfg), and a
rule enforced in three places is a rule that drifts. These tests fail if any one of
the three stops asking.

Nothing here touches a keychain item, spawns claude, or calls the network: the
Swapper is replaced by a recorder and every config file is a temp file.
"""
import ast, contextlib, importlib.util, io, json, os, sys, tempfile
sys.dont_write_bytecode = True      # a __pycache__ folder next to the tools once showed up as a fake profile

BASE = os.path.expanduser("~/.claude-profiles")     # run_dev.py remaps this to ../tools


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cswap = load("cswap", "claude-swap.py")
cauto = load("cauto", "claude-autoswitch.py")
cauto.log = lambda *a, **k: None    # the real log() appends to autoswitch.log next to the tools

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("PASS  " + name)
    else:
        failed += 1
        print("FAIL  " + name)


def cfg_file(obj=None, raw=None):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write(raw if raw is not None else json.dumps(obj))
    f.close()
    return f.name


class Recorder:
    """Stands in for claude-swap's Swapper. Records calls, moves nothing."""
    calls = []

    def swap(self, target, dry_run=False):
        Recorder.calls.append(("swap", target, dry_run))

    def status(self):
        Recorder.calls.append(("status",))

    def duplicates(self):
        return []


def run_swap_main(argv, cfg_path):
    """Run claude-swap's main() with a recorder. Returns (exit code or None, calls)."""
    Recorder.calls = []
    old = (cswap.Swapper, cswap.SWAP_CFG, sys.argv)
    cswap.Swapper, cswap.SWAP_CFG, sys.argv = Recorder, cfg_path, ["claude-swap"] + argv
    code = None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cswap.main()
    except SystemExit as e:
        code = e.code
    finally:
        cswap.Swapper, cswap.SWAP_CFG, sys.argv = old
    return code, list(Recorder.calls)


# ---- S1: the reader fails closed -------------------------------------------------
check("S1a missing file -> off", cswap.swap_enabled("/nonexistent/autoswitch.json") is False)
check("S1b key absent -> off", cswap.swap_enabled(cfg_file({"enabled": True})) is False)
check("S1c unreadable JSON -> off", cswap.swap_enabled(cfg_file(raw="{not json")) is False)
check('S1d the text "true" is not consent -> off', cswap.swap_enabled(cfg_file({"swap_enabled": "true"})) is False)
check("S1e the number 1 is not consent -> off", cswap.swap_enabled(cfg_file({"swap_enabled": 1})) is False)
check("S1f JSON false -> off", cswap.swap_enabled(cfg_file({"swap_enabled": False})) is False)
check("S1g JSON true -> on", cswap.swap_enabled(cfg_file({"swap_enabled": True})) is True)

# ---- S2: claude-swap's command line ------------------------------------------------
off, on = cfg_file({"swap_enabled": False}), cfg_file({"swap_enabled": True})

code, calls = run_swap_main(["alpha"], off)
check("S2a off: `claude-swap <name>` exits 1 and never reaches the swap", code == 1 and calls == [])

last = cfg_file({"previous": "alpha"})
old_last, cswap.LAST = cswap.LAST, last
code, calls = run_swap_main(["--undo"], off)
check("S2b off: `claude-swap --undo` exits 1 and never reaches the swap", code == 1 and calls == [])
code, calls = run_swap_main(["--undo"], on)
check("S2c on: `--undo` swaps back to the recorded previous account", code is None and calls == [("swap", "alpha", False)])
cswap.LAST = old_last

code, calls = run_swap_main(["--dry-run", "alpha"], off)
check("S2d off: `--dry-run` still works (it changes nothing by definition)", code is None and calls == [("swap", "alpha", True)])

code, calls = run_swap_main([], off)
check("S2e off: status (no arguments) still works and is read-only", code is None and calls == [("status",)])

code, calls = run_swap_main(["alpha"], on)
check("S2f on: `claude-swap <name>` reaches the swap exactly once", code is None and calls == [("swap", "alpha", False)])

# ---- S3: the watcher's config reader -------------------------------------------------
def read_cfg(obj):
    old = cauto.CFG_FILE
    cauto.CFG_FILE = cfg_file(obj)
    try:
        return cauto.cfg()
    finally:
        cauto.CFG_FILE = old


c = read_cfg({"enabled": True, "auto_swap": True, "exclude_from_auto": []})
check("S3a auto_swap true but swap_enabled absent -> auto_swap is forced off", c["auto_swap"] is False and c["swap_enabled"] is False)
c = read_cfg({"enabled": True, "auto_swap": True, "swap_enabled": "true", "exclude_from_auto": []})
check('S3b swap_enabled as the text "true" -> still forced off', c["auto_swap"] is False and c["swap_enabled"] is False)
c = read_cfg({"enabled": True, "auto_swap": True, "swap_enabled": True, "exclude_from_auto": []})
check("S3c swap_enabled true -> auto_swap is honoured", c["auto_swap"] is True and c["swap_enabled"] is True)

# ---- S4: arming is refused while off -------------------------------------------------
path = cfg_file({"enabled": False, "auto_swap": False, "swap_enabled": False, "exclude_from_auto": []})
old = (cauto.CFG_FILE, sys.argv)
cauto.CFG_FILE, sys.argv = path, ["claude-autoswitch", "--arm"]
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        cauto.main()
except SystemExit as e:
    code = e.code
finally:
    cauto.CFG_FILE, sys.argv = old
after = json.load(open(path))
check("S4 off: `claude-autoswitch --arm` exits 1 and leaves the file unarmed",
      code == 1 and after["auto_swap"] is False and after["enabled"] is False)

# ---- S5: the cockpit keys ------------------------------------------------------------
# The key handlers live inside curses code that hands the terminal to a login flow, so
# they are checked structurally: EVERY `if` that guards a claude-swap subprocess must
# itself ask swap_enabled(). A text search would also match the comments above them.
# Two launches exist today: the number keys (swap to an account) and `u` (undo). The
# undo one was missed when the opt-in was first added, and this check is what found it.
tree = ast.parse(open(os.path.join(BASE, "claude-cockpit.py"), encoding="utf-8").read())


def walk_no_nested_if(node):
    """Like ast.walk, but does not look inside a nested `if`. WHY: the swap launch sits
    inside `if <guard>:` which itself sits inside `elif key == 'u':`. A plain walk would
    report the key test as a guard too. Only the NEAREST enclosing `if` is the guard."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If):
            continue
        yield from walk_no_nested_if(child)


def launches_swap(n):
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "run"
            and any(isinstance(a, ast.Name) and a.id == "SWAP" for arg in n.args for a in ast.walk(arg)))


guards = [ast.unparse(node.test) for node in ast.walk(tree) if isinstance(node, ast.If)
          and any(launches_swap(n) for stmt in node.body for n in walk_no_nested_if(stmt))]
check("S5 cockpit: both places that launch claude-swap (number keys, undo) ask swap_enabled() first",
      len(guards) == 2 and all("swap_enabled()" in g for g in guards))

# ---- S6: the cockpit header must tell "alerts only" from "will swap by itself" --------
# WHY: the header once read "AUTO-SWITCH ON" from `enabled` alone while the one-shot arm
# had long been used up, so its user believed it would swap and it did not. auto_armed()
# is what the header now asks. Same strictness as the watcher: both keys must be JSON true.
cockpit = load("cockpit", "claude-cockpit.py")
old_cfg = cockpit.AUTOCFG
for label, obj, want in [
        ("S6a alerts on, not armed -> not armed", {"enabled": True, "auto_swap": False, "swap_enabled": True}, False),
        ("S6b armed but swap opt-in off -> not armed", {"enabled": True, "auto_swap": True, "swap_enabled": False}, False),
        ('S6c auto_swap as the text "true" -> not armed', {"enabled": True, "auto_swap": "true", "swap_enabled": True}, False),
        ("S6d armed and opted in -> armed", {"enabled": True, "auto_swap": True, "swap_enabled": True}, True)]:
    cockpit.AUTOCFG = cfg_file(obj)
    check(label, cockpit.auto_armed() is want)
cockpit.AUTOCFG = "/nonexistent/autoswitch.json"
check("S6e unreadable settings -> not armed", cockpit.auto_armed() is False)
cockpit.AUTOCFG = old_cfg
old_log = cockpit.AUTOLOG
cockpit.AUTOLOG = cfg_file({})                      # just written = a live watcher
check("S6f a log written just now -> watcher alive", cockpit.watcher_alive() is True)
os.utime(cockpit.AUTOLOG, (0, 0))                   # written in 1970 = no watcher
check("S6g an old log -> no watcher", cockpit.watcher_alive() is False)
cockpit.AUTOLOG = "/nonexistent/autoswitch.log"
check("S6h no log at all -> no watcher", cockpit.watcher_alive() is False)
cockpit.AUTOLOG = old_log

print("\n%d/%d passed" % (passed, passed + failed))
sys.exit(1 if failed else 0)
