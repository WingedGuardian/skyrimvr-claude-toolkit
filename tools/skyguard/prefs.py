"""D15: hand the agent the user's standing preferences at session start.

KNOWLEDGEBASE.local.md -> the `## My preferences` section, framed for injection. Pure text in, text out,
and NEVER raises: session start must not fail (Plan 2), so any failure becomes one printed line plus an
audit line. Agents without session hooks get the same section through the core's fallback instruction.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

FILE = "KNOWLEDGEBASE.local.md"
CAP = 4096                                    # bytes of section body (R5)
_HEADING = re.compile(r"^##[ \t]+my preferences[ \t]*#*[ \t]*$", re.I)
_ENDS = re.compile(r"^#{1,2}[ \t]+\S")
_FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")


def section(text: str) -> Optional[str]:
    """The body under the first `## My preferences` heading outside a code fence, up to the next
    `#`/`##` heading outside a fence. None if there is no such heading."""
    lines = text.splitlines()
    in_fence, start = False, None
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and _HEADING.match(line):
            start = i + 1
            break
    if start is None:
        return None
    body, in_fence = [], False
    for line in lines[start:]:
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and _ENDS.match(line):
            break
        body.append(line)
    return "\n".join(body).strip("\n")


def _render(project, audit) -> str:
    path = Path(project) / FILE
    if not path.exists():
        return ""
    try:
        text = path.read_bytes().decode("utf-8-sig", "replace")
    except OSError as e:
        audit.log("session-kb-guard", "prefs FAILED", "could not read {}: {}".format(FILE, e.__class__.__name__))
        return "PREFERENCES: NOT INJECTED -- {} exists but could not be read; read its `## My preferences` yourself.".format(FILE)
    body = section(text)
    if body is None or not body.strip():
        return ""
    data = body.encode("utf-8")
    if len(data) > CAP:
        body = data[:CAP].decode("utf-8", "ignore").rstrip() + \
            "\n[... truncated at {} bytes -- read the rest in {}]".format(CAP, FILE)
    return "USER PREFERENCES (from {}, section `## My preferences`) -- follow these all session:\n{}".format(FILE, body)


def render(project, audit) -> str:
    """The framed text to inject, or "". Never raises."""
    try:
        return _render(project, audit)
    except Exception:                         # noqa: BLE001 -- never fail the session
        return ""
