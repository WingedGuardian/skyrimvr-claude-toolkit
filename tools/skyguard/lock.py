"""Layer 2: the read-only lock on the vanilla masters and archives (Plan 6 Task 8; mechanism M-D, R1-R5, R11).

M-D = the read-only attribute PLUS a file-level, non-inherited deny ACE for the current user's SID with the
mask (WD, AD, WA, WEA). Denying write-attributes is what stops `-Force` from clearing the bit. Measured in
docs/superpowers/measurements/2026-10-04-plan6-ml2.md; what it does NOT stop (accepted by the user, written
into the README): an xEdit save renames the locked master into `zEdit Backups/` (the bytes survive) and a
Codex `workspace-write` sandbox cannot lift it (the user runs tools/skyguard-unlock.cmd).

Rules this module keeps:
  * OFF BY DEFAULT (`DEFAULT`, R3): nothing here runs unless `lock lock` is asked for.
  * The set is an EXPLICIT name list per edition (R1), never a glob, vetted with lstat: one link, a regular
    file, not a reparse point. Every skip is named with its reason.
  * The filesystem is the truth (R4): status re-reads attributes and ACLs; the manifest is only a record, and
    only BASENAMES from it are ever used, joined onto Data/, so a forged manifest cannot redirect an unlock.
  * Bit BEFORE the deny, and the deny REMOVED before the bit (measured: `attrib +R` under a WA deny prints
    "Access denied" and exits 0). Every action re-reads the result and exits per the X4 contract: 0 verified,
    1 partial or disagreeing, 2 refused or unreadable, 3 unsupported (R11: POSIX).
  * Never clears protection the toolkit did not set (R5): a file that was already read-only is tagged with the
    mask variant (WD, AD, WA), which the filesystem itself carries, so even a lost manifest cannot make an
    unlock clear that bit.
  * Applies in-process (ctypes): one process per file measured 1.3-10.7 s for the VR set, in-process 0.02 s.
  * Every mutating call is sandbox-asserted on the RESOLVED path when SKYGUARD_LOCK_SANDBOX is set;
    `SandboxViolation` derives from BaseException so nothing in between can swallow it.
  * Every lift goes through `state.write()` (kind `lock`, this session's id) BEFORE the unlock acts.

Known limit, not widened here: a forged `persistent.lock_disabled` buys persistence (owned by Tasks 10 and 12).
"""
from __future__ import annotations

import json
import os
import re
import stat as _stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import state as _state

DEFAULT = "off"            # layer2.default (R3): flipped to "on" only after the Final Manual Batch
MECHANISM = "M-D"
ENV_SANDBOX = "SKYGUARD_LOCK_SANDBOX"
ENV_SESSION = "SKYGUARD_SESSION_ID"
ENV_AGENT = "SKYGUARD_AGENT"
USER_SESSION = "user-tty"  # the lift id of a person typing at a terminal: no agent session can ever match it
LIFT_SECONDS = 3600.0      # a lock lift lasts an hour (state caps every lift at 8 h)

# Access-mask bits. The whitelist is exactly these four (L4, L5): never W/M/F, SYNCHRONIZE, WDAC, WO, D or DE.
_WD, _AD, _WEA, _WA = 0x2, 0x4, 0x10, 0x100
MASK_FULL = _WD | _AD | _WEA | _WA        # (WD,AD,WA,WEA)
MASK_PRE = _WD | _AD | _WA                # (WD,AD,WA): the tag for a file that was already read-only
_LABEL = {MASK_FULL: "full", MASK_PRE: "pre"}
_REPARSE = 0x400

_MASTERS = ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm"]
_BSA_BASE = ["Animations", "Interface", "Meshes0", "Meshes1", "Misc", "Patch", "Shaders", "Sounds"] \
    + ["Textures%d" % i for i in range(9)]
_LANGS = ["en", "fr", "de", "it", "es", "ja", "pl", "ru", "cs"]    # INFERRED: the official voice languages
_CCC_LINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,120}\.(?:esm|esl|esp)\Z", re.I)
_EDIT_BACKUP_DIRS = ("zEdit Backups", "xEdit Backups", "SSEEdit Backups", "TES5Edit Backups", "Edit Backups")


class SandboxViolation(BaseException):
    """A mutating call aimed outside $SKYGUARD_LOCK_SANDBOX. Never an Exception: nothing may swallow it."""


class Refused(Exception):
    """The action is refused (exit 2); the message is the user-facing line."""


def supported() -> bool:
    return os.name == "nt"


# ---- sandbox ------------------------------------------------------------------------------------------------

def _resolve(path) -> str:
    real = os.path.realpath(os.fspath(path))
    for pre in ("\\\\?\\UNC\\", "\\\\?\\", "//?/"):
        if real.startswith(pre):
            real = ("\\\\" + real[len(pre):]) if "UNC" in pre else real[len(pre):]
            break
    return os.path.normcase(os.path.normpath(real))


def _assert_sandbox(path) -> str:
    """No-op when SKYGUARD_LOCK_SANDBOX is unset (production). Otherwise the RESOLVED path must be strictly
    inside the resolved sandbox: a junction, `..` or a hardlinked path outside it is a violation."""
    raw = os.environ.get(ENV_SANDBOX, "")
    if not raw.strip():
        return str(path)
    root = _resolve(raw)
    if os.path.dirname(root) == root:
        raise SandboxViolation("sandbox root is a drive root: " + root)
    target = _resolve(path)
    if target == root:
        raise SandboxViolation("target is the sandbox root itself: " + target)
    try:
        common = os.path.commonpath([root, target])
    except ValueError:
        raise SandboxViolation("target on another drive: " + target)
    if common != root:
        raise SandboxViolation("target resolves outside the sandbox: %s -> %s" % (path, target))
    return target


