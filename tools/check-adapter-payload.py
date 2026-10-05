#!/usr/bin/env python3
"""check-adapter-payload -- does a toolkit payload carry the adapter kit?

    python tools/check-adapter-payload.py <payload root>

Run by the release workflow on the folder it zips, and by tools/verify-release.sh on the zip a user
downloads. The zip is built by rule, so these files ship today with no list; this makes a rule change
that drops one fail the release instead of leaving an unsupported agent with no ADAPTING.md and no way
to tell. `tests/` is not shipped: the toy-agent test is a CI gate, not a bundle member.

Exit 0 = every path present and non-empty, 1 = at least one missing (each named), 2 = bad usage.
"""
from __future__ import annotations

import sys
from pathlib import Path

REQUIRED = (
    "ADAPTING.md",
    "tools/adapting/adapter_template.py",
    "tools/adapting/capability-report.template.json",
    "tools/adapter_conformance.py",
    "tools/inventory-runtimes.py",
    "tools/skyguard/neutral.py",
    ".claude/skills/toolkit-setup/SKILL.md",
    ".agents/skills/toolkit-setup/SKILL.md",
)


def checks(root: Path):
    """Yield (path, problem-or-None) for every required path."""
    for rel in REQUIRED:
        p = root / rel
        if not p.is_file():
            yield rel, "MISSING"
        elif p.stat().st_size == 0:
            yield rel, "EMPTY"
        else:
            yield rel, None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        sys.stderr.write("usage: check-adapter-payload.py <payload root>\n")
        return 2
    fails = 0
    for rel, problem in checks(Path(argv[0])):
        print("  {:<58} {}".format(rel, "ok" if problem is None else "!! " + problem))
        fails += problem is not None
    print("ADAPTER PAYLOAD: " + ("OK" if not fails else "{} CHECK(S) FAILED".format(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
