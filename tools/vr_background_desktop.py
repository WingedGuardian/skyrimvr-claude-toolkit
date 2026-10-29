#!/usr/bin/env python3
"""Process isolation for headless Skyrim VR: start owned processes on a private desktop. Windows only.

Used by tools/vr-headless.py (`launch`, `run`). It creates processes and reads window ownership; it never
switches the user's input desktop, activates a window, injects keyboard/mouse input or captures the cursor.
Runtime control of the game goes through DevBench.

    python tools/vr_background_desktop.py --probe <new-evidence.json>

The probe starts one disposable window on a fresh private desktop and proves it stayed there (no Steam, no
SteamVR, no game). Run it once before trusting the background desktop on a new machine.

Two placements:
  * BackgroundDesktop -- a new desktop on WinSta0 that is never shown. It is created WITHOUT the
    DESKTOP_SWITCHDESKTOP right, and nothing here can switch to it. The user keeps working undisturbed.
  * CurrentDesktop -- the user's own input desktop, starting windows with SW_SHOWNOACTIVATE. That is a request
    at startup, not a guarantee: SteamVR or Skyrim may still activate themselves later. Agree that with the user.

Bound with ctypes to the documented Win32 calls (CreateDesktopW, OpenInputDesktop, GetUserObjectInformationW,
EnumDesktopWindows, CreateProcessW with STARTUPINFOW.lpDesktop, OpenProcess, GetProcessTimes, TerminateProcess),
so the toolkit needs no pywin32.
"""
from __future__ import annotations

import ctypes
import datetime
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

DESKTOP_READOBJECTS, DESKTOP_CREATEWINDOW, DESKTOP_CREATEMENU, DESKTOP_HOOKCONTROL = 0x1, 0x2, 0x4, 0x8
DESKTOP_ENUMERATE, DESKTOP_WRITEOBJECTS = 0x40, 0x80
TH32CS_SNAPTHREAD = 0x00000004
UOI_NAME = 2
STARTF_USESHOWWINDOW = 0x1
CREATE_NO_WINDOW = 0x08000000
PROCESS_TERMINATE, PROCESS_QUERY_LIMITED_INFORMATION, SYNCHRONIZE = 0x1, 0x1000, 0x100000
NAME_PREFIX = "SkyrimVRHeadless_"
_EPOCH_1601 = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


class LaunchIdentityError(OSError):
    """CreateProcess succeeded, but the launched identity or placement could not be recorded."""


