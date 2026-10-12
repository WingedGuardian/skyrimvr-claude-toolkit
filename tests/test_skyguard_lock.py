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


def _acl(p):
    """icacls output that is known to be REAL (review M4): a failed or empty icacls run would make `"DENY" not in ...`
    pass vacuously, so it fails the test instead, carrying what icacls printed."""
    r = subprocess.run([str(SYS32 / "icacls.exe"), str(p)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip(), (p, r.returncode, r.stdout, r.stderr)
    return r.stdout


def _attrib(flag, p):
    r = subprocess.run([str(SYS32 / "attrib.exe"), flag, str(p)], capture_output=True, text=True)
    assert r.returncode == 0, (flag, p, r.returncode, r.stdout, r.stderr)


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


_BYPASS_PRIVILEGES = ("SeBackupPrivilege", "SeRestorePrivilege")


def _set_privileges(names, enable):
    """Enable or disable privileges in THIS process's token; returns the names whose state changed. A process
    holding SeBackupPrivilege/SeRestorePrivilege ENABLED is not refused by a deny ACE when it opens with backup intent
    -- SetFileAttributesW and a FILE_FLAG_BACKUP_SEMANTICS open both do -- so every scenario that measures a deny
    entry biting (the l2 ACE/bit order twins, the deny-on-delete handle read) cannot go red in such a process.
    INFERRED for windows-latest, whose elevated runner account is the one place those four went green-when-mutated."""
    import ctypes
    from ctypes import wintypes as wt

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

    class TP(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wt.DWORD), ("Luid", LUID), ("Attributes", wt.DWORD)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
    adv.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.POINTER(TP), wt.DWORD, ctypes.POINTER(TP),
                                          ctypes.POINTER(wt.DWORD)]
    k32.GetCurrentProcess.restype = wt.HANDLE
    tok = wt.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x20 | 0x8, ctypes.byref(tok)):   # ADJUST | QUERY
        return []
    changed = []
    try:
        for name in names:
            tp, prev, need = TP(), TP(), wt.DWORD()
            if not adv.LookupPrivilegeValueW(None, name, ctypes.byref(tp.Luid)):
                continue
            tp.PrivilegeCount, tp.Attributes = 1, (0x2 if enable else 0)
            ok = adv.AdjustTokenPrivileges(tok, False, ctypes.byref(tp), ctypes.sizeof(prev), ctypes.byref(prev),
                                           ctypes.byref(need))
            # ERROR_NOT_ALL_ASSIGNED (1300): the token does not hold it -- nothing changed
            if ok and ctypes.get_last_error() != 1300 and prev.PrivilegeCount == 1 \
                    and bool(prev.Attributes & 0x2) != enable:
                changed.append(name)
    finally:
        k32.CloseHandle(tok)
    return changed


@pytest.fixture
def bed(monkeypatch):
    # The scenarios measure what a deny entry does to an ordinary process: one without enabled backup/restore
    # privileges (see _set_privileges). Disabled for the test, re-enabled afterwards exactly as found.
    disabled = _set_privileges(_BYPASS_PRIVILEGES, False) if WINDOWS else []
    try:
        yield from _bed(monkeypatch)
    finally:
        if disabled:
            _set_privileges(disabled, True)


def _bed(monkeypatch):
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
    _attrib("+R", pre)
    assert _ro(pre)
    res = lock.lock_all(str(bed.root))
    assert res.exit == 0, res.lines
    if not lose_manifest:
        manifest = json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))
        assert manifest["files"]["Skyrim - Interface.bsa"]["preexisting_ro"] is True
        assert manifest["files"]["Skyrim.esm"]["preexisting_ro"] is False
    assert _ro(pre) and "DENY" in _acl(pre)
    if lose_manifest:
        (bed.root / ".skyguard" / "lock.json").unlink()
    res = lock.unlock(str(bed.root), None, SESSION)
    assert res.exit == 0, res.lines
    assert _ro(pre), "unlock cleared a read-only bit the user set (R5)"
    assert "DENY" not in _acl(pre)
    for n in VR_FILES:
        if n != "Skyrim - Interface.bsa":
            assert not _ro(bed.path(n)) and "DENY" not in _acl(bed.path(n)), n
    _attrib("-R", pre)


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
            "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"), "COMSPEC": str(SYS32 / "cmd.exe"), "SKYGUARD_NO_PAUSE": "1",
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
    assert cli.main(["guards", "status", "--root", str(tmp_path)]) == 0     # Task 9: shadow mode, read-only here
    assert cli.main(["guards"]) == 2
    assert cli.main(["nonsense"]) == 2
    assert cli.main([]) == 2


