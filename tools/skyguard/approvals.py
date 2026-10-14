"""The user's typed save approvals, read from the agent's own session transcript (save policy, user ruling 2026-10-13).

    read(transcript_path, agent) -> Grants

A command that changes more than two saves, or names saves with a wildcard, runs only when the USER has typed a line

    SAVE_ACCESS_APPROVAL {"op": "delete", "files": ["Save1", "Save2", "Save3"]}
    SAVE_ACCESS_APPROVAL {"op": "delete", "pattern": "autosave*.ess", "count": 3}

in this session. `op` is `write` (a write, copy onto, edit, ReSaver --apply or DevBench save), `move` (a move or
rename) or `delete`. A file is named by its file name or stem, with or without the folder: a save and its .skse
co-save are one save, so naming either covers both. A pattern grant covers a command whose wildcard is that exact
file-name pattern (compared case-insensitively) and matches exactly `count` saves when the guard checks. A grant
lasts the session: the whole transcript is read each time, so an approval typed earlier still counts, and only for
the files (or the pattern and count) and the operation it names.

ONLY GENUINE USER TURNS COUNT (prompt-injection safe). Everything else in a transcript can be steered by the agent or
by what it read -- tool results, the agent's own text, files injected as context, hook output, skill text, a
subagent's prompt -- so none of it is read:
  * Claude Code (MEASURED on this machine's transcripts, 2026-10-13): a `type: "user"` line whose `origin.kind` is
    `human`, not `isMeta`, not `isSidechain`, not `isCompactSummary`, whose `promptSource` is absent, `typed` or
    `queued` (a `suggestion_accepted` prompt was written by the model), and whose content is a string or text
    blocks only (a line holding a `tool_result` block is a tool result). Also a `queued_command` attachment
    (typed while the agent was busy) whose `origin.kind` is `human` and whose `commandMode` is `prompt`. A line with
    no `origin` at all (older Claude Code, a subagent's prompt, a task notification) is NOT read: such an
    approval is refused, never guessed.
  * Codex: only the interactive TUI (`session_meta.payload.originator == "codex-tui"`), and only its
    `item_completed` `UserMessage` items (the typed prompt; environment and AGENTS.md text are separate
    `response_item`s, never read). `codex exec` takes its prompt from whoever launched it, so it never approves.
  * Any other agent, a missing or unreadable transcript: no grants (bulk save changes are refused there).

KNOWN LIMIT: the transcript is a file the agent can also write. A guard that reads it cannot stop an agent that
forges a user line in it on purpose; it stops an approval arriving through anything the agent READ.
"""
from __future__ import annotations

import json
import os
import re
from typing import FrozenSet, NamedTuple, Tuple

KEY = "SAVE_ACCESS_APPROVAL"
OPS = ("write", "move", "delete")
_LINE = re.compile(r"^[ \t]*" + KEY + r"[ \t]+(\{[^\n]*\})[ \t]*$", re.MULTILINE)
_SAVE_EXT = re.compile(r"\.(ess|skse)$", re.IGNORECASE)
# Claude: what a typed prompt carries (see the module docstring).
CLAUDE_PROMPT_SOURCES = (None, "typed", "queued")
CODEX_TUI = "codex-tui"
MAX_BYTES = 512 * 1024 * 1024          # a transcript larger than this is not read (it is refused, never guessed)


class Grants(NamedTuple):
    files: FrozenSet[Tuple[str, str]] = frozenset()          # (stem, op), casefolded
    patterns: FrozenSet[Tuple[str, str, int]] = frozenset()   # (file-name pattern, op, count), casefolded
    why: str = ""                                             # "" when the transcript was read


def stem(name: str) -> str:
    """A save's identity: its file name without the folder and without .ess / .skse, casefolded."""
    base = re.split(r"[/\\]+", name.strip().rstrip("/\\"))[-1]
    return _SAVE_EXT.sub("", base).casefold()


