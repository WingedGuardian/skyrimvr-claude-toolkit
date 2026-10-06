"""Deferred row 26 (Plan 8 T6): end a deliberately spinning test process by itself, whatever the test does.

The catastrophic-regex test plants `re.match(r'(a+)+$', 'aaa...b')` in a copy of run.py -- minutes to hours of CPU in
one C call that holds the GIL. Its mutant (`engine-watchdog-disarmed`) removes the only thing that ends it, so a test
timeout killed the bash wrapper and left a native python.exe spinning a core, orphaned. The planted code starts this
reaper first:

    python spin_reaper.py <pid> <seconds> [<marker file>]

It opens the target by HANDLE (Windows) at once, so a reused pid can never be the one it ends, waits up to <seconds>,
and terminates the target if it is still running (exit code 9). On POSIX it is the target's child and polls its
parent: once reparented, the target is gone and it does nothing. It writes <marker file> when armed, so a test can
prove it ran. Stdlib only; its own I/O is detached so it never holds the hook's output pipe open.
"""
from __future__ import annotations

import os
import sys
import time


def _windows(pid: int, seconds: float) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    synchronize, terminate = 0x00100000, 0x0001
    h = k32.OpenProcess(synchronize | terminate, False, pid)
    if not h:
        return "gone"
    try:
        if k32.WaitForSingleObject(h, int(seconds * 1000)) == 0x102:      # WAIT_TIMEOUT: still spinning
            k32.TerminateProcess(h, 9)
            return "terminated"
        return "exited"
    finally:
        k32.CloseHandle(h)


def _posix(pid: int, seconds: float) -> str:
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
    pid, seconds = int(argv[1]), float(argv[2])
    marker = argv[3] if len(argv) > 3 else ""
    if marker:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("armed {}\n".format(pid))
    what = _windows(pid, seconds) if os.name == "nt" else _posix(pid, seconds)
    if marker:
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write(what + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