def test_default_is_off_until_the_batch_decides(bed):
    assert _lock().DEFAULT == "off"
    assert "default off" in " ".join(_lock().status(str(bed.root)).lines).lower() or WINDOWS


def _sh_with_python(bed, tmp_path, value):
    from conftest import BASH
    env = {k: os.environ[k] for k in ("SystemRoot", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "PATH")
           if k in os.environ}
    env["SKYGUARD_PYTHON"] = value
    env["SKYGUARD_LOCK_SANDBOX"] = str(SCRATCH)
    return subprocess.run([BASH, str(REPO / "tools" / "skyguard.sh"), "lock", "status", "--json", "--root",
                           str(bed.root)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)


def _py_wrapper(folder: Path, mark: Path) -> Path:
    """A bash script standing in for an interpreter: it records that it ran (with any --tag), then runs Python."""
    folder.mkdir(parents=True, exist_ok=True)
    w = folder / "python"
    w.write_bytes(('#!/bin/bash\nif [ "$1" = "--tag" ]; then shift; echo tag >> "%s"; else echo plain >> "%s"; fi\n'
                   'exec "%s" "$@"\n' % (mark.as_posix(), mark.as_posix(), sys.executable.replace("\\", "/")))
                  .encode("ascii"))
    return w


@needs_windows
def test_skyguard_sh_runs_a_skyguard_python_whose_path_has_spaces(bed, tmp_path):
    """Lane A M3: the value was word-split, so `C:/Program Files/...` failed its probe and the loop fell through to
    python3/python with no word -- the user's choice silently ignored. The wrapper must be the one that ran."""
    mark = tmp_path / "ran.txt"
    w = _py_wrapper(tmp_path / "my python dir", mark)
    r = _sh_with_python(bed, tmp_path, w.as_posix())
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert mark.read_text(encoding="ascii").split() == ["plain", "plain"]      # the probe, then the run


@needs_windows
def test_skyguard_sh_still_splits_a_launcher_with_arguments(bed, tmp_path):
    """The twin: a value that is not an existing path (`py -3` style) is still split into words."""
    mark = tmp_path / "ran.txt"
    w = _py_wrapper(tmp_path / "nospace", mark)
    r = _sh_with_python(bed, tmp_path, w.as_posix() + " --tag")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert mark.read_text(encoding="ascii").split() == ["tag", "tag"]


@needs_windows
def test_the_bash_wrapper_called_by_a_backslash_path_from_elsewhere_runs_its_own_package(bed, tmp_path):
    """Task 16 found it: `bash C:\\...\\tools\\skyguard.sh` split its path on `/` only, so `here` was `.` and it ran
    `./skyguard` from the CALLER's folder -- `lock lock` then did nothing at all. Called from a folder with no
    skyguard package, it must still find its own."""
    from conftest import BASH
    env = {k: os.environ[k] for k in ("SystemRoot", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "PATH")
           if k in os.environ}
    env["SKYGUARD_PYTHON"] = sys.executable.replace("\\", "/")
    env["SKYGUARD_LOCK_SANDBOX"] = str(SCRATCH)
    script = str(REPO / "tools" / "skyguard.sh").replace("/", "\\")
    r = subprocess.run([BASH, script, "lock", "status", "--json", "--root", str(bed.root)], cwd=str(tmp_path),
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert json.loads(r.stdout)["edition"] == "vr", r.stdout


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


# ---- Plan 6 final fix (lane A I2): lock and unlock never change ACL inheritance ------------------------------------
# _set_dacl passed UNPROTECTED_DACL_SECURITY_INFORMATION on every write, so a file whose inheritance was turned off
# gained every inheritable ACE of Data\ on `lock lock` and KEPT them after `lock unlock` (MEASURED by the reviewer). The
# lock now keeps the file's current protection when it adds its entry, records the original in the manifest, and puts
# exactly that back on unlock. Compared as the DACL's SDDL ("D:P..." = protected), before lock and after unlock.

def _sddl(p):
    import ctypes
    from ctypes import wintypes as wt
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [wt.LPCWSTR, ctypes.c_int, wt.DWORD, vp, vp, ctypes.POINTER(vp), vp,
                                          ctypes.POINTER(vp)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [vp, wt.DWORD, wt.DWORD,
                                                                         ctypes.POINTER(wt.LPWSTR), vp]
    k32.LocalFree.argtypes = [vp]
    dacl, sd, out = vp(), vp(), wt.LPWSTR()
    rc = adv.GetNamedSecurityInfoW(str(p), 1, 0x4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    assert rc == 0, rc
    try:
        assert adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4, ctypes.byref(out), None)
        try:
            return out.value
        finally:
            k32.LocalFree(ctypes.cast(out, vp))
    finally:
        k32.LocalFree(sd)


def _no_inheritance(p):
    r = subprocess.run([str(SYS32 / "icacls.exe"), str(p), "/inheritance:d"], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _sddl(p).startswith("D:P"), _sddl(p)


@needs_windows
def test_lock_and_unlock_keep_a_protected_dacl_exactly(bed):
    lock = _lock()
    p = bed.path("Skyrim.esm")
    _no_inheritance(p)
    before = _sddl(p)
    assert lock.lock_all(str(bed.root)).exit == 0
    during = _sddl(p)
    assert during.startswith("D:P") and ";ID;" not in during, during      # no inherited entry was pulled in
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION).exit == 0
    assert _sddl(p) == before


@needs_windows
def test_lock_and_unlock_keep_an_inheriting_dacl_exactly(bed):
    """The twin: a file that inherits (the default) keeps inheriting, and its DACL comes back identical."""
    lock = _lock()
    p = bed.path("Update.esm")
    before = _sddl(p)
    assert not before.startswith("D:P"), before
    assert lock.lock_all(str(bed.root)).exit == 0
    assert not _sddl(p).startswith("D:P")
    assert lock.unlock(str(bed.root), ["Update.esm"], SESSION).exit == 0
    assert _sddl(p) == before


def _inherit_without_auto_inherited(bed, name):
    """Recreate Data/<name> under a folder DACL written by the legacy API, so the file inherits its entries WITHOUT
    the SE_DACL_AUTO_INHERITED bit (SDDL `D:(...ID...)`, no `AI`). MEASURED: every file under windows-latest's
    D:\\a\\_temp looks like this; a local temp folder written through the modern API carries `AI` and hid the defect."""
    import ctypes
    from ctypes import wintypes as wt
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(vp), vp]
    adv.SetFileSecurityW.argtypes = [wt.LPCWSTR, wt.DWORD, vp]
    k32.LocalFree.argtypes = [vp]
    sd = vp()
    sddl = "D:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)(A;OICI;FA;;;%s)" % _sid()
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None)
    try:
        assert adv.SetFileSecurityW(str(bed.data), 0x4, sd), ctypes.get_last_error()
    finally:
        k32.LocalFree(sd)
    p = bed.path(name)
    p.unlink()
    p.write_bytes(os.urandom(1024))
    return p


@needs_windows
def test_lock_and_unlock_keep_an_inheriting_dacl_without_the_auto_inherited_bit(bed):
    """lock + unlock wrote the DACL through SetNamedSecurityInfoW, which marks it auto-inherited: a file without
    the bit came back `D:AI(...)` -- the same entries, a different DACL (windows-latest CI, test above)."""
    lock = _lock()
    p = _inherit_without_auto_inherited(bed, "Update.esm")
    before = _sddl(p)
    assert before.startswith("D:(") and ";ID;" in before, before
    assert lock.lock_all(str(bed.root)).exit == 0
    during = _sddl(p)
    assert during.startswith("D:(") and lock.read_state(p).ours == "full", during
    assert lock.unlock(str(bed.root), ["Update.esm"], SESSION).exit == 0
    assert _sddl(p) == before


@needs_windows
def test_unlock_restores_the_protection_the_manifest_recorded(bed):
    """Recorded at lock time, restored on unlock: a protection flipped while the file was locked is put back."""
    lock = _lock()
    p = bed.path("Dawnguard.esm")
    _no_inheritance(p)
    assert lock.lock_all(str(bed.root)).exit == 0
    man = json.loads((bed.root / ".skyguard" / "lock.json").read_text(encoding="utf-8"))
    assert man["files"]["Dawnguard.esm"]["prior_protected"] is True
    r = subprocess.run([str(SYS32 / "icacls.exe"), str(p), "/inheritance:e"], capture_output=True, text=True)
    assert r.returncode == 0 and not _sddl(p).startswith("D:P"), (r.stdout, r.stderr)
    assert lock.unlock(str(bed.root), ["Dawnguard.esm"], SESSION).exit == 0
    assert _sddl(p).startswith("D:P"), _sddl(p)


# ---- Plan 6 final fix (A-I3 / C-I1, ruling): a lock lift's expiry is real ------------------------------------------
# The first guarded hook call after a lock lift's expires_at relocks the files it lifted (engine.shadow, which every
# decision path already runs and which already reads the state); session start stays the backstop. Fake clock: the lift
# is written at T and the hook is called at T +/- a second around T + LIFT_SECONDS.

T0 = 1_900_000_000.0


def _hook(bed, now):
    from skyguard import audit, engine
    from skyguard.verdict import Verdict
    return engine.shadow(Verdict(), str(bed.root), "sess-other", "", audit.Audit(str(bed.root)), now=now)


def _audit_log(bed):
    p = bed.root / ".claude" / "backups" / "AUDIT_LOG.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _lifts(bed):
    from skyguard import state
    return state.read(str(_state_file(bed))).lifts


@needs_windows
def test_the_first_hook_call_after_a_lock_lift_expires_relocks_the_file_and_logs_it(bed):
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=T0).exit == 0
    assert not _locked(bed, "Skyrim.esm")
    v = _hook(bed, T0 + lock.LIFT_SECONDS + 1)
    assert v.decision == "allow"                                   # the decision is untouched
    assert _locked(bed, "Skyrim.esm")
    assert not [x for x in _lifts(bed) if x["kind"] == "lock"]     # cleared, so the next call does nothing
    assert "RELOCK" in _audit_log(bed) and "Skyrim.esm" in _audit_log(bed)