# ---- targets (R1) -------------------------------------------------------------------------------------------

@dataclass
class Targets:
    edition: str | None
    files: list = field(default_factory=list)       # Paths: exist, single-link, regular, not a reparse point
    skips: list = field(default_factory=list)       # (name, reason)
    problem: str = ""


def detect_edition(data_dir):
    """'vr' (SkyrimVR.exe), 'se' (SkyrimSE.exe), 'ae' (SkyrimSE.exe + Skyrim.ccc), or None when neither or both
    executables are beside Data/ (the edition is not guessed)."""
    root = Path(data_dir).parent
    vr, se = (root / "SkyrimVR.exe").is_file(), (root / "SkyrimSE.exe").is_file()
    if vr == se:
        return None
    if vr:
        return "vr"
    return "ae" if (root / "Skyrim.ccc").is_file() else "se"


def _read_ccc(data_dir):
    """Plugin names from the game's own manifest, each a bare filename (a path, a drive or a '..' is dropped)."""
    try:
        with open(str(Path(data_dir).parent / "Skyrim.ccc"), "rb") as fh:
            text = fh.read(256 * 1024).decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if _CCC_LINE.match(line) and line not in out:
            out.append(line)
        if len(out) >= 1000:
            break
    return out


def _names(edition, ccc=()):
    """The explicit per-edition name list (R1). Never a glob."""
    masters = list(_MASTERS)
    archives = ["Skyrim - %s.bsa" % b for b in _BSA_BASE] + ["Skyrim - Voices_%s0.bsa" % lang for lang in _LANGS]
    if edition == "vr":
        return masters + ["SkyrimVR.esm", "Skyrim_VR - Main.bsa"] + archives
    names = masters + archives + ["_ResourcePack.esl", "_ResourcePack.bsa"]
    for plugin in ccc:
        names.append(plugin)
        names.append(plugin.rsplit(".", 1)[0] + ".bsa")
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _is_reparse(st) -> bool:
    return _stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _links(path, st):
    """The number of hard links, read from an open FILE HANDLE (GetFileInformationByHandle) on Windows: lstat's
    st_nlink is not trustworthy there (an ACE such as a user's deny on DELETE made it report a wrong count and a
    plain file read as multi-link). None when it cannot be read; elsewhere lstat's own count."""
    if not supported():
        return st.st_nlink
    try:
        return _win().link_count(os.path.normpath(os.fspath(path)))
    except OSError:
        return None


def _skip_reason(st, path):
    """Why this lstat result is not lockable, or None."""
    if _is_reparse(st):
        return "reparse point"
    if not _stat.S_ISREG(st.st_mode):
        return "not a regular file"
    n = _links(path, st)
    if n is None:
        return "cannot be opened (a deny entry or another lock is on it)"
    if n != 1:
        return "multi-link (mod manager copy)"
    return None


def targets(data_dir, edition=None) -> Targets:
    data_dir = Path(data_dir)
    edition = edition or detect_edition(data_dir)
    if edition is None:
        return Targets(None, problem="cannot tell the edition: need exactly one of SkyrimVR.exe / SkyrimSE.exe "
                                     "beside Data/")
    ccc = _read_ccc(data_dir) if edition in ("se", "ae") else []
    names = _names(edition, ccc)
    t = Targets(edition)
    for name in names:
        p = data_dir / name
        try:
            st = os.lstat(str(p))
        except FileNotFoundError:
            t.skips.append((name, "missing"))
            continue
        except OSError as e:
            t.skips.append((name, "unreadable (%s)" % e.__class__.__name__))
            continue
        reason = _skip_reason(st, p)
        if reason:
            t.skips.append((name, reason))
        else:
            t.files.append(p)
    return t


# ---- Windows primitives (in-process; ctypes, stdlib only) ---------------------------------------------------

