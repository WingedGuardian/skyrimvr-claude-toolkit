"""Read an agent's own session transcript and count its tool calls -- the OUTSIDE half of hook coverage.

    count_calls(agent, path) -> (surface, {tool_name: n})

A hook cannot see the calls it was never run for (a Windows surface that emits no PreToolUse at all, E24),
so coverage compares what the transcript says ran with what the hook recorded. This module only reads the
transcript. Any shape it does not recognise raises `Unmeasurable` -- never a count of zero, because "zero
calls" would read as NOT EXERCISED and hide a parser that has silently stopped matching.

Codex (0.160.0, MEASURED in Plan 4 Task 0 Step 5 and Step 6; tests/fixtures/codex/0.160.0/rollout-*.jsonl):
  * Surface = line 1 `session_meta` `payload.originator`: `codex_exec` is `codex exec`, `codex-tui` is the
    interactive TUI. `payload.source` is NEVER used: the TUI probe, run inside VS Code's terminal, recorded
    `source=vscode` for what is the TUI. Any other originator (desktop app, IDE extension) is reported as
    UNCLAIMED -- a surface is claimed only after a live probe (D14).
  * Codex runs tools in "code mode": the raw `response_item` is always `custom_tool_call` `name:"exec"` with
    JavaScript input, so the tool name is not in it. The typed `event_msg` `item_completed` items are counted
    instead: `CommandExecution` (shell), `FileChange` (apply_patch, also a patch run through the shell),
    `McpToolCall` (`server`, `tool`). A call a hook blocked leaves no item, so hooked >= called stays the
    right inequality. The rollout records no hook runs.
Claude Code (MEASURED, tests/fixtures/claude/transcript-p.jsonl): every line carries `entrypoint`
  (`sdk-cli` for `claude -p`); a tool call is a `tool_use` block in an `assistant` message's content.
"""
from __future__ import annotations

import json
from pathlib import Path


class Unmeasurable(Exception):
    """The transcript is missing, unreadable, or not in a shape this module was measured against."""


# ---- Codex rollout constants (each from the measurement cited in the module docstring) --------------
CODEX_META_TYPE = "session_meta"            # line 1 `type`
CODEX_SURFACE_FIELD = "originator"          # `payload.originator`; NOT `payload.source` (vscode for the TUI)
CODEX_SURFACES = {"codex_exec": "exec", "codex-tui": "tui"}   # the claimed surfaces (D14)
CODEX_ITEM_EVENT = "event_msg"              # line `type`
CODEX_ITEM_DONE = "item_completed"          # `payload.type`
CODEX_SHELL_ITEM = "CommandExecution"       # maps to hook tool_name "Bash"
CODEX_PATCH_ITEM = "FileChange"             # apply_patch, or a patch run through the shell
CODEX_MCP_ITEM = "McpToolCall"              # `server`, `tool` -> hook tool_name mcp__<server>__<tool>
# Item types measured as not being tool calls. Any OTHER type is a shape this parser was not measured
# against and could be a renamed tool call, so it is Unmeasurable rather than silently uncounted.
# Reasoning and ContextCompaction: MEASURED in the Task 0 TUI rollout and the Task 14 exec runs (codex-cli 0.160.0).
CODEX_NON_TOOL_ITEMS = ("UserMessage", "AgentMessage", "Reasoning", "ContextCompaction")
# Every measured rollout carries a UserMessage item and has >= 20 lines; a longer one with no recognised item at
# all is a shape change, not a quiet session.
CODEX_MIN_LINES_FOR_ITEMS = 10

# ---- Claude transcript constants ---------------------------------------------------------------------
CLAUDE_SURFACE_FIELD = "entrypoint"
CLAUDE_ASSISTANT = "assistant"
CLAUDE_TOOL_BLOCK = "tool_use"


def _lines(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise Unmeasurable("transcript unreadable: {} ({})".format(path, e.__class__.__name__))
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            raise Unmeasurable("transcript line {} is not JSON".format(n))
        if not isinstance(d, dict):
            raise Unmeasurable("transcript line {} is not an object".format(n))
        out.append(d)
    if not out:
        raise Unmeasurable("transcript is empty")
    return out


def _codex(path):
    rows = _lines(path)
    meta = rows[0]
    pl = meta.get("payload")
    if meta.get("type") != CODEX_META_TYPE or not isinstance(pl, dict):
        raise Unmeasurable("line 1 is not a {}".format(CODEX_META_TYPE))
    orig = pl.get(CODEX_SURFACE_FIELD)
    if not isinstance(orig, str) or not orig:
        raise Unmeasurable("session_meta has no {}".format(CODEX_SURFACE_FIELD))
    surface = CODEX_SURFACES.get(orig, "unclaimed:" + orig)
    counts, recognised = {}, 0
    for d in rows[1:]:
        if d.get("type") != CODEX_ITEM_EVENT:
            continue
        p = d.get("payload")
        if not isinstance(p, dict) or p.get("type") != CODEX_ITEM_DONE:
            continue
        item = p.get("item")
        t = item.get("type") if isinstance(item, dict) else None
        if not isinstance(t, str):
            raise Unmeasurable("an item_completed event has no item type")
        if t == CODEX_SHELL_ITEM:
            name = "Bash"
        elif t == CODEX_PATCH_ITEM:
            name = "apply_patch"
        elif t == CODEX_MCP_ITEM:
            if not isinstance(item.get("server"), str) or not isinstance(item.get("tool"), str):
                raise Unmeasurable("an McpToolCall item has no server/tool")
            name = "mcp__{}__{}".format(item["server"], item["tool"])
        elif t in CODEX_NON_TOOL_ITEMS:
            recognised += 1
            continue
        else:
            raise Unmeasurable("unrecognised item type {!r}".format(t[:40]))
        recognised += 1
        counts[name] = counts.get(name, 0) + 1
    if len(rows) > CODEX_MIN_LINES_FOR_ITEMS and recognised == 0:
        # A rollout with real content and not one recognised item: the item event itself was renamed or
        # moved (an unmeasured surface). Reading that as zero calls would read as NOT EXERCISED.
        raise Unmeasurable("{} lines but no recognised {} item".format(len(rows), CODEX_ITEM_DONE))
    return surface, counts


def _claude(path):
    rows = _lines(path)
    surface = None
    counts, seen = {}, set()
    for d in rows:
        e = d.get(CLAUDE_SURFACE_FIELD)
        if isinstance(e, str) and e and surface is None:
            surface = e
        if d.get("type") != CLAUDE_ASSISTANT:
            continue
        msg = d.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            raise Unmeasurable("an assistant line has no content list")
        for b in content:
            if isinstance(b, dict) and b.get("type") == CLAUDE_TOOL_BLOCK:
                name = b.get("name")
                if not isinstance(name, str) or not name:
                    raise Unmeasurable("a tool_use block has no name")
                bid = b.get("id")
                if isinstance(bid, str) and bid in seen:     # a streamed block can repeat across lines
                    continue
                seen.add(bid)
                counts[name] = counts.get(name, 0) + 1
    if surface is None:
        raise Unmeasurable("no line carries {}".format(CLAUDE_SURFACE_FIELD))
    return "claude:" + surface, counts


def count_calls(agent, path):
    if agent == "codex":
        return _codex(path)
    if agent == "claude":
        return _claude(path)
    raise Unmeasurable("no transcript parser for agent {!r}".format(agent))
