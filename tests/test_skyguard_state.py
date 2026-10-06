"""Plan 6 Task 4 Step 2 (M2 contents S1-S8): the lift-state reader, written BEFORE it exists.

Task 4 wrote these as xfail(strict=True); Task 7 built `tools/skyguard/state.py` and flipped them to
plain tests (asserts unchanged). API as built:
    state.write(path, data)              atomic; computes `sum`
    state.read(path) -> st               never raises; on rejection st.lifts == [] and st.reason
    state.honoured(st, session_id, now, session_start) -> list of honoured lifts
    state.canonical_sum(data) -> str     sha256 of the canonical JSON without `sum`
Times are epoch seconds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))

H = 3600.0
NOW = 1_800_000_000.0
SESSION = "sess-A"
START = NOW - 2 * H


def _state():
    from skyguard import state  # noqa: E402 -- absent until Task 7
    return state


def _lift(**kw):
    lift = {"kind": "guards", "reason": "test", "session_id": SESSION, "agent": "claude",
            "created": NOW - H, "expires_at": NOW + H}
    lift.update(kw)
    return lift


def _write(tmp_path, lifts, **extra):
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"v": 1, "lifts": lifts, "persistent": {}}
    data.update(extra)
    st.write(str(p), data)
    return p


def _forge(tmp_path, body, raw_pad=0):
    """A state file with a CORRECT sum that state.write() would refuse to produce (what a same-user forger can do)."""
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = dict(body)
    body["sum"] = st.canonical_sum(body)
    text = json.dumps(body) + " " * raw_pad
    p.write_bytes(text.encode("ascii"))
    return p


def _good_body(**extra):
    body = {"v": 1, "lifts": [_lift()], "persistent": {}}
    body.update(extra)
    return body


def _honoured(p, session=SESSION, now=NOW, start=START):
    st = _state()
    return st.honoured(st.read(str(p)), session, now, start)


def test_s1_hand_edit_with_a_wrong_checksum_reads_as_guards_on(tmp_path):
    p = _write(tmp_path, [_lift()])
    data = json.loads(p.read_text(encoding="utf-8"))
    data["lifts"][0]["reason"] = "edited by hand"
    p.write_text(json.dumps(data), encoding="utf-8")
    st = _state().read(str(p))
    assert st.lifts == [] and "sum" in st.reason.lower()


def test_s2_recomputed_checksum_in_the_same_session_is_honoured(tmp_path):
    p = _write(tmp_path, [_lift()])
    data = json.loads(p.read_text(encoding="utf-8"))
    data["lifts"][0]["reason"] = "forged, but with a correct sum"
    body = {k: v for k, v in data.items() if k != "sum"}
    data["sum"] = _state().canonical_sum(body)
    p.write_text(json.dumps(data), encoding="utf-8")
    assert len(_honoured(p)) == 1  # honoured -- R6: forgery buys no silence (SHADOW is Task 9's test)


def test_s3_another_session_id_is_ignored(tmp_path):
    p = _write(tmp_path, [_lift(session_id="sess-OTHER")])
    assert _honoured(p) == []


def test_s4_past_expires_at_is_ignored(tmp_path):
    p = _write(tmp_path, [_lift(expires_at=NOW - 1)])
    assert _honoured(p) == []


@pytest.mark.parametrize("shape", ["garbage", "directory", "oversized", "unknown_version", "junction"])
def test_s5_unreadable_shapes_read_as_guards_on_without_raising(tmp_path, shape):
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if shape == "garbage":
        p.write_bytes(b"\x00not json{")
    elif shape == "directory":
        p.mkdir()
    elif shape == "oversized":
        good = _forge(tmp_path, _good_body(lifts=[_lift(reason="x" * (70 * 1024))]))
        assert good.stat().st_size > 64 * 1024
    elif shape == "unknown_version":
        _forge(tmp_path, _good_body(v=99))
    elif shape == "junction":
        real = tmp_path / "elsewhere"
        real.mkdir()
        (real / "state.json").write_text("{}", encoding="utf-8")
        p.parent.rmdir() if not any(p.parent.iterdir()) else None
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(p.parent), str(real)], capture_output=True)
            if r.returncode != 0:
                pytest.skip("cannot create a junction here")
        else:
            os.symlink(str(real), str(p.parent))
    got = st.read(str(p))
    assert got.lifts == [] and got.reason


def test_s6_deleted_state_reads_as_guards_on(tmp_path):
    got = _state().read(str(tmp_path / ".skyguard" / "state.json"))
    assert got.lifts == []


def test_s7_expires_at_beyond_8h_is_clamped(tmp_path):
    created = NOW - H
    p = _write(tmp_path, [_lift(created=created, expires_at=created + 24 * H)])
    assert len(_honoured(p, now=created + 8 * H - 60)) == 1
    assert _honoured(p, now=created + 8 * H + 60) == []


def test_s8_a_lift_dated_before_the_session_start_is_ignored(tmp_path):
    p = _write(tmp_path, [_lift(created=START - 60)])
    assert _honoured(p) == []


# ---- Task 7 additions: the clauses S1-S8 do not pin on their own ----------------------------------

def test_a_valid_state_reached_through_a_junction_is_still_rejected(tmp_path):
    """l2-state-reparse: a VALID, correctly summed state behind a junction must not be read. (S5's junction row
    points at a file that would fail on the sum anyway, so it cannot tell the reparse check from the sum check.)"""
    if os.name != "nt":
        pytest.skip("junctions are a Windows shape")
    st = _state()
    real = tmp_path / "elsewhere"
    real.mkdir()
    st.write(str(real / "state.json"), {"v": 1, "lifts": [_lift()], "persistent": {}})
    link = tmp_path / ".skyguard"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create a junction here")
    got = st.read(str(link / "state.json"))
    assert got.lifts == [] and "reparse" in got.reason


def test_a_lift_dated_in_the_future_is_ignored(tmp_path):
    """l2-state-future: `created` after `now` (plus skew) would let a forged date stretch the 8 h clamp."""
    p = _write(tmp_path, [_lift(created=NOW + 600, expires_at=NOW + 3600)])
    assert _honoured(p) == []


def test_a_little_clock_skew_is_tolerated(tmp_path):
    p = _write(tmp_path, [_lift(created=NOW + 5, expires_at=NOW + 3600)])
    assert len(_honoured(p)) == 1


def test_a_bool_is_not_the_version_one(tmp_path):
    p = _forge(tmp_path, _good_body(v=True))
    got = _state().read(str(p))
    assert got.lifts == [] and "version" in got.reason


@pytest.mark.parametrize("bad", [
    {"kind": "everything"}, {"session_id": 7}, {"created": "yesterday"},
    {"files": "Skyrim.esm"}, {"reason": None}, {"created": True}, {"expires_at": True},
])
def test_a_malformed_lift_rejects_the_whole_state(tmp_path, bad):
    p = _forge(tmp_path, _good_body(lifts=[_lift(), _lift(**bad)]))
    got = _state().read(str(p))
    assert got.lifts == [] and got.reason


def test_a_missing_state_is_safe_and_not_a_rejection(tmp_path):
    got = _state().read(str(tmp_path / ".skyguard" / "state.json"))
    assert got.lifts == [] and got.reason == "" and not got.rejected


def test_a_rejection_is_marked_so_the_caller_logs_it(tmp_path):
    p = _forge(tmp_path, _good_body(v=99))
    got = _state().read(str(p))
    assert got.rejected and got.reason and got.lock_disabled is None


def test_the_persistent_lock_disable_is_read_back(tmp_path):
    p = _write(tmp_path, [], persistent={"lock_disabled": {"since": NOW - H, "by_session": SESSION}})
    got = _state().read(str(p))
    assert got.lock_disabled == {"since": NOW - H, "by_session": SESSION}


def test_a_rejected_state_never_reports_the_lock_disabled(tmp_path):
    p = _write(tmp_path, [], persistent={"lock_disabled": {"since": NOW, "by_session": SESSION}})
    data = json.loads(p.read_text(encoding="utf-8"))
    data["persistent"]["lock_disabled"]["since"] = 1.0
    p.write_text(json.dumps(data), encoding="utf-8")
    got = _state().read(str(p))
    assert got.lock_disabled is None and got.rejected


def test_a_failed_write_leaves_the_old_state_and_no_temp_file(tmp_path, monkeypatch):
    st = _state()
    p = _write(tmp_path, [_lift(reason="old")])
    before = p.read_bytes()

    def boom(*a, **k):
        raise OSError("replace failed")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        st.write(str(p), {"v": 1, "lifts": [_lift(reason="new")], "persistent": {}})
    monkeypatch.undo()
    assert p.read_bytes() == before
    # B-M3: the state lock's sidecar is permanent by design (the OS lock lives on it); no TEMP file may remain.
    assert sorted(x.name for x in p.parent.iterdir()) == ["state.json", "state.json.lock"]


def test_write_computes_the_sum_itself_and_ignores_a_caller_supplied_one(tmp_path):
    st = _state()
    p = tmp_path / "state.json"
    st.write(str(p), {"v": 1, "lifts": [], "persistent": {}, "sum": "0" * 64})
    data = json.loads(p.read_text(encoding="utf-8"))
    body = {k: v for k, v in data.items() if k != "sum"}
    assert data["sum"] == st.canonical_sum(body) != "0" * 64


def test_the_session_start_comes_from_the_health_session_record(tmp_path):
    from skyguard import health
    st = _state()
    d = tmp_path / "hb"
    assert health.session_record(str(d), "sess-A", "start", "startup", transcript="/t.jsonl")
    assert health.session_record(str(d), "sess-A", "pre-tool", "Bash")
    start = st.session_start(str(d), "sess-A")
    assert start is not None and abs(start - __import__("time").time()) < 60
    assert st.session_start(str(d), "sess-NONE") is None


def test_an_unknown_session_start_honours_nothing(tmp_path):
    p = _write(tmp_path, [_lift()])
    s = _state()
    assert s.honoured(s.read(str(p)), SESSION, NOW, None) == []


def test_write_refuses_a_nan_and_a_hand_written_nan_is_rejected(tmp_path):
    st = _state()
    with pytest.raises(ValueError):
        st.write(str(tmp_path / "s.json"), {"v": 1, "lifts": [_lift(expires_at=float("nan"))], "persistent": {}})
    p = tmp_path / "state.json"
    p.write_text('{"v":1,"lifts":[],"persistent":{},"x":NaN,"sum":"0"}', encoding="utf-8")
    got = st.read(str(p))
    assert got.lifts == [] and got.rejected


# ---- Fix round 1 (Task 7 review) -----------------------------------------------------------------

@pytest.mark.parametrize("payload", ["[" * 60000, '{"a":' * 12000], ids=["nested-list", "nested-object"])
def test_s5_hostile_nesting_under_the_size_cap_is_rejected_not_raised(tmp_path, payload):
    """l2-state-never-raises: json.loads raises RecursionError on this, which is not an OSError or ValueError."""
    p = tmp_path / ".skyguard" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text(payload, encoding="ascii")
    assert p.stat().st_size < 64 * 1024
    got = _state().read(str(p))
    assert got.lifts == [] and got.rejected and got.reason


def test_the_session_start_is_the_last_start_line(tmp_path):
    """A resume appends a second `start`; a lift made before it must not be honoured after it."""
    st = _state()
    d = tmp_path / "hb" / "sessions"
    d.mkdir(parents=True)
    (d / "sess-A.log").write_text("50.000000 start startup /t.jsonl\n60.000000 pre-tool Bash\n"
                                  "120.000000 start resume /t.jsonl\n130.000000 pre-tool Bash\n", encoding="utf-8")
    start = st.session_start(str(tmp_path / "hb"), "sess-A")
    assert start == 120.0
    p = _write(tmp_path, [_lift(created=100.0, expires_at=1e12)])
    assert st.honoured(st.read(str(p)), SESSION, 200.0, start) == []


def test_a_garbled_last_start_line_gives_no_start_not_the_earlier_one(tmp_path):
    d = tmp_path / "hb" / "sessions"
    d.mkdir(parents=True)
    (d / "sess-A.log").write_text("50.000000 start startup /t\nnotatime start resume /t\n", encoding="utf-8")
    assert _state().session_start(str(tmp_path / "hb"), "sess-A") is None


def _lstat_as(monkeypatch, target, **over):
    """Make os.lstat report `over` for `target` only (the state file), everything else untouched."""
    import types
    real = os.lstat

    def fake(p, *a, **k):
        r = real(p, *a, **k)
        if os.path.normcase(str(p)) != os.path.normcase(str(target)):
            return r
        d = {n: getattr(r, n) for n in dir(r) if n.startswith("st_")}
        d.update(over)
        return types.SimpleNamespace(**d)
    monkeypatch.setattr(os, "lstat", fake)


def test_a_state_file_that_is_itself_a_reparse_point_is_rejected(tmp_path, monkeypatch):
    """l2-state-reparse-file: the symlinked-file clause (a real symlink needs a privilege this user may lack)."""
    p = _write(tmp_path, [_lift()])
    real = os.lstat(str(p))
    _lstat_as(monkeypatch, p, st_file_attributes=getattr(real, "st_file_attributes", 0) | 0x400)
    got = _state().read(str(p))
    assert got.lifts == [] and "reparse" in got.reason and "file" in got.reason


def test_a_non_regular_state_path_is_rejected_even_when_it_opens(tmp_path, monkeypatch):
    """l2-state-not-regular: a directory fails open() anyway, so only a path that OPENS can tell this clause."""
    import stat
    p = _write(tmp_path, [_lift()])
    _lstat_as(monkeypatch, p, st_mode=stat.S_IFIFO | 0o600)
    got = _state().read(str(p))
    assert got.lifts == [] and "regular" in got.reason


def _fstat_as(monkeypatch, **over):
    import types
    real = os.fstat

    def fake(fd):
        r = real(fd)
        d = {n: getattr(r, n) for n in dir(r) if n.startswith("st_")}
        d.update(over)
        return types.SimpleNamespace(**d)
    monkeypatch.setattr(os, "fstat", fake)


def test_a_file_swapped_between_lstat_and_open_is_rejected(tmp_path, monkeypatch):
    """l2-state-toctou-id: the handle is not the file lstat vetted."""
    p = _write(tmp_path, [_lift()])
    _fstat_as(monkeypatch, st_ino=os.lstat(str(p)).st_ino + 1)
    got = _state().read(str(p))
    assert got.lifts == [] and got.rejected and "changed" in got.reason


def test_a_handle_that_turns_out_to_be_a_reparse_point_is_rejected(tmp_path, monkeypatch):
    """l2-state-toctou-reparse"""
    p = _write(tmp_path, [_lift()])
    _fstat_as(monkeypatch, st_file_attributes=0x400)
    got = _state().read(str(p))
    assert got.lifts == [] and got.rejected and "changed" in got.reason


def test_a_file_of_exactly_the_cap_is_read_and_one_byte_more_is_not(tmp_path):
    """l2-state-size-if: the boundary, with VALID json and a CORRECT sum, so only the size clause can reject."""
    st = _state()
    body = _good_body()
    n = _forge(tmp_path, body).stat().st_size
    p = _forge(tmp_path, body, raw_pad=64 * 1024 - n)
    assert p.stat().st_size == 64 * 1024
    assert len(st.read(str(p)).lifts) == 1
    p = _forge(tmp_path, body, raw_pad=64 * 1024 + 1 - n)
    assert p.stat().st_size == 64 * 1024 + 1
    got = st.read(str(p))
    assert got.lifts == [] and "larger" in got.reason


@pytest.mark.parametrize("dis", [
    "yes", {"since": "now", "by_session": SESSION}, {"since": NOW, "by_session": 7}, {"by_session": SESSION},
    {"since": True, "by_session": SESSION},
], ids=["string", "since-str", "by-session-int", "since-missing", "since-bool"])
def test_a_correctly_summed_state_with_a_bad_lock_disabled_is_rejected(tmp_path, dis):
    """l2-state-disable-shape (and, for since-bool, the bool exclusion in _num)."""
    p = _forge(tmp_path, _good_body(lifts=[], persistent={"lock_disabled": dis}))
    got = _state().read(str(p))
    assert got.lock_disabled is None and got.rejected


@pytest.mark.parametrize("field", ["created", "expires_at"])
def test_a_bool_is_not_a_time(tmp_path, field):
    """l2-state-num-bool: True == 1 and is an int, so isinstance() would let it through."""
    p = _forge(tmp_path, _good_body(lifts=[_lift(**{field: True})]))
    got = _state().read(str(p))
    assert got.lifts == [] and got.rejected


def test_state_safe_is_fresh_on_every_access(tmp_path):
    """l2-state-safe-fresh: mutating one SAFE must not change the next."""
    st = _state()
    st.State.SAFE.lifts.append({"kind": "guards"})
    assert st.State.SAFE.lifts == []
    st.read(str(tmp_path / "nope" / "state.json")).lifts.append(1)
    assert st.read(str(tmp_path / "nope" / "state.json")).lifts == []


@pytest.mark.parametrize("now,start", [(float("nan"), START), (float("inf"), START), (NOW, float("nan")),
                                       (NOW, float("-inf")), (NOW, True), (True, START), ("x", START)])
def test_a_non_finite_now_or_start_honours_nothing(tmp_path, now, start):
    """l2-state-honoured-finite"""
    p = _write(tmp_path, [_lift()])
    s = _state()
    assert s.honoured(s.read(str(p)), SESSION, now, start) == []


def test_write_refuses_what_read_would_reject(tmp_path):
    """l2-state-write-validates: a malformed lift, a bad persistent block, a bad version, an oversize body."""
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    for bad in ({"v": 1, "lifts": [_lift(kind="x")], "persistent": {}},
                {"v": 1, "lifts": [_lift(created=True)], "persistent": {}},
                {"v": 1, "lifts": [], "persistent": {"lock_disabled": {"since": "x", "by_session": "s"}}},
                {"v": 1, "lifts": "none", "persistent": {}},
                {"v": 2, "lifts": [], "persistent": {}},
                {"v": True, "lifts": [], "persistent": {}},
                {"v": 1, "lifts": [_lift(reason="x" * (70 * 1024))], "persistent": {}}):
        with pytest.raises(ValueError):
            st.write(str(p), bad)
        assert not p.exists()
        assert not p.parent.exists() or list(p.parent.iterdir()) == []


# ---- final review B-M3: one state lock for every write and every read-modify-write -------------------------------

_HOLDER = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom skyguard import state\n"
           "with state.locked(sys.argv[2]):\n    open(sys.argv[3], 'w').close()\n    time.sleep(float(sys.argv[4]))\n")


def _hold_lock(tmp_path, p, seconds):
    """Another process holding the state lock for `seconds` (as a CLI writer mid read-modify-write would)."""
    import time
    flag = tmp_path / "held"
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(REPO / "tools"), str(p), str(flag), str(seconds)])
    deadline = time.monotonic() + 60
    while not flag.exists():
        assert proc.poll() is None and time.monotonic() < deadline, "the lock holder did not start"
        time.sleep(0.02)
    return proc


def test_a_write_waits_while_another_process_holds_the_state_lock(tmp_path):
    import time
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    proc = _hold_lock(tmp_path, p, 1.5)
    try:
        t0 = time.monotonic()
        st.write(str(p), {"v": 1, "lifts": [], "persistent": {}})
        waited = time.monotonic() - t0
    finally:
        proc.wait(timeout=60)
    assert waited >= 1.0, "write() did not wait for the lock ({:.2f} s)".format(waited)
    assert st.read(str(p)).rejected is False and p.exists()


def test_a_state_lock_that_stays_busy_refuses_the_write_and_leaves_the_file(tmp_path):
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    st.write(str(p), {"v": 1, "lifts": [_lift()], "persistent": {}})
    before = p.read_bytes()
    proc = _hold_lock(tmp_path, p, 3.0)
    try:
        with pytest.raises(OSError):
            with st.locked(str(p), wait=0.2):
                pass
    finally:
        proc.wait(timeout=60)
    assert p.read_bytes() == before


def test_write_inside_locked_does_not_wait_on_its_own_lock(tmp_path):
    st = _state()
    p = tmp_path / ".skyguard" / "state.json"
    with st.locked(str(p), wait=0.2):
        st.write(str(p), {"v": 1, "lifts": [], "persistent": {}})
    assert st.read(str(p)).rejected is False
