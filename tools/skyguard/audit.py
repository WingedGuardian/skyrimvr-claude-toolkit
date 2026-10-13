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
        # fix-limits 2: while a guards lift is on record the rule lines of one call are HELD (engine.hold_if_shadowing)
        # until shadow mode has decided, so a call it lets through is one audit record, not a DENY line plus a SHADOW
        # line. None = not holding (every line is written as it fires, the default and the only mode without a lift).
        self._held: Optional[List[Tuple[str, str, str, str]]] = None   # (hook, level, decision, msg)

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
        """Record that a rule fired. The log line is the same one log() writes (hook-canary and kb-guard read it);
        while holding it is kept for release() / take_refusals() instead."""
        self.fired.append((rule_id, decision))
        if self._held is not None:
            self._held.append((hook, level or decision.upper(), decision, msg))
            return
        self.log(hook, level or decision.upper(), msg)

    def hold(self) -> None:
        if self._held is None:
            self._held = []

    def release(self) -> None:
        """Write every held line exactly as rule() would have, and stop holding. Idempotent."""
        held, self._held = self._held, None
        for hook, level, _decision, msg in held or ():
            self.log(hook, level, msg)

    def take_refusals(self) -> List[Tuple[str, str, str, str]]:
        """Stop holding: write the held lines that are not a refusal (DENY / ASK) as they were, and return the
        refusal lines for the caller to fold into its own record (engine.shadow). [] when nothing was held."""
        held, self._held = self._held or [], None
        refusals = []
        for hook, level, decision, msg in held:
            if level in ("DENY", "ASK"):
                refusals.append((hook, level, decision, msg))
            else:
                self.log(hook, level, msg)
        return refusals
