"""Layer 2: the read-only lock on the vanilla masters and archives (Plan 6 Task 8; mechanism M-D, R1-R5, R11).

M-D = the read-only attribute PLUS a file-level, non-inherited deny ACE for the current user's SID with the
mask (WD, AD, WA, WEA). Denying write-attributes is what stops `-Force` from clearing the bit. Measured in
docs/evidence/2026-10-04-plan6-ml2.md; what it does NOT stop (accepted by the user, written
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
        adv.GetSecurityDescriptorControl.argtypes = [vp, ct.POINTER(ct.c_uint16), ct.POINTER(ct.c_uint32)]
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

    def _control(self, path) -> int:
        """The security descriptor's control bits (SE_DACL_PROTECTED 0x1000, SE_DACL_AUTO_INHERITED 0x400, ...)."""
        ct = self.ct
        dacl, sd = self._get_dacl(path)
        try:
            control, rev = ct.c_uint16(), ct.c_uint32()
            if not self.adv.GetSecurityDescriptorControl(sd, ct.byref(control), ct.byref(rev)):
                raise ct.WinError(ct.get_last_error())
            return control.value
        finally:
            self.k32.LocalFree(sd)

    def protected(self, path) -> bool:
        """SE_DACL_PROTECTED (0x1000): the file's inheritance is turned off."""
        return bool(self._control(path) & 0x1000)

    def _set_dacl(self, path, dacl, protected=None):
        """Plan 6 final fix (lane A I2): write the DACL with the protection `protected` names, or -- None -- the one the
        file has NOW. Always passing UNPROTECTED turned a protected DACL into an inheriting one for good.

        SetNamedSecurityInfoW re-runs inheritance and always marks the result auto-inherited (SE_DACL_AUTO_INHERITED,
        SDDL `AI`). A file whose DACL never carried that bit -- every file under windows-latest's D:\\a\\_temp, and any
        folder tree whose ACL was written by a legacy tool -- came back from lock + unlock with `AI` added: the same
        entries, not the same DACL. While the protection is unchanged and the bit is absent, the DACL is written as
        it is instead (SetFileSecurityW: no inheritance pass, the control bits we give it). A protection CHANGE still
        needs the inheritance pass, so it still goes through SetNamedSecurityInfoW."""
        control = self._control(path)
        now = bool(control & 0x1000)
        protected = now if protected is None else protected
        if protected == now and not control & 0x400:
            self._set_dacl_as_is(path, dacl, protected)
            return
        rc = self.adv.SetNamedSecurityInfoW(path, 1, 0x4 | (0x80000000 if protected else 0x20000000), None, None,
                                            dacl, None)
        if rc:
            raise self.ct.WinError(rc)

    def _set_dacl_as_is(self, path, dacl, protected):
        ct = self.ct
        vp = ct.c_void_p
        adv = self.adv
        adv.InitializeSecurityDescriptor.argtypes = [vp, ct.c_uint32]
        adv.SetSecurityDescriptorDacl.argtypes = [vp, ct.c_int, vp, ct.c_int]
        adv.SetSecurityDescriptorControl.argtypes = [vp, ct.c_uint16, ct.c_uint16]
        adv.SetFileSecurityW.argtypes = [ct.c_wchar_p, ct.c_uint32, vp]
        sd = ct.create_string_buffer(64)                  # SECURITY_DESCRIPTOR_MIN_LENGTH is 40 on x64
        if not (adv.InitializeSecurityDescriptor(sd, 1) and adv.SetSecurityDescriptorDacl(sd, 1, dacl, 0)
                and adv.SetSecurityDescriptorControl(sd, 0x1000, 0x1000 if protected else 0)
                and adv.SetFileSecurityW(path, 0x4, sd)):
            raise ct.WinError(ct.get_last_error())

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

    def remove_ours(self, path, masks, protected=None):
        """Delete this user's explicit deny ACEs whose mask is one of `masks`. Returns how many. `protected`: the DACL
        protection to write back (the one recorded at lock time), None for the file's current one."""
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
                self._set_dacl(path, dacl, protected)
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


