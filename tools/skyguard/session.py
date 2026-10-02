"""SessionStart: run tools/kb-guard.sh and report (port of session-kb-guard.sh). Never fails the session."""
from __future__ import annotations

import subprocess
from pathlib import Path

HOOK = "session-kb-guard"


def run(project, source, bash, audit):
    if source == "compact":
        return ""
    tool = "{}/tools/kb-guard.sh".format(project)
    if not Path(tool).is_file():
        audit.raw("[{}] {} NOT RUN: tools/kb-guard.sh missing\n".format(audit.stamp(), HOOK))
        return "KB GUARD: NOT RUN -- {} is missing, so your knowledgebase was NOT snapshotted this session.".format(tool)
    r = subprocess.run([bash, tool], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = r.stdout.decode("utf-8", "replace").rstrip("\n")
    lines = [output] if output else []
    if r.returncode == 2:
        lines.append("KB GUARD: the above means NOTHING was snapshotted this session -- unchecked, not clean.")
    flat = output.replace("\n", " ").encode("utf-8")[:200]          # bash: tr '\n' ' ' | head -c 200
    audit.raw(b"[" + audit.stamp().encode() + b"] " + HOOK.encode() + b" rc=" + str(r.returncode).encode()
              + b" " + flat + b"\n")
    return "\n".join(lines)
