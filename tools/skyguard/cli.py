"""The `skyguard` command line: `lock`, `guards` and `doctor` (Plan 6 Task 8).

    python tools/skyguard lock <lock|unlock [file...]|status [--json]|disable|enable> [--root R] [--session S]
    python tools/skyguard guards <on|off|status>      shadow mode ships with the engine work (Task 9)
    python tools/skyguard doctor [--root R] [--json]  the hook health report (tools/skyguard/health.py)

Exit codes follow the X4 contract: 0 verified, 1 partial or disagreeing, 2 refused or unreadable, 3 unsupported.
"""
from __future__ import annotations

import sys

USAGE = "usage: skyguard <lock|guards|doctor> ... (see tools/skyguard/cli.py)"


def main(argv=None, out=None) -> int:
    out = sys.stdout if out is None else out
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=out)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "lock":
        from . import lock
        return lock.main(rest, out)
    if cmd == "guards":
        print("guards: not available in this build (shadow mode ships with the engine work).", file=out)
        return 3
    if cmd == "doctor":
        from . import health
        from . import lock as _lock
        if "--root" not in rest:
            rest = rest + ["--root", _lock.default_root()]
        try:
            return health.main(["report"] + rest)
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 2
    print(USAGE, file=out)
    return 2