def _api():
    """user32/kernel32 with argument types declared (Windows only; imported lazily so the module loads anywhere)."""
    from ctypes import wintypes as w
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sig = {
        (user32, "CreateDesktopW"): (w.HANDLE, [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p]),
        (user32, "OpenInputDesktop"): (w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
        (user32, "CloseDesktop"): (w.BOOL, [w.HANDLE]),
        (user32, "GetThreadDesktop"): (w.HANDLE, [w.DWORD]),
        (user32, "GetUserObjectInformationW"): (w.BOOL, [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
                                                         ctypes.POINTER(w.DWORD)]),
        (user32, "GetWindowThreadProcessId"): (w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
        (kernel32, "OpenProcess"): (w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
        (kernel32, "CloseHandle"): (w.BOOL, [w.HANDLE]),
        (kernel32, "TerminateProcess"): (w.BOOL, [w.HANDLE, ctypes.c_uint]),
        (kernel32, "GetProcessTimes"): (w.BOOL, [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4),
        (kernel32, "GetCurrentThreadId"): (w.DWORD, []),
        (kernel32, "WaitForSingleObject"): (w.DWORD, [w.HANDLE, w.DWORD]),
        (kernel32, "GetExitCodeProcess"): (w.BOOL, [w.HANDLE, ctypes.POINTER(w.DWORD)]),
    }
    for (dll, name), (res, args) in sig.items():
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = res, args
    return user32, kernel32, w


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return ok


def _object_name(user32, w, handle) -> str:
    buf = ctypes.create_unicode_buffer(256)
    need = w.DWORD(0)
    _check(user32.GetUserObjectInformationW(handle, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(need)))
    return buf.value


def input_desktop() -> str:
    """The name of the desktop that currently receives the user's input (read-only)."""
    user32, _, w = _api()
    handle = _check(user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS))
    try:
        return _object_name(user32, w, handle)
    finally:
        user32.CloseDesktop(handle)


def process_thread_ids(pid) -> list:
    """Every thread id of `pid`, from one Toolhelp32 snapshot (no PowerShell: its startup alone can exceed any short
    timeout on a loaded machine, and a timed-out listing must not read as "not on this desktop"). OSError when the
    snapshot cannot be taken."""
    from ctypes import wintypes as w

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                    ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG),
                    ("dwFlags", w.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype, kernel32.CreateToolhelp32Snapshot.argtypes = w.HANDLE, [w.DWORD, w.DWORD]
    for name in ("Thread32First", "Thread32Next"):
        fn = getattr(kernel32, name)
        fn.restype, fn.argtypes = w.BOOL, [w.HANDLE, ctypes.POINTER(THREADENTRY32)]
    kernel32.CloseHandle.restype, kernel32.CloseHandle.argtypes = w.BOOL, [w.HANDLE]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snap or snap == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())  # no snapshot: unknown, never "no threads"
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(THREADENTRY32)
        tids = []
        ok = kernel32.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID == int(pid):
                tids.append(entry.th32ThreadID)
            ok = kernel32.Thread32Next(snap, ctypes.byref(entry))
        return tids
    finally:
        kernel32.CloseHandle(snap)


def thread_desktop(thread_id) -> str:
    user32, _, w = _api()
    handle = _check(user32.GetThreadDesktop(thread_id))   # borrowed: never closed
    return _object_name(user32, w, handle)


def _filetime(ft) -> datetime.datetime:
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return _EPOCH_1601 + datetime.timedelta(microseconds=ticks // 10)


def parse_start(stamp: str) -> datetime.datetime:
    """A PowerShell round-trip ('o') UTC start time, e.g. 2026-10-16T14:00:00.1234567Z (7 digits; Python takes 6)."""
    stamp = re.sub(r"(\.\d{6})\d+(?=Z$)", r"\1", stamp).replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(stamp)


def process_generation(pid):
    """(creation time, still running) for a live PID, or None when no such process can be opened."""
    _, kernel32, w = _api()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:     # ERROR_INVALID_PARAMETER: no such PID
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times = [w.FILETIME() for _ in range(4)]
        _check(kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]))
        status = kernel32.WaitForSingleObject(handle, 0)
        if status not in (0, 0x102):
            raise ctypes.WinError(ctypes.get_last_error())
        running = status == 0x102     # WAIT_TIMEOUT: not signalled = running
        return _filetime(times[0]), running
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid, start_time: str) -> bool:
    """True only when this PID is running AND is the same process generation (creation time) as recorded."""
    gen = process_generation(pid)
    if gen is None:
        return False
    created, running = gen
    return running and abs((created - parse_start(start_time)).total_seconds()) <= 0.001


def terminate_verified(pid, start_time: str) -> None:
    """Terminate one owned process, re-checking its creation time on the OPENED handle first (PID reuse)."""
    _, kernel32, w = _api()
    handle = _check(kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)))
    try:
        times = [w.FILETIME() for _ in range(4)]
        _check(kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]))
        if abs((_filetime(times[0]) - parse_start(start_time)).total_seconds()) > 0.001:
            raise RuntimeError("PID {} was reused by another process; refusing to terminate it".format(pid))
        _check(kernel32.TerminateProcess(handle, 0))
    finally:
        kernel32.CloseHandle(handle)


def _window_owners(handle, wanted) -> list:
    """PIDs in `wanted` that own a window on this desktop. Read-only: no activation, no input."""
    user32, _, w = _api()
    found = set()
    proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def collect(hwnd, _unused):
        pid = w.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in wanted:
            found.add(pid.value)
        return True
    callback = proto(collect)
    enum = user32.EnumDesktopWindows
    enum.argtypes, enum.restype = [w.HANDLE, proto, w.LPARAM], w.BOOL
    ctypes.set_last_error(0)
    ok = enum(handle, callback, 0)
    error = ctypes.get_last_error()
    if not ok and error:      # a desktop with no windows returns FALSE with ERROR_SUCCESS: that is not an error
        raise ctypes.WinError(error)
    return sorted(found)