class _Win:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        ct = ctypes
        self.ct = ct
        self.adv = adv = ct.WinDLL("advapi32", use_last_error=True)
        self.k32 = k32 = ct.WinDLL("kernel32", use_last_error=True)

        class TRUSTEE_W(ct.Structure):
            _fields_ = [("pMultipleTrustee", ct.c_void_p), ("MultipleTrusteeOperation", ct.c_int),
                        ("TrusteeForm", ct.c_int), ("TrusteeType", ct.c_int), ("ptstrName", ct.c_void_p)]

        class EXPLICIT_ACCESS_W(ct.Structure):
            _fields_ = [("grfAccessPermissions", wt.DWORD), ("grfAccessMode", ct.c_int),
                        ("grfInheritance", wt.DWORD), ("Trustee", TRUSTEE_W)]

        class ACE_HEADER(ct.Structure):
            _fields_ = [("AceType", ct.c_ubyte), ("AceFlags", ct.c_ubyte), ("AceSize", wt.WORD)]

        class ACL_SIZE_INFORMATION(ct.Structure):
            _fields_ = [("AceCount", wt.DWORD), ("AclBytesInUse", wt.DWORD), ("AclBytesFree", wt.DWORD)]

        self.EXPLICIT_ACCESS_W, self.ACE_HEADER, self.ACL_SIZE_INFORMATION = (
            EXPLICIT_ACCESS_W, ACE_HEADER, ACL_SIZE_INFORMATION)
        vp = ct.c_void_p
        adv.GetNamedSecurityInfoW.argtypes = [wt.LPCWSTR, ct.c_int, wt.DWORD, vp, vp, ct.POINTER(vp), vp, ct.POINTER(vp)]
        adv.SetNamedSecurityInfoW.argtypes = [wt.LPWSTR, ct.c_int, wt.DWORD, vp, vp, vp, vp]
        adv.SetEntriesInAclW.argtypes = [wt.ULONG, ct.POINTER(EXPLICIT_ACCESS_W), vp, ct.POINTER(vp)]
        adv.GetAclInformation.argtypes = [vp, vp, wt.DWORD, ct.c_int]
        adv.GetAce.argtypes = [vp, wt.DWORD, ct.POINTER(vp)]
        adv.DeleteAce.argtypes = [vp, wt.DWORD]
        adv.EqualSid.argtypes = [vp, vp]
        adv.ConvertSidToStringSidW.argtypes = [vp, ct.POINTER(wt.LPWSTR)]
        adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)]
        adv.GetTokenInformation.argtypes = [wt.HANDLE, ct.c_int, vp, wt.DWORD, ct.POINTER(wt.DWORD)]
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.LocalFree.argtypes = [vp]
        k32.GetFileAttributesW.argtypes = [wt.LPCWSTR]
        k32.GetFileAttributesW.restype = wt.DWORD
        k32.SetFileAttributesW.argtypes = [wt.LPCWSTR, wt.DWORD]

        class BY_HANDLE_FILE_INFORMATION(ct.Structure):
            _fields_ = [("dwFileAttributes", wt.DWORD), ("ftCreationTime", wt.FILETIME),
                        ("ftLastAccessTime", wt.FILETIME), ("ftLastWriteTime", wt.FILETIME),
                        ("dwVolumeSerialNumber", wt.DWORD), ("nFileSizeHigh", wt.DWORD),
                        ("nFileSizeLow", wt.DWORD), ("nNumberOfLinks", wt.DWORD),
                        ("nFileIndexHigh", wt.DWORD), ("nFileIndexLow", wt.DWORD)]

        self.BY_HANDLE_FILE_INFORMATION = BY_HANDLE_FILE_INFORMATION
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, vp, wt.DWORD, wt.DWORD, vp]
        k32.CreateFileW.restype = vp
        k32.GetFileInformationByHandle.argtypes = [vp, ct.POINTER(BY_HANDLE_FILE_INFORMATION)]
        k32.CloseHandle.argtypes = [vp]
        self._sid_buf = None
        self._sid_addr = None
        self.sid_str = ""
        self._load_sid()

    def _load_sid(self):
        """The current user's SID, read in-process (a spawned `whoami` crashed inside Codex's sandbox)."""
        ct, wt = self.ct, __import__("ctypes.wintypes", fromlist=["x"])
        tok = wt.HANDLE()
        if not self.adv.OpenProcessToken(self.k32.GetCurrentProcess(), 0x8, ct.byref(tok)):
            raise ct.WinError(ct.get_last_error())
        try:
            need = wt.DWORD()
            self.adv.GetTokenInformation(tok, 1, None, 0, ct.byref(need))
            buf = ct.create_string_buffer(need.value)
            if not self.adv.GetTokenInformation(tok, 1, buf, need, ct.byref(need)):
                raise ct.WinError(ct.get_last_error())
        finally:
            self.k32.CloseHandle(tok)
        self._sid_buf = buf                                   # keep alive: the SID lives inside it
        self._sid_addr = ct.cast(buf, ct.POINTER(ct.c_void_p))[0]
        self.sid_str = self._sid_string(self._sid_addr)

    def _sid_string(self, addr) -> str:
        ct, wt = self.ct, __import__("ctypes.wintypes", fromlist=["x"])
        out = wt.LPWSTR()
        if not self.adv.ConvertSidToStringSidW(ct.c_void_p(addr), ct.byref(out)):
            return "?"
        try:
            return out.value
        finally:
            self.k32.LocalFree(ct.cast(out, ct.c_void_p))

    def link_count(self, path) -> int:
        """nNumberOfLinks from a handle opened with FILE_READ_ATTRIBUTES only (needs no right a deny ACE on the
        file's data, delete or write could have taken away). Raises OSError when the file cannot be opened."""
        ct = self.ct
        h = self.k32.CreateFileW(path, 0x80, 0x7, None, 3, 0x02000000, None)   # share all, OPEN_EXISTING
        if h is None or h == ct.c_void_p(-1).value:
            raise ct.WinError(ct.get_last_error())
        try:
            info = self.BY_HANDLE_FILE_INFORMATION()
            if not self.k32.GetFileInformationByHandle(h, ct.byref(info)):
                raise ct.WinError(ct.get_last_error())
            return info.nNumberOfLinks
        finally:
            self.k32.CloseHandle(h)

    def attrs(self, path) -> int:
        a = self.k32.GetFileAttributesW(path)
        if a == 0xFFFFFFFF:
            raise self.ct.WinError(self.ct.get_last_error())
        return a

    def set_readonly(self, path, on: bool):
        a = self.attrs(path)
        new = (a | 0x1) if on else (a & ~0x1)
        if new != a and not self.k32.SetFileAttributesW(path, new or 0x80):
            raise self.ct.WinError(self.ct.get_last_error())

    def _get_dacl(self, path):
        ct = self.ct
        dacl, sd = ct.c_void_p(), ct.c_void_p()
        rc = self.adv.GetNamedSecurityInfoW(path, 1, 0x4, None, None, ct.byref(dacl), None, ct.byref(sd))
        if rc:
            raise ct.WinError(rc)
        return dacl, sd

    def _set_dacl(self, path, dacl):
        rc = self.adv.SetNamedSecurityInfoW(path, 1, 0x4 | 0x20000000, None, None, dacl, None)
        if rc:
            raise self.ct.WinError(rc)

    def aces(self, path):
        """The explicit (non-inherited) ACEs: [{type, mask, sid, mine}]. type: 0 allow, 1 deny, other."""
        ct = self.ct
        dacl, sd = self._get_dacl(path)
        out = []
        try:
            if not dacl.value:
                return out
            info = self.ACL_SIZE_INFORMATION()
            if not self.adv.GetAclInformation(dacl, ct.byref(info), ct.sizeof(info), 2):
                raise ct.WinError(ct.get_last_error())
            for i in range(info.AceCount):
                pace = ct.c_void_p()
                if not self.adv.GetAce(dacl, i, ct.byref(pace)):
                    raise ct.WinError(ct.get_last_error())
                hdr = ct.cast(pace, ct.POINTER(self.ACE_HEADER)).contents
                if hdr.AceFlags & 0x10:           # INHERITED_ACE
                    continue
                if hdr.AceType in (0, 1):
                    mask = ct.cast(ct.c_void_p(pace.value + 4), ct.POINTER(ct.c_uint32)).contents.value
                    sid = pace.value + 8
                    out.append({"type": hdr.AceType, "mask": mask, "sid": self._sid_string(sid),
                                "mine": bool(self.adv.EqualSid(ct.c_void_p(sid), ct.c_void_p(self._sid_addr)))})
                else:
                    out.append({"type": hdr.AceType, "mask": 0, "sid": "", "mine": False})
        finally:
            self.k32.LocalFree(sd)
        return out

    def add_deny(self, path, mask):
        if mask not in _LABEL:
            raise ValueError("mask outside the whitelist")
        ct = self.ct
        ea = self.EXPLICIT_ACCESS_W()
        ea.grfAccessPermissions, ea.grfAccessMode, ea.grfInheritance = mask, 3, 0   # DENY_ACCESS, no inheritance
        ea.Trustee.TrusteeForm, ea.Trustee.TrusteeType, ea.Trustee.ptstrName = 0, 1, self._sid_addr
        dacl, sd = self._get_dacl(path)
        new = ct.c_void_p()
        try:
            if not dacl.value:
                raise OSError("file has no DACL; refusing to build one")
            rc = self.adv.SetEntriesInAclW(1, ct.byref(ea), dacl, ct.byref(new))
            if rc:
                raise ct.WinError(rc)
            self._set_dacl(path, new)
        finally:
            if new.value:
                self.k32.LocalFree(new)
            self.k32.LocalFree(sd)

    def remove_ours(self, path, masks):
        """Delete this user's explicit deny ACEs whose mask is one of `masks`. Returns how many."""
        ct = self.ct
        dacl, sd = self._get_dacl(path)
        removed = 0
        try:
            if not dacl.value:
                return 0
            info = self.ACL_SIZE_INFORMATION()
            if not self.adv.GetAclInformation(dacl, ct.byref(info), ct.sizeof(info), 2):
                raise ct.WinError(ct.get_last_error())
            for i in reversed(range(info.AceCount)):
                pace = ct.c_void_p()
                if not self.adv.GetAce(dacl, i, ct.byref(pace)):
                    raise ct.WinError(ct.get_last_error())
                hdr = ct.cast(pace, ct.POINTER(self.ACE_HEADER)).contents
                if hdr.AceType != 1 or hdr.AceFlags & 0x10:
                    continue
                mask = ct.cast(ct.c_void_p(pace.value + 4), ct.POINTER(ct.c_uint32)).contents.value
                if mask not in masks:
                    continue
                if not self.adv.EqualSid(ct.c_void_p(pace.value + 8), ct.c_void_p(self._sid_addr)):
                    continue
                if not self.adv.DeleteAce(dacl, i):
                    raise ct.WinError(ct.get_last_error())
                removed += 1
            if removed:
                self._set_dacl(path, dacl)
        finally:
            self.k32.LocalFree(sd)
        return removed