@needs_windows
def test_a_hook_call_before_the_expiry_relocks_nothing(bed):
    """The twin: the same call a second BEFORE the expiry leaves the lifted file open and the lift recorded."""
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=T0).exit == 0
    _hook(bed, T0 + lock.LIFT_SECONDS - 1)
    assert not _locked(bed, "Skyrim.esm")
    assert [x for x in _lifts(bed) if x["kind"] == "lock"]
    assert "RELOCK" not in _audit_log(bed)


@needs_windows
def test_an_expired_lift_does_not_relock_a_file_a_live_lift_still_covers(bed):
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=T0).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm", "Update.esm"], "sess-lock-2", now=T0 + 1800).exit == 0
    _hook(bed, T0 + lock.LIFT_SECONDS + 1)                          # the first lift is due, the second is not
    assert not _locked(bed, "Skyrim.esm") and not _locked(bed, "Update.esm")
    assert [x["session_id"] for x in _lifts(bed)] == ["sess-lock-2"]
    _hook(bed, T0 + 1800 + lock.LIFT_SECONDS + 1)                   # now the second is due too
    assert _locked(bed, "Skyrim.esm") and _locked(bed, "Update.esm")


@needs_windows
def test_an_expired_unlock_all_relocks_every_target_and_nothing_while_disabled(bed):
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), None, SESSION, now=T0).exit == 0
    _hook(bed, T0 + lock.LIFT_SECONDS + 1)
    assert all(_locked(bed, n) for n in VR_FILES)
    # the persistent disable is the user's: an expired lift never relocks over it
    assert lock.unlock(str(bed.root), None, SESSION, now=T0 + 10).exit == 0
    assert lock.disable(str(bed.root), SESSION).exit == 0
    _hook(bed, T0 + 10 + lock.LIFT_SECONDS + 1)
    assert not any(_locked(bed, n) for n in VR_FILES)
    assert "nothing relocked (the lock is disabled)" in _audit_log(bed)


