"""Plan 6 Phase A: the sandbox guard every mutating spike call goes through.

An attribute or an ACL belongs to the FILE OBJECT, not to the path. A hardlinked
"copy", a junction, or a path that resolves through `..` would put a lock or a
deny ACE on the user's real game files. So every lock, unlock, ACL or write
probe first calls `assert_inside()` on the RESOLVED path, and every lock call
also calls `assert_lockable()` (single link, not a real-install file ID).

`SandboxViolation` derives from BaseException on purpose: an `except Exception`
anywhere in a probe must not be able to swallow it (X4 pattern).
"""

from __future__ import annotations

import json
import os
import sys

ENV = "SKYGUARD_LOCK_SANDBOX"
BASELINE = "C:/Temp/skyguard-ml2/real-baseline.json"


class SandboxViolation(BaseException):
    """A mutating call aimed outside the scratch sandbox. Never caught."""


def _strip_prefix(p: str) -> str:
    for pre in ("\\\\?\\UNC\\", "\\\\?\\", "//?/"):
        if p.startswith(pre):
            return ("\\\\" + p[len(pre):]) if "UNC" in pre else p[len(pre):]
    return p


def resolve(path) -> str:
    """realpath (follows symlinks AND junctions on 3.8+), prefix stripped, case-folded."""
    real = os.path.realpath(os.fspath(path))
    real = _strip_prefix(real)
    return os.path.normcase(os.path.normpath(real))


def sandbox_root() -> str:
    raw = os.environ.get(ENV, "")
    if not raw.strip():
        raise SandboxViolation("%s is unset: refusing every mutating call" % ENV)
    root = resolve(raw)
    if os.path.dirname(root) == root:
        raise SandboxViolation("sandbox root is a drive root: %s" % root)
    return root


def assert_inside(path) -> str:
    """Return the resolved path if it is STRICTLY inside the sandbox, else raise."""
    root = sandbox_root()
    target = resolve(path)
    if target == root:
        raise SandboxViolation("target is the sandbox root itself: %s" % target)
    try:
        common = os.path.commonpath([root, target])
    except ValueError:  # different drives
        raise SandboxViolation("target on another drive: %s" % target)
    if common != root:
        raise SandboxViolation("target resolves outside the sandbox: %s -> %s" % (path, target))
    return target


def _real_ids():
    try:
        with open(BASELINE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    return {(f["st_dev"], f["st_ino"]) for f in data.get("files", [])}


def assert_lockable(path) -> str:
    """Sandbox check + single-link + not the file object of any baselined real file."""
    target = assert_inside(path)
    st = os.stat(target)
    if st.st_nlink != 1:
        raise SandboxViolation("st_nlink=%d (a hardlink may be a real file): %s" % (st.st_nlink, target))
    if (st.st_dev, st.st_ino) in _real_ids():
        raise SandboxViolation("same file ID as a real-install file: %s" % target)
    return target


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(assert_inside(arg))
