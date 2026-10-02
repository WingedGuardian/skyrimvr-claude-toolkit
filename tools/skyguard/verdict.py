"""Verdict and advice for the guard engine."""
from __future__ import annotations

from typing import List, NamedTuple


class Verdict(NamedTuple):
    decision: str = "allow"        # allow | ask | deny
    reason: str = ""
    context: str = ""              # advice text for the model


class Advice:
    def __init__(self) -> None:
        self.items: List[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def joined(self) -> str:
        return " | ".join(self.items)