@needs_windows
def test_an_expired_lift_relocks_only_the_files_it_named(bed):
    """A file opened with no lift on record (its record cleared, or opened outside skyguard) is session start's to
    report and relock; the expiry relocks what ITS lift opened."""
    from skyguard import state
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=T0).exit == 0
    assert lock.unlock(str(bed.root), ["Update.esm"], "sess-lock-2", now=T0).exit == 0
    st = state.read(str(_state_file(bed)))
    state.write(str(_state_file(bed)), {"v": 1, "persistent": {},
                                        "lifts": [x for x in st.lifts if x["session_id"] == SESSION]})
    _hook(bed, T0 + lock.LIFT_SECONDS + 1)
    assert _locked(bed, "Skyrim.esm") and not _locked(bed, "Update.esm")


@needs_windows
def test_a_cmd_style_unlock_with_no_lift_record_survives_hook_calls(bed):
    """The user's own tools/skyguard-unlock.cmd records no lift: the hook must never undo that deliberate unlock, even
    while another file's lift expires beside it. (Session start keeps its old behaviour for it.)"""
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    lock._remove_file(bed.path("Dawnguard.esm"), False)              # what the .cmd does: no lift, no record
    assert not _locked(bed, "Dawnguard.esm")
    for k in range(3):
        _hook(bed, T0 + k)
    assert not _locked(bed, "Dawnguard.esm")
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=T0).exit == 0
    for k in range(3):
        _hook(bed, T0 + lock.LIFT_SECONDS + 1 + k)
    assert _locked(bed, "Skyrim.esm") and not _locked(bed, "Dawnguard.esm")