def _remove_file(path, keep_bit, protected=None):
    """Deny ACE first (with WA denied `attrib -R` is itself refused), then the bit, unless it was the user's.
    protected: the DACL protection recorded at lock time (the manifest's prior_protected), None = keep the current."""
    p = os.path.normpath(os.fspath(path))
    w = _win()
    w.remove_ours(p, (MASK_FULL, MASK_PRE), protected)
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
    p = os.path.normpath(str(path))
    return {"prior_attrs": _win().attrs(p),
            "prior_aces": [[a["type"], a["mask"], a["sid"]] for a in st.aces],
            "prior_protected": _win().protected(p)}         # final fix (lane A I2): unlock writes exactly this back


# ---- lock ------------------------------------------------------------------------------------------------------

def lock_all(root, only=None) -> Result:
    g = _guard(root)
    if isinstance(g, Result):
        return g
    try:
        sf = _skyguard_dir(root) / "state.json"
        _assert_sandbox(sf)
        with _state.locked(sf):
            return _lock_all(root, only=only)
    except Refused as exc:
        return _res(2, str(exc))
    except (OSError, ValueError) as exc:
        return _res(2, "Lock operation did not complete (%s); inspect lock status." % type(exc).__name__)


def _lock_all(root, only=None) -> Result:
    """Lock the derived target set. only: a predicate on a target's NAME -- lock just those (relock_expired); every
    other record in the manifest is kept as it is."""
    g = _guard(root)
    if isinstance(g, Result):
        return g
    data_dir, t = g
    st0 = _state.read(str(Path(root) / ".skyguard" / "state.json"))
    if st0.lock_disabled:
        return _res(2, "The lock is disabled (persistent, since %s)." % time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(st0.lock_disabled["since"])), "Run `bash tools/skyguard.sh lock enable` to turn it back on.")
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
        elif only is not None and not only(name):
            carry[name] = rec                    # a target this call does not touch: its record stays
    try:
        for p in t.files:
            name = p.name
            if only is not None and not only(name):
                continue
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
                info = {k: prev[k] for k in ("prior_attrs", "prior_aces", "prior_protected") if k in prev} or info
            record[name] = dict(info, preexisting_ro=pre, locked_at=prev.get("locked_at", time.time()))
            # Written per file, atomically: a crash mid-lock leaves a list that matches what is locked.
            _write_manifest(root, t.edition, dict(carry, **record))  # per file
        _write_manifest(root, t.edition, dict(carry, **record))
    except Refused as e:
        return _res(2, str(e))
    record.update(carry)
    skipped = [(n, r) for n, r in t.skips if r != "missing" and (only is None or only(n))]
    partial = failed + skipped
    if partial:
        names = ["%s (%s)" % (n, r) for n, r in partial]
        return _res(1, "Locked %d of %d vanilla files; %d not locked." % (len(record), len(t.files) + len(skipped),
                                                                          len(partial)),
                    "Not locked: " + _names_list(names, 3), data={"locked": sorted(record), "not_locked": partial})
    return _res(0, "Locked %d vanilla files (M-D, verified by re-read)." % len(record),
                "Unlock one with `bash tools/skyguard.sh lock unlock <file>`; the Python-free route is tools\\skyguard-unlock.cmd.",
                data={"locked": sorted(record)})


# ---- unlock ----------------------------------------------------------------------------------------------------

MAX_LIFTS = 100


