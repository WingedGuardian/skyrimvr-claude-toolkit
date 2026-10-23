"""Approval scope comparison. Only a qualified host reader may supply human evidence.

This module does not scrape arbitrary transcripts or infer human origin from a role
label alone. Unknown host readers cannot authorize bulk save mutations.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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

    def matches(self, session_id, operation, paths, command="", wildcard=False, logical_count=None):
        resolved = tuple(sorted({canonical(path) for path in paths}))
        count = len(resolved) if logical_count is None else logical_count
        return (self.reader in QUALIFIED_READERS and self.origin == "typed-human"
                and bool(session_id) and self.session_id == session_id
                and operation in ("write", "move", "delete") and self.operation == operation
                and tuple(sorted(self.paths)) == resolved
                and (not wildcard or (type(self.count) is int and self.count == count and bool(command)
                     and self.command_sha256 == hashlib.sha256(command.encode("utf-8")).hexdigest())))


def needs_approval(existing_logical_saves, wildcard=False):
    return wildcard or existing_logical_saves > 2


KEY = "SAVE_ACCESS_APPROVAL"
_LINE = re.compile(r"^[ \t]*" + KEY + r"[ \t]+(\{[^\n]*\})[ \t]*$", re.MULTILINE)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(c, dict) and c.get("type") == "text"
                                        and isinstance(c.get("text"), str) for c in content):
        return "\n".join(c["text"] for c in content)
    return ""


def human_texts(lines, host, session_id):
    """Qualified transcript shapes; unknown origins and summaries grant nothing.

    Codex exec prompts can originate from another process, so only TUI completed
    UserMessage events qualify. Claude requires explicit human provenance. These
    are evidence readers, not a filesystem boundary against transcript forgery.
    """
    if not session_id or host not in ("claude", "codex"):
        return
    tui = False
    first = True
    for raw in lines:
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            if first and host == "codex":
                return
            continue
        if not isinstance(row, dict):
            continue
        if first and host == "codex":
            first = False
            meta = row.get("payload")
            tui = (row.get("type") == "session_meta" and isinstance(meta, dict)
                   and meta.get("originator") == "codex-tui" and meta.get("id") == session_id)
            if not tui:
                return
            continue
        first = False
        if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):
            continue
        if row.get("sessionId", session_id) != session_id:
            continue
        if host == "codex":
            payload = row.get("payload")
            if not (tui and row.get("type") == "event_msg" and isinstance(payload, dict)
                    and payload.get("type") == "item_completed"):
                continue
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "UserMessage":
                yield _text(item.get("content"))
            continue
        if row.get("type") == "attachment":
            att = row.get("attachment")
            origin = att.get("origin") if isinstance(att, dict) else None
            if (isinstance(att, dict) and att.get("type") == "queued_command"
                    and att.get("commandMode") == "prompt" and isinstance(origin, dict)
                    and origin.get("kind") == "human"):
                yield _text(att.get("prompt"))
            continue
        origin = row.get("origin")
        msg = row.get("message")
        if (row.get("type") == "user" and isinstance(origin, dict) and origin.get("kind") == "human"
                and row.get("promptSource") in (None, "typed", "queued")
                and isinstance(msg, dict) and msg.get("role") == "user"):
            yield _text(msg.get("content"))


def read(transcript, host, session_id):
    """Return canonical grants and a refusal reason. Legacy basename grants do not qualify."""
    if host not in ("claude", "codex") or not session_id:
        return [], "no qualified human reader/session identity"
    reader = "claude-user-message-v1" if host == "claude" else "codex-tui-event-v1"
    grants = []
    try:
        if not transcript or os.path.getsize(transcript) > 512 * 1024 * 1024:
            return [], "transcript missing or exceeds the reader's byte budget"
        with open(transcript, "rb") as stream:
            for text in human_texts(stream, host, session_id):
                for match in _LINE.finditer(text):
                    try:
                        data = json.loads(match.group(1))
                        if (data.get("v") != 1 or data.get("session_id") != session_id
                                or data.get("op") not in ("write", "move", "delete")
                                or not isinstance(data.get("paths"), list) or not data["paths"]
                                or not all(isinstance(p, str) and os.path.isabs(p) for p in data["paths"])):
                            continue
                        paths = tuple(sorted(canonical(p) for p in data["paths"]))
                        if len(set(paths)) != len(paths):
                            continue
                        grants.append(Approval(session_id, data["op"], paths, reader, "typed-human",
                                               data.get("command_sha256", ""), data.get("count", 0)))
                    except (ValueError, TypeError, AttributeError):
                        continue
    except (OSError, ValueError):
        return [], "transcript could not be read"
    return grants, "" if grants else "no matching canonical typed human grant"


def example(session_id, operation, paths, command="", wildcard=False, logical_count=0):
    grant = {"v": 1, "session_id": session_id, "op": operation,
             "paths": sorted({canonical(p) for p in paths})}
    if wildcard:
        grant.update(count=logical_count, command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest())
    return KEY + " " + json.dumps(grant, ensure_ascii=True)