class BackgroundDesktop:
    show_window = 0           # SW_HIDE; the private desktop is never displayed

    def __init__(self, name=None):
        user32, _, _ = _api()
        self.name = name or (NAME_PREFIX + uuid.uuid4().hex)
        if not re.fullmatch(re.escape(NAME_PREFIX) + r"[A-Za-z0-9_]+", self.name):
            raise ValueError("a dedicated headless desktop name is required, got {!r}".format(self.name))
        self.original_input = input_desktop()
        self._launch_handles = {}
        access = (DESKTOP_READOBJECTS | DESKTOP_WRITEOBJECTS | DESKTOP_CREATEWINDOW | DESKTOP_CREATEMENU |
                  DESKTOP_ENUMERATE | DESKTOP_HOOKCONTROL)        # deliberately no DESKTOP_SWITCHDESKTOP
        self.handle = _check(user32.CreateDesktopW(self.name, None, None, 0, access, None))
        self.verify()

    def verify(self):
        active = input_desktop()
        if active != self.original_input or active == self.name:
            raise RuntimeError("the input desktop changed; refusing to start a headless process")

    def start(self, command, cwd):
        """Start `command`; retain its process handle until close() to bind the launcher lifetime."""
        from ctypes import wintypes as w
        self.verify()
        _, kernel32, _ = _api()
        exe = str(Path(command[0]).resolve())
        if not Path(exe).is_file():
            raise FileNotFoundError(exe)

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                        ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                        ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                        ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                        ("hStdError", w.HANDLE)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                        ("dwThreadId", w.DWORD)]
        desktop = ctypes.create_unicode_buffer("WinSta0\\" + self.name)
        si = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW), lpDesktop=ctypes.cast(desktop, w.LPWSTR),
                          dwFlags=STARTF_USESHOWWINDOW, wShowWindow=self.show_window)
        pi = PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([exe, *map(str, command[1:])]))
        create = kernel32.CreateProcessW
        create.restype = w.BOOL
        _check(create(exe, cmdline, None, None, False, CREATE_NO_WINDOW, None, str(cwd), ctypes.byref(si),
                      ctypes.byref(pi)))
        retained = False
        try:
            times = [w.FILETIME() for _ in range(4)]
            _check(kernel32.GetProcessTimes(pi.hProcess, *[ctypes.byref(t) for t in times]))
            created = _filetime(times[0]).isoformat()
            record = {"pid": pi.dwProcessId, "main_thread": pi.dwThreadId, "desktop": self.name,
                      "executable": exe, "start_time": created}
            self._launch_handles[pi.dwProcessId] = (pi.hProcess, record)
            retained = True
        except Exception as exc:
            raise LaunchIdentityError("a launcher was created but its identity is unreadable") from exc
        finally:
            kernel32.CloseHandle(pi.hThread)
            if not retained:
                kernel32.CloseHandle(pi.hProcess)
        try:
            self.verify()
        except Exception as exc:
            raise LaunchIdentityError("a launcher was created but its placement is unproved") from exc
        return pi.dwProcessId, record

    def launch_lifetime(self, pid):
        """Bound a launched PID using its still-open CreateProcess handle, including after exit."""
        _, kernel32, w = _api()
        handle, record = self._launch_handles[int(pid)]
        observed = datetime.datetime.now(datetime.timezone.utc)
        status = kernel32.WaitForSingleObject(handle, 0)
        if status not in {0, 0x102}:
            raise ctypes.WinError(ctypes.get_last_error())
        times = [w.FILETIME() for _ in range(4)]
        _check(kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]))
        created = _filetime(times[0])
        if created != parse_start(record["start_time"]):
            raise OSError("launcher creation time does not match the retained handle")
        exited = status == 0 and bool(times[1].dwLowDateTime or times[1].dwHighDateTime)
        if status == 0 and not exited:
            raise OSError("launcher is signalled but its exit time is unreadable")
        return dict(record, exit_time=_filetime(times[1]).isoformat() if exited else None,
                    observed_until=observed.isoformat())

    def process_on_desktop(self, pid):
        """Prove placement even for windowless helpers; a missing/unreadable thread is not proof."""
        try:
            tids = process_thread_ids(pid)
            if not tids:
                return False
            for tid in tids:
                try:
                    name = thread_desktop(tid)
                except OSError:
                    continue  # no user32 desktop: neutral; ancestry is proved by the caller
                if name != self.name:
                    return False
            return True
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            return False

    def close(self):
        for handle, _record in self._launch_handles.values():
            _api()[1].CloseHandle(handle)
        self._launch_handles.clear()
        if self.handle:
            _api()[0].CloseDesktop(self.handle)
            self.handle = None

    def boundary(self, pids):
        """Which of `pids` own windows here and on the user's input desktop. isolated = none on the user's."""
        user32, _, _ = _api()
        wanted = set(pids)
        user = _check(user32.OpenInputDesktop(0, False, DESKTOP_ENUMERATE | DESKTOP_READOBJECTS))
        try:
            visible = _window_owners(user, wanted)
        finally:
            user32.CloseDesktop(user)
        self.verify()
        return {"desktop": self.name, "input_desktop": input_desktop(),
                "private_window_pids": _window_owners(self.handle, wanted), "shared_desktop_window_pids": visible,
                "isolated": not visible}


