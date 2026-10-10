#!/usr/bin/env python3
"""
Tests for ~/.claude-profiles/claude-meters.py (usage history + run-out forecast).
Pure math against hand-worked answers; no keychain, no network, temp files only.
Run with the watcher's interpreter:  /usr/bin/python3 tests/test_meters.py
"""
import importlib.util, json, os, sys, tempfile, traceback
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("cm", os.path.expanduser("~/.claude-profiles/claude-meters.py"))
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)

AT = 1_800_000_000.0   # fixed "now" so every expected value is exact


def iso(h_from_now):
    import datetime
    return datetime.datetime.fromtimestamp(AT + h_from_now * 3600, datetime.timezone.utc).isoformat()


def acct(wk, R_h, f=0.0, F_h=None):
    return {"seven": wk, "seven_resets": iso(R_h), "five": f, "five_resets": iso(F_h) if F_h else None}


def near(a, b, tol):
    assert a is not None and abs(a - b) <= tol, f"{a} != {b} (±{tol})"


CASES = []


def case(name):
    def d(fn):
        CASES.append((name, fn)); return fn
    return d


@case("M1 cost: Opus 5.5 1M in + 1M out + 1M cache read + 1M 5m write + 1M 1h write = 4+20+0.2+5+8 = $37.20")
def m1():
    u = {"input_tokens": 1e6, "output_tokens": 1e6, "cache_read_input_tokens": 1e6,
         "cache_creation": {"ephemeral_5m_input_tokens": 1e6, "ephemeral_1h_input_tokens": 1e6}}
    near(cm.usage_cost("claude-opus-5-5", u), 37.2, 1e-9)


@case("M2 cost: no breakdown -> cache_creation_input_tokens priced as 5-minute writes; Sonnet = half of Opus")
def m2():
    u = {"input_tokens": 1e6, "cache_creation_input_tokens": 1e6}
    near(cm.usage_cost("claude-sonnet-5-5", u), 2 + 2.5, 1e-9)
    near(cm.usage_cost("<synthetic>", u), 0.0, 0)


@case("M3 transcript scan counts each message ONCE (3 lines, same id) and resumes without recounting")
def m3():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    line = lambda mid, ts: json.dumps({"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": "claude-opus-5-5", "usage": {"output_tokens": 1e6}}}) + "\n"
    tmp.write(line("msg_A", "2026-10-01T10:00:00Z") * 3)
    tmp.close()
    hours = {}
    off, last = cm.scan_file(tmp.name, 0, None, hours)
    near(sum(hours.values()), 20.0, 1e-9)               # $20, not $60
    with open(tmp.name, "a") as f:                        # another block of msg_A, then msg_B
        f.write(line("msg_A", "2026-10-01T10:00:01Z") + line("msg_B", "2026-10-01T11:30:00Z"))
    cm.scan_file(tmp.name, off, last, hours)
    near(sum(hours.values()), 40.0, 1e-9)               # only msg_B added
    os.unlink(tmp.name)


@case("M4 meter increments: rises summed, a drop counts as a refill (new value only)")
def m4():
    rows = [{"ts": 0, "acc": {"a": {"seven": 10}, "b": {"seven": 95}}},
            {"ts": 3600, "acc": {"a": {"seven": 14}, "b": {"seven": 99}}},
            {"ts": 7200, "acc": {"a": {"seven": 14}, "b": {"seven": 3}}}]   # b refilled
    near(sum(d for _, _, d in cm.increments(rows)), 4 + 4 + 3, 1e-9)


@case("M5 K (weekly pts per 5h pt) from the log; seed when evidence < 20 five-points")
def m5():
    rows = [{"ts": 0, "acc": {"a": {"five": 0, "seven": 50}}},
            {"ts": 1, "acc": {"a": {"five": 40, "seven": 62}}}]
    near(cm.estimate_k(rows)[0], 12 / 40, 1e-9)
    near(cm.estimate_k(rows[:1])[0], cm.K_SEED, 0)


@case("M6 sim: one empty account, 1 pt/h, no 5h pressure -> dry at 100 h")
def m6():
    r = cm.simulate({"a": acct(0, 200)}, 1.0, 1.0, at=AT)
    near(r["first_dry_h"], 100, 0.1)


@case("M7 sim: 5h cap binds — K=0.3 means 30 pts per window; at 10 pts/h dry at 3 h, blocked 2 h per 5 h")
def m7():
    r = cm.simulate({"a": acct(0, 500)}, 10.0, 0.3, at=AT, horizon=10)
    near(r["first_dry_h"], 3.0, 0.06)
    near(r["blocked_h"], 4.0, 0.15)       # blocked 3-5 h and 8-10 h


@case("M8 sim: spends the account that refills SOONEST first, so nothing expires unused")
def m8():
    st = {"later": acct(0, 100), "soon": acct(50, 10)}
    r = cm.simulate(st, 5.0, 1.0, at=AT, horizon=20)
    assert r["use_first"] == "soon", r
    near(r["wasted"], 0.0, 0.3)            # 50 left at 5/h = exactly 10 h


