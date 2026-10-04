"""Plan 6 Task 3 C10: an in-process (ctypes, stdlib-only) apply/remove of the four mechanisms,
so the session-start relock does not pay one process spawn per file.

Same semantics as lockprobe.py (bit first, then the deny ACE; remove the ACE first, then the bit),
same mask whitelist, same sandbox assertion. Its result is checked against icacls' reading of the
DACL (an independent parser) by the caller.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
import lockprobe  # noqa: E402

BITS = {"WD": 0x2, "AD": 0x4, "WEA": 0x10, "WA": 0x100}
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x4
UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
DENY_ACCESS = 3
NO_INHERITANCE = 0
TRUSTEE_IS_SID = 0
TRUSTEE_IS_USER = 1
ACCESS_DENIED_ACE_TYPE = 1
INHERITED_ACE = 0x10
FILE_ATTRIBUTE_READONLY = 0x1

adv = ctypes.WinDLL("advapi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class TRUSTEE_W(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("ptstrName", ctypes.c_void_p)]


class EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wt.DWORD), ("grfAccessMode", ctypes.c_int),
                ("grfInheritance", wt.DWORD), ("Trustee", TRUSTEE_W)]


class ACE_HEADER(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wt.WORD)]


class ACCESS_DENIED_ACE(ctypes.Structure):
    _fields_ = [("Header", ACE_HEADER), ("Mask", wt.DWORD), ("SidStart", wt.DWORD)]


class ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [("AceCount", wt.DWORD), ("AclBytesInUse", wt.DWORD), ("AclBytesFree", wt.DWORD)]


adv.GetNamedSecurityInfoW.argtypes = [wt.LPCWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p)]
adv.SetNamedSecurityInfoW.argtypes = [wt.LPWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p]
adv.SetEntriesInAclW.argtypes = [wt.ULONG, ctypes.POINTER(EXPLICIT_ACCESS_W), ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p)]
adv.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
adv.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_int]
adv.GetAce.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]
adv.DeleteAce.argtypes = [ctypes.c_void_p, wt.DWORD]
adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
k32.LocalFree.argtypes = [ctypes.c_void_p]
k32.GetFileAttributesW.argtypes = [wt.LPCWSTR]
k32.GetFileAttributesW.restype = wt.DWORD
k32.SetFileAttributesW.argtypes = [wt.LPCWSTR, wt.DWORD]

_SID = None


def sid_ptr():
    global _SID
    if _SID is None:
        p = ctypes.c_void_p()
        if not adv.ConvertStringSidToSidW(lockprobe.user_sid(), ctypes.byref(p)):
            raise ctypes.WinError(ctypes.get_last_error())
        _SID = p
    return _SID


def _attr(path, on):
    a = k32.GetFileAttributesW(path)
    if a == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    new = (a | FILE_ATTRIBUTE_READONLY) if on else (a & ~FILE_ATTRIBUTE_READONLY)
    if new != a and not k32.SetFileAttributesW(path, new or 0x80):
        raise ctypes.WinError(ctypes.get_last_error())


def _get_dacl(path):
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    rc = adv.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None,
                                   ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc:
        raise ctypes.WinError(rc)
    return dacl, sd


def _set_dacl(path, dacl):
    rc = adv.SetNamedSecurityInfoW(path, SE_FILE_OBJECT,
                                   DACL_SECURITY_INFORMATION | UNPROTECTED_DACL_SECURITY_INFORMATION,
                                   None, None, dacl, None)
    if rc:
        raise ctypes.WinError(rc)


def apply(mech, path):
    target = os.path.normpath(sandbox.assert_lockable(path))
    ro, mask = lockprobe.MECHANISMS[mech]
    if ro:
        _attr(target, True)
    if mask:
        lockprobe.check_mask(mask)
        bits = 0
        for t in mask:
            bits |= BITS[t]
        ea = EXPLICIT_ACCESS_W()
        ea.grfAccessPermissions, ea.grfAccessMode, ea.grfInheritance = bits, DENY_ACCESS, NO_INHERITANCE
        ea.Trustee.TrusteeForm, ea.Trustee.TrusteeType, ea.Trustee.ptstrName = TRUSTEE_IS_SID, TRUSTEE_IS_USER, sid_ptr()
        dacl, sd = _get_dacl(target)
        new = ctypes.c_void_p()
        try:
            rc = adv.SetEntriesInAclW(1, ctypes.byref(ea), dacl, ctypes.byref(new))
            if rc:
                raise ctypes.WinError(rc)
            _set_dacl(target, new)
        finally:
            k32.LocalFree(new)
            k32.LocalFree(sd)


def remove(path):
    target = os.path.normpath(sandbox.assert_inside(path))
    dacl, sd = _get_dacl(target)
    try:
        info = ACL_SIZE_INFORMATION()
        if not adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        removed = 0
        for i in reversed(range(info.AceCount)):
            pace = ctypes.c_void_p()
            if not adv.GetAce(dacl, i, ctypes.byref(pace)):
                raise ctypes.WinError(ctypes.get_last_error())
            ace = ctypes.cast(pace, ctypes.POINTER(ACCESS_DENIED_ACE)).contents
            if ace.Header.AceType != ACCESS_DENIED_ACE_TYPE or ace.Header.AceFlags & INHERITED_ACE:
                continue
            sid_addr = pace.value + ACCESS_DENIED_ACE.SidStart.offset
            if adv.EqualSid(ctypes.c_void_p(sid_addr), sid_ptr()):
                if not adv.DeleteAce(dacl, i):
                    raise ctypes.WinError(ctypes.get_last_error())
                removed += 1
        if removed:
            _set_dacl(target, dacl)
    finally:
        k32.LocalFree(sd)
    _attr(target, False)
    return removed