# The state lock is an OS lock (state.locked -> backup._exclusive): only ANOTHER PROCESS can hold it against this one.
# (A thread cannot stand in for it: state._HELD makes a second locked() in the same process re-entrant.)
_HOLDER = ("import sys\nsys.path.insert(0, sys.argv[1])\nfrom skyguard import state\n"
           "with state.locked(sys.argv[2], wait=30):\n    open(sys.argv[3], 'w').close()\n    sys.stdin.read()\n")


def _hold_state_lock(folder, sf):
    """Another process holding the state lock (as a CLI writer mid read-modify-write would) until _release()."""
    flag = Path(folder) / ("held-%s" % uuid.uuid4().hex[:8])
    proc = subprocess.Popen([sys.executable, "-B", "-c", _HOLDER, str(REPO / "tools"), str(sf), str(flag)],
                            stdin=subprocess.PIPE)
    deadline = time.monotonic() + 60
    while not flag.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            pytest.fail("the state lock holder did not start (exit %r)" % proc.poll())
        time.sleep(0.02)
    return proc


def _release(proc):
    if proc.poll() is None:
        proc.stdin.close()
    proc.wait(timeout=60)


@needs_windows
def test_a_busy_state_file_skips_the_relock_without_blocking_the_hook(bed):
    lock = _lock()
    assert lock.lock_all(str(bed.root)).exit == 0
    assert lock.unlock(str(bed.root), ["Skyrim.esm"], SESSION, now=T0).exit == 0
    proc = _hold_state_lock(bed.root, _state_file(bed))                # another process holds the state lock
    try:
        t = time.monotonic()
        v = _hook(bed, T0 + lock.LIFT_SECONDS + 1)
        took = time.monotonic() - t
        assert took < 2.0, "the hook blocked %.2f s on a busy state lock" % took
        assert v.decision == "allow"
        assert not _locked(bed, "Skyrim.esm") and "state file is busy" in _audit_log(bed)
        assert [x for x in _lifts(bed) if x["kind"] == "lock"], "the skipped relock must leave its lift for a retry"
    finally:
        _release(proc)
    _hook(bed, T0 + lock.LIFT_SECONDS + 2)                            # released: the next call relocks
    assert _locked(bed, "Skyrim.esm")