_WIN = None


def _win() -> _Win:
    global _WIN
    if _WIN is None:
        _WIN = _Win()
    return _WIN


@dataclass
class FileState:
    readonly: bool
    ours: str | None            # "full" | "pre" | None: this user's explicit deny ACE of exactly our mask
    aces: list


def read_state(path) -> FileState:
    """Re-read the filesystem (never the manifest): the bit and our ACE."""
    p = os.path.normpath(os.fspath(path))
    w = _win()
    aces = w.aces(p)
    ours = None
    for a in aces:
        if a["type"] == 1 and a["mine"] and a["mask"] in _LABEL:
            ours = _LABEL[a["mask"]]
            if ours == "full":
                break
    return FileState(bool(w.attrs(p) & 0x1), ours, aces)


def _assert_plain(path, single: bool):
    """The choke every mutation passes: sandbox, then lstat. A reparse point is never touched (the ACL call
    follows it to the real file); `single` additionally refuses a file with more than one link, since an
    attribute or an ACL belongs to the FILE OBJECT and would land on the manager's staging copy."""
    _assert_sandbox(path)
    st = os.lstat(os.fspath(path))
    if _is_reparse(st) or not _stat.S_ISREG(st.st_mode):
        raise ValueError("not a plain file: " + os.path.basename(str(path)))
    if single and _links(path, st) != 1:
        raise ValueError("multi-link (mod manager copy): " + os.path.basename(str(path)))


