"""The audit trail and timestamps, in the exact line formats the bash hooks wrote."""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Tuple, Union


class Audit:
    def __init__(self, project: str):
        self.project = project
        self.backups = Path(project) / ".claude" / "backups"
        self.path = self.backups / "AUDIT_LOG.txt"
        self.fired: List[Tuple[str, str]] = []            # (rule id, decision), in firing order; memory only

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

    def log_strict(self, hook: str, level: str, msg: str) -> None:
        """Like log(), but an append that fails RAISES (OSError). For a line a decision depends on: shadow mode
        converts a refusal only after its SHADOW line is on disk."""
        self.backups.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as fh:
            fh.write("[{}] {} {}: {}\n".format(self.stamp(), hook, level, msg).encode("utf-8"))

    def log(self, hook: str, level: str, msg: str) -> None:
        self.raw("[{}] {} {}: {}\n".format(self.stamp(), hook, level, msg))

    def rule(self, hook: str, rule_id: str, decision: str, msg: str, level: Optional[str] = None) -> None:
        """Record that a rule fired. The log line is the same one log() writes (hook-canary and kb-guard read it)."""
        self.fired.append((rule_id, decision))
        self.log(hook, level or decision.upper(), msg)
