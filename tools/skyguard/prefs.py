"""D15: hand the agent the user's standing preferences at session start.

KNOWLEDGEBASE.local.md -> the `## My preferences` section, framed for injection. Pure text in, text out,
and NEVER raises: session start must not fail (Plan 2), so any failure becomes one printed line plus an
audit line. Agents without session hooks get the same section through the core's fallback instruction.

Plan 8 T6 (deferred rows 18-20): a fence opened and never closed is plain text (it used to swallow every later
section); `## My preferences (anything)` is the section too, and a heading that evidently means it but is not it
(`### My preferences`, `## My preferences for combat`) is named in one line and an audit line, never dropped
silently; and only the first READ_CAP bytes of the file are read.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

FILE = "KNOWLEDGEBASE.local.md"
CAP = 4096                                    # bytes of section body (R5)
READ_CAP = 1024 * 1024                        # bytes of the file read at all (row 20), before CAP applies
_HEADING = re.compile(r"^##[ \t]+my[ \t]+preferences[ \t]*(?:\([^)\n]*\)[ \t]*)?:?[ \t]*#*[ \t]*$", re.I)
_NEAR = re.compile(r"^#{1,6}[ \t]+.*\bmy[ \t]+preferences\b", re.I)
_ENDS = re.compile(r"^#{1,2}[ \t]+\S")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _closes(line: str, opener: str) -> bool:
    """CommonMark: a closing fence is the opener's character, at least as long, and nothing after it."""
    m = _FENCE.match(line)
    return bool(m) and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener) and \
        not line[m.end():].strip()


def _fenced(lines: List[str]) -> List[bool]:
    """Per line: True inside a CLOSED code fence (its two fence lines included). An opener that is never closed is
    plain text, so it cannot hide a heading after it or carry the section past the next one."""
    out = [False] * len(lines)
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if m:
            end = next((j for j in range(i + 1, len(lines)) if _closes(lines[j], m.group(1))), None)
            if end is not None:
                for k in range(i, end + 1):
                    out[k] = True
                i = end + 1
                continue
        i += 1
    return out


def _section(text: str) -> Tuple[Optional[str], bool]:
    """(body, ended): the body under the first `## My preferences` heading outside a code fence, up to the next
    `#`/`##` heading outside a fence; ended is False when the text ran out first. (None, False) if no heading."""
    lines = text.splitlines()
    fenced = _fenced(lines)
    start = next((i + 1 for i, line in enumerate(lines) if not fenced[i] and _HEADING.match(line)), None)
    if start is None:
        return None, False
    body, ended = [], False
    for i in range(start, len(lines)):
        if not fenced[i] and _ENDS.match(lines[i]):
            ended = True
            break
        body.append(lines[i])
    return "\n".join(body).strip("\n"), ended


def section(text: str) -> Optional[str]:
    """The body under the first `## My preferences` heading outside a code fence, up to the next
    `#`/`##` heading outside a fence. None if there is no such heading."""
    return _section(text)[0]


def _near_miss(text: str) -> Optional[str]:
    lines = text.splitlines()
    fenced = _fenced(lines)
    return next((line.strip() for i, line in enumerate(lines) if not fenced[i] and _NEAR.match(line)), None)


def _not_injected(audit, why: str) -> str:
    audit.log("session-kb-guard", "prefs NOT INJECTED", why)
    return "PREFERENCES: NOT INJECTED -- {}".format(why)


def _render(project, audit) -> str:
    path = Path(project) / FILE
    if not path.exists():
        return ""
    try:
        with open(str(path), "rb") as fh:
            data = fh.read(READ_CAP)
            cut = bool(fh.read(1))
    except OSError as e:
        audit.log("session-kb-guard", "prefs FAILED", "could not read {}: {}".format(FILE, e.__class__.__name__))
        return "PREFERENCES: NOT INJECTED -- {} exists but could not be read; read its `## My preferences` yourself.".format(FILE)
    text = data.decode("utf-8-sig", "replace")
    body, ended = _section(text)
    if body is None:
        if cut:
            return _not_injected(audit, "{} is larger than {} bytes and its first {} bytes have no `## My preferences` "
                                        "section; read it yourself.".format(FILE, READ_CAP, READ_CAP))
        near = _near_miss(text)
        if near:
            return _not_injected(audit, "{} has the heading `{}`, which is not `## My preferences`; rename it to "
                                        "that, or read the section yourself.".format(FILE, near))
        return ""
    if not body.strip():
        return ""
    data = body.encode("utf-8")
    if len(data) > CAP:
        body = data[:CAP].decode("utf-8", "ignore").rstrip() + \
            "\n[... truncated at {} bytes -- read the rest in {}]".format(CAP, FILE)
    elif cut and not ended:
        body = body.rstrip() + "\n[... truncated: only the first {} bytes of {} are read -- read the rest " \
                               "there]".format(READ_CAP, FILE)
    return "USER PREFERENCES (from {}, section `## My preferences`) -- follow these all session:\n{}".format(FILE, body)


def render(project, audit) -> str:
    """The framed text to inject, or "". Never raises."""
    try:
        return _render(project, audit)
    except Exception:                         # noqa: BLE001 -- never fail the session
        return ""