def _apply_file(path, mask, pre_ro):
    """Bit first (unless the user already set it), then the deny. A deny that fails rolls the bit back."""
    p = os.path.normpath(os.fspath(path))
    w = _win()
    if not pre_ro:
        w.set_readonly(p, True)
    try:
        w.add_deny(p, mask)
    except BaseException:
        if not pre_ro:
            try:
                w.set_readonly(p, False)
            except OSError:
                pass
        raise


def apply_one(path) -> str:
    """Lock one file; returns the label of the mark it now carries ("full" or "pre"). Raises SandboxViolation
    or ValueError (not a plain single-link file) before touching anything."""
    _assert_plain(path, True)
    st = read_state(path)
    if st.ours is not None:
        return st.ours                      # already locked: idempotent
    for a in st.aces:
        # The user's OWN deny entry for this account would be merged with ours by the ACL API (and unlock could
        # then not tell them apart): refuse, never touch it.
        if a["type"] == 1 and a["mine"] and a["mask"] not in _LABEL:
            raise ValueError("has its own deny entry for this account: " + os.path.basename(str(path)))
    pre_ro = st.readonly
    mask = MASK_PRE if pre_ro else MASK_FULL
    _apply_file(path, mask, pre_ro)
    return _LABEL[mask]


def _verify_locked(path, expected) -> bool:
    st = read_state(path)
    return st.readonly and st.ours == expected


def _keep_bit(st: FileState, preexisting: bool) -> bool:
    keep_bit = preexisting or st.ours == "pre"
    return keep_bit


def _remove_file(path, keep_bit):
    """Deny ACE first (with WA denied `attrib -R` is itself refused), then the bit, unless it was the user's."""
    p = os.path.normpath(os.fspath(path))
    w = _win()
    w.remove_ours(p, (MASK_FULL, MASK_PRE))
    if not keep_bit:
        w.set_readonly(p, False)


def _undo(path, before: FileState) -> bool:
    """Put back exactly what a failed lock changed (our ACE; the bit only if WE set it) and verify it."""
    try:
        _remove_file(path, before.readonly)
        st = read_state(path)
        return st.ours is None and st.readonly == before.readonly
    except OSError:
        return False


def _verify_unlocked(path, keep_bit) -> bool:
    st = read_state(path)
    return st.ours is None and (keep_bit or not st.readonly)


# ---- files under .skyguard ------------------------------------------------------------------------------------

def _skyguard_dir(root) -> Path:
    """<root>/.skyguard, created if needed; refused if it is a reparse point (writes would land elsewhere)."""
    d = Path(root) / ".skyguard"
    try:
        st = os.lstat(str(d))
    except FileNotFoundError:
        _assert_sandbox(d)
        d.mkdir(parents=True, exist_ok=True)
        return d
    if _is_reparse(st) or not _stat.S_ISDIR(st.st_mode):
        raise Refused("the .skyguard folder is a link or not a folder; refusing to write through it")
    return d


