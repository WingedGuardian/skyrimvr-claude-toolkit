"""The ONE apply_patch path parser (spec section 5 adapter duties; D2a).

Blocking and backups both ask this module which files a patch touches, so the two can never disagree.
Any grammar it does not know raises PatchParseError -- a missing Begin/End, an unknown `*** ` header, an
empty path, a Move not directly after an Update, a patch that touches nothing. The caller must refuse:
a partial path list is an allow for the paths it missed.

Grammar: Codex apply_patch, confirmed on captured payloads (Add/Update/Delete on 0.159.3; Move and
multi-file on 0.160.0 per the X4 toolkit's lane-B measurements, re-captured in Plan 4 Task 0). Inside a
hunk every content line starts with `+`, `-`, ` ` or `@@`, so `+*** Delete File: x` is CONTENT.
Ported from the X4 toolkit (agent/guards/adapters/patch_paths.py); Python 3.8 syntax here.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

BEGIN, END = "*** Begin Patch", "*** End Patch"
_HEADERS = (("*** Add File:", "add"), ("*** Update File:", "update"), ("*** Delete File:", "delete"))
_MOVE = "*** Move to:"
_EOF = "*** End of File"
# apply_patch at the start of a shell command, optionally after `cd <dir> &&` (or `;`). Codex applies a
# patch run through the shell and reports it as a Bash call (X4 MEASURED, 0.160.0).
_SHELL_FORM = re.compile(
    r"""^\s*(?:cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*)?(?:apply_patch|applypatch)(?=\s|$|<)""")
# The same word at ANY command position: used to refuse what _SHELL_FORM cannot attribute.
_ANY_POSITION = re.compile(r"(?:^|[;&|\n]|\$\()\s*(?:apply_patch|applypatch)(?=\s|$|<)")
_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?:\n|$)", re.S)
_QUOTED = re.compile(r"\s*(['\"])(?P<body>\*\*\* Begin Patch.*\*\*\* End Patch)\1\s*$", re.S)


class PatchParseError(ValueError):
    pass


def parse_patch(text: str) -> List[Tuple[str, str]]:
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].strip() != BEGIN:
        raise PatchParseError("no '*** Begin Patch' line")
    if lines[-1].strip() != END:
        raise PatchParseError("no '*** End Patch' line")
    out: List[Tuple[str, str]] = []
    prev_was_update_header = False
    for line in lines[1:-1]:
        if not line.startswith("*** "):
            prev_was_update_header = False
            continue
        for head, op in _HEADERS:
            if line.startswith(head):
                path = line[len(head):].strip()
                if not path:
                    raise PatchParseError("empty path after " + head)
                out.append((op, path))
                prev_was_update_header = op == "update"
                break
        else:
            if line.startswith(_MOVE):
                path = line[len(_MOVE):].strip()
                if not prev_was_update_header or not path:
                    raise PatchParseError("'*** Move to:' must directly follow an Update header")
                out[-1] = ("move_from", out[-1][1])
                out.append(("move_to", path))
                prev_was_update_header = False
            elif line.strip() == _EOF:
                prev_was_update_header = False
            else:
                raise PatchParseError("unknown patch header: " + line[:60])
    if not out:
        raise PatchParseError("the patch touches no file")
    return out


def shell_patch(cmd: str) -> Optional[Tuple[str, Optional[str]]]:
    m = _SHELL_FORM.match(cmd)
    if not m:
        if _ANY_POSITION.search(cmd):
            raise PatchParseError("apply_patch at a shell position this parser cannot attribute")
        return None
    rest = cmd[m.end():]
    h = _HEREDOC.search(rest)
    if h:
        body = h.group("body")
    else:
        q = _QUOTED.match(rest)
        if not q:
            raise PatchParseError("apply_patch through the shell without a heredoc or a quoted patch")
        body = q.group("body")
    d = m.group("dir")
    if d and d[0] in "\"'":
        d = d[1:-1]
    return body, d