class CurrentDesktop(BackgroundDesktop):
    """Owned processes on the user's current input desktop, without any frontend or desktop input."""
    show_window = 4           # SW_SHOWNOACTIVATE

    def __init__(self, name=None):
        user32, _, _ = _api()
        self.original_input = input_desktop()
        self.name = name or self.original_input
        self._launch_handles = {}
        if self.name != self.original_input:
            raise RuntimeError("the selected desktop is no longer the input desktop")
        self.handle = _check(user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS | DESKTOP_ENUMERATE))
        self.verify()

    def verify(self):
        if input_desktop() != self.original_input:
            raise RuntimeError("the input desktop changed; refusing a current-desktop launch")

    def boundary(self, pids):
        result = super().boundary(pids)
        result.update(mode="current-desktop", isolated=False,
                      on_selected_desktop=result["private_window_pids"] == result["shared_desktop_window_pids"])
        return result


def probe(output):
    """Prove isolation with a disposable window before trusting the private desktop with Steam or Skyrim."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("refusing to overwrite probe evidence: {}".format(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    desktop = BackgroundDesktop()
    try:
        pid, _ = desktop.start([sys.executable, str(Path(__file__).resolve()), "--probe-child", desktop.name,
                                str(output)], Path(__file__).parent)
        deadline = time.monotonic() + 12
        while not output.exists():
            if time.monotonic() > deadline:
                raise TimeoutError("the probe did not report its isolated window")
            time.sleep(0.05)
        boundary = desktop.boundary([pid])
        if pid not in boundary["private_window_pids"] or not boundary["isolated"]:
            raise RuntimeError("the probe window was not isolated: {}".format(boundary))
        deadline = time.monotonic() + 15
        while process_generation(pid) and process_generation(pid)[1]:
            if time.monotonic() > deadline:
                raise TimeoutError("the probe did not finish")
            time.sleep(0.1)
        evidence = json.loads(output.read_text(encoding="utf-8"))
        if not (evidence["thread_desktop"] == desktop.name and
                evidence["input_desktop_before"] == evidence["input_desktop_after"] == desktop.original_input):
            raise RuntimeError("probe evidence does not show isolation: {}".format(evidence))
        evidence["boundary_while_window_alive"] = boundary
        output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        desktop.verify()
        return evidence
    finally:
        desktop.close()


def probe_child(expected, output):
    _, kernel32, _ = _api()
    actual = thread_desktop(kernel32.GetCurrentThreadId())
    before = input_desktop()
    if actual != expected or before == expected:        # never create even the probe window unless attached right
        raise RuntimeError("probe not isolated; no window created")
    import tkinter
    window = tkinter.Tk()
    window.title("Headless isolation probe")
    window.geometry("160x60")
    window.update()
    Path(output).write_text(json.dumps({"thread_desktop": actual, "input_desktop_before": before,
                                        "input_desktop_after": input_desktop(),
                                        "window_created_on_private_desktop": True}, indent=2) + "\n",
                            encoding="utf-8")
    time.sleep(2)
    window.destroy()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--probe-child":
        probe_child(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "--probe":
        print(json.dumps(probe(sys.argv[2]), indent=2))
    else:
        sys.exit("usage: python tools/vr_background_desktop.py --probe <new-evidence.json>")