def _atomic_write(path: Path, text: str):
    _assert_sandbox(path)
    if os.path.lexists(str(path)) and _is_reparse(os.lstat(str(path))):
        raise Refused(path.name + " is a link; refusing to write through it")
    fd, tmp = tempfile.mkstemp(prefix=".w-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_manifest(root):
    """-> (files: {name: record}, status) with status ok | absent | unreadable. Only basenames survive."""
    p = Path(root) / ".skyguard" / "lock.json"
    try:
        st = os.lstat(str(p))
    except OSError:
        return {}, "absent"
    try:
        if _is_reparse(st) or not _stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
            return {}, "unreadable"
        data = json.loads(p.read_text(encoding="utf-8"))
        files = data["files"]
        if not isinstance(files, dict):
            return {}, "unreadable"
        out = {}
        for name, rec in files.items():
            if isinstance(name, str) and name == os.path.basename(name) and name and isinstance(rec, dict):
                out[name] = rec
        return out, "ok"
    except (OSError, ValueError, KeyError, TypeError):
        return {}, "unreadable"


def _write_manifest(root, edition, files: dict):
    d = _skyguard_dir(root)
    data = {"v": 1, "mechanism": MECHANISM, "edition": edition, "updated": time.time(), "files": files}
    _atomic_write(d / "lock.json", json.dumps(data, indent=1, sort_keys=True) + "\n")
    data_dir = Path(root) / "Data"
    lines = [os.path.normpath(str(data_dir / n)) for n in sorted(files)]
    _atomic_write(d / "locked.txt", "".join(x + "\n" for x in lines))


# ---- results ---------------------------------------------------------------------------------------------------

@dataclass
class Result:
    exit: int
    lines: list
    data: dict = field(default_factory=dict)


def _clip(s: str, n: int = 190) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _res(code, *lines, data=None):
    return Result(code, [_clip(x) for x in lines if x][:2], data or {})


def _names_list(names, limit=4) -> str:
    names = list(names)
    s = ", ".join(names[:limit])
    return s + (" and %d more" % (len(names) - limit) if len(names) > limit else "")


def _guard(root):
    """Common preconditions -> (data_dir, Targets) or a Result to return."""
    if not supported():
        return _res(3, "Layer 2 lock: unsupported on this platform (Windows only in v4.0.0).")
    data_dir = Path(root) / "Data"
    if not data_dir.is_dir():
        return _res(2, "No Data/ folder under the install root; nothing to lock.")
    t = targets(data_dir)
    if t.problem:
        return _res(2, t.problem)
    return data_dir, t


def _prior(st: FileState, path) -> dict:
    return {"prior_attrs": _win().attrs(os.path.normpath(str(path))),
            "prior_aces": [[a["type"], a["mask"], a["sid"]] for a in st.aces]}


# ---- lock ------------------------------------------------------------------------------------------------------

def lock_all(root) -> Result:
    g = _guard(root)
    if isinstance(g, Result):
        return g
    data_dir, t = g
    st0 = _state.read(str(Path(root) / ".skyguard" / "state.json"))
    if st0.lock_disabled:
        return _res(2, "The lock is disabled (persistent, since %s)." % time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(st0.lock_disabled["since"])), "Run `lock enable` to turn it back on.")
    if not t.files:
        return _res(2, "No vanilla master or archive found in Data/ to lock.")
    old, _status = _read_manifest(root)
    record, failed = {}, []
    # Records of files locked earlier that are no longer in the target set (e.g. became multi-link) are carried.
    carry = {}
    in_set = {p.name for p in t.files}
    for name, rec in old.items():
        if name not in in_set:
            try:
                if read_state(data_dir / name).ours is not None:
                    carry[name] = rec
            except OSError:
                pass
    try:
        for p in t.files:
            name = p.name
            before = None
            try:
                before = read_state(p)
                info = _prior(before, p)
                label = apply_one(p)
                ok = _verify_locked(p, label)
            except ValueError as e:
                failed.append((name, str(e).split(":")[0]))
                continue
            except OSError as e:
                failed.append((name, "error " + e.__class__.__name__))
                continue
            if not ok:
                # Undo exactly what THIS call changed, and say whether the undo itself verified.
                undone = before.ours is None and _undo(p, before)
                failed.append((name, "did not verify on re-read" + ("; undone" if undone else "; UNDO FAILED")))
                continue
            prev = old.get(name, {})
            pre = label == "pre" or bool(prev.get("preexisting_ro"))
            if before.ours is not None:              # already ours: keep the record of what it was BEFORE us
                info = {k: prev[k] for k in ("prior_attrs", "prior_aces") if k in prev} or info
            record[name] = dict(info, preexisting_ro=pre, locked_at=prev.get("locked_at", time.time()))
            # Written per file, atomically: a crash mid-lock leaves a list that matches what is locked.
            _write_manifest(root, t.edition, dict(carry, **record))  # per file
        _write_manifest(root, t.edition, dict(carry, **record))
    except Refused as e:
        return _res(2, str(e))
    record.update(carry)
    skipped = [(n, r) for n, r in t.skips if r != "missing"]
    partial = failed + skipped
    if partial:
        names = ["%s (%s)" % (n, r) for n, r in partial]
        return _res(1, "Locked %d of %d vanilla files; %d not locked." % (len(record), len(t.files) + len(skipped),
                                                                          len(partial)),
                    "Not locked: " + _names_list(names, 3), data={"locked": sorted(record), "not_locked": partial})
    return _res(0, "Locked %d vanilla files (M-D, verified by re-read)." % len(record),
                "Unlock one with `lock unlock <file>`; the Python-free route is tools/skyguard-unlock.cmd.",
                data={"locked": sorted(record)})


# ---- unlock ----------------------------------------------------------------------------------------------------

MAX_LIFTS = 100


def _live_lifts(lifts, now):
    """The lifts worth keeping: not yet expired, and at most MAX_LIFTS (the newest). Without this every unlock
    appends a lift for good and state.json hits state.MAX_BYTES after ~300, after which EVERY unlock fails."""
    live = [x for x in lifts if x["expires_at"] > now]
    live.sort(key=lambda x: x["created"])
    return live[-MAX_LIFTS:]


def _interactive() -> bool:
    """True when a person is at a terminal: stdin AND stdout are consoles (an agent's shell has pipes)."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


RESERVED_LINES = ("The session id `user-tty` is reserved for a person typing at a terminal; it cannot be passed or exported.",
                  "Use the real session id, or run the command yourself in a terminal; nothing was changed.")


def reserved_session(s) -> bool:
    """`user-tty` is minted only by main() from a console (stdin and stdout both ttys). Passing it as --session or
    SKYGUARD_SESSION_ID would let a piped agent shell write a lift that looks like the user's own."""
    return isinstance(s, str) and s.strip().lower() == USER_SESSION


def _valid_session(s):
    from . import health
    return isinstance(s, str) and bool(health._SESSION_ID.match(s))


def _travelled(data_dir: Path):
    """Locked files that moved: xEdit's save renames the locked master into `<editor> Backups/<name>.<stamp>.bak`
    and Vortex's collision handling into `<name>.vortex_backup`; the mark travels with the renamed file."""
    cands = []
    for d in _EDIT_BACKUP_DIRS:
        sub = data_dir / d
        if sub.is_dir() and not _is_reparse(os.lstat(str(sub))):
            cands += [sub / n for n in sorted(os.listdir(str(sub))) if n.lower().endswith(".bak")]
    try:
        cands += [data_dir / n for n in sorted(os.listdir(str(data_dir))) if n.lower().endswith(".vortex_backup")]
    except OSError:
        pass
    out = []
    for p in cands[:2000]:
        try:
            st = os.lstat(str(p))
            if _is_reparse(st) or not _stat.S_ISREG(st.st_mode):
                continue
            if read_state(p).ours is not None:
                out.append(p)
        except OSError:
            continue
    return out


