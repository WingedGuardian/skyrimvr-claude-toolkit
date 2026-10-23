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

import bisect
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FILE = "KNOWLEDGEBASE.local.md"
CAP = 4096                                    # bytes of section body (R5)
READ_CAP = 1024 * 1024                        # bytes of the file read at all (row 20), before CAP applies
_HEADING = re.compile(r"^##[ \t]+my[ \t]+preferences[ \t]*(?:\([^)\n]*\)[ \t]*)?:?[ \t]*#*[ \t]*$", re.I)
_NEAR = re.compile(r"^#{1,6}[ \t]+.*\bmy[ \t]+preferences\b", re.I)
_ENDS = re.compile(r"^#{1,2}[ \t]+\S")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _closers(lines: List[str], ms) -> Dict[str, Tuple[List[int], List[int], List[int]]]:
    """Per fence character, the lines that can close a fence (CommonMark: a bare fence, nothing after it): their
    indices, their fence lengths, and the suffix maximum of those lengths. A closer must be the opener's character and
    at least as long, so the character is the key."""
    found: Dict[str, Tuple[List[int], List[int]]] = {}
    for i, m in enumerate(ms):
        if m and not lines[i][m.end():].strip():
            fence = m.group(1)
            key = fence[0]
            ix, ln = found.setdefault(key, ([], []))
            ix.append(i)
            ln.append(len(fence))
    out = {}
    for key, (ix, ln) in found.items():
        suf = list(ln)
        for k in range(len(suf) - 2, -1, -1):
            suf[k] = max(suf[k], suf[k + 1])
        out[key] = (ix, ln, suf)
    return out


def _next_closer(closers, i: int, opener: str) -> Optional[int]:
    """The first line after i that closes `opener`, or None. Review I1: the old form rescanned the rest of the file
    from every opener that never closes (a ```a line has an info string, so it is no closer): 120 KB took 138 s, past the
    SessionStart timeout. Here a never-closing opener costs one bisect (the suffix maximum says no closer is long
    enough), and the caller jumps past every closer it is handed, so each closer is stepped over at most once."""
    ix, ln, suf = closers.get(opener[0], ((), (), ()))
    q = bisect.bisect_right(ix, i)
    if q == len(ix) or suf[q] < len(opener):
        return None
    while ln[q] < len(opener):
        q += 1
    return ix[q]


def _fenced(lines: List[str]) -> List[bool]:
    """Per line: True inside a CLOSED code fence (its two fence lines included). An opener that is never closed is
    plain text, so it cannot hide a heading after it or carry the section past the next one. Linear in the lines."""
    out = [False] * len(lines)
    ms = [_FENCE.match(line) for line in lines]          # each line is matched once
    closers = _closers(lines, ms)
    i = 0
    while i < len(lines):
        m = ms[i]
        if m:
            end = _next_closer(closers, i, m.group(1))
            if end is not None:
                out[i:end + 1] = [True] * (end + 1 - i)
                i = end + 1
                continue
        i += 1
    return out


def _scan(text: str):
    lines = text.splitlines()
    return lines, _fenced(lines)


def _section(text: str, scanned=None) -> Tuple[Optional[str], bool]:
    """(body, ended): the body under the first `## My preferences` heading outside a code fence, up to the next
    `#`/`##` heading outside a fence; ended is False when the text ran out first. (None, False) if no heading."""
    lines, fenced = scanned or _scan(text)
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


def _near_miss(text: str, scanned=None) -> Optional[str]:
    lines, fenced = scanned or _scan(text)
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
    scanned = _scan(text)                     # once: the section and the near-miss search share it
    body, ended = _section(text, scanned)
    if body is None:
        if cut:
            return _not_injected(audit, "{} is larger than {} bytes and its first {} bytes have no `## My preferences` "
                                        "section; read it yourself.".format(FILE, READ_CAP, READ_CAP))
        near = _near_miss(text, scanned)
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


def render(project, audit=None) -> str:
    """The framed text to inject, or "". Never raises."""
    if audit is None:
        class QuietAudit:
            def log(self, *args):
                pass
        audit = QuietAudit()
    try:
        return _render(project, audit)
    except Exception:                         # noqa: BLE001 -- never fail the session
        return ""
