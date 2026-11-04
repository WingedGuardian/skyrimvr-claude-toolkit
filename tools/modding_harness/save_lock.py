"""Save locks: the OS refuses to delete or change a save until the USER unlocks it. Optional; off unless switched on.

A pre-call guard cannot see inside every script, and a copy of every save costs space a user may not have. So this
protection lives in the filesystem: each save gets NTFS deny rules for its own user, and lifting them needs an
administrator -- a UAC prompt only the person at the keyboard can approve. No agent, script or tool running as the
user can delete, overwrite, rename, recycle or unlock a locked save. Locking needs no admin.

Per save file:
    DENY  <user>        delete, write data, append data, write attributes, change permissions, take ownership
    ALLOW OWNER RIGHTS  read only   (replaces the owner's IMPLICIT right to rewrite the permissions; without it the
                                     owner could simply remove the deny rule first)
Per save folder (this folder only, never inherited by new files):
    DENY  <user>        delete files inside it, delete the folder itself, change permissions, take ownership
    ALLOW OWNER RIGHTS  read only
Windows lets a delete through if EITHER the file grants DELETE or its folder grants "delete files inside", so the
folder rule is what makes the file rule hold. New files still work, so new saves, autosaves and quicksaves do.
The same either-or applies one level up: the PARENT folder carries no rule, so the save folder itself can still be
renamed or moved (measured 2026-11-03). Its saves stay locked inside it; `off` then cannot reach them by the old path,
so every folder ever locked is recorded with its identity (<switch>-folders.json) and `off` refuses to report success
while one of them is not where it was.

Reading: is_locked, is_plain and status_map read the DACL through the Win32 API (SDDL), never icacls text, so a path
the console code page cannot show or a localized account name cannot mislead them. A read that fails RAISES: "could
not read" is never "not locked". A rule is ours by its shape (explicit, not inherited, one of the two deny masks,
whichever SID it names): `off` from another administrator account lifts a rule that names the original user. Locked is
a narrower question, "does a rule of ours bind THIS user?": a rule naming someone else leaves this user free to write,
so locking adds this user's own rule and status does not count that save as protected. A hand-made deny of the same
shape naming a group this user belongs to reads as not binding; locking it then fails loudly rather than reporting a
lock.

Unlocking: an elevated token carries the user's SID too, so the deny rule binds an administrator as well and plain
icacls is refused even elevated (measured 2026-10-30). unlock() switches on SeRestorePrivilege -- held only by an
elevated token -- and rewrites the DACL without the two rules. A normal process is refused the privilege.

LIMITS: permissions are per USER, not per program -- the game cannot delete or overwrite a locked save either (its
rotating autosave/quicksave slots and anything written in the last few minutes are left unlocked). And an agent that
itself runs elevated ("Run as administrator") is not held by the locks: hosts should warn, and refuse to switch
locking on from an elevated process. And the locks OUTLAST the toolkit: once it is removed, ordinary Windows tools
cannot lift them, so hosts must tell the user to run `off` (elevated) first. Windows/NTFS only; elsewhere every call
raises Unsupported.

    turn_on(config, folders, extensions, rotating)   # write the switch, lock it, lock every folder now
    turn_off(config, folders, extensions)             # elevated only; lifts every lock, verifies, then says so
    auto_lock(state, before, files, folders, ...)     # per sentinel look: lock new or changed saves once settled
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

SETTLE_S = 180                      # a file written in the last 3 minutes may still be in use by the game
VERIFY_S = 3600                     # auto_lock re-reads the real ACLs at most hourly
REPLACE_WAITS = (0.05, 0.1, 0.25, 0.5, None)    # seconds between tries at replacing the record; None: give up
ROTATING = re.compile(r"^(?:autosave|quicksave)", re.I)
OWNER_RIGHTS = "*S-1-3-4"
FILE_DENY = "(DE,WD,AD,WA,WDAC,WO)"
FOLDER_DENY = "(DC,DE,WDAC,WO)"
# The same two rules as the SDDL a DACL read returns them in (measured 2026-11-03). Compared by the bits, not the
# text, because SDDL may spell a mask in hex.
_SDDL_RIGHTS = {"CC": 0x1, "DC": 0x2, "LC": 0x4, "SW": 0x8, "RP": 0x10, "WP": 0x20, "DT": 0x40, "LO": 0x80, "CR": 0x100,
                "SD": 0x10000, "RC": 0x20000, "WD": 0x40000, "WO": 0x80000, "FA": 0x1F01FF, "FR": 0x120089,
                "FW": 0x120116, "FX": 0x1200A0, "GA": 0x10000000, "GX": 0x20000000, "GW": 0x40000000,
                "GR": 0x80000000}
_LOCK_MASKS = (0x2 | 0x4 | 0x100 | 0x10000 | 0x40000 | 0x80000,     # the file rule: DCLCCRSDWDWO
               0x40 | 0x10000 | 0x40000 | 0x80000)                  # the folder rule: DTSDWDWO
_FILE_NOT_THERE = (FileNotFoundError, NotADirectoryError)
NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
ELEVATED_WARNING = ("this process runs elevated (as administrator): save locks do not stop anything started from "
                    "here. Run agents from a normal, non-admin terminal.")


_WIN = None
_ALIASES = {}


class Unsupported(RuntimeError):
    pass


def supported():
    return os.name == "nt"


def _require():
    if not supported():
        raise Unsupported("save locks need Windows/NTFS")


def _win():
    """The Win32 calls used here, bound once."""
    global _WIN
    if _WIN is None:
        import ctypes
        from ctypes import wintypes as w
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = w.HANDLE
        k32.CreateFileW.restype = w.HANDLE
        k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        k32.CloseHandle.argtypes = [w.HANDLE]
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p
        k32.GetSystemDirectoryW.argtypes = [w.LPWSTR, w.UINT]
        k32.GetVolumePathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        k32.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
        adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
        adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        adv.AdjustTokenPrivileges.argtypes = [w.HANDLE, w.BOOL, ctypes.c_void_p, w.DWORD, ctypes.c_void_p,
                                              ctypes.c_void_p]
        adv.GetSecurityInfo.argtypes = [w.HANDLE, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p)]
        adv.SetSecurityInfo.argtypes = [w.HANDLE, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p]
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.c_void_p]
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.BOOL),
                                                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL)]
        _WIN = (ctypes, w, adv, k32)
    return _WIN


def _icacls(*args):
    """Run icacls for a WRITE, by full path. Its text is an error message for a person and is never parsed: icacls
    writes the console code page, and localized Windows translates its words."""
    ctypes, w, _adv, k32 = _win()
    buf = ctypes.create_unicode_buffer(512)
    if not k32.GetSystemDirectoryW(buf, 512):
        raise ctypes.WinError(ctypes.get_last_error())
    r = subprocess.run([os.path.join(buf.value, "icacls.exe"), *args], capture_output=True, text=True, encoding="oem",
                       errors="replace", **NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def is_elevated():
    """True when this process holds admin rights (a "Run as administrator" terminal or anything it started)."""
    if not supported():
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def user_sid():
    """This process's user as a SID string, from its own token. No program is run: `whoami` can resolve to Git's under
    Git Bash, and a command's output has a code page. Raises OSError when the token cannot be read."""
    _require()
    ctypes, w, adv, k32 = _win()
    token = w.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))    # TokenUser; fails, reporting the size
        buf = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, buf, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        text = w.LPWSTR()
        if not adv.ConvertSidToStringSidW(ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        k32.CloseHandle(token)


def is_member(name, extensions):
    low = str(name).lower()
    return any(low.endswith(ext.lower()) for ext in extensions)


def _aces(sddl):
    """(type, flags, rights, sid) of each ACE in a DACL's SDDL. Anything that is not a DACL's text raises: no rules
    found in it would read as a plain file."""
    if not sddl.startswith("D:"):
        raise OSError("unexpected permission text: {!r}".format(sddl[:40]))
    found = []
    for raw in re.findall(r"\(([^()]*)", sddl):
        parts = raw.split(";")
        if len(parts) >= 6:
            found.append((parts[0], parts[1], parts[2], parts[5]))
    return found


def _rights_mask(text):
    """The bits an SDDL rights field spells; None for a token this module does not know."""
    if text[:2].lower() == "0x":
        try:
            return int(text, 16)
        except ValueError:
            return None
    mask = 0
    for token in (text[i:i + 2] for i in range(0, len(text), 2)):
        if token not in _SDDL_RIGHTS:
            return None
        mask |= _SDDL_RIGHTS[token]
    return mask


def _is_lock_deny(ace):
    kind, flags, rights, _sid = ace
    return kind == "D" and not flags and _rights_mask(rights) in _LOCK_MASKS


def _is_owner_grant(ace):
    kind, flags, _rights, sid = ace
    return kind == "A" and "ID" not in flags and sid == "OW"


def _alias_sid(alias):
    """The SID behind a name SDDL uses instead of one (a local account can be "LA", Guests is "BG"). None: no such name."""
    if alias not in _ALIASES:
        ctypes, w, adv, k32 = _win()
        sid, text = ctypes.c_void_p(), w.LPWSTR()
        found = None
        if adv.ConvertStringSidToSidW(alias, ctypes.byref(sid)):
            try:
                if adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                    found = text.value
                    k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            finally:
                k32.LocalFree(sid)
        _ALIASES[alias] = found
    return _ALIASES[alias]


def _names_sid(ace_sid, sid):
    return ace_sid == sid or (not ace_sid.startswith("S-") and _alias_sid(ace_sid) == sid)


def _sddl_binds(sddl, sid):
    """A lock rule of ours names `sid`, with or without the OWNER RIGHTS rule beside it (an `off` from another account
    once removed only that one). A rule naming another account binds that account and leaves `sid` free to write."""
    return any(_is_lock_deny(a) and _names_sid(a[3], sid) for a in _aces(sddl))


def _sddl_plain(sddl):
    """Neither rule is present. An OWNER RIGHTS rule alone is not a lock but not plain either: it still takes away the
    owner's implicit right to change the permissions."""
    return not any(_is_lock_deny(a) or _is_owner_grant(a) for a in _aces(sddl))


def _open_for_security(path, access):
    """A handle on a file or folder with only security access: no data access, so an open by the game cannot refuse it
    with a sharing violation. Raises the OSError subclass for the failure."""
    ctypes, w, _adv, k32 = _win()
    h = k32.CreateFileW(str(path), access, 7, None, 3, 0x02000000, None)    # any sharing, existing, backup semantics
    if h in (None, w.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def _handle_sddl(h):
    """The DACL of an open handle as SDDL."""
    ctypes, w, adv, k32 = _win()
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    rc = adv.GetSecurityInfo(h, 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc:
        raise ctypes.WinError(rc)
    text = w.LPWSTR()
    try:
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value or ""
        finally:
            k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        k32.LocalFree(sd)


def _read_sddl(path):
    """The DACL of `path` as SDDL. Raises OSError: a failure is never an empty answer."""
    h = _open_for_security(path, 0x00020000)                                # READ_CONTROL
    try:
        return _handle_sddl(h)
    finally:
        _win()[3].CloseHandle(h)


def is_locked(path):
    """True when this module's deny rule binds this user. Raises OSError when the rules cannot be read."""
    _require()
    return _sddl_binds(_read_sddl(path), user_sid())


def is_plain(path):
    """True when the path carries neither of this module's rules. Raises OSError when they cannot be read."""
    _require()
    return _sddl_plain(_read_sddl(path))


def _rules_map(folder, extensions):
    """{save path: DACL SDDL} for every save in `folder`. A save gone since the listing is left out; any other read
    failure raises, naming the path."""
    found = {}
    with os.scandir(folder) as entries:
        for e in entries:
            if e.is_file() and is_member(e.name, extensions):
                try:
                    found[e.path] = _read_sddl(e.path)
                except _FILE_NOT_THERE:
                    continue
                except OSError as exc:
                    raise OSError("cannot read the permissions of {}: {}".format(e.path, exc)) from exc
    return found


def status_map(folder, extensions):
    """{save path: does a rule bind this user?} for every save in `folder`. Raises OSError when a save's permissions
    cannot be read."""
    _require()
    sid = user_sid()
    return {path: _sddl_binds(sddl, sid) for path, sddl in _rules_map(folder, extensions).items()}


def _apply_rule(path, sid, rights, what):
    """Add a lock rule for `sid`, then read it back. Bound already is done: the rule itself refuses a second icacls. A
    rule naming someone else does not count: it leaves `sid` free to write."""
    _require()
    if _sddl_binds(_read_sddl(path), sid):
        return
    rc, out = _icacls(str(path), "/deny", "*{}:{}".format(sid, rights), "/grant", OWNER_RIGHTS + ":(R)")
    if rc:
        raise OSError("lock failed for {}{}: {}".format(what, path, out))
    if not _sddl_binds(_read_sddl(path), sid):
        raise OSError("lock failed for {}{}: icacls reported success but the rule is not there".format(what, path))


def lock_file(path, sid):
    _apply_rule(path, sid, FILE_DENY, "")


def lock_folder(path, sid):
    # No (OI)/(CI): these rules apply to the folder object only and are never inherited by new files.
    _apply_rule(path, sid, FOLDER_DENY, "folder ")


def strip_lock_aces(sddl, sid):
    """The SDDL with our explicit rules removed: a lock-shaped deny under ANY SID (an `off` from another administrator
    account, or an account SDDL names by alias), any explicit deny for `sid`, and the OWNER RIGHTS grant. Inherited (ID)
    rules and everyone else's stay."""
    def keep(ace):
        parts = ace.split(";")
        if len(parts) < 6 or "ID" in parts[1]:
            return True
        fields = (parts[0], parts[1], parts[2], parts[5])
        return not ((parts[0] == "D" and parts[5] == sid) or _is_lock_deny(fields) or _is_owner_grant(fields))
    return re.sub(r"\(([^()]*)\)", lambda m: m.group(0) if keep(m.group(1)) else "", sddl)


def _enable_restore_privilege():
    """Switch on SeRestorePrivilege: with it Windows grants WRITE_DAC whatever the DACL says. Only an elevated token
    holds it, so a normal process fails here -- that refusal IS the protection."""
    ctypes, wintypes, adv, k32 = _win()

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", LUID), ("Attributes", wintypes.DWORD)]

    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        luid = LUID()
        if not adv.LookupPrivilegeValueW(None, "SeRestorePrivilege", ctypes.byref(luid)):
            raise ctypes.WinError(ctypes.get_last_error())
        tp = TOKEN_PRIVILEGES(1, luid, 0x2)
        ok = adv.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        err = ctypes.get_last_error()
        if not ok or err == 1300:                 # ERROR_NOT_ALL_ASSIGNED: this token does not hold it
            raise PermissionError("not elevated: Windows refused the restore privilege")
    finally:
        k32.CloseHandle(token)


def _rewrite_dacl(path, edit):
    """Open `path` with backup semantics (WRITE_DAC granted by the restore privilege) and set edit(DACL SDDL)."""
    ctypes, wintypes, adv, k32 = _win()
    try:
        h = _open_for_security(path, 0x00040000 | 0x00020000)
    except OSError as e:
        raise PermissionError("cannot open {} for a permission change: {}".format(path, e))
    sd2 = ctypes.c_void_p()
    try:
        old = _handle_sddl(h)
        new = edit(old)
        if new == old:
            return False
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(new, 1, ctypes.byref(sd2), None):
            raise ctypes.WinError(ctypes.get_last_error())
        present, defaulted, dacl2 = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(sd2, ctypes.byref(present), ctypes.byref(dacl2), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        flags = new.split("(", 1)[0]
        info = 4 | (0x80000000 if "P" in flags else 0x20000000)     # keep the DACL's protected/inheriting state
        rc = adv.SetSecurityInfo(h, 1, info, None, None, dacl2, None)
        if rc:
            raise ctypes.WinError(rc)
        return True
    finally:
        if sd2:
            k32.LocalFree(sd2)
        k32.CloseHandle(h)


def unlock(path, sid):
    """Remove the rules, using the restore privilege only an elevated (UAC-approved) process holds."""
    _require()
    try:
        _enable_restore_privilege()
        _rewrite_dacl(path, lambda sddl: strip_lock_aces(sddl, sid))
    except PermissionError as e:
        raise PermissionError("unlock refused for {} (run from an elevated prompt): {}".format(path, e))
    except OSError as e:
        raise PermissionError("unlock failed for {}: {}".format(path, e))
    try:
        plain = is_plain(path)
    except OSError as e:
        raise PermissionError("unlock of {} could not be confirmed: {}".format(path, e))
    if not plain:
        raise PermissionError("unlock did not take for {}".format(path))


def backup_then_unlock(save, partners, sid, backup):
    """The one sanctioned way to change a locked save: back up the save and its `partners` (e.g. a co-save) with
    `backup(paths)`, which must raise on any failure -- and then nothing is unlocked."""
    group = [Path(save)] + [Path(p) for p in partners]
    present = [p for p in group if p.exists()]
    backup([str(p) for p in present])
    for p in present:
        unlock(p, sid)
    return present


def candidates(folder, extensions, rotating=ROTATING, settle=SETTLE_S, now=None):
    """(to lock, [(path, reason) left unlocked]) for the saves in `folder`."""
    now = time.time() if now is None else now
    todo, skipped = [], []
    for e in os.scandir(folder):
        if not (e.is_file() and is_member(e.name, extensions)):
            continue
        if rotating is not None and rotating.match(e.name):
            skipped.append((e.path, "rotating slot"))
        elif now - e.stat().st_mtime < settle:
            skipped.append((e.path, "written in the last {} s".format(settle)))
        else:
            todo.append(e.path)
    return todo, skipped


def lock(folder, extensions, rotating=ROTATING, settle=SETTLE_S, dry_run=False):
    sid = user_sid()
    todo, skipped = candidates(folder, extensions, rotating, settle)
    state = status_map(folder, extensions)
    todo = [p for p in todo if not state.get(p)]
    if not dry_run:
        for p in todo:
            lock_file(p, sid)
        if not is_locked(folder):
            lock_folder(folder, sid)
    return todo, skipped


def read_config(path):
    """{'enabled': bool, ...}; a missing or unreadable switch means OFF."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False}
    return data if isinstance(data, dict) else {"enabled": False}


def has_acls(folder):
    """True when `folder`'s volume keeps permissions (NTFS, ReFS; not exFAT or FAT32). Raises OSError if it cannot tell."""
    ctypes, w, _adv, k32 = _win()
    root = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumePathNameW(str(folder), root, 1024):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = w.DWORD()
    if not k32.GetVolumeInformationW(root.value, None, 0, None, None, ctypes.byref(flags), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(flags.value & 0x8)                                          # FILE_PERSISTENT_ACLS


def _record_path(config):
    config = Path(config)
    return config.with_name(config.stem + "-folders.json")


def _read_record(config):
    """(rows, lost): each folder ever locked as [path, volume, file id], and whether the record was ever unreadable."""
    try:
        data = json.loads(_record_path(config).read_text(encoding="utf-8"))
        rows = [list(row) for row in data["rows"]]
        if any(len(row) != 3 for row in rows):
            raise ValueError("malformed row")
        return rows, bool(data.get("lost"))
    except FileNotFoundError:
        return [], False
    except (OSError, ValueError, KeyError, TypeError):
        return [], True


def _write_record(config, rows, lost):
    path = _record_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(".part-{}-{}".format(os.getpid(), path.name))
    try:
        part.write_text(json.dumps({"rows": rows, "lost": lost}, indent=0), encoding="utf-8")
        for wait in REPLACE_WAITS:
            try:
                os.replace(str(part), str(path))
                break
            except PermissionError:     # a scanner holding a file just written (seen 2026-11-03 in about 1 test run in 3)
                if wait is None:
                    raise
                time.sleep(wait)
    finally:
        if part.exists():
            part.unlink()                       # same-call scratch this function created


def _identity(folder):
    st = os.stat(str(folder))
    return [str(folder), st.st_dev, st.st_ino]


def _volume_key(volume):
    """What every Python agrees on about a volume. 3.12 reports the volume serial in 64 bits and earlier versions only
    its low 32 (measured 2026-11-03: one NTFS folder, st_dev differs between 3.10.10 and 3.12.10, its low 32 bits and
    st_ino do not), and a record may hold either form."""
    return volume & 0xFFFFFFFF if isinstance(volume, int) else volume


def _same_folder(row, now):
    """Is this record row the folder an _identity() call sees now, whichever Python wrote each?"""
    return row[0] == now[0] and row[2] == now[2] and _volume_key(row[1]) == _volume_key(now[1])


def _remember(config, folder):
    """Record a folder this module locked, before the rule goes on, so `off` can account for it if it moves. A new
    folder at an old path is a new row: the old identity's saves are still locked somewhere else."""
    row = _identity(folder)
    rows, lost = _read_record(config)
    if not any(_same_folder(old, row) for old in rows):
        _write_record(config, rows + [row], lost)


def turn_on(config, folders, extensions, rotating=ROTATING, settle=SETTLE_S):
    """Lock every folder now, then write and lock the switch (an agent cannot edit it off). Everything checkable is
    checked before any rule goes on, and the switch comes LAST: it needs an elevated prompt to undo, so a folder that
    fails leaves it unset, and the error says which locks were applied. Refused from an elevated process: the locks
    would not hold the agent that is asking."""
    _require()
    if is_elevated():
        raise PermissionError("refusing to switch save locking on from an elevated process: " + ELEVATED_WARNING)
    config = Path(config)
    folders = list(folders)
    user_sid()
    on_already = bool(read_config(config).get("enabled"))
    if not on_already and config.exists() and is_locked(config):
        raise PermissionError("{} is locked; switch locking off (elevated) first".format(config))
    config.parent.mkdir(parents=True, exist_ok=True)
    for folder in folders + [config.parent]:
        if not has_acls(folder):
            raise Unsupported("save locks need a volume that keeps permissions (NTFS); {} has none".format(folder))
    report = []
    for folder in folders:
        try:
            _remember(config, folder)
            report.append((str(folder),) + tuple(len(x) for x in lock(folder, extensions, rotating, settle)))
        except OSError as e:
            done = sum(r[1] for r in report)
            raise OSError("save locking is NOT on: {} ({} save(s) locked before this; `off` from an elevated prompt "
                          "lifts them)".format(e, done)) from e
    if not on_already:
        config.write_text(json.dumps({"enabled": True}) + "\n", encoding="utf-8")
    if not is_locked(config):
        lock_file(config, user_sid())
    return report


def turn_off(config, folders=(), extensions=(), forget_missing=False):
    """Disable locking AND lift every lock in `folders` and in every folder locked earlier (the folder rule and each
    locked save). Elevated only: an agent must not be able to do this. The locks outlast the toolkit -- once it is gone,
    ordinary tools cannot lift them (icacls is refused even elevated) -- so `off` is the way back to a plain folder, and
    is what a user runs before removing the toolkit.

    Plain means re-read and verified. If any lock cannot be lifted or read, or a folder locked earlier is no longer
    where it was, the switch stays ON and that is raised; the locks it could lift stay lifted. forget_missing=True
    drops the record of folders that are gone (the user deleted them, or moved them and unlocked them there)."""
    _require()
    if not is_elevated():
        raise PermissionError("switching save locking off needs an elevated prompt")
    sid = user_sid()
    config = Path(config)
    rows, lost = _read_record(config)
    sweep, missing = [str(f) for f in folders], []
    for path, volume, file_id in rows:
        try:
            same = _same_folder([path, volume, file_id], _identity(path))
        except OSError:
            same = False
        if same:
            sweep.append(path)
        else:
            missing.append(path)
    sweep = list(dict.fromkeys(sweep))
    lifted, failed = 0, []
    for folder in sweep:
        try:
            targets = [p for p, sddl in _rules_map(folder, extensions).items() if not _sddl_plain(sddl)]
            if not is_plain(folder):
                targets.insert(0, str(folder))
        except OSError as e:
            failed.append("{} ({})".format(folder, e))
            continue
        for p in targets:
            try:
                unlock(p, sid)
                lifted += 1
            except PermissionError as e:
                failed.append(str(e))
    if not failed:
        for folder in sweep:                # re-read, so "plain" is what the disk says and not what was attempted
            try:
                left = [p for p, sddl in _rules_map(folder, extensions).items() if not _sddl_plain(sddl)]
                if not is_plain(folder):
                    left.append(folder)
            except OSError as e:
                failed.append("{} ({})".format(folder, e))
                continue
            failed.extend("{} is still locked".format(p) for p in left)
    if failed:
        raise PermissionError("save locking stays ON: {} lock(s) could not be lifted or read: {}".format(
            len(failed), "; ".join(failed[:5])))
    if (missing or lost) and not forget_missing:
        raise PermissionError(
            "save locking stays ON: {} lock(s) lifted, but {}. Put a moved folder back and run this again, or unlock "
            "it where it is now (`unlock <folder>`); if it is gone for good, run `off --forget-missing`.".format(
                lifted, "the record of locked folders could not be read, so saves locked earlier may still be locked"
                if lost and not missing else
                "{} folder(s) locked earlier are not where they were and their saves may still be locked: {}".format(
                    len(missing), "; ".join(missing[:5]))))
    if config.exists() and not is_plain(config):
        unlock(config, sid)
    config.write_text(json.dumps({"enabled": False}) + "\n", encoding="utf-8")
    if rows or lost:
        _write_record(config, [], False)
    return lifted


def auto_lock(state, before, files, folders, now=None, rotating=ROTATING, settle=SETTLE_S, verify=VERIFY_S,
              extensions=None, config=None):
    """Lock every settled save not locked yet, and give each folder its rule the first time it is seen. `before`/`files`
    are the sentinel's previous and current snapshots ({key: [size, mtime_ns, path]}); `state` (the sentinel's dict)
    remembers what is locked, so a call locks only new or changed saves, and the real ACLs are re-read at most every
    `verify` seconds (at the next look when a read failed). `config`, the switch, says where `off`'s record lives.
    Returns what could not be locked: a save's path, or "<folder> (<reason>)" for a folder's rule or a failed read."""
    _require()
    now = time.time() if now is None else now
    sid = user_sid()
    locked = state.setdefault("locked", {})
    ruled = state.setdefault("lock_folders", [])
    failed = []
    if now - state.get("lock_verified", 0) > verify:
        locked.clear()
        del ruled[:]
        exts = extensions or tuple({os.path.splitext(p)[1].lower() for _, _, p in files.values()}) or (".none",)
        good = True
        for folder in folders:
            try:
                real = {os.path.normcase(p) for p, ok in status_map(folder, exts).items() if ok}
                locked.update({k: True for k, (_, _, p) in files.items() if os.path.normcase(p) in real})
            except OSError as e:
                failed.append("{} ({})".format(folder, e))
                good = False
        if good:
            state["lock_verified"] = now
    for folder in folders:
        if folder in ruled:
            continue
        try:
            if config is not None:
                _remember(config, folder)
        except OSError as e:
            failed.append("{} (not recorded for `off`: {})".format(folder, e))
        try:
            if not is_locked(folder):
                lock_folder(folder, sid)
            ruled.append(folder)
        except OSError as e:
            failed.append("{} ({})".format(folder, e))
    for key, (size, mtime, path) in files.items():
        changed = key in before and list(before[key][:2]) != [size, mtime]
        if key in locked and not changed:
            continue                       # locked, and not changed since the last look: nothing to do
        if (rotating is not None and rotating.match(os.path.basename(path))) or now - mtime / 1e9 < settle:
            locked.pop(key, None)
            continue
        try:
            lock_file(path, sid)
            locked[key] = True
        except OSError:
            failed.append(path)
    for key in [k for k in locked if k not in files]:
        del locked[key]
    return failed
