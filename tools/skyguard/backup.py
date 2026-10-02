"""Back up a file before it is edited (port of backup-before-edit.sh). Never blocks."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .grep import grep

HOOK = "backup-before-edit"
SKIP = (r"[/\\]\.claude[/\\](backups|hooks|plans)[/\\]", r"[/\\]node_modules[/\\]")


def run(project, tool_name, file_path, payload_empty, audit):
    if payload_empty:
        audit.log(HOOK, "BLIND", "received an EMPTY payload -- no backup was taken for this call")
        return
    if not file_path or not os.path.isfile(file_path):
        return
    if any(grep(p, file_path) for p in SKIP):
        return
    backups = Path(project) / ".claude" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    ts = audit.stamp()
    # bash: sed 's|[/\\:]|_|g' | sed 's|^_*||'
    safe = re.sub(r"[/\\:]", "_", file_path).lstrip("_")
    try:
        shutil.copyfile(file_path, str(backups / "{}__{}".format(ts, safe)))
    except OSError:
        audit.log(HOOK, "FAILED", "could not copy {} -- NO BACKUP EXISTS for this edit".format(file_path))
        return
    audit.raw("[{t}] {n} -> {p} (backup: {t}__{s})\n".format(t=ts, n=tool_name, p=file_path, s=safe))