_RMW = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom skyguard import state\n"
        "sf, n, tag, go = sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]\n"
        "import os\nwhile not os.path.exists(go):\n    time.sleep(0.005)\n"
        "for i in range(n):\n"
        "    with state.locked(sf, wait=30):\n"
        "        st = state.read(sf)\n"
        "        assert not st.rejected, st.reason\n"
        "        add = {'kind': 'guards', 'reason': '%s-%d' % (tag, i), 'session_id': tag, 'agent': 't',\n"
        "               'created': 1.0, 'expires_at': 2.0}\n"
        "        state.write(sf, {'v': 1, 'lifts': st.lifts + [add], 'persistent': {}})\n")


def test_the_state_lock_serialises_read_modify_writes_across_processes(tmp_path):
    """Two processes each append N lifts by read-modify-write under state.locked, released together: none is lost.
    Without the lock they interleave and overwrite each other's appends (or os.replace collides)."""
    from skyguard import state
    sf = tmp_path / ".skyguard" / "state.json"
    state.write(str(sf), {"v": 1, "lifts": [], "persistent": {}})
    go, n = tmp_path / "go", 40
    procs = [subprocess.Popen([sys.executable, "-B", "-c", _RMW, str(REPO / "tools"), str(sf), str(n), tag, str(go)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for tag in ("a", "b")]
    time.sleep(1.0)                                                    # both interpreters up and waiting
    go.write_text("", encoding="ascii")
    outs = [p.communicate(timeout=120) for p in procs]
    reasons = sorted(x["reason"] for x in state.read(str(sf)).lifts)
    want = sorted("%s-%d" % (t, i) for t in ("a", "b") for i in range(n))
    lost = len(set(want) - set(reasons))
    assert [p.returncode for p in procs] == [0, 0] and reasons == want, "lost %d of %d read-modify-writes; %s" % (
        lost, len(want), [o[1].strip().splitlines()[-1:] for o in outs])


def test_a_state_lock_holder_that_dies_releases_the_lock(tmp_path):
    """Replaces fix A's "breaks a stale lock": the kept lock is the OS's, so a killed holder (a hook killed at its
    timeout) leaves nothing to break. While it lives a writer gets StateBusy and the file is untouched; once it is
    killed -- no cleanup ran -- the next writer gets the lock at once. Re-entrant inside one read-modify-write."""
    from skyguard import state
    sf = tmp_path / ".skyguard" / "state.json"
    body = {"v": 1, "lifts": [], "persistent": {}}
    state.write(str(sf), body)
    before = sf.read_bytes()
    proc = _hold_state_lock(tmp_path, sf)
    try:
        with pytest.raises(state.StateBusy):
            state.write(str(sf), dict(body, persistent={"lock_disabled": {"since": 1.0, "by_session": "s"}}), wait=0.1)
        assert sf.read_bytes() == before
    finally:
        proc.kill()                                                    # dies holding it: no __exit__, no unlink
        proc.wait(timeout=60)
    after = dict(body, persistent={"lock_disabled": {"since": 2.0, "by_session": "s"}})
    state.write(str(sf), after, wait=2.0)
    assert state.read(str(sf)).lock_disabled == {"since": 2.0, "by_session": "s"}
    with state.locked(sf, 0.1):                                        # re-entrant inside one read-modify-write
        state.write(str(sf), body, wait=0.1)
    assert state.read(str(sf)).lock_disabled is None
