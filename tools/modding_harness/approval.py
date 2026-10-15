"""Approval scope comparison. Only a qualified host reader may supply human evidence.

This module does not scrape arbitrary transcripts or infer human origin from a role
label alone. Unknown host readers cannot authorize bulk save mutations.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .profiles import canonical

QUALIFIED_READERS = frozenset({"codex-tui-event-v1", "claude-user-message-v1"})


@dataclass(frozen=True)
class Approval:
    session_id: str
    operation: str
    paths: tuple[str, ...]
    reader: str
    origin: str
    command_sha256: str = ""
    count: int = 0

    def matches(self, session_id, operation, paths, command="", wildcard=False):
        resolved = tuple(sorted({canonical(path) for path in paths}))
        return (self.reader in QUALIFIED_READERS and self.origin == "typed-human"
                and bool(session_id) and self.session_id == session_id
                and operation in ("write", "move", "delete") and self.operation == operation
                and tuple(sorted(self.paths)) == resolved
                and (not wildcard or (self.count == len(resolved) and bool(command)
                     and self.command_sha256 == hashlib.sha256(command.encode("utf-8")).hexdigest())))


def needs_approval(existing_logical_saves, wildcard=False):
    return wildcard or existing_logical_saves > 2
