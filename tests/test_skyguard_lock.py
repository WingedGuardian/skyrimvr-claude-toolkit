"""Plan 6 Task 8: the Layer 2 vanilla-file lock (M-D), its CLI, and the Python-free unlock.

EVERY lock in this file is applied to a scratch bed under pytest's base temp (the harness only lets Python
write inside its own scratch, so C:/Temp/plan6-lock/ is not reachable from here) and the suite sets
SKYGUARD_LOCK_SANDBOX to that folder, so `lock.py` refuses anything else. Never the install, never Documents. Each bed is unlocked (verified:
no deny ACE, bit clear) and deleted afterwards, whatever the test did.

Twins (mutation clauses, each proved red by rewriting `lock.py` in a private copy of the package and
running the SAME scenario against it): l2-targets-glob, l2-lock-single-link-targets,
l2-lock-single-link-apply, l2-lock-sandbox, l2-lock-verify-reread, l2-unlock-verify-reread,
l2-lock-preexisting-ro, l2-lock-preexisting-tag.
"""

from __future__ import annotations

import importlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))

SCRATCH = None   # set by the session fixture below: a folder inside the harness scratch (system temp)
WINDOWS = os.name == "nt"
SYS32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
needs_windows = pytest.mark.skipif(not WINDOWS, reason="the lock is a Windows mechanism (R11)")
SESSION = "sess-lock-1"
FULL = "(DENY)(WD,AD,WEA,WA)"   # the order icacls prints for (WD,AD,WA,WEA)
PRE = "(DENY)(WD,AD,WA)"

VR_FILES = ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "Skyrim - Textures0.bsa", "Skyrim - Interface.bsa"]
_ids = itertools.count()


def _lock():
    from skyguard import lock  # noqa: E402 -- absent until Task 8
    return lock


class Bed:
    def __init__(self, root: Path):
        self.root = root
        self.data = root / "Data"

    def path(self, name):
        return self.data / name


def _icacls(p):
    r = subprocess.run([str(SYS32 / "icacls.exe"), str(p)], capture_output=True, text=True)
    return r.stdout


def _ro(p):
    return bool(os.stat(str(p)).st_file_attributes & 1)


