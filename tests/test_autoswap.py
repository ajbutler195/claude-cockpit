#!/usr/bin/env python3
"""
Tests for the ONE-SHOT auto-swap in ~/.claude-profiles/claude-autoswitch.py.

Every external effect is stubbed: no keychain access, no server call, no real
swap, no real config write (cfg() is pointed at a temp file). Run with the SAME
interpreter launchd uses for the watcher:

    /usr/bin/python3 tests/test_autoswap.py
"""
import importlib.util, json, os, sys, tempfile, traceback
sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("aw", os.path.expanduser("~/.claude-profiles/claude-autoswitch.py"))
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)
aw.log = lambda msg: LOGS.append(msg)
LOGS = []

EMAIL = {n: f"{n}@test.invalid" for n in ["alpha", "beta", "gamma", "ops", "delta"]}
NAME = {v: k for k, v in EMAIL.items()}


class FakeSwapper:
    """Fleet is `fleet`; usage + movability per account from dicts."""
    shared = "SHARED"

    def __init__(self, fleet, fleet_usage, usage, movable=None):
        self.fleet, self.fleet_usage, self.usage = fleet, fleet_usage, usage
        self.movable = movable if movable is not None else {n: True for n in usage}
        self.read_usage_of = []

    def _oauth(self, svc):
        return {"tok": "fleet"}, None

    def whoami(self, o):
        return (EMAIL[self.fleet], "u")

    def movable_source(self, n):
        return ("slot", "anchor", {}) if self.movable.get(n) else (None, None, None)

    def account_usage(self, n):
        self.read_usage_of.append(n)
        return self.usage.get(n)


def make(fleet="alpha", five=98, seven=40, usage=None, movable=None, swap_rcs=None, profiles=None):
    usage = usage if usage is not None else {
        "beta": {"five": 30, "seven": 50}, "gamma": {"five": 10, "seven": 60},
        "ops": {"five": 0, "seven": 5}, "delta": {"five": 70, "seven": 10}}
    sw = FakeSwapper(fleet, {"five": five, "seven": seven}, usage, movable)
    aw.cswap.live_usage = lambda o, *a, **k: {"five": five, "seven": seven}
    aw.cswap.usage_age = lambda key: 0.0          # reading counts as fresh: no session-cache fallback
    aw.cswitch.profile_for_email = lambda e: NAME.get(e)
    calls = {"swaps": [], "cfg": [], "notes": [], "order": []}
    rcs = list(swap_rcs or [0])

    def runner(t):
        calls["swaps"].append(t); calls["order"].append(("swap", t))
        return rcs.pop(0) if rcs else 0

    def writer(**kw):
        calls["cfg"].append(kw); calls["order"].append(("cfg", kw))

    w = aw.Watcher(sw=sw, swap_runner=runner, notifier=lambda t, b: calls["notes"].append((t, b)),
                   cfg_writer=writer,
                   profile_lister=lambda: profiles or ["alpha", "beta", "gamma", "ops", "delta"])
    return w, sw, calls


def C(**over):
    c = dict(aw.DEFAULTS)
    # swap_enabled True: this suite models a machine whose user has opted in to the
    # no-browser swap (an armed watcher implies it). The opt-in itself, and what happens
    # with it off, is covered by tests/test_swap_enabled.py and case A15 below.
    c.update({"enabled": True, "auto_swap": True, "swap_enabled": True, "exclude_from_auto": ["ops"],
              "switch_at_pct": 97, "notify": True})
    c.update(over)
    return c


RESULTS, CASES = [], []


def case(name):
    def deco(fn):
        CASES.append((name, fn)); return fn
    return deco


@case("A1 below threshold -> nothing: no swap, no config write, no alert")
def a1():
    w, sw, k = make(five=96, seven=40)
    assert w.evaluate(C()) is None
    assert k["swaps"] == [] and k["cfg"] == [] and k["notes"] == []


@case("A2 crossing while armed -> swaps ONCE to lowest-usage eligible; ops (lowest of all) never picked")
def a2():
    w, sw, k = make(five=97.0)
    assert w.evaluate(C()) == "swapped"
    assert k["swaps"] == ["gamma"], k["swaps"]          # gamma 10% beats beta 30%; ops 0% excluded
    assert "ops" not in sw.read_usage_of, "excluded account should not even be read"


@case("A3 disarm is written BEFORE the swap runs (a crash mid-swap cannot cause a 2nd swap)")
def a3():
    w, sw, k = make()
    w.evaluate(C())
    assert k["order"][0] == ("cfg", {"auto_swap": False}), k["order"]
    assert k["order"][1][0] == "swap"


@case("A4 second crossing after the one swap (auto_swap now false) -> alert only, no swap")
def a4():
    w, sw, k = make()
    assert w.evaluate(C(auto_swap=False)) == "alerted"
    assert k["swaps"] == [] and k["cfg"] == [] and len(k["notes"]) == 1
    assert "claude-swap gamma" in k["notes"][0][1]


