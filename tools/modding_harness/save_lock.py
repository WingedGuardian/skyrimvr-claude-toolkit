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
    DENY  <user>        delete files inside it, delete or rename the folder, change permissions, take ownership
    ALLOW OWNER RIGHTS  read only
Windows lets a delete through if EITHER the file grants DELETE or its folder grants "delete files inside", so the
folder rule is what makes the file rule hold. New files still work, so new saves, autosaves and quicksaves do.

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
    turn_off(config, folders, extensions)             # elevated only; lifts every lock (run before uninstalling)
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
ROTATING = re.compile(r"^(?:autosave|quicksave)", re.I)
OWNER_RIGHTS = "*S-1-3-4"
FILE_DENY = "(DE,WD,AD,WA,WDAC,WO)"
FOLDER_DENY = "(DC,DE,WDAC,WO)"
NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
ELEVATED_WARNING = ("this process runs elevated (as administrator): save locks do not stop anything started from "
                    "here. Run agents from a normal, non-admin terminal.")


class Unsupported(RuntimeError):
    pass


def supported():
    return os.name == "nt"


def _require():
    if not supported():
        raise Unsupported("save locks need Windows/NTFS")


def _icacls(*args):
    r = subprocess.run(["icacls", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       **NO_WINDOW)
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
    _require()
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True,
                         **NO_WINDOW).stdout.strip()
    return out.rsplit(",", 1)[1].strip('"')


def is_member(name, extensions):
    low = str(name).lower()
    return any(low.endswith(ext.lower()) for ext in extensions)


def _locked_block(text):
    return "(DENY)" in text and ("OWNER RIGHTS" in text or "S-1-3-4" in text)


def is_locked(path):
    """True when the path carries this module's deny rule."""
    _require()
    rc, out = _icacls(str(path))
    return rc == 0 and _locked_block(out)


def status_map(folder, extensions):
    """{save path: locked?} for every save in `folder`, from ONE icacls listing (a per-file call costs ~20 ms, two
    minutes over a 5,000-save folder; matching each line against every name cost 30 s, so the lookup is a dict)."""
    _require()
    names = {e.name.lower(): e.path for e in os.scandir(folder) if e.is_file() and is_member(e.name, extensions)}
    full = {os.path.join(str(folder), n).lower(): p for n, p in names.items()}
    rc, out = _icacls(os.path.join(str(folder), "*"))
    blocks, current = {}, None
    for line in out.splitlines():
        if line and not line[0].isspace():
            low = line.lower()          # "<path> <first rule>": the path may hold spaces, so try each space as its end
            current = next((full[low[:i]] for i, c in enumerate(low) if c == " " and low[:i] in full), None)
            if current:
                blocks[current] = line
        elif current:
            blocks[current] += "\n" + line
    return {p: _locked_block(blocks.get(p, "")) for p in names.values()}


def lock_file(path, sid):
    _require()
    rc, out = _icacls(str(path), "/deny", "*{}:{}".format(sid, FILE_DENY), "/grant", OWNER_RIGHTS + ":(R)")
    if rc:
        raise OSError("lock failed for {}: {}".format(path, out))


def lock_folder(path, sid):
    _require()
    # No (OI)/(CI): these rules apply to the folder object only and are never inherited by new files.
    rc, out = _icacls(str(path), "/deny", "*{}:{}".format(sid, FOLDER_DENY), "/grant", OWNER_RIGHTS + ":(R)")
    if rc:
        raise OSError("lock failed for folder {}: {}".format(path, out))


def strip_lock_aces(sddl, sid):
    """The SDDL with our explicit rules removed: the deny for `sid` and the OWNER RIGHTS grant. Inherited (ID) rules
    and everyone else's stay."""
    def keep(ace):
        parts = ace.split(";")
        if len(parts) < 6 or "ID" in parts[1]:
            return True
        return not ((parts[0] == "D" and parts[5] == sid) or (parts[0] == "A" and parts[5] == "OW"))
    return re.sub(r"\(([^()]*)\)", lambda m: m.group(0) if keep(m.group(1)) else "", sddl)