def _live_lifts(lifts, now):
    """Prune old permissions, preserving expired lock obligations until relock succeeds.

    Pending retries occupy one reserved slot, with the union of their scopes.
    Evicted lock permissions expire immediately instead of losing their files.
    """
    pending = [x for x in lifts if x["kind"] == "lock" and _state.lift_expired(x, now)]
    live = [x for x in lifts if not _state.lift_expired(x, now)]
    live.sort(key=lambda x: x["created"])
    limit = MAX_LIFTS - 1 if pending or any(x["kind"] == "lock" for x in live[:-MAX_LIFTS]) else MAX_LIFTS
    evicted, live = live[:-limit], live[-limit:]
    pending += [dict(x, expires_at=min(x["expires_at"], now)) for x in evicted if x["kind"] == "lock"]
    if len(pending) > 1:
        merged = dict(pending[0], reason="expired lock retry", session_id="lock-relock", agent="system",
                      created=min(x["created"] for x in pending), expires_at=min(now, *(x["expires_at"] for x in pending)))
        if any(x.get("files") is None for x in pending):
            merged.pop("files", None)
        else:
            merged["files"] = sorted({name.lower(): name for x in pending for name in x["files"]}.values())
        pending = [merged]
    return pending + live


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
                       "(at a terminal it is automatic).", "Without one, run tools\\skyguard-unlock.cmd in your own terminal.")
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
        # A failure retains its expired record through the writer below.
        relock_expired(root, now=now, wait=_state.LOCK_WAIT)
        sf = _skyguard_dir(root) / "state.json"
        _assert_sandbox(sf)
        with _state.locked(sf):                             # read-modify-write under the one state lock
            cur = _state.read(str(sf))
            manifest, mstatus = _read_manifest(root)
            lift = {"kind": "lock", "reason": "lock unlock " + (_names_list(chosen, 3) if names else "all"),
                    "session_id": session,
                    "agent": "user" if session == USER_SESSION else (os.environ.get(ENV_AGENT, "cli") or "cli"),
                    "created": now, "expires_at": now + LIFT_SECONDS}
            if names:
                lift["files"] = list(chosen)
            persistent = {"lock_disabled": cur.lock_disabled} if cur.lock_disabled else {}
            lifts = _live_lifts(list(cur.lifts) + [lift], now)
            _state.write(str(sf), {"v": 1, "lifts": lifts, "persistent": persistent})
            try:
                return _unlock_files(root, data_dir, t, names, chosen, manifest)
            except (OSError, ValueError) as exc:
                return _res(1, "Unlock may be partial (%s); inspect lock status." % type(exc).__name__,
                            "The recorded lift is retained for relock.")
    except Refused as e:
        return _res(2, str(e))
    except (ValueError, OSError) as e:
        return _res(2, "Could not record the lift (%s); nothing was unlocked." % e.__class__.__name__)


