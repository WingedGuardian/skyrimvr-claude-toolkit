"""Verdict and advice for the guard engine.

A verdict carries two independent facts (Plan 4 R9):
  decision -- what the agent is told: allow | ask | deny
  status   -- how far the engine got: evaluated | partial | failed
and a stable reason CODE naming the rule that decided (codes.py). An INERT refusal is decision=deny,
status=failed: the agent is stopped AND the telemetry records that nothing was checked. The first three
fields keep their order, so every Verdict("deny", reason) written before Plan 4 is unchanged.
"""
from __future__ import annotations

from typing import List, NamedTuple

DECISIONS = ("allow", "ask", "deny")
STATUSES = ("evaluated", "partial", "failed")


class Verdict(NamedTuple):
    decision: str = "allow"        # allow | ask | deny
    reason: str = ""
    context: str = ""              # advice text for the model
    status: str = "evaluated"      # evaluated | partial | failed
    code: str = ""                 # e.g. "write.DENY_PLUGIN"; "" until codes.classify() fills it
    # Plan 6 final fix (lane A C1): a NEVER_SHADOW refusal (engine.NEVER_SHADOW) the same call ALSO produced, when the
    # decision above is a different refusal that outranked it (a deny beside a lift rule's ask). engine.shadow returns
    # it instead of converting the call, so a decoy refusal can never hide a lift or self-state refusal.
    never: object = None


class Advice:
    def __init__(self) -> None:
        self.items: List[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def joined(self) -> str:
        return " | ".join(self.items)
