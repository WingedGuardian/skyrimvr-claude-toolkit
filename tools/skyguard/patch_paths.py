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
# routes red team: case-insensitive (Windows finds `APPLY_PATCH` on PATH as apply_patch), and the program word
# may be quoted whole (`'apply_patch' <<EOF` runs the same program).
_SHELL_FORM = re.compile(
    r"""^\s*(?:cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*)?(?P<q>['"]?)(?:apply_patch|applypatch)(?P=q)"""
    r"""(?=\s|$|<)""", re.I)
# The same word where a shell would RUN it: at a command position, after env assignments / sudo-style
# wrappers / a path, with an optional .exe, or as the first word inside a quoted `bash -lc "..."` string.
# A mere mention (`rg -n apply_patch`, `echo apply_patch`) is not a run.
_NAME = r"(?:[^\s'\"`;&|()<>]*[/\\])?(?:apply_patch|applypatch)(?:\.exe)?(?=[\s;&|)<>'\"`]|$)"
_CMD_POS = (r"(?:^|[;&|\n(`{]|\$\()\s*"
            r"(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S*|sudo|env|exec|command|nohup|time|builtin)\s+)*")
_ANY_POSITION = re.compile(_CMD_POS + _NAME, re.I)
_QUOTED_POSITION = re.compile(r"[\"']\s*" + _NAME, re.I)
# routes red team: a patch BODY beside any spelling of the program (`a=apply_patch; $a <<EOF`, a quoted path with a
# space) is a patch the shell may apply, whatever the run position looks like. Unattributable -> refused.
_BODY_LINE = re.compile(r"^\s*\*\*\*\s*begin\s+patch\s*$", re.I | re.M)
_PROGRAM_WORD = re.compile(r"apply_?patch", re.I)
_WRAPPER = re.compile(r"\b(?:ba|z|da|k)?sh\b|pwsh|powershell|\bcmd(?:\.exe)?\b|\beval\b|\s-l?c\b", re.I)
_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1(?P<tail>[^\n]*)\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?:\n|$)", re.S)
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
    for raw in lines[1:-1]:
        line = raw.strip()                        # Codex trims before matching headers (measured 0.160.0)
        if not line.startswith("***"):
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
            elif line == _EOF:
                prev_was_update_header = False
            else:
                raise PatchParseError("unknown patch header: " + line[:60])
    if not out:
        raise PatchParseError("the patch touches no file")
    return out


def has_patch_body(text: str) -> bool:
    """A `*** Begin Patch` line anywhere in text (any case and spacing)."""
    return bool(_BODY_LINE.search(text))


def _runs_apply_patch(cmd: str) -> bool:
    return bool(_ANY_POSITION.search(cmd) or (_QUOTED_POSITION.search(cmd) and _WRAPPER.search(cmd)))


def shell_patch(cmd: str) -> Optional[Tuple[str, Optional[str]]]:
    m = _SHELL_FORM.match(cmd)
    if not m:
        if _runs_apply_patch(cmd):
            raise PatchParseError("apply_patch at a shell position this parser cannot attribute")
        if _BODY_LINE.search(cmd) and _PROGRAM_WORD.search(cmd):
            raise PatchParseError("a patch body beside apply_patch that this parser cannot attribute")
        return None
    rest = cmd[m.end():]
    h = _HEREDOC.search(rest)
    if h:
        # Nothing may precede the heredoc on the command line or follow its terminator: a second patch or
        # any further command would run unseen.
        if rest[:h.start()].strip() or h.group("tail").strip() or rest[h.end():].strip():
            raise PatchParseError("text around the apply_patch heredoc that this parser does not inspect")
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
