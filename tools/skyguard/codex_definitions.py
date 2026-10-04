#!/usr/bin/env python3
"""codex_definitions -- the ONE renderer of the Codex hook definitions (agent/targets/codex/hooks.json).

    python tools/skyguard/codex_definitions.py render --template T --root-win W --root-posix P --out O

Fills `{{CODEX_ROOT}}` (the install root, Windows spelling, forward slashes) and `{{CODEX_ROOT_POSIX}}`.
Codex hashes the rendered command text, so this rendering is part of the frozen surface: a change to how a
root is normalised (slashes, a trailing slash, case) changes the hash on every machine and silently stops
every approved hook (E18). tests/test_codex_definitions.py pins it against the hashes Codex 0.160.0 itself
reported. setup.sh (Plan 4 Task 11) must call THIS, never re-implement it with sed -- that is the Task 11
contract.

Roots it refuses (exit 2, the reason on stderr), because the hooks could not run there or the JSON could
not hold them -- and a hook that cannot start lets the command RUN:
  %  cmd.exe expands %NAME% inside the quoted -File path of commandWindows
  !  delayed expansion (when enabled) expands !NAME! the same way
  $ `  bash expands them inside the double-quoted path of `command`
  "  ends the quoted path (and the JSON string)
  \\  in the POSIX root; Windows roots have every backslash turned into `/`
Case is kept as given: Codex keys and hashes the text it reads.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BAD_ROOT_CHARS = '%!$`"'
PLACEHOLDERS = ("{{CODEX_ROOT_POSIX}}", "{{CODEX_ROOT}}")


class RootRefused(ValueError):
    pass


def root_problem(root: str):
    """A sentence if the hooks cannot run from this root, else None."""
    bad = sorted({c for c in str(root) if c in BAD_ROOT_CHARS})
    if bad:
        return ("the install root contains {} -- the Codex hook command would be expanded or cut there, so the "
                "hooks cannot run (and a hook that cannot start lets the command RUN). Move the toolkit to a "
                "folder without % ! $ ` \" and re-approve the hooks.".format(" ".join(repr(c) for c in bad)))
    return None


def normalise_win(root: str) -> str:
    r = str(root).replace(chr(92), "/")
    if not re.match(r"^[A-Za-z]:/", r + "/"):
        raise RootRefused("the Windows root is not an absolute drive path: {!r}".format(root))
    r = r.rstrip("/")
    return r


def normalise_posix(root: str) -> str:
    r = str(root)
    if chr(92) in r:
        raise RootRefused("the POSIX root contains a backslash: {!r}".format(root))
    if not r.startswith("/"):
        raise RootRefused("the POSIX root is not absolute: {!r}".format(root))
    r = r.rstrip("/")
    return r


def render(template: str, root_win: str, root_posix: str) -> str:
    for r in (root_win, root_posix):
        p = root_problem(r)
        if p:
            raise RootRefused(p)
    win, posix = normalise_win(root_win), normalise_posix(root_posix)
    out = template.replace(PLACEHOLDERS[0], posix).replace(PLACEHOLDERS[1], win)
    if "{{" in out:
        raise RootRefused("the template has a placeholder this renderer does not fill")
    json.loads(out)   # never write a definition Codex cannot parse
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codex_definitions")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("render")
    r.add_argument("--template", required=True, type=Path)
    r.add_argument("--root-win", required=True)
    r.add_argument("--root-posix", required=True)
    r.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    if a.cmd != "render":
        ap.print_usage(sys.stderr)
        return 2
    try:
        text = render(a.template.read_text(encoding="utf-8"), a.root_win, a.root_posix)
    except (RootRefused, ValueError, OSError) as e:
        sys.stderr.write("codex_definitions: NOT rendered: {}\n".format(e))
        return 2
    a.out.write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
