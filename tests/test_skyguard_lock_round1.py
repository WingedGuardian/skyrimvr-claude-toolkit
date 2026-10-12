"""Plan 6 Task 8, fix round 1: findings 1-8 of the review. Same scratch rules as test_skyguard_lock.py
(beds under pytest's base temp, sandbox variable always set, teardown strips every ACE). Each scenario is reused
by a mutation twin that rewrites lock.py (or the .cmd) and must go red for the NAMED reason.

Twins: l2-lock-own-deny, l2-lock-undo, l2-state-lift-prune, l2-lock-manifest-incremental, l2-unlock-tty,
l2-unlock-ace-before-bit, l2-lock-bit-before-ace, l2-lock-handle-link-count, l2-cmd-keeps-the-users-bit,
l2-cmd-verifies, l2-cmd-ace-before-bit, l2-cmd-r1-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

import test_skyguard_lock as tl
from test_skyguard_lock import (REPO, SESSION, SYS32, VR_FILES, _icacls, _lock, _locked, _mutant, _nopy_env, _red,
                                _ro, _run_cmd, _sid, _stage_cmd, _state_file, bed, _scratch_root,  # noqa: F401
                                needs_windows)


def _grant(p, spec):
    subprocess.run([str(SYS32 / "icacls.exe"), str(p)] + spec, capture_output=True)


def _attrib(flag, p):
    subprocess.run([str(SYS32 / "attrib.exe"), flag, str(p)], capture_output=True)


# ---- findings 1, 2: a deny ACE of the user's own, and the undo -------------------------------------------

def _scenario_own_deny_skipped(lock, bed):
    p = bed.path("Update.esm")
    _grant(p, ["/deny", "*%s:(WEA)" % _sid()])
    before = _icacls(p)
    assert "(DENY)(WEA)" in before
    res = lock.lock_all(str(bed.root))
    assert res.exit == 1, "lock must report the file it refused: %s" % res.lines
    assert "Update.esm" in " ".join(res.lines), res.lines
    assert _icacls(p) == before and not _ro(p), "the user's own deny ACE was merged into or the file was touched"
    files = json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))["files"]
    assert "Update.esm" not in files
    assert lock.unlock(str(bed.root), None, SESSION).exit == 0
    assert "(DENY)(WEA)" in _icacls(p), "unlock removed the user's own deny ACE"
    assert all(not _locked(bed, n) for n in VR_FILES)


@needs_windows
def test_a_target_with_the_users_own_deny_ace_is_refused_and_the_ace_survives(bed):
    _scenario_own_deny_skipped(_lock(), bed)


def _scenario_undo_on_verify_failure(lock, bed, monkeypatch):
    monkeypatch.setattr(lock, "_verify_locked", lambda *a, **k: False)
    res = lock.lock_all(str(bed.root))
    assert res.exit == 1, res.lines
    for n in VR_FILES:
        p = bed.path(n)
        assert not _ro(p) and "DENY" not in _icacls(p), "a failed lock left %s partly applied" % n
    assert not json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))["files"]


@needs_windows
def test_a_lock_that_does_not_verify_is_undone_exactly(bed, monkeypatch):
    _scenario_undo_on_verify_failure(_lock(), bed, monkeypatch)


@needs_windows
def test_a_preexisting_readonly_file_keeps_its_bit_when_a_failed_lock_is_undone(bed, monkeypatch):
    lock = _lock()
    pre = bed.path("Skyrim - Interface.bsa")
    _attrib("+R", pre)
    monkeypatch.setattr(lock, "_verify_locked", lambda *a, **k: False)
    lock.lock_all(str(bed.root))
    assert _ro(pre) and "DENY" not in _icacls(pre)
    _attrib("-R", pre)


@needs_windows
def test_other_explicit_aces_survive_lock_and_unlock_byte_for_byte(bed):
    lock = _lock()
    p = bed.path("Skyrim.esm")
    _grant(p, ["/grant", "*S-1-1-0:(R)"])
    before = _icacls(p)
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), None, SESSION).exit == 0
    assert _icacls(p) == before, "the file's ACL after lock+unlock differs from before"


# ---- finding 3: the state file does not grow without bound ----------------------------------------------

def _scenario_lift_prune(lock, bed):
    lock.lock_all(str(bed.root))
    now = time.time()
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=now - 7200).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=now).exit == 0
    from skyguard import state
    lifts = state.read(str(_state_file(bed))).lifts
    assert len(lifts) == 1, "an expired lift was kept (%d lifts)" % len(lifts)
    for i in range(400):
        res = lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=now + i)
        assert res.exit == 0, "unlock #%d failed: %s" % (i, res.lines)
    assert len(state.read(str(_state_file(bed))).lifts) <= 100
    assert _state_file(bed).stat().st_size < state.MAX_BYTES


@needs_windows
def test_state_does_not_grow_without_bound_over_400_unlocks(bed):
    _scenario_lift_prune(_lock(), bed)


# ---- finding 5: the manifest is written per file ---------------------------------------------------------

def _scenario_incremental_manifest(lock, bed, monkeypatch):
    calls = [0]
    real = lock._apply_file

    def boom(*a, **k):
        calls[0] += 1
        if calls[0] == 3:
            raise KeyboardInterrupt
        return real(*a, **k)
    monkeypatch.setattr(lock, "_apply_file", boom)
    with pytest.raises(KeyboardInterrupt):
        lock.lock_all(str(bed.root))
    mf = bed.root / ".skyguard" / "lock.json"
    assert mf.exists(), "a crash mid-lock left no manifest at all"
    files = sorted(json.loads(mf.read_text(encoding="utf-8"))["files"])
    locked = sorted(n for n in VR_FILES if _locked(bed, n))
    assert files == locked and len(locked) == 2, (files, locked)
    listed = (bed.root / ".skyguard" / "locked.txt").read_text(encoding="utf-8").splitlines()
    assert len(listed) == 2


@needs_windows
def test_manifest_is_written_per_file_so_a_crash_leaves_an_accurate_list(bed, monkeypatch):
    _scenario_incremental_manifest(_lock(), bed, monkeypatch)


# ---- finding 6: a person at a terminal -------------------------------------------------------------------

def _scenario_tty(lock, bed, monkeypatch):
    monkeypatch.setattr(lock, "_interactive", lambda: True)
    assert lock.main(["unlock", "Skyrim.esm", "--root", str(bed.root)]) == 0, "a terminal user was refused"
    from skyguard import state
    lift = state.read(str(_state_file(bed))).lifts[-1]
    assert lift["agent"] == "user" and lift["session_id"] == "user-tty"
    assert not _ro(bed.path("Skyrim.esm"))


@needs_windows
def test_a_person_at_a_terminal_can_unlock_without_a_session_id(bed, monkeypatch):
    lock = _lock()
    lock.lock_all(str(bed.root))
    monkeypatch.setattr(lock, "_interactive", lambda: False)
    assert lock.main(["unlock", "Skyrim.esm", "--root", str(bed.root)]) == 2
    assert _locked(bed, "Skyrim.esm")
    _scenario_tty(lock, bed, monkeypatch)


# ---- finding 7: link count from the file handle ----------------------------------------------------------

def _scenario_handle_link_count(lock, bed):
    p = bed.path("Dawnguard.esm")
    _grant(p, ["/deny", "*%s:(D)" % _sid()])
    # MEASURED: a (DENY)(D) entry for the user makes the file un-openable (even for read), and lstat then reports
    # st_nlink 0, which used to be named "multi-link". The real reason must be named.
    t = lock.targets(str(bed.data))
    reason = dict(t.skips).get("Dawnguard.esm")
    assert "Dawnguard.esm" not in {x.name for x in t.files} and reason, "unlockable file entered the target set"
    assert "multi-link" not in reason and "cannot be opened" in reason, "named as: %s" % reason
    os.link(str(bed.path("Skyrim.esm")), str(bed.root / "managerlink.bin"))
    assert dict(lock.targets(str(bed.data)).skips)["Skyrim.esm"] == "multi-link (mod manager copy)"


@needs_windows
def test_a_user_deny_on_delete_does_not_make_a_file_look_multilink(bed):
    _scenario_handle_link_count(_lock(), bed)


# ---- ordering --------------------------------------------------------------------------------------------

def _scenario_unlock_ok(lock, bed):
    assert lock.lock_all(str(bed.root)).exit == 0
    res = lock.unlock(str(bed.root), None, SESSION)
    assert res.exit == 0, "unlock failed: %s" % res.lines
    assert all(not _ro(bed.path(n)) for n in VR_FILES)


def _scenario_lock_ok(lock, bed):
    res = lock.lock_all(str(bed.root))
    assert res.exit == 0, "lock failed: %s" % res.lines
    assert all(_locked(bed, n) for n in VR_FILES)


@needs_windows
def test_unlock_and_lock_order_the_ace_and_the_bit_correctly(bed):
    _scenario_lock_ok(_lock(), bed)
    assert _lock().unlock(str(bed.root), None, SESSION).exit == 0


# ---- finding 4, 5: the .cmd ------------------------------------------------------------------------------

def _cmd_env(**extra):
    env = _nopy_env()
    env.update(extra)
    return env


def _cmd_text():
    return (REPO / "tools" / "skyguard-unlock.cmd").read_bytes().decode("ascii").replace("\r\n", "\n")


def _stage_text(bed, text):
    (bed.root / "tools").mkdir(exist_ok=True)
    dst = bed.root / "tools" / "skyguard-unlock.cmd"
    dst.write_bytes(text.replace("\n", "\r\n").encode("ascii"))
    return dst


def _scenario_cmd_extra_deny(bed, text=None):
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    script = _stage_text(bed, text if text is not None else _cmd_text())
    p = bed.path("Skyrim.esm")
    _grant(p, ["/deny", "*S-1-1-0:(WA)"])
    try:
        r = _run_cmd(script, _cmd_env())
        assert r.returncode != 0, "the .cmd reported success though Skyrim.esm is still locked: %s" % r.stdout
        assert "Skyrim.esm" in r.stdout and "NOT unlocked" in r.stdout, r.stdout
        assert _ro(p), "precondition: the extra deny must have kept the bit"
        for n in VR_FILES:
            if n != "Skyrim.esm":
                assert not _ro(bed.path(n)) and "DENY" not in _icacls(bed.path(n)), n
    finally:
        _grant(p, ["/remove:d", "*S-1-1-0"])


@needs_windows
def test_cmd_fails_loudly_when_an_extra_deny_stops_the_unlock(bed):
    _scenario_cmd_extra_deny(bed)


def _scenario_cmd_ordering_and_keep(bed, text=None):
    lock = _lock()
    pre = bed.path("Skyrim - Interface.bsa")
    _attrib("+R", pre)
    assert lock.lock_all(str(bed.root)).exit == 0
    r = _run_cmd(_stage_text(bed, text if text is not None else _cmd_text()), _cmd_env())
    try:
        assert r.returncode == 0, "the .cmd failed: %s" % r.stdout
        assert _ro(pre), "the .cmd cleared a read-only bit the user set"
        assert "DENY" not in _icacls(pre)
        assert all(not _ro(bed.path(n)) for n in VR_FILES if n != "Skyrim - Interface.bsa"), \
            "bit left on a locked file"
    finally:
        _attrib("-R", pre)


@needs_windows
def test_cmd_keeps_the_users_bit_and_removes_the_ace_before_the_bit(bed):
    _scenario_cmd_ordering_and_keep(bed)


def _scenario_cmd_r1_only(bed, text=None):
    lock = _lock()
    lock.lock_all(str(bed.root))
    other = bed.data / "Unofficial.esp"            # in Data, but not a vanilla name
    bak = bed.data / "Skyrim.esm.bak"              # a lookalike
    outside = bed.root / "Skyrim.esm"              # a vanilla name, but not in Data
    for f in (other, bak, outside):
        f.write_bytes(b"x")
    for f in (other, bak):
        lock.apply_one(f)                          # carries the toolkit's mark: only the name check protects it
    _attrib("+R", outside)
    lst = bed.root / ".skyguard" / "locked.txt"
    lst.write_text("%s\n%s\n%s\n%s\n" % (other, bak, outside, bed.data / ".." / "Data" / "Skyrim.esm.bak"),
                   encoding="utf-8")
    try:
        r = _run_cmd(_stage_text(bed, text if text is not None else _cmd_text()), _cmd_env())
        assert r.returncode == 3 and "REFUSED" in r.stdout, r.stdout
        assert _ro(outside), "the .cmd touched a vanilla name outside Data"
        for f in (other, bak):
            assert _ro(f) and "DENY" in _icacls(f), "the .cmd unlocked a file that is not a vanilla name"
        assert all(_locked(bed, n) for n in VR_FILES), "a refused list must not unlock anything else"
    finally:
        _attrib("-R", outside)


@needs_windows
def test_cmd_only_acts_on_exact_vanilla_names_inside_data(bed):
    _scenario_cmd_r1_only(bed)


@needs_windows
def test_cmd_without_a_list_falls_back_to_the_fixed_names_and_never_to_python(bed):
    lock = _lock()
    pre = bed.path("Skyrim - Interface.bsa")
    _attrib("+R", pre)
    lock.lock_all(str(bed.root))
    (bed.root / ".skyguard" / "locked.txt").unlink()
    r = _run_cmd(_stage_cmd(bed), _cmd_env())
    assert r.returncode == 0, r.stdout
    for n in VR_FILES:
        assert "DENY" not in _icacls(bed.path(n)), n
        if n != "Skyrim - Interface.bsa":
            assert not _ro(bed.path(n)), n
    assert _ro(pre)
    _attrib("-R", pre)


@needs_windows
def test_cmd_leaves_a_read_only_file_it_did_not_lock_alone(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    mine = bed.path("Update.esm")
    subprocess.run([str(SYS32 / "icacls.exe"), str(mine), "/remove:d", "*" + _sid()], capture_output=True)
    assert _ro(mine), "precondition: read-only with no toolkit ACE"
    r = _run_cmd(_stage_cmd(bed), _cmd_env())
    assert r.returncode == 0, r.stdout
    assert _ro(mine), "the .cmd cleared a bit on a file without the toolkit's ACE"
    _attrib("-R", mine)


def _raw(cmdline, env, timeout):
    """Run with stdin held OPEN and never written (subprocess.run would close it at once, and `pause` would
    read EOF and return). A script that reaches `pause` blocks here until the timeout, and the child is killed."""
    class Out:
        pass
    p = subprocess.Popen(cmdline, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, env=env)
    try:
        out = p.stdout.read() if p.wait(timeout) is not None else b""
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise
    finally:
        p.stdin.close()
    r = Out()
    r.returncode, r.stdout = p.returncode, out.decode("ascii", "replace")
    return r


@needs_windows
def test_cmd_does_not_pause_for_an_agent_but_does_for_a_double_click(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    script = _stage_cmd(bed)
    env = _nopy_env()
    del env["SKYGUARD_NO_PAUSE"]
    # an agent's `cmd /c script` with stdin held open: a pause would block until the timeout
    r = _raw("%s /d /c %s" % (SYS32 / "cmd.exe", script), env, 120)
    assert r.returncode == 0, r.stdout
    lock.lock_all(str(bed.root))
    # what Explorer runs on a double-click: cmd.exe /c ""<script>" "  -> must reach the pause
    with pytest.raises(subprocess.TimeoutExpired):
        _raw('%s /c ""%s" "' % (SYS32 / "cmd.exe", script), env, 45)


# ---- mutation twins --------------------------------------------------------------------------------------

def _cmd_mut(anchor, replacement):
    text = _cmd_text()
    assert text.count(anchor) == 1, "cmd mutation anchor moved or not unique: %r" % anchor
    return text.replace(anchor, replacement)


@needs_windows
def test_mutation_l2_lock_own_deny(bed, tmp_path):
    m = _mutant(tmp_path, '        if a["type"] == 1 and a["mine"] and a["mask"] not in _LABEL:\n', "        if False:\n")
    _red(_scenario_own_deny_skipped, "must report the file it refused", m, bed)


@needs_windows
def test_mutation_l2_lock_undo(bed, tmp_path, monkeypatch):
    m = _mutant(tmp_path, "            undone = before.ours is None and _undo(p, before)\n", "            undone = False\n")
    _red(_scenario_undo_on_verify_failure, "partly applied", m, bed, monkeypatch)


@needs_windows
def test_mutation_l2_state_lift_prune(bed, tmp_path):
    m = _mutant(tmp_path, "def _live_lifts(lifts, now):\n", "def _live_lifts(lifts, now):\n    return list(lifts)\n")
    _red(_scenario_lift_prune, "expired lift was kept", m, bed)


@needs_windows
def test_mutation_l2_lock_manifest_incremental(bed, tmp_path, monkeypatch):
    m = _mutant(tmp_path, "        _write_manifest(root, t.edition, dict(carry, **record))  # per file\n", "        pass\n")
    _red(_scenario_incremental_manifest, "left no manifest", m, bed, monkeypatch)


@needs_windows
def test_mutation_l2_unlock_tty(bed, tmp_path, monkeypatch):
    lock = _lock()
    lock.lock_all(str(bed.root))
    m = _mutant(tmp_path, " or (USER_SESSION if _interactive() else None)", "")
    _red(_scenario_tty, "terminal user was refused", m, bed, monkeypatch)


@needs_windows
def test_mutation_l2_unlock_ace_before_bit(bed, tmp_path):
    # Our deny ACE denies WA (write attributes), so clearing the bit before removing it is itself refused. The
    # `protected` argument is fix-A I2's (unlock writes back the DACL protection recorded at lock time).
    m = _mutant(tmp_path,
                "    w.remove_ours(p, (MASK_FULL, MASK_PRE), protected)\n    if not keep_bit:\n"
                "        w.set_readonly(p, False)\n",
                "    if not keep_bit:\n        w.set_readonly(p, False)\n"
                "    w.remove_ours(p, (MASK_FULL, MASK_PRE), protected)\n")
    _red(_scenario_unlock_ok, "unlock failed", m, bed)


@needs_windows
def test_mutation_l2_lock_bit_before_ace(bed, tmp_path):
    m = _mutant(tmp_path, "    if not pre_ro:\n        w.set_readonly(p, True)\n    try:\n        w.add_deny(p, mask)\n",
                "    try:\n        w.add_deny(p, mask)\n        if not pre_ro:\n            w.set_readonly(p, True)\n")
    _red(_scenario_lock_ok, "lock failed", m, bed)


@needs_windows
def test_mutation_l2_lock_handle_link_count(bed, tmp_path):
    m = _mutant(tmp_path, "def _links(path, st):\n", "def _links(path, st):\n    return st.st_nlink\n")
    # Where the mutant goes red depends on the interpreter's lstat. Up to 3.11 it opens the file, fails on the deny
    # entry and reports st_nlink 0 ("multi-link"); from 3.12 Windows lstat reads it without opening the file and
    # reports the true 1 (MEASURED on this deny bed: 0 on 3.10.10, 1 on 3.12.10), so the un-openable file walks into
    # the target set. Both are the defect the handle read prevents; each version is held to its own failure.
    import sys
    why = "unlockable file entered the target set" if sys.version_info >= (3, 12) else "named as: multi-link"
    _red(_scenario_handle_link_count, why, m, bed)


@needs_windows
def test_mutation_l2_cmd_keeps_the_users_bit(bed):
    t = _cmd_mut("if defined PRE goto verify\n", "rem\n")
    with pytest.raises(AssertionError, match="cleared a read-only bit"):
        _scenario_cmd_ordering_and_keep(bed, t)


@needs_windows
def test_mutation_l2_cmd_verifies(bed):
    t = _cmd_mut("if defined BAD goto failed\n", "rem\n")
    with pytest.raises(AssertionError, match="reported success"):
        _scenario_cmd_extra_deny(bed, t)


@needs_windows
def test_mutation_l2_cmd_ace_before_bit(bed):
    text = _cmd_text()
    a = '"%S32%\\icacls.exe" "%F%" /remove:d *%SID% >nul\n'
    b = '"%S32%\\attrib.exe" -R "%F%"\n'
    assert text.count(a) == 1 and text.count(b) == 1, "cmd ordering anchors moved"
    swapped = text.replace(a, "@@A@@").replace(b, a).replace("@@A@@", b)
    with pytest.raises(AssertionError, match="cmd failed|bit left on a locked file"):
        _scenario_cmd_ordering_and_keep(bed, swapped)


@needs_windows
def test_mutation_l2_cmd_r1_only(bed):
    t = _cmd_mut("if not defined OK goto refuse\n", "rem\n")
    with pytest.raises(AssertionError, match="not a vanilla name"):
        _scenario_cmd_r1_only(bed, t)