def _unlock_files(root, data_dir, t, names, chosen, manifest):
    """Undo file permissions while unlock() still owns the state writer lock."""
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
            prot = manifest.get(name, {}).get("prior_protected")
            _remove_file(p, keep, prot if isinstance(prot, bool) else None)
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
    return _res(0, "Unlocked %d files for %d min: the first guarded tool call after that relocks them (session start "
                   "at the latest)." % (len(done), int(LIFT_SECONDS // 60)), extra, data={"unlocked": done})


# ---- the expiry (Plan 6 final fix, A-I3 / C-I1 ruling) -----------------------------------------------------------

HOOK_STATE_WAIT = 0.2      # the per-call hook path never blocks on the state lock longer than this


def relock_expired(root, now=None, wait=HOOK_STATE_WAIT):
    """Relock what an EXPIRED lock lift opened; retire it after success. -> None when none is due, else a Result
    whose data["relocked"] names the files locked again. Called by engine.shadow on every guarded hook call (a time
    compare when nothing is due); session start stays the backstop. A file a LIVE lock lift (any session) still
    covers stays open; nothing is relocked while the lock is persistently disabled or was never applied."""
    now = time.time() if now is None else now
    sf = Path(root) / ".skyguard" / "state.json"
    st = _state.read(str(sf))
    if st.rejected or not any(x["kind"] == "lock" and _state.lift_expired(x, now) for x in st.lifts):
        return None
    _assert_sandbox(sf)
    # A hook path: a SHORT wait for the state lock; busy -> StateBusy (the caller logs it and the next call retries).
    with _state.locked(sf, wait):
        st = _state.read(str(sf))                            # re-read under the lock
        if st.rejected:
            return None
        due = [x for x in st.lifts if x["kind"] == "lock" and _state.lift_expired(x, now)]
        if not due:
            return None
        live = [x for x in st.lifts if x["kind"] == "lock" and not _state.lift_expired(x, now)]
        keep = [x for x in st.lifts if not (x["kind"] == "lock" and _state.lift_expired(x, now))]
        persistent = {"lock_disabled": dict(st.lock_disabled)} if st.lock_disabled else {}
        # Serialize the filesystem operation with new lifts and persistent
        # disable decisions. Retire the obligation only after success.
        if st.lock_disabled or not supported() or not (Path(root) / ".skyguard" / "lock.json").is_file():
            _state.write(str(sf), {"v": 1, "lifts": keep, "persistent": persistent})
            return _res(0, "An expired lock lift was cleared; nothing relocked (the lock is %s)."
                        % ("disabled" if st.lock_disabled else "not applied"), data={"relocked": []})
        if any(x.get("files") is None for x in live):
            _state.write(str(sf), {"v": 1, "lifts": keep, "persistent": persistent})
            return _res(0, "An expired lock lift was cleared; a live lift still opens every file.", data={"relocked": []})
        covered = {f.lower() for x in live for f in x["files"]}
        want = None if any(x.get("files") is None for x in due) else {f.lower() for x in due for f in x["files"]}

        def only(name):
            return name.lower() not in covered and (want is None or name.lower() in want)

        res = lock_all(root, only=only)
        if res.exit == 0:
            _state.write(str(sf), {"v": 1, "lifts": keep, "persistent": persistent})
        return Result(res.exit, res.lines, dict(res.data, relocked=[n for n in res.data.get("locked", []) if only(n)]))


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
    _assert_sandbox(sf)
    with _state.locked(sf):                                 # read-modify-write under the one state lock
        cur = _state.read(str(sf))
        persistent = {"lock_disabled": {"since": time.time(), "by_session": session or "manual"}} if disabled else {}
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
    return _res(0, "The lock is persistently disabled; every session start will say so until `bash tools/skyguard.sh lock enable`.")


def enable(root, session=None) -> Result:
    if not supported():
        return _res(3, "Layer 2 lock: unsupported on this platform (Windows only in v4.0.0).")
    try:
        sf = _skyguard_dir(root) / "state.json"
        _assert_sandbox(sf)
        with _state.locked(sf):
            _write_persistent(root, session, False)
            return lock_all(root)
    except Refused as e:
        return _res(2, str(e))
    except (ValueError, OSError) as e:
        return _res(2, "Could not clear the disable (%s)." % e.__class__.__name__)


# ---- CLI -------------------------------------------------------------------------------------------------------

def default_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def main(argv=None, out=None) -> int:
    import argparse
    out = sys.stdout if out is None else out
    # Task 15 fix round 2 (N1): no abbreviated options anywhere in the CLI the guard rules read.
    ap = argparse.ArgumentParser(prog="skyguard lock", description="Layer 2 read-only lock (M-D, off by default).",
                                 allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--root", default=None, help="the Skyrim install root (default: this toolkit's)")
    common.add_argument("--session", default=None, help="session id for the lift (default $SKYGUARD_SESSION_ID)")
    for name in ("lock", "disable", "enable"):
        sub.add_parser(name, parents=[common], allow_abbrev=False)
    up = sub.add_parser("unlock", parents=[common], allow_abbrev=False)
    up.add_argument("files", nargs="*")
    sp = sub.add_parser("status", parents=[common], allow_abbrev=False)
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
