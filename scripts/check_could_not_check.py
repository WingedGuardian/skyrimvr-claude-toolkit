#!/usr/bin/env python3
"""Fail when a pytest `-rs` log lists a COULD NOT CHECK skip nobody allowed.

    check_could_not_check.py <pytest-output.log> [--allow SUBSTRING ...]

A COULD NOT CHECK skip means a test did not run, and a green run with one is a claim
the suite did not earn. The only expected one is the Docker daemon being absent; every
other one must be named with --allow, in the CI step, where a reviewer sees it.

Exit 0 clean / 1 an unallowed skip / 2 nothing could be checked (no pytest summary, or skips
counted but no -rs section to read them from).
"""
import re
import sys


def main(argv):
    args = argv[1:]
    if not args:
        print("usage: check_could_not_check.py <log> [--allow SUBSTRING ...]", file=sys.stderr)
        return 2
    path, allow, i = args[0], [], 1
    while i < len(args):
        if args[i] == "--allow" and i + 1 < len(args):
            allow.append(args[i + 1])
            i += 2
        else:
            print("unknown argument: " + args[i], file=sys.stderr)
            return 2
    # An `if: always()` step after a failed suite step reads a log nobody wrote: say so, not a traceback.
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as e:
        print("COULD NOT CHECK: cannot read %s (%s) -- did the step that writes it run?" % (path, e.strerror))
        return 2
    if not any(re.search(r"\b\d+ (passed|failed|skipped|error)", line) for line in lines):
        print("COULD NOT CHECK: no pytest summary line in " + path)
        return 2
    skipped = sum(int(m.group(1)) for line in lines for m in [re.search(r"(\d+) skipped", line)] if m)
    if skipped and not any("short test summary info" in line for line in lines):
        print("COULD NOT CHECK: %d skip(s) counted but there is no 'short test summary' section "
              "-- run pytest with -rs so they can be read" % skipped)
        return 2
    bad = [line for line in lines
           if line.startswith("SKIPPED") and "COULD NOT CHECK" in line
           and not any(a in line for a in allow)]
    for line in bad:
        print("UNALLOWED COULD NOT CHECK skip: " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
