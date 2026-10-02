"""The audit trail and timestamps, in the exact line formats the bash hooks wrote."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Union


class Audit:
    def __init__(self, project: str):
        self.project = project
        self.backups = Path(project) / ".claude" / "backups"
        self.path = self.backups / "AUDIT_LOG.txt"

    @staticmethod
    def stamp() -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    def raw(self, line: Union[str, bytes]) -> None:
        try:
            self.backups.mkdir(parents=True, exist_ok=True)
            data = line.encode("utf-8") if isinstance(line, str) else line
            with open(self.path, "ab") as fh:
                fh.write(data)
        except OSError:
            pass                                          # bash: >> "$AUDIT_LOG" 2>/dev/null

    def log(self, hook: str, level: str, msg: str) -> None:
        self.raw("[{}] {} {}: {}\n".format(self.stamp(), hook, level, msg))
