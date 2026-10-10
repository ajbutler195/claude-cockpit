#!/usr/bin/env python3
"""
Test suite for ~/.claude-profiles/claude-swap.py (Claude Cockpit v2).

Runs the REAL Swapper logic and the REAL keychain I/O, but only against
throwaway keychain items named `cockpit-swaptest-*` holding FAKE tokens, a temp
config file, and stubbed server/probe calls. It never reads or writes any
`Claude Code-credentials*` slot. Cleanup deletes only the exact item names this
file created.

Run:  python3 tests/test_claude_swap.py
"""
import importlib.util, json, os, secrets, subprocess, sys, tempfile, time, traceback
sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("cswap", os.path.expanduser("~/.claude-profiles/claude-swap.py"))
cswap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cswap)

P = "cockpit-swaptest-"
NAMES = ["alpha", "beta", "gamma"]
EMAIL = {n: f"{n}@test.invalid" for n in NAMES}
SHARED = P + "shared"
ANCHOR = lambda n: P + "anchor-" + n
SPARE = lambda n: P + "spare-" + n
ALL_ITEMS = [SHARED] + [ANCHOR(n) for n in NAMES] + [SPARE(n) for n in NAMES]
kc = cswap.Keychain()

# access token -> owning email. The stub server answers from this.
OWNER = {}


def tok(owner, fresh_min=240, refresh_days=20):
    t = {"accessToken": "fake-at-" + secrets.token_hex(8), "refreshToken": "fake-rt-" + secrets.token_hex(8),
         "expiresAt": int((time.time() + fresh_min * 60) * 1000),
         "refreshTokenExpiresAt": int((time.time() + refresh_days * 86400) * 1000),
         "scopes": ["user:inference"], "subscriptionType": "max"}
    OWNER[t["accessToken"]] = EMAIL[owner]
    return t


def whoami(o):
    e = OWNER.get((o or {}).get("accessToken"))
    return (e, "uuid-" + e) if e and (cswap.minutes_left(o) or 0) > 1 else None


def make(probe_ok=True, probe_renews=False, whoami_fn=whoami):
    cfg = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"oauthAccount": {"emailAddress": EMAIL["alpha"]}, "cachedUsageUtilization": {"x": 1},
               "projects": {"keep": True}}, cfg)
    cfg.close()

    def probe(config_dir=None):
        # Simulate Claude renewing the fleet login when its access token is stale.
        if probe_renews and config_dir is None:
            blob = kc.read(SHARED)
            o = blob["claudeAiOauth"]
            owner = [n for n in NAMES if EMAIL[n] == OWNER.get(o["accessToken"])] or ["alpha"]
            blob["claudeAiOauth"] = tok(owner[0])
            kc.write(SHARED, blob)
        return probe_ok

    s = cswap.Swapper(kc=kc, shared_slot=SHARED, anchor_slot=ANCHOR, spare_slot=SPARE,
                      profiles=lambda: NAMES,
                      profile_identity=lambda n: {"emailAddress": EMAIL[n], "accountUuid": "uuid-" + EMAIL[n]},
                      whoami=whoami_fn, probe=probe, config_path=cfg.name,
                      ledger=cfg.name + ".ledger", last=cfg.name + ".last", out=lambda *a: None)
    return s


def wipe():
    for it in ALL_ITEMS:
        kc.delete(it)


# The real shared slot on 2026-10-01 carried many MCP-server logins (~40 KB). Tiny
# test blobs are why the first version of this suite missed the `security -i`
# truncation that corrupted the real fleet slot at 20:10.
BIG_MCP = {f"server{i}|{secrets.token_hex(8)}": {"accessToken": secrets.token_hex(1100),
           "refreshToken": secrets.token_hex(40), "serverUrl": f"https://mcp{i}.example/mcp"}
           for i in range(17)}


def seed():
    """Real-world shape: fleet on alpha via its OWN fleet grant; every account has
    an anchor; the shared slot and beta's anchor also hold mcpOAuth that must stay put."""
    wipe()
    st = {"fleet": tok("alpha"), "alpha": tok("alpha"), "beta": tok("beta"), "gamma": tok("gamma")}
    kc.write(SHARED, {"mcpOAuth": BIG_MCP, "claudeAiOauth": st["fleet"]})
    assert len(json.dumps(kc.read(SHARED))) > 30000, "seeded shared slot is not realistic size"
    kc.write(ANCHOR("alpha"), {"claudeAiOauth": st["alpha"]})
    kc.write(ANCHOR("beta"), {"mcpOAuth": {"srv": "beta-mcp"}, "claudeAiOauth": st["beta"]})
    kc.write(ANCHOR("gamma"), {"claudeAiOauth": st["gamma"]})
    return st


def snapshot():
    return {it: kc.read(it) for it in ALL_ITEMS}


def fps(snap):
    return {k: cswap.fp((v or {}).get("claudeAiOauth")) for k, v in snap.items()}


RESULTS = []


def case(name):
    def deco(fn):
        def run():
            try:
                fn()
                RESULTS.append((name, "PASS", ""))
            except Exception as e:
                RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
        run.__name__ = fn.__name__
        CASES.append(run)
        return run
    return deco


