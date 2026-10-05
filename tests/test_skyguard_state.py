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
        good = _write(tmp_path, [_lift(reason="x" * (70 * 1024))])
        assert good.stat().st_size > 64 * 1024
    elif shape == "unknown_version":
        _write(tmp_path, [_lift()], v=99)
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
    p = _write(tmp_path, [_lift()], v=True)
    got = _state().read(str(p))
    assert got.lifts == [] and "version" in got.reason


@pytest.mark.parametrize("bad", [
    {"kind": "everything"}, {"session_id": 7}, {"created": "yesterday"},
    {"files": "Skyrim.esm"}, {"reason": None}, {"created": True},
])
def test_a_malformed_lift_rejects_the_whole_state(tmp_path, bad):
    p = _write(tmp_path, [_lift(), _lift(**bad)])
    got = _state().read(str(p))
    assert got.lifts == [] and got.reason


def test_a_missing_state_is_safe_and_not_a_rejection(tmp_path):
    got = _state().read(str(tmp_path / ".skyguard" / "state.json"))
    assert got.lifts == [] and got.reason == "" and not got.rejected


def test_a_rejection_is_marked_so_the_caller_logs_it(tmp_path):
    p = _write(tmp_path, [_lift()], v=99)
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
    assert [x.name for x in p.parent.iterdir()] == ["state.json"]


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
