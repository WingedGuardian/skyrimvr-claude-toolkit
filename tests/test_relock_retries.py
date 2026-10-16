"""Durable retry obligations; fake ACL operations only, in private fixture roots."""
import os
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import cli, doctor, lock, session, state


@pytest.fixture
def pending(tmp_path, monkeypatch):
    now = time.time()
    sf = tmp_path / ".skyguard/state.json"
    lift = {"kind": "lock", "files": ["Skyrim.esm"], "reason": "fixture", "session_id": "old",
            "agent": "claude", "created": now - 3600, "expires_at": now - 1}
    state.write(sf, {"v": 1, "lifts": [lift], "persistent": {}})
    (sf.parent / "lock.json").write_text("{}")
    monkeypatch.setattr(lock, "_assert_sandbox", lambda path: None)
    monkeypatch.setattr(lock, "supported", lambda: True)
    return tmp_path, sf, lift, now


def test_failed_expiry_relock_is_retried_until_success(pending, monkeypatch):
    root, sf, lift, now = pending
    calls = []
    def fake(root, only=None):
        assert os.path.normcase(os.path.abspath(str(sf))) in state._HELD
        calls.append(only)
        assert only("Skyrim.esm") and not only("Update.esm")
        return lock.Result(1, ["failed"], {"not_locked": [("Skyrim.esm", "error OSError")]}) if len(calls) == 1 else lock.Result(0, ["locked"], {"locked": ["Skyrim.esm"]})
    monkeypatch.setattr(lock, "lock_all", fake)
    assert lock.relock_expired(root, now).exit == 1
    assert state.read(sf).lifts == [lift]
    assert lock.relock_expired(root, now + 1).data["relocked"] == ["Skyrim.esm"]
    assert state.read(sf).lifts == [] and len(calls) == 2


def test_unrelated_guard_write_keeps_expired_lock_obligation(pending):
    root, sf, lift, now = pending
    cli._guards_write(root, lambda row: row["kind"] != "guards", now=now)
    assert state.read(sf).lifts == [lift]
    assert lock._live_lifts([lift], now) == [lift]


def test_doctor_does_not_report_failed_relock_as_cleared(pending, monkeypatch):
    root, sf, lift, now = pending
    monkeypatch.setattr(lock, "lock_all", lambda *a, **kw: lock.Result(1, ["failed"], {"not_locked": [("Skyrim.esm", "error OSError")]}))
    assert doctor.clear_expired(root, now) == []
    assert state.read(sf).lifts == [lift]


def test_failed_session_relock_keeps_an_expired_retry_without_success_claim(pending, monkeypatch):
    root, sf, lift, now = pending
    lift = dict(lift, expires_at=now + 600)
    state.write(sf, {"v": 1, "lifts": [lift], "persistent": {}})
    monkeypatch.setattr(session, "_engaged", lambda root: True)
    monkeypatch.setattr(session, "_scan", lambda root, lifts: (["Skyrim.esm"], []))
    monkeypatch.setattr(session, "_relock", lambda root: "Vanilla-file relock FAILED: fixture")
    monkeypatch.setattr(session, "_guard_drift", lambda root: None)
    banners = session.layer2_start(root)
    kept = state.read(sf).lifts
    assert len(kept) == 1 and state.lift_expired(kept[0], time.time())
    assert "FAILED" in "\n".join(banners)
    assert not any("; relocked." in line for line in banners)
    monkeypatch.setattr(lock, "lock_all", lambda *a, **kw: lock.Result(0, ["locked"], {"locked": ["Skyrim.esm"]}))
    assert lock.relock_expired(root).exit == 0
    assert state.read(sf).lifts == []


def test_session_filesystem_exception_still_expires_and_keeps_retry(pending, monkeypatch):
    root, sf, lift, now = pending
    state.write(sf, {"v": 1, "lifts": [dict(lift, expires_at=now + 600)], "persistent": {}})
    monkeypatch.setattr(session, "_engaged", lambda root: True)
    monkeypatch.setattr(session, "_scan", lambda *a: ([], []))
    monkeypatch.setattr(session, "_guard_drift", lambda root: None)
    def fail(root):
        raise OSError("fixture")
    monkeypatch.setattr(session, "_relock", fail)
    assert "FAILED" in "\n".join(session.layer2_start(root))
    kept = state.read(sf).lifts
    assert len(kept) == 1 and state.lift_expired(kept[0], time.time())


def test_failed_session_drift_relock_records_retry_without_an_earlier_lift(pending, monkeypatch):
    root, sf, lift, now = pending
    state.write(sf, {"v": 1, "lifts": [], "persistent": {}})
    monkeypatch.setattr(session, "_engaged", lambda root: True)
    monkeypatch.setattr(session, "_scan", lambda *a: ([], ["Skyrim.esm"]))
    monkeypatch.setattr(session, "_relock", lambda root: "Vanilla-file relock FAILED: fixture")
    monkeypatch.setattr(session, "_guard_drift", lambda root: None)
    assert "relock pending" in "\n".join(session.layer2_start(root))
    kept = state.read(sf).lifts
    assert len(kept) == 1 and state.lift_expired(kept[0], time.time())
    monkeypatch.setattr(lock, "lock_all", lambda *a, **kw: lock.Result(0, ["locked"], {"locked": ["Skyrim.esm"]}))
    assert lock.relock_expired(root).exit == 0 and state.read(sf).lifts == []


@pytest.mark.parametrize("operation", ["lock", "unlock", "enable"])
def test_file_permission_operations_hold_the_state_writer_lock(pending, monkeypatch, operation):
    from types import SimpleNamespace
    root, sf, lift, now = pending
    monkeypatch.setattr(lock, "_guard", lambda root: (root / "Data", SimpleNamespace(edition="vr")))
    monkeypatch.setattr(lock, "_read_manifest", lambda root: ({}, "ok"))
    monkeypatch.setattr(lock, "_resolve_names", lambda *a: ["Skyrim.esm"])
    monkeypatch.setattr(lock, "relock_expired", lambda *a, **kw: None)
    called = []
    def fake(*a, **kw):
        assert os.path.normcase(os.path.abspath(str(sf))) in state._HELD
        called.append(True)
        return lock.Result(0, ["fixture"], {})
    monkeypatch.setattr(lock, "_lock_all", fake)
    monkeypatch.setattr(lock, "_unlock_files", fake)
    result = lock.unlock(root, ["Skyrim.esm"], "fixture", now=now) if operation == "unlock" else (
        lock.enable(root) if operation == "enable" else lock.lock_all(root))
    assert result.exit == 0 and called == [True]