def _claude_texts(lines):
    for raw in lines:
        if KEY.encode() not in raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        origin = row.get("origin")
        human = isinstance(origin, dict) and origin.get("kind") == "human"
        if row.get("type") == "attachment":
            att = row.get("attachment")
            if isinstance(att, dict) and att.get("type") == "queued_command" and att.get("commandMode") == "prompt":
                aorigin = att.get("origin")
                if isinstance(aorigin, dict) and aorigin.get("kind") == "human" and isinstance(att.get("prompt"), str):
                    yield att["prompt"]
            continue
        if row.get("type") != "user" or not human:
            continue
        if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):
            continue
        if row.get("promptSource") not in CLAUDE_PROMPT_SOURCES:
            continue
        msg = row.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            if not all(isinstance(b, dict) and b.get("type") == "text" for b in content):
                continue                                   # a tool_result (or anything else) in the line: not typed
            yield "\n".join(b.get("text", "") for b in content if isinstance(b.get("text"), str))


def _codex_texts(lines):
    first = True
    for raw in lines:
        if first:
            first = False
            try:
                meta = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            pl = meta.get("payload") if isinstance(meta, dict) else None
            if not (isinstance(meta, dict) and meta.get("type") == "session_meta" and isinstance(pl, dict)
                    and pl.get("originator") == CODEX_TUI):
                return                                     # not the interactive TUI: nothing approves
            continue
        if KEY.encode() not in raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        p = row.get("payload") if isinstance(row, dict) else None
        if not (row.get("type") == "event_msg" and isinstance(p, dict) and p.get("type") == "item_completed"):
            continue
        item = p.get("item")
        if not (isinstance(item, dict) and item.get("type") == "UserMessage" and isinstance(item.get("content"), list)):
            continue
        yield "\n".join(c.get("text", "") for c in item["content"]
                        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str))


def parse(texts) -> Tuple[FrozenSet[Tuple[str, str]], FrozenSet[Tuple[str, str, int]]]:
    """The grants in these user texts. A line that does not parse, or names no known op, grants nothing."""
    files, patterns = set(), set()
    for text in texts:
        for m in _LINE.finditer(text):
            try:
                d = json.loads(m.group(1))
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            op = d.get("op")
            op = op.strip().lower() if isinstance(op, str) else None
            if isinstance(d.get("pattern"), str):
                n = d.get("count")
                if op in OPS and isinstance(n, int) and not isinstance(n, bool) and n >= 0 and d["pattern"].strip():
                    patterns.add((re.split(r"[/\\]+", d["pattern"].strip())[-1].casefold(), op, n))
                continue
            for f in d.get("files") if isinstance(d.get("files"), list) else ():
                if isinstance(f, str) and op in OPS and stem(f):
                    files.add((stem(f), op))
                elif isinstance(f, dict):              # {"path": ..., "operation": ...} (the per-file form)
                    fop = f.get("operation", f.get("op"))
                    fop = fop.strip().lower() if isinstance(fop, str) else None
                    fp = f.get("path")
                    if fop in OPS and isinstance(fp, str) and stem(fp):
                        files.add((stem(fp), fop))
    return frozenset(files), frozenset(patterns)


def read(transcript_path, agent: str) -> Grants:
    """Every grant the user typed in this session. Never raises: an unreadable or unknown transcript grants nothing,
    and `why` says so (the refusal names it)."""
    if agent not in ("claude", "codex"):
        return Grants(why="no transcript reader for this agent")
    if not isinstance(transcript_path, str) or not transcript_path:
        return Grants(why="the hook was given no transcript")
    try:
        if os.path.getsize(transcript_path) > MAX_BYTES:
            return Grants(why="the transcript is too large to read")
        with open(transcript_path, "rb") as fh:            # streamed: a long session is read a line at a time
            texts = _claude_texts(fh) if agent == "claude" else _codex_texts(fh)
            files, patterns = parse(texts)
    except OSError:
        return Grants(why="the transcript could not be read")
    return Grants(files, patterns)