@case("M9 sim: an exhausted account is blocked until its refill, then serves again")
def m9():
    r = cm.simulate({"a": acct(100, 2)}, 1.0, 1.0, at=AT, horizon=10)
    near(r["first_dry_h"], 0.0, 0.06)
    near(r["blocked_h"], 2.0, 0.1)


@case("M10 safe pace: never blocked at the safe pace, blocked just above it")
def m10():
    st = {"a": acct(60, 30), "b": acct(20, 90)}
    s = cm.safe_rate(st, 0.3, at=AT)
    assert cm.simulate(st, s, 0.3, at=AT)["blocked_h"] == 0
    assert cm.simulate(st, s * 1.05 + 0.05, 0.3, at=AT)["blocked_h"] > 0


@case("M11 pace from meters: 24 points over the last 24 h = 1.0 pt/h, labelled meters")
def m11():
    rows = [{"ts": AT - 24 * 3600 - 60 + i * 3600, "acc": {"a": {"seven": float(i)}}} for i in range(26)]
    p = cm.paces(rows, {}, None, at=AT)
    near(p["24h"]["v"], 1.0, 0.05)
    assert p["24h"]["src"] == "meters" and p["7d"]["src"] == "calibrating"


@case("M12 pace estimate from tokens: $ x calibrated points-per-$ / hours, labelled est.")
def m12():
    hours = {str(int((AT - 3600 * (i + 1)) // 3600)): 10.0 for i in range(48)}   # $10/h for 48 h
    cal = {"ready": True, "pts_per_usd": 0.5}
    p = cm.paces([], hours, cal, at=AT)
    near(p["48h"]["v"], 5.0, 0.3)
    assert p["48h"]["src"] == "est"


@case("M13 calibration refuses to fit on < 1 h or < 5 points of evidence")
def m13():
    rows = [{"ts": AT, "acc": {"a": {"seven": 10}}}, {"ts": AT + 600, "acc": {"a": {"seven": 20}}}]
    assert cm.calibration(rows, {str(int(AT // 3600)): 5.0})["ready"] is False


class FakeServer:
    """Stands in for urllib.request.urlopen: counts calls, answers 200 or 429."""
    def __init__(self, status=200):
        self.calls, self.status = 0, status

    def __call__(self, req, timeout=None):
        import io, urllib.error
        self.calls += 1
        if self.status == 429:
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {"retry-after": "0"}, io.BytesIO(b"{}"))
        body = {"five_hour": {"utilization": 40.0}, "seven_day": {"utilization": 60.0},
                "account": {"email": "x@test.invalid", "uuid": "u-x"}}
        return io.BytesIO(json.dumps(body).encode())


def with_fake(server, fn):
    import urllib.request, time as _t
    sw = cm.cswap
    old_open, old_cache = urllib.request.urlopen, sw.USAGE_CACHE
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False); tmp.close(); os.unlink(tmp.name)
    urllib.request.urlopen, sw.USAGE_CACHE = server, tmp.name
    try:
        fn(sw, {"accessToken": "fake-" + str(_t.time()), "expiresAt": (_t.time() + 7200) * 1000})
    finally:
        urllib.request.urlopen, sw.USAGE_CACHE = old_open, old_cache
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


@case("U1 usage cache: a second read inside max_age does NOT call the server")
def u1():
    srv = FakeServer()
    def body(sw, tok):
        assert sw.live_usage(tok, "acct", 240)["five"] == 40.0
        assert sw.live_usage(tok, "acct", 240)["five"] == 40.0
        assert srv.calls == 1, srv.calls
    with_fake(srv, body)


@case("U2 a 429 sets a backoff: the next read returns the last good value without calling")
def u2():
    srv = FakeServer()
    def body(sw, tok):
        sw.live_usage(tok, "acct", 0)              # 200, cached
        srv.status = 429
        assert sw.live_usage(tok, "acct", 0)["five"] == 40.0     # 429 -> last good value
        n = srv.calls
        assert sw.live_usage(tok, "acct", 0)["five"] == 40.0     # inside backoff: no call
        assert srv.calls == n, (srv.calls, n)
    with_fake(srv, body)


@case("U3 identity is cached per token: asking twice calls the server once")
def u3():
    srv = FakeServer()
    def body(sw, tok):
        assert sw.server_whoami(tok)[0] == "x@test.invalid"
        assert sw.server_whoami(tok)[0] == "x@test.invalid"
        assert srv.calls == 1, srv.calls
    with_fake(srv, body)


if __name__ == "__main__":
    print(f"python {sys.version.split()[0]}")
    ok = 0
    for name, fn in CASES:
        try:
            fn(); print(f"PASS  {name}"); ok += 1
        except Exception as e:
            print(f"FAIL  {name}\n      {type(e).__name__}: {e}\n      " +
                  traceback.format_exc(limit=2).replace("\n", "\n      "))
    print(f"\n{ok}/{len(CASES)} passed")
    sys.exit(0 if ok == len(CASES) else 1)