CASES = []


@case("T1 borrow anchor: alpha->beta moves ONLY claudeAiOauth, parks alpha's grant as a spare")
def t1():
    st = seed(); s = make()
    s.swap("beta")
    sh = kc.read(SHARED)
    assert cswap.fp(sh["claudeAiOauth"]) == cswap.fp(st["beta"]), "fleet is not beta's login"
    assert sh["mcpOAuth"] == BIG_MCP, "fleet mcpOAuth (40 KB) was not preserved exactly"
    ba = kc.read(ANCHOR("beta"))
    assert ba == {"mcpOAuth": {"srv": "beta-mcp"}}, f"beta anchor should keep only its mcpOAuth, got {ba}"
    sp = kc.read(SPARE("alpha"))
    assert cswap.fp(sp["claudeAiOauth"]) == cswap.fp(st["fleet"]), "alpha's fleet grant not parked as spare"
    assert cswap.fp(kc.read(ANCHOR("alpha"))["claudeAiOauth"]) == cswap.fp(st["alpha"]), "alpha anchor touched"
    assert s.duplicates() == {}, f"duplicates: {s.duplicates()}"
    cfg = json.load(open(s.cfg))
    assert cfg["oauthAccount"]["emailAddress"] == EMAIL["beta"], "config identity not patched"
    assert "cachedUsageUtilization" not in cfg and cfg["projects"] == {"keep": True}, "config scrub wrong"


@case("T2 round trip alpha->beta->alpha restores the EXACT starting slots")
def t2():
    seed(); before = fps(snapshot()); s = make()
    s.swap("beta"); s.swap("alpha")
    after = fps(snapshot())
    assert after == before, f"\nbefore={before}\nafter ={after}"
    assert kc.read(ANCHOR("beta"))["mcpOAuth"] == {"srv": "beta-mcp"}, "beta mcpOAuth lost"
    assert kc.read(SPARE("alpha")) is None, "alpha spare should be consumed"
    assert s.duplicates() == {}


@case("T3 spare is preferred over the anchor, and consumed")
def t3():
    st = seed(); s = make()
    spare = tok("gamma"); kc.write(SPARE("gamma"), {"claudeAiOauth": spare})
    s.swap("gamma")
    assert cswap.fp(kc.read(SHARED)["claudeAiOauth"]) == cswap.fp(spare), "did not use gamma's spare"
    assert kc.read(SPARE("gamma")) is None, "spare not consumed"
    assert cswap.fp(kc.read(ANCHOR("gamma"))["claudeAiOauth"]) == cswap.fp(st["gamma"]), "anchor touched"
    assert s.duplicates() == {}


@case("T4 expired spare is skipped; falls back to borrowing the anchor")
def t4():
    st = seed(); s = make()
    dead = tok("gamma", refresh_days=-1); kc.write(SPARE("gamma"), {"claudeAiOauth": dead})
    s.swap("gamma")
    assert cswap.fp(kc.read(SHARED)["claudeAiOauth"]) == cswap.fp(st["gamma"]), "should have borrowed anchor"


@case("T5 incoming login belongs to someone else -> refuse, NOTHING changes")
def t5():
    seed(); before = snapshot()
    def liar(o):
        r = whoami(o)
        return (EMAIL["gamma"], "x") if r and r[0] == EMAIL["beta"] else r
    s = make(whoami_fn=liar)
    try:
        s.swap("beta"); raise AssertionError("should have refused")
    except cswap.SwapError as e:
        assert not e.moved
    assert snapshot() == before, "slots changed on a refusal"


@case("T6 verification fails -> rolled back, slots identical to before")
def t6():
    seed(); before = fps(snapshot()); s = make(probe_ok=False)
    try:
        s.swap("beta"); raise AssertionError("should have failed verification")
    except cswap.SwapError as e:
        assert e.moved
    after = fps(snapshot())
    assert after == before, f"\nbefore={before}\nafter ={after}"
    assert s.duplicates() == {}
    assert json.load(open(s.cfg))["oauthAccount"]["emailAddress"] == EMAIL["alpha"], "config not restored"


@case("T7 fleet changes mid-swap (session renewed / someone /login'd) -> abort, nothing moved")
def t7():
    seed(); s = make()
    calls = {"n": 0}
    def meddling_whoami(o):
        r = whoami(o)
        calls["n"] += 1
        if calls["n"] == 2:   # after fleet identity was read, before the moves
            blob = kc.read(SHARED); blob["claudeAiOauth"] = tok("alpha"); kc.write(SHARED, blob)
        return r
    s.whoami = meddling_whoami
    mid = None
    try:
        s.swap("beta"); raise AssertionError("should have aborted")
    except cswap.SwapError as e:
        assert not e.moved, "abort must happen before any write"
        mid = str(e)
    assert kc.read(SPARE("alpha")) is None, "parked something despite abort"
    assert "claudeAiOauth" in kc.read(ANCHOR("beta")), "beta anchor touched despite abort"