def _enable_restore_privilege():
    """Switch on SeRestorePrivilege: with it Windows grants WRITE_DAC whatever the DACL says. Only an elevated token
    holds it, so a normal process fails here -- that refusal IS the protection."""
    import ctypes
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", LUID), ("Attributes", wintypes.DWORD)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.c_void_p, ctypes.c_void_p]
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
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    adv.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    adv.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    h = k32.CreateFileW(str(path), 0x00040000 | 0x00020000, 7, None, 3, 0x02000000, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise PermissionError("cannot open {} for a permission change: {}".format(
            path, ctypes.WinError(ctypes.get_last_error())))
    held = []
    try:
        dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
        rc = adv.GetSecurityInfo(h, 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
        if rc:
            raise ctypes.WinError(rc)
        held.append(sd)
        text = wintypes.LPWSTR()
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        old = text.value
        k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        new = edit(old)
        if new == old:
            return False
        sd2 = ctypes.c_void_p()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(new, 1, ctypes.byref(sd2), None):
            raise ctypes.WinError(ctypes.get_last_error())
        held.append(sd2)
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
        for p in held:
            k32.LocalFree(p)
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
    if is_locked(path):
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


def turn_on(config, folders, extensions, rotating=ROTATING, settle=SETTLE_S):
    """Enable locking, lock the switch file itself (an agent cannot edit it off), and lock every folder now.
    Refused from an elevated process: the locks would not hold the agent that is asking."""
    _require()
    if is_elevated():
        raise PermissionError("refusing to switch save locking on from an elevated process: " + ELEVATED_WARNING)
    config = Path(config)
    sid = user_sid()
    if not read_config(config).get("enabled"):
        if config.exists() and is_locked(config):
            raise PermissionError("{} is locked; switch locking off (elevated) first".format(config))
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"enabled": True}) + "\n", encoding="utf-8")
    if not is_locked(config):
        lock_file(config, sid)
    return [(str(f),) + tuple(len(x) for x in lock(f, extensions, rotating, settle)) for f in folders]


def turn_off(config, folders=(), extensions=()):
    """Disable locking AND lift every lock in `folders` (the folder rule and each locked save). Elevated only: an
    agent must not be able to do this. The locks outlast the toolkit -- once it is gone, ordinary tools cannot lift
    them (icacls is refused even elevated) -- so `off` is the way back to a plain folder, and is what a user runs
    before removing the toolkit. If any lock cannot be lifted, the switch stays ON and the failures are raised."""
    _require()
    if not is_elevated():
        raise PermissionError("switching save locking off needs an elevated prompt")
    sid = user_sid()
    config = Path(config)
    lifted, failed = 0, []
    for folder in folders:
        try:
            targets = [p for p, locked in status_map(folder, extensions).items() if locked]
            if is_locked(folder):
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
    if failed:
        raise PermissionError("save locking stays ON: {} lock(s) could not be lifted: {}".format(
            len(failed), "; ".join(failed[:5])))
    if config.exists() and is_locked(config):
        unlock(config, sid)
    config.write_text(json.dumps({"enabled": False}) + "\n", encoding="utf-8")
    return lifted


def auto_lock(state, before, files, folders, now=None, rotating=ROTATING, settle=SETTLE_S, verify=VERIFY_S,
              extensions=None):
    """Lock every settled save not locked yet. `before`/`files` are the sentinel's previous and current snapshots
    ({key: [size, mtime_ns, path]}); `state` (the sentinel's dict) remembers what is locked, so a call locks only new
    or changed saves, and the real ACLs are re-read (one listing per folder) at most every `verify` seconds.
    Returns the paths that could not be locked."""
    _require()
    now = time.time() if now is None else now
    sid = user_sid()
    locked = state.setdefault("locked", {})
    if now - state.get("lock_verified", 0) > verify:
        locked.clear()
        exts = extensions or tuple({os.path.splitext(p)[1].lower() for _, _, p in files.values()}) or (".none",)
        for folder in folders:
            try:
                real = {os.path.normcase(p) for p, ok in status_map(folder, exts).items() if ok}
                locked.update({k: True for k, (_, _, p) in files.items() if os.path.normcase(p) in real})
                if not is_locked(folder):
                    lock_folder(folder, sid)
            except OSError:
                continue
        state["lock_verified"] = now
    failed = []
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