def _resolve_names(root, data_dir, t, args, manifest):
    """The files an `unlock <arg>` may name: this edition's derived names plus the manifest's, as BASENAMES
    inside Data/. A path is accepted only if it resolves to Data/<one of those>."""
    known = {n.lower(): n for n in _names(t.edition, _read_ccc(data_dir) if t.edition in ("se", "ae") else [])}
    known.update({n.lower(): n for n in manifest})
    out = []
    for a in args:
        base = os.path.basename(a.replace("\\", "/"))
        if base != a:
            if _resolve(Path(a) if os.path.isabs(a) else Path(root) / a) != _resolve(data_dir / base):
                raise Refused("%s is not inside Data/; only the toolkit's vanilla files can be unlocked." % base)
        if base.lower() not in known:
            raise Refused("%s is not a file the lock covers." % base)
        out.append(known[base.lower()])
    return out


def unlock(root, names, session, now=None) -> Result:
    g = _guard(root)
    if isinstance(g, Result):
        return g
    data_dir, t = g
    if not _valid_session(session):
        return _res(2, "No session id: pass --session <id> or set SKYGUARD_SESSION_ID so the lift is recorded "
                       "(at a terminal it is automatic).", "Without one, run tools/skyguard-unlock.cmd.")
    manifest, mstatus = _read_manifest(root)
    try:
        chosen = _resolve_names(root, data_dir, t, names, manifest) if names else None
    except Refused as e:
        return _res(2, str(e))
    if chosen is None:
        universe = {n.lower(): n for n in _names(t.edition, _read_ccc(data_dir) if t.edition in ("se", "ae") else [])}
        universe.update({n.lower(): n for n in manifest})
        chosen = [n for n in universe.values() if os.path.lexists(str(data_dir / n))]
    now = time.time() if now is None else now
    try:
        sf = _skyguard_dir(root) / "state.json"
        cur = _state.read(str(sf))
        lift = {"kind": "lock", "reason": "lock unlock " + (_names_list(chosen, 3) if names else "all"),
                "session_id": session,
                "agent": "user" if session == USER_SESSION else (os.environ.get(ENV_AGENT, "cli") or "cli"),
                "created": now, "expires_at": now + LIFT_SECONDS}
        if names:
            lift["files"] = list(chosen)
        persistent = {"lock_disabled": cur.lock_disabled} if cur.lock_disabled else {}
        _assert_sandbox(sf)
        lifts = _live_lifts(list(cur.lifts) + [lift], now)
        _state.write(str(sf), {"v": 1, "lifts": lifts, "persistent": persistent})
    except Refused as e:
        return _res(2, str(e))
    except (ValueError, OSError) as e:
        return _res(2, "Could not record the lift (%s); nothing was unlocked." % e.__class__.__name__)
    done, failed, kept, settled = [], [], [], set()
    todo = [(data_dir / n, n) for n in chosen]
    if not names:
        todo += [(p, p.name) for p in _travelled(data_dir)]
    for p, name in todo:
        try:
            _assert_plain(p, False)
            st = read_state(p)
            if st.ours is None:
                if st.readonly and names:               # asked for by name: say why it stays
                    failed.append((name, "read-only, not set by the toolkit; left"))
                else:
                    settled.add(name)                   # not locked by us: nothing to undo
                continue
            keep = _keep_bit(st, bool(manifest.get(name, {}).get("preexisting_ro")))
            _remove_file(p, keep)
            if _verify_unlocked(p, keep):
                done.append(name)
                if keep:
                    kept.append(name)
            else:
                failed.append((name, "did not verify on re-read"))
        except ValueError as e:
            failed.append((name, str(e).split(":")[0]))
        except OSError as e:
            failed.append((name, "error " + e.__class__.__name__))
    remaining = {n: r for n, r in manifest.items() if n not in done and n not in settled}
    try:
        _write_manifest(root, t.edition, remaining)
    except Refused as e:
        return _res(2, str(e))
    extra = "Already read-only before the lock, so still read-only: " + _names_list(kept, 3) + "." if kept else ""
    if failed:
        return _res(1, "Unlocked %d files; %d not unlocked." % (len(done), len(failed)),
                    "Not unlocked: " + _names_list(["%s (%s)" % f for f in failed], 3),
                    data={"unlocked": done, "failed": failed})
    return _res(0, "Unlocked %d files; the lift is recorded for this session and expires in %d min."
                % (len(done), int(LIFT_SECONDS // 60)), extra, data={"unlocked": done})


# ---- status ----------------------------------------------------------------------------------------------------

def status(root) -> Result:
    g = _guard(root)
    if isinstance(g, Result):
        return g
    data_dir, t = g
    manifest, mstatus = _read_manifest(root)
    st0 = _state.read(str(Path(root) / ".skyguard" / "state.json"))
    names = [p.name for p in t.files] + [n for n in manifest if n not in {p.name for p in t.files}]
    rows, drift, orphan_locked = [], [], []
    for n in names:
        p = data_dir / n
        try:
            osst = os.lstat(str(p))
            fs = read_state(p)
        except OSError:
            if n in manifest:
                rows.append({"name": n, "state": "missing", "size": 0, "preexisting_ro": False})
                drift.append(n)
            continue
        if fs.ours is not None and fs.readonly:
            state = "locked"
        elif fs.ours is not None:
            state = "drift"
        elif fs.readonly:
            state = "read-only (not ours)"
        else:
            state = "unlocked"
        rows.append({"name": n, "state": state, "size": osst.st_size,
                     "preexisting_ro": fs.ours == "pre" or bool(manifest.get(n, {}).get("preexisting_ro"))})
        if n in manifest and state != "locked":
            drift.append(n)
        if n not in manifest and fs.ours is not None:
            orphan_locked.append(n)
    travelled = [os.path.relpath(str(p), str(data_dir)) for p in _travelled(data_dir)]
    locked_n = sum(1 for r in rows if r["state"] == "locked")
    data = {"edition": t.edition, "mechanism": MECHANISM, "default": DEFAULT, "supported": True,
            "disabled": bool(st0.lock_disabled), "manifest": mstatus, "files": rows,
            "skipped": [{"name": n, "reason": r} for n, r in t.skips if r != "missing"],
            "missing": sum(1 for _n, r in t.skips if r == "missing"),
            "not_in_manifest": orphan_locked, "travelled": travelled, "drift": drift}
    head = "Layer 2 lock (%s, default %s): " % (MECHANISM, DEFAULT)
    if locked_n == 0 and not manifest and not orphan_locked:
        head += "not locked."
    else:
        head += "%d of %d vanilla files locked." % (locked_n, len(rows))
    if st0.lock_disabled:
        head += " Persistently DISABLED."
    problems = []
    if drift:
        problems.append("Not locked but recorded: " + _names_list(drift, 3) + ".")
    if orphan_locked:
        problems.append("Locked but not in the manifest: " + _names_list(orphan_locked, 3) + ".")
    if travelled:
        problems.append("Locked files that moved into backups: " + _names_list(travelled, 2) + ".")
    if not problems and data["skipped"]:
        problems.append("Skipped: " + _names_list(["%s (%s)" % (s["name"], s["reason"]) for s in data["skipped"]], 2) + ".")
    code = 1 if (drift or orphan_locked or travelled) else 0
    return _res(code, head, " ".join(problems), data=data)


# ---- disable / enable -----------------------------------------------------------------------------------------

def _write_persistent(root, session, disabled: bool):
    sf = _skyguard_dir(root) / "state.json"
    cur = _state.read(str(sf))
    persistent = {"lock_disabled": {"since": time.time(), "by_session": session or "manual"}} if disabled else {}
    _assert_sandbox(sf)
    _state.write(str(sf), {"v": 1, "lifts": _live_lifts(list(cur.lifts), time.time()), "persistent": persistent})


def disable(root, session=None) -> Result:
    if not supported():
        return _res(3, "Layer 2 lock: unsupported on this platform (Windows only in v4.0.0).")
    try:
        _write_persistent(root, session, True)
    except Refused as e:
        return _res(2, str(e))
    except (ValueError, OSError) as e:
        return _res(2, "Could not record the disable (%s)." % e.__class__.__name__)
    return _res(0, "The lock is persistently disabled; every session start will say so until `lock enable`.")


def enable(root, session=None) -> Result:
    if not supported():
        return _res(3, "Layer 2 lock: unsupported on this platform (Windows only in v4.0.0).")
    try:
        _write_persistent(root, session, False)
    except Refused as e:
        return _res(2, str(e))
    except (ValueError, OSError) as e:
        return _res(2, "Could not clear the disable (%s)." % e.__class__.__name__)
    return lock_all(root)


# ---- CLI -------------------------------------------------------------------------------------------------------

def default_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def main(argv=None, out=None) -> int:
    import argparse
    out = sys.stdout if out is None else out
    ap = argparse.ArgumentParser(prog="skyguard lock", description="Layer 2 read-only lock (M-D, off by default).")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None, help="the Skyrim install root (default: this toolkit's)")
    common.add_argument("--session", default=None, help="session id for the lift (default $SKYGUARD_SESSION_ID)")
    for name in ("lock", "disable", "enable"):
        sub.add_parser(name, parents=[common])
    up = sub.add_parser("unlock", parents=[common])
    up.add_argument("files", nargs="*")
    sp = sub.add_parser("status", parents=[common])
    sp.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if not a.cmd:
        print("usage: lock <lock|unlock [file...]|status [--json]|disable|enable> [--root R] [--session S]", file=out)
        return 2
    root = a.root or default_root()
    explicit = a.session or os.environ.get(ENV_SESSION)
    if reserved_session(explicit):
        for line in RESERVED_LINES:
            print(line, file=out)
        return 2
    session = explicit or (USER_SESSION if _interactive() else None)     # user-tty is minted HERE, from a terminal only
    try:
        if a.cmd == "lock":
            res = lock_all(root)
        elif a.cmd == "unlock":
            res = unlock(root, a.files or None, session)
        elif a.cmd == "status":
            res = status(root)
        elif a.cmd == "disable":
            res = disable(root, session)
        else:
            res = enable(root, session)
    except SandboxViolation as e:
        print("REFUSED (sandbox): " + _clip(str(e)), file=out)
        return 2
    except Refused as e:
        print(_clip(str(e)), file=out)
        return 2
    if a.cmd == "status" and a.json:
        print(json.dumps(dict(res.data, exit=res.exit), indent=1), file=out)
    else:
        for line in res.lines:
            print(line, file=out)
    return res.exit