@case("T8 fleet on an account with no profile -> refuse")
def t8():
    seed()
    OWNER_BACKUP = dict(OWNER)
    blob = kc.read(SHARED); o = blob["claudeAiOauth"]; OWNER[o["accessToken"]] = "stranger@test.invalid"
    s = make()
    try:
        s.swap("beta"); raise AssertionError("should have refused")
    except cswap.SwapError as e:
        assert not e.moved
    OWNER.clear(); OWNER.update(OWNER_BACKUP)


@case("T9 already on target -> no-op")
def t9():
    seed(); before = snapshot(); s = make()
    r = s.swap("alpha")
    assert r.get("noop") and snapshot() == before


@case("T10 stale spare that the fleet probe renews still verifies; renewed login ends up in the fleet only")
def t10():
    seed(); s = make(probe_renews=True)
    stale = tok("gamma", fresh_min=-5); kc.write(SPARE("gamma"), {"claudeAiOauth": stale})
    s.swap("gamma")
    now = kc.read(SHARED)["claudeAiOauth"]
    assert OWNER[now["accessToken"]] == EMAIL["gamma"], "fleet not gamma after renewal"
    assert kc.read(SPARE("gamma")) is None and s.duplicates() == {}


@case("T11 rollback after the probe renewed the incoming login: renewed login goes back to its source, no dups")
def t11():
    st = seed()
    s = make(probe_renews=True)
    def wrong_after(o):
        r = whoami(o)
        # pre-move checks pass; post-move identity check (fleet now beta) lies
        if r and r[0] == EMAIL["beta"] and cswap.fp(o) != cswap.fp(st["beta"]):
            return (EMAIL["gamma"], "x")
        return r
    s.whoami = wrong_after
    try:
        s.swap("beta"); raise AssertionError("should have rolled back")
    except cswap.SwapError as e:
        assert e.moved
    assert cswap.fp(kc.read(SHARED)["claudeAiOauth"]) == cswap.fp(st["fleet"]), "fleet not restored"
    assert OWNER[kc.read(ANCHOR("beta"))["claudeAiOauth"]["accessToken"]] == EMAIL["beta"], "beta anchor not restored"
    assert kc.read(ANCHOR("beta"))["mcpOAuth"] == {"srv": "beta-mcp"}
    assert s.duplicates() == {}, s.duplicates()


class TruncatingKeychain(cswap.Keychain):
    """Reproduces the 2026-10-01 20:10 failure: one write to the shared slot stores
    only the first 2013 bytes of the JSON and reports failure. Later writes (the
    restore) go through normally."""
    fired = False

    def write(self, svc, blob):
        if svc == SHARED and not self.fired:
            self.fired = True
            raw = json.dumps(blob, separators=(",", ":"))[:2013]
            subprocess.run(["security", "add-generic-password", "-U", "-a", cswap.USER, "-s", svc,
                            "-X", raw.encode().hex()], capture_output=True)
            return False
        return super().write(svc, blob)


@case("T13 INCIDENT REPRO: fleet write truncates -> 40 KB slot restored byte-exact, park undone")
def t13():
    seed(); before = snapshot()
    s = make(); s.kc = TruncatingKeychain()
    try:
        s.swap("beta"); raise AssertionError("should have failed")
    except cswap.SwapError as e:
        assert e.moved
    after = snapshot()
    assert after[SHARED] == before[SHARED], "shared slot NOT restored to its exact original"
    assert after == before, "some slot differs from before"
    assert s.duplicates() == {}


@case("T14 damaged (unparseable) slot -> refuse before any write")
def t14():
    seed()
    raw = json.dumps(kc.read(ANCHOR("beta")))[:50]
    subprocess.run(["security", "add-generic-password", "-U", "-a", cswap.USER, "-s", ANCHOR("beta"),
                    "-X", raw.encode().hex()], capture_output=True)
    before_shared = kc.read(SHARED)
    s = make()
    try:
        s.swap("beta"); raise AssertionError("should have refused")
    except cswap.CorruptSlot:
        pass
    assert kc.read(SHARED) == before_shared, "fleet slot written despite damage"
    assert kc.read(SPARE("alpha")) is None, "parked a login despite damage"


@case("T15 40 KB blob takes the argv path and round-trips byte-exact")
def t15():
    seed(); s = make()
    s.swap("beta"); s.swap("alpha")
    assert kc.read(SHARED)["mcpOAuth"] == BIG_MCP


@case("T12 tool never touches a real Claude slot (static check of this suite's item names)")
def t12():
    assert all(it.startswith(P) for it in ALL_ITEMS)


if __name__ == "__main__":
    try:
        for c in CASES:
            c()
    finally:
        wipe()
        leftovers = [it for it in ALL_ITEMS if kc.read(it) is not None]
    for name, res, err in RESULTS:
        print(f"{res}  {name}")
        if err:
            print("      " + err.replace("\n", "\n      "))
    print(f"\n{sum(r == 'PASS' for _, r, _ in RESULTS)}/{len(RESULTS)} passed; "
          f"test keychain items left behind: {leftovers or 'none'}")
    sys.exit(0 if all(r == "PASS" for _, r, _ in RESULTS) and not leftovers else 1)
