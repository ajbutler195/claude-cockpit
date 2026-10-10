"""Tests for the configurable FLEET (claude-switch.py _fleet_config_dir, fleet_env,
FLEET_SLOT; claude-swap.py _guard_slot).

Run:  /usr/bin/python3 run_dev.py test_fleet_dir.py      (3.9, like the launchd watcher)

WHY THESE TESTS EXIST: in fleet-dir mode the shared default keychain slot
("Claude Code-credentials") belongs to another long-running Claude process. The
cockpit's whole job is rewriting "the fleet" slot, so an unaimed fleet there would
rewrite that process's login. These tests pin (1) that an ordinary machine is unchanged
when nothing is configured, (2) that in fleet-dir mode the fleet is the named dir's own
slot, (3) that bad config fails CLOSED, and (4) that nothing can read/write/delete the
default slot in that mode.

Touches NO keychain item: the Keychain guard tests mock subprocess.run, and
assert that it was never reached.
"""
import hashlib, importlib.util, os, shutil, sys, tempfile

sys.dont_write_bytecode = True
BASE = os.path.expanduser("~/.claude-profiles")       # remapped to ../tools by run_dev.py
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (("   -> " + str(detail)) if (detail and not ok) else ""))


def load(fname, modname, env):
    """Fresh import of one tool under a given environment (module-level code reads it)."""
    for k in ("COCKPIT_FLEET_CONFIG_DIR", "CLAUDE_CONFIG_DIR"):
        os.environ.pop(k, None)
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location(modname, os.path.join(BASE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha8(s):
    return hashlib.sha256(s.encode()).hexdigest()[:8]


tmp = tempfile.mkdtemp(prefix="fleettest_")
fleet = os.path.join(tmp, "editing_config")
os.makedirs(fleet)
open(os.path.join(fleet, ".claude.json"), "w").write("{}")
try:
    # F1 ordinary machine: nothing configured -> exactly the old behaviour
    sw = load("claude-switch.py", "sw_unset", {})
    env = {"CLAUDE_CONFIG_DIR": "/x", "A": "1"}
    check("F1 unset: fleet slot is the shared default and CFG is ~/.claude.json",
          sw.FLEET_DIR is None and sw.FLEET_SLOT == "Claude Code-credentials"
          and sw.CFG == os.path.expanduser("~/.claude.json") and sw.DEFAULT_SLOT == sw.FLEET_SLOT)
    check("F1b unset: fleet_env ERASES CLAUDE_CONFIG_DIR (invariant #4)",
          "CLAUDE_CONFIG_DIR" not in sw.fleet_env(dict(env)) and sw.fleet_env(dict(env))["A"] == "1")

    # F2 fleet-dir mode: env var names the fleet dir
    sw = load("claude-switch.py", "sw_set", {"COCKPIT_FLEET_CONFIG_DIR": fleet})
    check("F2 set: fleet slot is Claude Code-credentials-<sha8(dir)>, not the shared slot",
          sw.FLEET_DIR == fleet and sw.FLEET_SLOT == "Claude Code-credentials-" + sha8(fleet)
          and sw.FLEET_SLOT != "Claude Code-credentials")
    check("F2b set: CFG is <dir>/.claude.json", sw.CFG == os.path.join(fleet, ".claude.json"))
    check("F2c set: fleet_env SETS CLAUDE_CONFIG_DIR to the fleet dir (does not erase it)",
          sw.fleet_env({"CLAUDE_CONFIG_DIR": "/pinned/elsewhere"}).get("CLAUDE_CONFIG_DIR") == fleet
          and sw.fleet_env({}).get("CLAUDE_CONFIG_DIR") == fleet)
    prof = os.path.join(BASE, "someprofile")
    check("F2d profile slots stay derived from the bare prefix, unaffected by fleet mode",
          sw.slot_for(prof) == "Claude Code-credentials-" + sha8(prof))

    # F3 pins Claude Code's slot naming against values computed OUTSIDE this code:
    #   printf '%s' /Users/you/.claude | shasum -a 256 | cut -c1-8   -> 34f6278e
    # WHY hardcoded and not recomputed here: recomputing with the same formula the tool
    # uses would pass even if both were wrong. The original pinned a hash read off a
    # real keychain; the path was changed to a neutral one for publication.
    check("F3 /Users/you/.claude -> Claude Code-credentials-34f6278e (a fleet config dir's own slot)",
          sw.slot_for("/Users/you/.claude") == "Claude Code-credentials-34f6278e")
    check("F3b a trailing slash names a DIFFERENT slot (a3b43677), so it must be refused",
          sw.slot_for("/Users/you/.claude/") == "Claude Code-credentials-a3b43677")

    # F4 file source + precedence (call the function with a temp BASE)
    pbase = os.path.join(tmp, "profiles_base"); os.makedirs(pbase)
    sw.BASE = pbase
    os.environ.pop("COCKPIT_FLEET_CONFIG_DIR", None)
    open(os.path.join(pbase, "fleet_config_dir"), "w").write(fleet + "\n")
    check("F4 fleet_config_dir FILE is honoured when the env var is absent (newline stripped)",
          sw._fleet_config_dir() == fleet)
    other = os.path.join(tmp, "other"); os.makedirs(other); open(os.path.join(other, ".claude.json"), "w").write("{}")
    os.environ["COCKPIT_FLEET_CONFIG_DIR"] = other
    check("F4b env var wins over the file", sw._fleet_config_dir() == other)

    # F5 fail closed: every bad value raises instead of falling back to the default slot
    bad = {"relative path": "editing_config", "trailing slash": fleet + "/", "missing dir": os.path.join(tmp, "nope"),
           "dir without .claude.json": tmp, "dot segment": fleet + "/../editing_config"}
    for label, v in bad.items():
        os.environ["COCKPIT_FLEET_CONFIG_DIR"] = v
        try:
            sw._fleet_config_dir(); ok = False
        except SystemExit:
            ok = True
        check(f"F5 invalid fleet dir fails CLOSED ({label})", ok)
    os.environ.pop("COCKPIT_FLEET_CONFIG_DIR", None)

    # F6 the guard: the default slot is untouchable in fleet mode, and subprocess is never reached
    swap = load("claude-swap.py", "swap_set", {"COCKPIT_FLEET_CONFIG_DIR": fleet})
    calls = []
    real_run = swap.subprocess.run
    swap.subprocess.run = lambda *a, **k: calls.append(a) or type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()
    kc = swap.Keychain()
    for op, args in (("read", ("Claude Code-credentials",)), ("write", ("Claude Code-credentials", {"a": 1})),
                     ("delete", ("Claude Code-credentials",))):
        try:
            getattr(kc, op)(*args); ok = False
        except swap.SwapError:
            ok = True
        check(f"F6 Keychain.{op}('Claude Code-credentials') is REFUSED in fleet mode", ok)
    check("F6b ...and no `security` process was spawned by any refused call", calls == [], calls)
    kc.read(swap.SHARED_SLOT); kc.read("Claude Code-credentials-deadbeef")
    check("F6c the fleet slot and profile slots are still readable (guard is not over-broad)", len(calls) == 2, len(calls))
    swap.subprocess.run = real_run
    check("F6d claude-swap's SHARED_SLOT is the fleet slot and Swapper defaults to it",
          swap.SHARED_SLOT == "Claude Code-credentials-" + sha8(fleet)
          and swap.Swapper(kc=object()).shared == swap.SHARED_SLOT)

    # F7 ordinary machine: the guard must be inert when nothing is configured (an ordinary machine's fleet IS that slot)
    swap = load("claude-swap.py", "swap_unset", {})
    calls = []
    swap.subprocess.run = lambda *a, **k: calls.append(a) or type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()
    swap.Keychain().read("Claude Code-credentials")
    check("F7 unset: the shared slot IS readable (ordinary machine behaviour unchanged)", len(calls) == 1)
    swap.subprocess.run = real_run

    # F8 every tool still imports cleanly in fleet mode (module-level code reads the new constants)
    for fname in ("claude-autoswitch.py", "claude-meters.py", "claude-cockpit.py", "claude-usage.py", "claude-ping.py"):
        try:
            load(fname, "imp_" + fname.replace("-", "_").replace(".py", ""), {"COCKPIT_FLEET_CONFIG_DIR": fleet}); ok, d = True, ""
        except BaseException as e:      # SystemExit included: a tool must not die at import
            ok, d = False, repr(e)
        check(f"F8 {fname} imports in fleet mode", ok, d)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    for k in ("COCKPIT_FLEET_CONFIG_DIR", "CLAUDE_CONFIG_DIR"):
        os.environ.pop(k, None)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