def _sid():
    out = subprocess.run([str(SYS32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                         capture_output=True, text=True).stdout
    return out.strip().split(",")[1].strip('"')


def _scrub(root: Path):
    """Remove any deny ACE and read-only bit under `root` (raw primitives, no toolkit code), then delete it.
    Two processes for the whole tree: the deny ACEs first (with WA denied attrib -R is refused), then the bits."""
    if not root.exists():
        return
    sid = _sid()
    subprocess.run([str(SYS32 / "icacls.exe"), str(root), "/remove:d", "*" + sid, "/T", "/C"], capture_output=True)
    subprocess.run([str(SYS32 / "icacls.exe"), str(root), "/remove:d", "*S-1-1-0", "/T", "/C"], capture_output=True)
    subprocess.run([str(SYS32 / "attrib.exe"), "-R", os.path.join(str(root), "*"), "/S", "/D"], capture_output=True)
    shutil.rmtree(str(root), ignore_errors=True)
    assert not root.exists(), "scratch bed could not be removed: %s" % root


@pytest.fixture(scope="session", autouse=True)
def _scratch_root(tmp_path_factory):
    global SCRATCH
    SCRATCH = tmp_path_factory.mktemp("lockscratch")
    yield SCRATCH


@pytest.fixture
def bed(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = SCRATCH / ("t8-%s-%d" % (uuid.uuid4().hex[:6], next(_ids)))
    (root / "Data").mkdir(parents=True)
    (root / "SkyrimVR.exe").write_bytes(b"MZ")
    b = Bed(root)
    for n in VR_FILES:
        b.path(n).write_bytes(os.urandom(1024))
    monkeypatch.setenv("SKYGUARD_LOCK_SANDBOX", str(SCRATCH))
    monkeypatch.delenv("SKYGUARD_SESSION_ID", raising=False)
    try:
        yield b
    finally:
        _scrub(root)


def _state_file(b):
    return b.root / ".skyguard" / "state.json"


def _locked(b, name):
    p = b.path(name)
    return _ro(p) and FULL in _icacls(p)


# ---- targets (R1) --------------------------------------------------------------------------------------

def test_targets_vr_is_the_named_files_that_exist(bed):
    t = _lock().targets(str(bed.data))
    assert t.edition == "vr"
    assert sorted(p.name for p in t.files) == sorted(VR_FILES)


def test_targets_names_each_skip_and_why(bed):
    t = _lock().targets(str(bed.data))
    skips = dict(t.skips)
    assert skips["HearthFires.esm"] == "missing"
    assert skips["Skyrim - Voices_ru0.bsa"] == "missing"
    assert "SkyrimVR.esm" in skips


def _scenario_lookalikes(lock, bed):
    for n in ("Skyrim.esm.bak", "Update.esm.disabled", "Skyrim - Textures9.bsa", "skyrim - Mod.bsa",
              "Skyrim_VR - Main.bsa.bak", "SkyrimVR.esm~", "Unofficial Skyrim Patch.esp",
              "Skyrim - Textures0.bsa.vortex_backup", "Skyrim - Textures10.bsa"):
        bed.path(n).write_bytes(b"lookalike")
    t = lock.targets(str(bed.data))
    got = {p.name for p in t.files}
    assert got == set(VR_FILES), "a lookalike name entered the target set: %s" % sorted(got - set(VR_FILES))
    if WINDOWS:
        lock.lock_all(str(bed.root))
        for n in ("Skyrim.esm.bak", "Update.esm.disabled", "Skyrim - Textures9.bsa", "skyrim - Mod.bsa"):
            assert not _ro(bed.path(n)) and "DENY" not in _icacls(bed.path(n)), n


def test_targets_lookalike_names_are_never_locked(bed):
    _scenario_lookalikes(_lock(), bed)


def test_targets_se_and_ae_editions_and_ccc(bed):
    lock = _lock()
    (bed.root / "SkyrimVR.exe").unlink()
    (bed.root / "SkyrimSE.exe").write_bytes(b"MZ")
    (bed.root / "Skyrim.ccc").write_text(
        "ccBGSSSE001-Fish.esm\r\nccQDRSSE001-SurvivalMode.esl\n..\\evil.esm\n"
        "C:/x/y.esm\n; comment\n\nnot a plugin.txt\n", encoding="ascii")
    for n in ("ccBGSSSE001-Fish.esm", "ccBGSSSE001-Fish.bsa", "ccQDRSSE001-SurvivalMode.esl", "evil.esm",
              "SkyrimVR.esm", "_ResourcePack.esl"):
        bed.path(n).write_bytes(b"x" * 16)
    t = lock.targets(str(bed.data))
    assert t.edition == "ae"
    names = {p.name for p in t.files}
    assert {"ccBGSSSE001-Fish.esm", "ccBGSSSE001-Fish.bsa", "ccQDRSSE001-SurvivalMode.esl",
            "_ResourcePack.esl"} <= names
    assert "evil.esm" not in names and "SkyrimVR.esm" not in names
    (bed.root / "Skyrim.ccc").unlink()
    assert lock.targets(str(bed.data)).edition == "se"


def test_targets_vr_ignores_ccc_and_ambiguous_exe_is_a_problem(bed):
    lock = _lock()
    (bed.root / "Skyrim.ccc").write_text("ccBGSSSE001-Fish.esm\n", encoding="ascii")
    bed.path("ccBGSSSE001-Fish.esm").write_bytes(b"x")
    assert "ccBGSSSE001-Fish.esm" not in {p.name for p in lock.targets(str(bed.data)).files}
    (bed.root / "SkyrimSE.exe").write_bytes(b"MZ")
    t = lock.targets(str(bed.data))
    assert t.edition is None and t.problem and t.files == []


@needs_windows
def test_targets_skip_multilink_and_reparse(bed):
    lock = _lock()
    other = bed.root / "elsewhere.bin"
    other.write_bytes(b"x" * 64)
    os.link(str(other), str(bed.path("Skyrim - Meshes0.bsa")))
    target_dir = bed.root / "tdir"
    target_dir.mkdir()
    r = subprocess.run([str(SYS32 / "cmd.exe"), "/c", "mklink", "/J", str(bed.path("Skyrim - Misc.bsa")),
                        str(target_dir)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cannot create a junction here")
    t = lock.targets(str(bed.data))
    skips = dict(t.skips)
    assert skips["Skyrim - Meshes0.bsa"] == "multi-link (mod manager copy)"
    assert skips["Skyrim - Misc.bsa"] == "reparse point"
    got = {p.name for p in t.files}
    assert "Skyrim - Meshes0.bsa" not in got and "Skyrim - Misc.bsa" not in got
    subprocess.run([str(SYS32 / "cmd.exe"), "/c", "rmdir", str(bed.path("Skyrim - Misc.bsa"))], capture_output=True)


# ---- lock / status / unlock ----------------------------------------------------------------------------

@needs_windows
def test_lock_blocks_the_writes_and_the_bit_clear_and_records_the_manifest(bed):
    lock = _lock()
    res = lock.lock_all(str(bed.root))
    assert res.exit == 0, res.lines
    assert len(res.lines) <= 2 and all(len(x) <= 200 for x in res.lines)
    for n in VR_FILES:
        assert _locked(bed, n), n
        p = bed.path(n)
        with pytest.raises(OSError):
            open(str(p), "r+b").close()
        before = p.read_bytes()
        with pytest.raises(OSError):
            p.write_bytes(b"overwritten")
        assert p.read_bytes() == before
        # `attrib -R` under a WA deny prints "Access denied" and exits 0: judge the EFFECT.
        subprocess.run([str(SYS32 / "attrib.exe"), "-R", str(p)], capture_output=True)
        assert _ro(p), "the read-only bit was cleared by a plain attrib -R"
    manifest = json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))
    assert sorted(manifest["files"]) == sorted(VR_FILES)
    assert all(v["preexisting_ro"] is False for v in manifest["files"].values())
    listed = (bed.root / ".skyguard" / "locked.txt").read_text(encoding="utf-8").splitlines()
    assert sorted(os.path.normcase(x) for x in listed) == sorted(os.path.normcase(str(bed.path(n))) for n in VR_FILES)


@needs_windows
def test_lock_is_idempotent_and_status_is_clean(bed):
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.lock_all(str(bed.root)).exit == 0
    assert _icacls(bed.path("Skyrim.esm")).count("(DENY)") == 1
    s = lock.status(str(bed.root))
    assert s.exit == 0 and s.data["files"] and all(f["state"] == "locked" for f in s.data["files"])
    assert all("size" in f for f in s.data["files"])


@needs_windows
def test_status_before_any_lock_is_not_locked_and_exit_0(bed):
    s = _lock().status(str(bed.root))
    assert s.exit == 0 and "not locked" in " ".join(s.lines).lower()


@needs_windows
def test_status_names_drift_and_exits_1(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    # drift behind the manifest's back: remove one file's lock with the raw primitives
    p = bed.path("Update.esm")
    subprocess.run([str(SYS32 / "icacls.exe"), str(p), "/remove:d", "*" + _sid()], capture_output=True)
    subprocess.run([str(SYS32 / "attrib.exe"), "-R", str(p)], capture_output=True)
    s = lock.status(str(bed.root))
    assert s.exit == 1
    assert [f["name"] for f in s.data["files"] if f["state"] != "locked"] == ["Update.esm"]
    assert "Update.esm" in " ".join(s.lines)


@needs_windows
def test_status_sees_a_lock_whose_manifest_is_gone(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    (bed.root / ".skyguard" / "lock.json").unlink()
    s = lock.status(str(bed.root))
    assert s.exit == 1 and s.data["not_in_manifest"], "the filesystem is the truth (R4)"


@needs_windows
def test_unlock_all_restores_files_records_a_lift_and_empties_locked_txt(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    res = lock.unlock(str(bed.root), None, SESSION)
    assert res.exit == 0, res.lines
    for n in VR_FILES:
        assert not _ro(bed.path(n)) and "DENY" not in _icacls(bed.path(n)), n
        bed.path(n).write_bytes(b"writable again")
    from skyguard import state
    st = state.read(str(_state_file(bed)))
    assert not st.rejected and len(st.lifts) == 1
    lift = st.lifts[0]
    assert lift["kind"] == "lock" and lift["session_id"] == SESSION and "files" not in lift
    assert lift["expires_at"] - lift["created"] <= state.MAX_LIFT_SECONDS
    assert (bed.root / ".skyguard" / "locked.txt").read_text(encoding="utf-8").strip() == ""
    assert lock.status(str(bed.root)).exit == 0


@needs_windows
def test_unlock_one_file_leaves_the_rest_locked_and_the_lift_names_it(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    res = lock.unlock(str(bed.root), ["Update.esm"], SESSION)
    assert res.exit == 0, res.lines
    assert not _ro(bed.path("Update.esm")) and "DENY" not in _icacls(bed.path("Update.esm"))
    for n in VR_FILES:
        if n != "Update.esm":
            assert _locked(bed, n), n
    from skyguard import state
    assert state.read(str(_state_file(bed))).lifts[0]["files"] == ["Update.esm"]
    listed = (bed.root / ".skyguard" / "locked.txt").read_text(encoding="utf-8")
    assert "Update.esm" not in listed and "Skyrim.esm" in listed


@needs_windows
def test_unlock_accepts_a_full_path_inside_data_and_refuses_everything_else(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    other = bed.root / "Unofficial.esp"
    other.write_bytes(b"x")
    subprocess.run([str(SYS32 / "attrib.exe"), "+R", str(other)], capture_output=True)
    before = _icacls(bed.path("Skyrim.esm"))
    for bad in ("Unofficial.esp", str(other), "..\\Unofficial.esp", "Data\\..\\Unofficial.esp"):
        res = lock.unlock(str(bed.root), [bad], SESSION)
        assert res.exit == 2, (bad, res.lines)
    assert _ro(other), "a file the toolkit never locked was touched"
    assert _icacls(bed.path("Skyrim.esm")) == before
    assert lock.unlock(str(bed.root), [str(bed.path("Skyrim.esm"))], SESSION).exit == 0
    assert not _ro(bed.path("Skyrim.esm"))
    subprocess.run([str(SYS32 / "attrib.exe"), "-R", str(other)], capture_output=True)


@needs_windows
def test_unlock_without_a_session_is_refused_and_changes_nothing(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    res = lock.unlock(str(bed.root), None, None)
    assert res.exit == 2
    assert all(_locked(bed, n) for n in VR_FILES)
    assert not _state_file(bed).exists()


@needs_windows
def test_unlock_session_comes_from_the_environment_or_the_flag(bed, monkeypatch, capsys):
    lock = _lock()
    lock.lock_all(str(bed.root))
    monkeypatch.setenv("SKYGUARD_SESSION_ID", "sess-from-env")
    assert lock.main(["unlock", "Skyrim.esm", "--root", str(bed.root)]) == 0
    from skyguard import state
    assert state.read(str(_state_file(bed))).lifts[0]["session_id"] == "sess-from-env"
    monkeypatch.delenv("SKYGUARD_SESSION_ID")
    assert lock.main(["unlock", "Update.esm", "--root", str(bed.root), "--session", "sess-flag"]) == 0
    assert state.read(str(_state_file(bed))).lifts[-1]["session_id"] == "sess-flag"
    assert lock.main(["unlock", "Dawnguard.esm", "--root", str(bed.root)]) == 2


@needs_windows
def test_unlock_refuses_when_the_lift_cannot_be_recorded(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    _state_file(bed).parent.mkdir(exist_ok=True)
    _state_file(bed).mkdir()          # state.json is a directory: nothing can be written there
    res = lock.unlock(str(bed.root), None, SESSION)
    assert res.exit == 2, res.lines
    assert all(_locked(bed, n) for n in VR_FILES), "unlocked without a recorded lift"
    _state_file(bed).rmdir()


@needs_windows
def test_partial_lock_exits_1_and_leaves_the_multilink_file_alone(bed):
    lock = _lock()
    os.link(str(bed.path("Skyrim - Textures0.bsa")), str(bed.root / "managerlink.bin"))
    res = lock.lock_all(str(bed.root))
    assert res.exit == 1, res.lines
    assert not _ro(bed.path("Skyrim - Textures0.bsa"))
    assert all(_locked(bed, n) for n in VR_FILES if n != "Skyrim - Textures0.bsa")


@needs_windows
def test_lock_with_no_vanilla_files_is_refused(bed):
    for n in VR_FILES:
        bed.path(n).unlink()
    assert _lock().lock_all(str(bed.root)).exit == 2


@pytest.mark.parametrize("which", ["lock", "unlock", "status"])
def test_unsupported_platform_exits_3_and_changes_nothing(bed, monkeypatch, which):
    lock = _lock()
    monkeypatch.setattr(lock, "supported", lambda: False)
    res = {"lock": lambda: lock.lock_all(str(bed.root)),
           "unlock": lambda: lock.unlock(str(bed.root), None, SESSION),
           "status": lambda: lock.status(str(bed.root))}[which]()
    assert res.exit == 3, res.lines
    assert not (bed.root / ".skyguard").exists()
    assert not _ro(bed.path("Skyrim.esm"))


# ---- R5: unlock never clears protection the toolkit did not set --------------------------------------

def _scenario_preexisting_ro(lock, bed, lose_manifest):
    pre = bed.path("Skyrim - Interface.bsa")
    subprocess.run([str(SYS32 / "attrib.exe"), "+R", str(pre)], capture_output=True)
    assert _ro(pre)
    assert lock.lock_all(str(bed.root)).exit == 0
    if not lose_manifest:
        manifest = json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))
        assert manifest["files"]["Skyrim - Interface.bsa"]["preexisting_ro"] is True
        assert manifest["files"]["Skyrim.esm"]["preexisting_ro"] is False
    assert _ro(pre) and "DENY" in _icacls(pre)
    if lose_manifest:
        (bed.root / ".skyguard" / "lock.json").unlink()
    assert lock.unlock(str(bed.root), None, SESSION).exit == 0
    assert _ro(pre), "unlock cleared a read-only bit the user set (R5)"
    assert "DENY" not in _icacls(pre)
    for n in VR_FILES:
        if n != "Skyrim - Interface.bsa":
            assert not _ro(bed.path(n)) and "DENY" not in _icacls(bed.path(n)), n
    subprocess.run([str(SYS32 / "attrib.exe"), "-R", str(pre)], capture_output=True)


@needs_windows
def test_unlock_leaves_a_preexisting_readonly_file_readonly(bed):
    _scenario_preexisting_ro(_lock(), bed, lose_manifest=False)


@needs_windows
def test_unlock_after_the_manifest_is_lost_still_leaves_the_users_readonly_bit(bed):
    _scenario_preexisting_ro(_lock(), bed, lose_manifest=True)


@needs_windows
def test_lock_with_an_unreadable_manifest_still_locks_and_rewrites_it(bed):
    lock = _lock()
    (bed.root / ".skyguard").mkdir()
    (bed.root / ".skyguard" / "lock.json").write_bytes(b"\x00garbage{")
    assert lock.lock_all(str(bed.root)).exit == 0
    assert sorted(json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))["files"]) \
        == sorted(VR_FILES)


@needs_windows
def test_unlock_sweeps_the_locked_file_that_travelled_into_a_backup_name(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    (bed.data / "zEdit Backups").mkdir()
    # what xEdit's save does: the locked master is renamed into the backup folder (the lock travels)
    os.replace(str(bed.path("Update.esm")), str(bed.data / "zEdit Backups" / "Update.esm.2026_10_05.bak"))
    bed.path("Update.esm").write_bytes(b"the new save")
    assert lock.status(str(bed.root)).data["travelled"], "status must report the travelled lock"
    assert lock.unlock(str(bed.root), None, SESSION).exit == 0
    bak = bed.data / "zEdit Backups" / "Update.esm.2026_10_05.bak"
    assert not _ro(bak) and "DENY" not in _icacls(bak)


@needs_windows
def test_disable_is_recorded_blocks_lock_and_enable_relocks(bed):
    lock = _lock()
    assert lock.disable(str(bed.root), SESSION).exit == 0
    from skyguard import state
    st = state.read(str(_state_file(bed)))
    assert st.lock_disabled and st.lock_disabled["by_session"] == SESSION
    assert lock.lock_all(str(bed.root)).exit == 2
    assert not _ro(bed.path("Skyrim.esm"))
    assert "disabled" in " ".join(lock.status(str(bed.root)).lines).lower()
    assert lock.enable(str(bed.root), SESSION).exit == 0
    assert state.read(str(_state_file(bed))).lock_disabled is None
    assert all(_locked(bed, n) for n in VR_FILES)


@needs_windows
def test_disable_keeps_the_existing_lifts(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION)
    lock.disable(str(bed.root), None)
    from skyguard import state
    st = state.read(str(_state_file(bed)))
    assert len(st.lifts) == 1 and st.lock_disabled["by_session"] == "manual"


@needs_windows
def test_state_folder_junction_is_refused_for_writes(bed):
    lock = _lock()
    elsewhere = bed.root / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run([str(SYS32 / "cmd.exe"), "/c", "mklink", "/J", str(bed.root / ".skyguard"),
                        str(elsewhere)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cannot create a junction here")
    res = lock.disable(str(bed.root), SESSION)
    assert res.exit == 2 and not list(elsewhere.iterdir())
    subprocess.run([str(SYS32 / "cmd.exe"), "/c", "rmdir", str(bed.root / ".skyguard")], capture_output=True)


# ---- sandbox ---------------------------------------------------------------------------------------------

def _scenario_sandbox(lock, bed, monkeypatch):
    box = SCRATCH / ("box-%s" % uuid.uuid4().hex[:6])
    box.mkdir()
    monkeypatch.setenv("SKYGUARD_LOCK_SANDBOX", str(box))
    try:
        with pytest.raises(lock.SandboxViolation):
            lock.lock_all(str(bed.root))
        assert not _ro(bed.path("Skyrim.esm")), "a file outside the sandbox was locked"
        with pytest.raises(lock.SandboxViolation):
            lock.apply_one(str(bed.path("Skyrim.esm")))
        assert not _ro(bed.path("Skyrim.esm")), "apply_one wrote outside the sandbox"
    finally:
        shutil.rmtree(str(box), ignore_errors=True)


@needs_windows
def test_sandbox_refuses_a_target_outside_it(bed, monkeypatch):
    _scenario_sandbox(_lock(), bed, monkeypatch)


@needs_windows
def test_sandbox_violation_is_not_an_exception_and_the_cli_exits_2(bed, monkeypatch):
    lock = _lock()
    assert issubclass(lock.SandboxViolation, BaseException) and not issubclass(lock.SandboxViolation, Exception)
    box = SCRATCH / ("box-%s" % uuid.uuid4().hex[:6])
    box.mkdir()
    monkeypatch.setenv("SKYGUARD_LOCK_SANDBOX", str(box))
    assert lock.main(["lock", "--root", str(bed.root)]) == 2
    assert not _ro(bed.path("Skyrim.esm"))
    shutil.rmtree(str(box), ignore_errors=True)


@needs_windows
def test_sandbox_resolves_a_junction_out_of_the_box(bed, monkeypatch):
    lock = _lock()
    box = SCRATCH / ("box-%s" % uuid.uuid4().hex[:6])
    box.mkdir()
    link = box / "j"
    r = subprocess.run([str(SYS32 / "cmd.exe"), "/c", "mklink", "/J", str(link), str(bed.data)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cannot create a junction here")
    monkeypatch.setenv("SKYGUARD_LOCK_SANDBOX", str(box))
    with pytest.raises(lock.SandboxViolation):
        lock.apply_one(str(link / "Skyrim.esm"))
    assert not _ro(bed.path("Skyrim.esm"))
    subprocess.run([str(SYS32 / "cmd.exe"), "/c", "rmdir", str(link)], capture_output=True)
    shutil.rmtree(str(box), ignore_errors=True)


# ---- re-read verification and the single-link choke -----------------------------------------------------

def _scenario_verify_reread(lock, bed, monkeypatch):
    monkeypatch.setattr(lock, "_apply_file", lambda path, *a, **k: None)   # claims success, does nothing
    res = lock.lock_all(str(bed.root))
    assert res.exit == 1, "lock reported %d though nothing was locked: %s" % (res.exit, res.lines)
    assert not _ro(bed.path("Skyrim.esm"))


@needs_windows
def test_lock_verifies_by_rereading_the_filesystem(bed, monkeypatch):
    _scenario_verify_reread(_lock(), bed, monkeypatch)


def _scenario_unlock_verify_reread(lock, bed, monkeypatch):
    assert lock.lock_all(str(bed.root)).exit == 0
    monkeypatch.setattr(lock, "_remove_file", lambda path, *a, **k: None)
    res = lock.unlock(str(bed.root), None, SESSION)
    assert res.exit == 1, "unlock reported %d though the files are still locked: %s" % (res.exit, res.lines)
    assert _locked(bed, "Skyrim.esm")


@needs_windows
def test_unlock_verifies_by_rereading_the_filesystem(bed, monkeypatch):
    _scenario_unlock_verify_reread(_lock(), bed, monkeypatch)


def _scenario_single_link_targets(lock, bed):
    os.link(str(bed.path("Skyrim.esm")), str(bed.root / "managerlink.bin"))
    assert "Skyrim.esm" not in {p.name for p in lock.targets(str(bed.data)).files}, "a multi-link file entered the target set"


def _scenario_single_link_apply(lock, bed):
    os.link(str(bed.path("Skyrim.esm")), str(bed.root / "managerlink.bin"))
    with pytest.raises(ValueError):
        lock.apply_one(str(bed.path("Skyrim.esm")))   # the TOCTOU choke: a link made after targets()
    assert not _ro(bed.path("Skyrim.esm")) and not _ro(bed.root / "managerlink.bin")


@needs_windows
def test_single_link_is_checked_in_targets(bed):
    _scenario_single_link_targets(_lock(), bed)


@needs_windows
def test_single_link_is_rechecked_at_apply(bed):
    _scenario_single_link_apply(_lock(), bed)


# ---- mutation twins ---------------------------------------------------------------------------------------

_pkg_ids = itertools.count()


def _mutant(tmp_path, anchor, replacement):
    src = (REPO / "tools" / "skyguard" / "lock.py").read_text(encoding="utf-8")
    assert src.count(anchor) == 1, "mutation anchor moved or is not unique: %r" % anchor
    name = "skyguard_mut_%d_%s" % (next(_pkg_ids), uuid.uuid4().hex[:4])
    dst = tmp_path / name
    shutil.copytree(str(REPO / "tools" / "skyguard"), str(dst),
                    ignore=shutil.ignore_patterns("__pycache__", "adapters", "*.pyc"))
    (dst / "lock.py").write_text(src.replace(anchor, replacement), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module(name + ".lock")
    finally:
        sys.path.remove(str(tmp_path))
    return mod


def _red(scenario, why, mod, *args):
    """The scenario must FAIL against the mutant, and for the NAMED reason (`why` is a substring of the failure
    text), or the twin goes red for something unrelated and proves nothing."""
    try:
        scenario(mod, *args)
    except (AssertionError, pytest.fail.Exception) as e:
        assert why in str(e), "the mutant failed, but not on the asserted check (%r): %s" % (why, str(e)[:300])
        return
    except BaseException as e:  # noqa: BLE001
        pytest.fail("mutant died of the wrong thing, not the asserted check: %r" % (e,))
    pytest.fail("the mutant SURVIVED: the scenario did not go red")


def test_mutation_l2_targets_glob(bed, tmp_path):
    m = _mutant(tmp_path, "    names = _names(edition, ccc)\n",
                "    names = sorted(p.name for p in Path(data_dir).glob('*S*') if p.is_file())\n")
    _red(_scenario_lookalikes, "lookalike name entered", m, bed)


@needs_windows
def test_mutation_l2_lock_single_link_targets(bed, tmp_path):
    m = _mutant(tmp_path, "    if n != 1:\n        return \"multi-link (mod manager copy)\"\n",
                "    if False:\n        return \"multi-link (mod manager copy)\"\n")
    _red(_scenario_single_link_targets, "multi-link file entered", m, bed)


@needs_windows
def test_mutation_l2_lock_single_link_apply(bed, tmp_path):
    m = _mutant(tmp_path, "    if single and _links(path, st) != 1:\n", "    if False:\n")
    _red(_scenario_single_link_apply, "DID NOT RAISE", m, bed)


@needs_windows
def test_mutation_l2_lock_sandbox(bed, tmp_path, monkeypatch):
    m = _mutant(tmp_path, "    if target == root:\n        raise SandboxViolation(", "    if True:\n        return str(path)\n    if False:\n        raise SandboxViolation(")
    _red(_scenario_sandbox, "DID NOT RAISE", m, bed, monkeypatch)


@needs_windows
def test_mutation_l2_lock_verify_reread(bed, tmp_path, monkeypatch):
    m = _mutant(tmp_path, "    return st.readonly and st.ours == expected\n", "    return True\n")
    _red(_scenario_verify_reread, "lock reported 0", m, bed, monkeypatch)


@needs_windows
def test_mutation_l2_unlock_verify_reread(bed, tmp_path, monkeypatch):
    m = _mutant(tmp_path, "    return st.ours is None and (keep_bit or not st.readonly)\n", "    return True\n")
    _red(_scenario_unlock_verify_reread, "unlock reported 0", m, bed, monkeypatch)


@needs_windows
def test_mutation_l2_lock_preexisting_ro(bed, tmp_path):
    m = _mutant(tmp_path, "    keep_bit = preexisting or st.ours == \"pre\"\n", "    keep_bit = False\n")
    _red(_scenario_preexisting_ro, "cleared a read-only bit", m, bed, False)


@needs_windows
def test_mutation_l2_lock_preexisting_tag(bed, tmp_path):
    m = _mutant(tmp_path, "    mask = MASK_PRE if pre_ro else MASK_FULL\n", "    mask = MASK_FULL\n")
    _red(_scenario_preexisting_ro, "cleared a read-only bit", m, bed, True)


# ---- the Python-free unlock (U2) ---------------------------------------------------------------------------

def _stage_cmd(bed):
    (bed.root / "tools").mkdir(exist_ok=True)
    dst = bed.root / "tools" / "skyguard-unlock.cmd"
    shutil.copyfile(str(REPO / "tools" / "skyguard-unlock.cmd"), str(dst))
    return dst


def _nopy_env():
    return {"PATH": r"C:\Windows\System32;C:\Windows", "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "COMSPEC": str(SYS32 / "cmd.exe"), "SKYGUARD_NO_PAUSE": "1",
            "SKYGUARD_LOCK_SANDBOX": str(SCRATCH)}


def _run_cmd(script, env):
    # The script path is NOT quoted: `cmd /c` strips the outer quotes of a line that starts with one (MEASURED).
    return subprocess.run("%s /d /c %s" % (SYS32 / "cmd.exe", script), capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


@needs_windows
def test_python_free_unlock_removes_the_lock_with_python_absent_from_path(bed):
    lock = _lock()
    pre = bed.path("Skyrim - Interface.bsa")
    subprocess.run([str(SYS32 / "attrib.exe"), "+R", str(pre)], capture_output=True)
    assert lock.lock_all(str(bed.root)).exit == 0
    script = _stage_cmd(bed)
    env = _nopy_env()
    where = subprocess.run("%s /d /c where python" % (SYS32 / "cmd.exe"), capture_output=True, text=True, env=env)
    assert where.returncode != 0, "python is still on the stripped PATH: the test would prove nothing"
    r = _run_cmd(script, env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "Skyrim.esm" in r.stdout, "the script must print what it did"
    for n in VR_FILES:
        assert "DENY" not in _icacls(bed.path(n)), n
        if n != "Skyrim - Interface.bsa":
            assert not _ro(bed.path(n)), n
    assert _ro(pre), "the .cmd cleared a read-only bit the user set (R5)"
    subprocess.run([str(SYS32 / "attrib.exe"), "-R", str(pre)], capture_output=True)


@needs_windows
def test_python_free_unlock_refuses_a_path_outside_the_sandbox_and_reports_missing(bed):
    lock = _lock()
    lock.lock_all(str(bed.root))
    script = _stage_cmd(bed)
    box = SCRATCH / ("box-%s" % uuid.uuid4().hex[:6])
    box.mkdir()
    env = _nopy_env()
    env["SKYGUARD_LOCK_SANDBOX"] = str(box)
    r = _run_cmd(script, env)
    assert r.returncode != 0
    assert all(_locked(bed, n) for n in VR_FILES), "the .cmd touched a file outside the sandbox"
    shutil.rmtree(str(box), ignore_errors=True)
    (bed.root / ".skyguard" / "locked.txt").write_text(str(bed.root / "elsewhere.bin") + "\n", encoding="utf-8")
    r = _run_cmd(script, _nopy_env())
    assert r.returncode == 3 and "REFUSED" in r.stdout and all(_locked(bed, n) for n in VR_FILES)


def test_python_free_unlock_script_is_pure_cmd_and_crlf():
    raw = (REPO / "tools" / "skyguard-unlock.cmd").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), "a .cmd must be CRLF (labels/goto)"
    text = raw.decode("ascii").lower()
    for forbidden in ("python", "py.exe", "powershell", "pwsh", "bash"):
        assert forbidden not in text, forbidden
    assert "attrib" in text and "icacls" in text and "locked.txt" in text


# ---- the CLI -----------------------------------------------------------------------------------------------

@needs_windows
def test_cli_runs_as_a_directory_and_maps_exit_codes(bed):
    env = {k: os.environ[k] for k in ("SystemRoot", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP") if k in os.environ}
    env["SKYGUARD_LOCK_SANDBOX"] = str(SCRATCH)
    run = lambda *a: subprocess.run([sys.executable, "-B", str(REPO / "tools" / "skyguard")] + list(a),
                                    capture_output=True, text=True, env=env, timeout=120)
    r = run("lock", "lock", "--root", str(bed.root))
    assert r.returncode == 0, (r.stdout, r.stderr)
    r = run("lock", "status", "--json", "--root", str(bed.root))
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["edition"] == "vr" and data["mechanism"] == "M-D" and data["default"] == "off"
    assert run("lock", "unlock", "--root", str(bed.root)).returncode == 2      # no session
    assert run("lock", "unlock", "--root", str(bed.root), "--session", SESSION).returncode == 0
    assert run("lock", "frobnicate").returncode == 2


def test_cli_dispatches_guards_and_doctor(tmp_path, capsys):
    from skyguard import cli
    assert cli.main(["guards", "status"]) == 3         # shadow mode ships with Task 9
    assert cli.main(["nonsense"]) == 2
    assert cli.main([]) == 2


def test_default_is_off_until_the_batch_decides(bed):
    assert _lock().DEFAULT == "off"
    assert "default off" in " ".join(_lock().status(str(bed.root)).lines).lower() or WINDOWS


@needs_windows
def test_wrapper_scripts_exist_and_the_cmd_twin_runs(bed):
    assert (REPO / "tools" / "skyguard.sh").is_file() and (REPO / "tools" / "skyguard.cmd").is_file()
    env = {k: os.environ[k] for k in ("SystemRoot", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP") if k in os.environ}
    env["SKYGUARD_PYTHON"] = sys.executable
    env["SKYGUARD_LOCK_SANDBOX"] = str(SCRATCH)
    r = subprocess.run("%s /d /c %s lock status --json --root %s" % (SYS32 / "cmd.exe", REPO / "tools" / "skyguard.cmd",
                                                                   bed.root),
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert json.loads(r.stdout)["edition"] == "vr"
