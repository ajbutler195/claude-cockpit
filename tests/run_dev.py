"""Run a cockpit test file against the DEV copy in ../tools instead of the live
~/.claude-profiles.

WHY: every cockpit tool and every test finds its siblings through
os.path.expanduser("~/.claude-profiles"). Overriding HOME would redirect that, but
`security` also follows HOME, so the real login keychain (which the swap tests use
for throwaway `cockpit-swaptest-*` items) becomes unreachable. So only paths under
~/.claude-profiles are remapped; every other expanduser call stays real.

Usage: /usr/bin/python3 run_dev.py <test_file.py>     (use /usr/bin/python3: that is
the 3.9 interpreter the launchd watcher runs, gotcha #49)
"""
import os, runpy, sys

sys.dont_write_bytecode = True      # gotcha #13/#30: bytecode dirs became fake profiles
DEV = os.path.normpath(os.environ.get("COCKPIT_DEV_TOOLS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))   # env override = mutation testing
_real_expanduser = os.path.expanduser


def _expanduser(p):
    if isinstance(p, str) and (p == "~/.claude-profiles" or p.startswith("~/.claude-profiles/")):
        return os.path.normpath(os.path.join(DEV, p[len("~/.claude-profiles/"):])) if p != "~/.claude-profiles" else DEV
    return _real_expanduser(p)


os.path.expanduser = _expanduser
test = os.path.abspath(sys.argv[1])
sys.argv = [test] + sys.argv[2:]
sys.path.insert(0, os.path.dirname(test))
runpy.run_path(test, run_name="__main__")
