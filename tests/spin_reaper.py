"""Deferred row 26 (Plan 8 T6): end a deliberately spinning test process by itself, whatever the test does.

The catastrophic-regex test plants `re.match(r'(a+)+$', 'aaa...b')` in a copy of run.py -- minutes to hours of CPU in
one C call that holds the GIL. Its mutant (`engine-watchdog-disarmed`) removes the only thing that ends it, so a test
timeout killed the bash wrapper and left a native python.exe spinning a core, orphaned. The planted code calls
`arm(seconds, marker)` first, in the process that is about to spin.

The reaper never looks its target up by pid (fix round 1, review M1: a pid can be reused by another process once the
target has exited, and the reaper might start only after that). `arm` runs IN THE TARGET and hands the reaper a
reference to the target itself, made before the reaper exists:
  - Windows: an inheritable process handle (SYNCHRONIZE | PROCESS_TERMINATE), passed in a handle list. The handle
    keeps the process object alive, so it can only ever name the target.
  - Linux: a pidfd (os.pidfd_open, Linux 5.3+), passed with pass_fds; the kill goes through it.
  - Other POSIX: the reaper is the target's child and checks os.getppid() before the kill. A residual window remains
    between that check and the kill; no supported test platform takes this branch (CI is Linux, development Windows).
The reaper waits up to <seconds> and ends the target (exit 9 / SIGKILL) only if it is still running. It writes
`armed <how>` to the marker when it starts and `exited` or `terminated` when it is done, so a test can prove it ran.
Stdlib only; its own I/O is detached so it never holds the hook's output pipe open.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

SYNCHRONIZE, PROCESS_TERMINATE = 0x00100000, 0x0001
WAIT_TIMEOUT = 0x102


def _k32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k32


def arm(seconds: float, marker: str = "") -> None:
    """Called in the process that is about to spin: start the reaper with a reference to THIS process."""
    me = [sys.executable, os.path.abspath(__file__), "", str(seconds), marker]
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        k32 = _k32()
        h = k32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, True, os.getpid())       # inheritable
        if not h:
            raise OSError("could not open a handle to this process")
        me[2] = "handle=%d" % h
        si = subprocess.STARTUPINFO()
        si.lpAttributeList = {"handle_list": [h]}                                   # only this handle is inherited
        subprocess.Popen(me, startupinfo=si, close_fds=True, **quiet)
        k32.CloseHandle(h)          # the reaper holds its own inherited copy
    elif hasattr(os, "pidfd_open"):
        fd = os.pidfd_open(os.getpid())
        me[2] = "pidfd=%d" % fd
        subprocess.Popen(me, pass_fds=(fd,), **quiet)
        os.close(fd)
    else:
        me[2] = "ppid=%d" % os.getpid()
        subprocess.Popen(me, **quiet)


def _windows(handle: int, seconds: float) -> str:
    k32 = _k32()
    try:
        if k32.WaitForSingleObject(handle, int(seconds * 1000)) == WAIT_TIMEOUT:      # still spinning
            k32.TerminateProcess(handle, 9)
            return "terminated"
        return "exited"
    finally:
        k32.CloseHandle(handle)


def _pidfd(fd: int, seconds: float) -> str:
    import select
    import signal
    try:
        ready, _, _ = select.select([fd], [], [], seconds)      # a pidfd is readable once the process has exited
        if ready:
            return "exited"
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        return "terminated"
    finally:
        os.close(fd)


def _ppid(pid: int, seconds: float) -> str:
    import signal
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if os.getppid() != pid:
            return "exited"
        time.sleep(0.1)
    if os.getppid() != pid:
        return "exited"
    os.kill(pid, signal.SIGKILL)
    return "terminated"


def main(argv) -> int:
    how, seconds = argv[1], float(argv[2])
    marker = argv[3] if len(argv) > 3 else ""
    kind, _, value = how.partition("=")
    if marker:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("armed {}\n".format(kind))
    run = {"handle": _windows, "pidfd": _pidfd, "ppid": _ppid}[kind]
    what = run(int(value), seconds)
    if marker:
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write(what + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
