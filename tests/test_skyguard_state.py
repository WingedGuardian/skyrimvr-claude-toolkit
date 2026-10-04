"""Plan 6 Task 4 Step 2 (M2 contents S1-S8): the lift-state reader, written BEFORE it exists.

Every test is xfail(strict=True) until Task 7 builds `tools/skyguard/state.py`; Task 7 flips them
to plain tests. strict=True means a test that starts passing early (XPASS) fails the suite, so a
half-built reader cannot slip through as "expected failure".

Assumed API (Task 7 Step 1-2; Task 7 may rename, and then edits these calls, never the asserts):
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

pytestmark = pytest.mark.xfail(strict=True, reason="Plan 6 Task 7 builds tools/skyguard/state.py")

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