@case("A5 first target has no movable login after all (exit 2) -> tries the next")
def a5():
    w, sw, k = make(swap_rcs=[2, 0])
    assert w.evaluate(C()) == "swapped"
    assert k["swaps"] == ["gamma", "beta"], k["swaps"]


@case("A6 a real failure (exit 4) -> stops, does NOT try another account, stays disarmed")
def a6():
    w, sw, k = make(swap_rcs=[4, 0])
    assert w.evaluate(C()) == "alerted"
    assert k["swaps"] == ["gamma"] and k["cfg"] == [{"auto_swap": False}]
    assert "FAILED" in k["notes"][-1][0]


@case("A7 nobody eligible -> alert, NO swap, stays ARMED (can still fire later)")
def a7():
    w, sw, k = make(usage={"beta": {"five": 80, "seven": 10}, "gamma": {"five": 5, "seven": 90},
                           "ops": {"five": 0, "seven": 0}, "delta": {"five": 61, "seven": 1}})
    assert w.evaluate(C()) == "alerted"
    assert k["swaps"] == [] and k["cfg"] == []


@case("A8 weekly limit alone triggers (session 50%, week 97%)")
def a8():
    w, sw, k = make(five=50, seven=97)
    assert w.evaluate(C()) == "swapped"


@case("A9 dry run while armed -> no swap, no disarm, no notification")
def a9():
    w, sw, k = make()
    assert w.evaluate(C(), do_switch=False) == "alerted"
    assert k["swaps"] == [] and k["cfg"] == [] and k["notes"] == []


@case("A15 swap opt-in OFF -> the alert names claude-switch (the command that works), never claude-swap")
def a15():
    w, sw, k = make()
    assert w.evaluate(C(auto_swap=False, swap_enabled=False)) == "alerted"
    assert k["swaps"] == [], k["swaps"]
    body = k["notes"][0][1]
    assert "claude-switch gamma" in body and "claude-swap" not in body, body


@case("A10 account without a movable login is skipped even if its usage is lowest")
def a10():
    w, sw, k = make(movable={"beta": True, "gamma": False, "ops": True, "delta": True})
    w.evaluate(C())
    assert k["swaps"] == ["beta"], k["swaps"]


@case("A11 ops excluded even when it is the ONLY candidate with room")
def a11():
    w, sw, k = make(usage={"beta": {"five": 90, "seven": 10}, "gamma": {"five": 99, "seven": 1},
                           "ops": {"five": 0, "seven": 0}, "delta": {"five": 75, "seven": 1}})
    assert w.evaluate(C()) == "alerted" and k["swaps"] == []


@case("A12 config FAILS CLOSED: missing exclude_from_auto -> watcher refuses to run")
def a12():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"enabled": True, "auto_swap": True}, tmp); tmp.close()
    old = aw.CFG_FILE; aw.CFG_FILE = tmp.name
    try:
        aw.cfg(); raise AssertionError("cfg() should have exited")
    except SystemExit:
        pass
    finally:
        aw.CFG_FILE = old; os.unlink(tmp.name)


@case("A13 exclusion is case-insensitive ('Ops' in config still excludes ops)")
def a13():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"enabled": True, "auto_swap": True, "exclude_from_auto": ["Ops"]}, tmp); tmp.close()
    old = aw.CFG_FILE; aw.CFG_FILE = tmp.name
    try:
        c = aw.cfg()
    finally:
        aw.CFG_FILE = old; os.unlink(tmp.name)
    assert c["exclude_from_auto"] == ["ops"]
    w, sw, k = make(usage={"beta": {"five": 90, "seven": 1}, "gamma": {"five": 90, "seven": 1},
                           "ops": {"five": 0, "seven": 0}, "delta": {"five": 90, "seven": 1}})
    assert w.evaluate(c) == "alerted" and k["swaps"] == []


@case("A14 set_cfg preserves every other key and the _comments")
def a14():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"_comment": "keep me", "enabled": True, "auto_swap": True, "exclude_from_auto": ["ops"]}, tmp)
    tmp.close()
    old = aw.CFG_FILE; aw.CFG_FILE = tmp.name
    try:
        aw.set_cfg(auto_swap=False)
        d = json.load(open(tmp.name))
    finally:
        aw.CFG_FILE = old; os.unlink(tmp.name)
    assert d == {"_comment": "keep me", "enabled": True, "auto_swap": False, "exclude_from_auto": ["ops"]}


if __name__ == "__main__":
    print(f"python {sys.version.split()[0]}")
    for name, fn in CASES:
        try:
            fn(); RESULTS.append((name, "PASS", ""))
        except Exception as e:
            RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
    for name, res, err in RESULTS:
        print(f"{res}  {name}")
        if err:
            print("      " + err.replace("\n", "\n      "))
    print(f"\n{sum(r == 'PASS' for _, r, _ in RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(r == "PASS" for _, r, _ in RESULTS) else 1)
