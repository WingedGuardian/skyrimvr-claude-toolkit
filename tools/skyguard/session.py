"""SessionStart: run tools/kb-guard.sh and report (port of session-kb-guard.sh). Never fails the session."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import prefs

HOOK = "session-kb-guard"


def _with_prefs(text, project, audit):
    extra = prefs.render(project, audit)
    return "\n".join(t for t in (text, extra) if t)


def run(project, source, bash, audit):
    if source == "compact":
        return _with_prefs("", project, audit)   # no snapshot on compact, but preferences survive it (R4)
    tool = "{}/tools/kb-guard.sh".format(project)
    if not Path(tool).is_file():
        audit.raw("[{}] {} NOT RUN: tools/kb-guard.sh missing\n".format(audit.stamp(), HOOK))
        missing = "KB GUARD: NOT RUN -- {} is missing, so your knowledgebase was NOT snapshotted this session.".format(tool)
        return _with_prefs(missing, project, audit)
    r = subprocess.run([bash, tool], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    raw = r.stdout.rstrip(b"\n")
    output = raw.decode("utf-8", "replace")
    lines = [output] if output else []
    if r.returncode == 2:
        lines.append("KB GUARD: the above means NOTHING was snapshotted this session -- unchecked, not clean.")
    flat = raw.replace(b"\n", b" ")[:200]        # bash: tr '\n' ' ' | head -c 200
    audit.raw(b"[" + audit.stamp().encode() + b"] " + HOOK.encode() + b" rc=" + str(r.returncode).encode()
              + b" " + flat + b"\n")
    return _with_prefs("\n".join(lines), project, audit)
