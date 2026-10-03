"""Codex CLI <-> skyguard. Translation only (spec section 5).

Codex blocks on exactly one thing: a parsed {"hookSpecificOutput":{"permissionDecision":"deny"}}.
"ask", exit 2, exit 1, a crash, malformed JSON and a timeout all let the command RUN (spec E1, E17;
re-measured in Plan 4 Task 0, codex-cli 0.160.0). So render() can emit only three bodies -- deny,
additionalContext, or nothing -- and an ask becomes a deny that says the user must run it themselves.
"""
from __future__ import annotations

import json
import ntpath
import posixpath
import sys

from .. import patch_paths
from ..request import Request
from ..verdict import Verdict

AGENT = "codex"
INSTRUCTION_LIMIT_BYTES = 32768     # E7: AGENTS.md past this is silently truncated
# Task 0 R7 (MEASURED): additionalContextLimit is in TOKENS and Codex cuts the MIDDLE of an oversized
# context, so we cap our own text, in bytes, well under the 2,500-token default (~10 KB).
CONTEXT_LIMIT = 8000
# Task 0 (MEASURED): a user-typed `!cmd` fires no PreToolUse, so the user can run what we refuse.
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- Codex cannot ask, so the toolkit refuses instead. Stop and tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. "
              "(In the Codex prompt they can type ! followed by the command.) ")
BANNER = "SKYGUARD: Codex hooks live (definitions v1)."
_NOTE = " [truncated by the toolkit at {n} bytes]"
_PREFS_MARK = "USER PREFERENCES ("
ENV_UNREADABLE = (
    "GUARD INERT: the Codex hook found .claude/skyrim-paths.env but could not read it, so it cannot tell "
    "which directories are the Skyrim install. Refusing rather than guarding the wrong set. "
    "Fix or delete that file (re-run setup.sh).")


def _s(v) -> str:
    return v if isinstance(v, str) else ""


def _cap(text: str) -> str:
    data = text.encode("utf-8", "replace")
    if len(data) <= CONTEXT_LIMIT:
        return text
    note = _NOTE.format(n=CONTEXT_LIMIT)
    keep = CONTEXT_LIMIT - len(note.encode("utf-8"))
    return data[:keep].decode("utf-8", "ignore") + note


def session_body(out: str) -> str:
    """BANNER + the SessionStart text, within CONTEXT_LIMIT bytes. Over the limit the kb-guard detail is cut
    first (the banner and the user's preferences are kept whole when they fit), and the cut is marked."""
    head = BANNER + "\n"
    if not out:
        return BANNER
    if len((head + out).encode("utf-8", "replace")) <= CONTEXT_LIMIT:
        return head + out
    i = out.find(_PREFS_MARK)
    detail, prefs = (out, "") if i < 0 else (out[:i].rstrip("\n"), out[i:])
    note = _NOTE.format(n=CONTEXT_LIMIT)
    fixed = len((head + note + ("\n" + prefs if prefs else "")).encode("utf-8", "replace"))
    room = CONTEXT_LIMIT - fixed
    if room < 0:                      # the preferences alone do not fit: cut them, drop the detail
        return _cap(head + prefs)
    kept = detail.encode("utf-8", "replace")[:room].decode("utf-8", "ignore")
    return head + kept + note + ("\n" + prefs if prefs else "")


def _resolve(path: str, cwd: str, cd_dir, platform: str) -> str:
    mod = ntpath if platform.startswith("win") else posixpath
    base = cwd
    if cd_dir:
        base = cd_dir if mod.isabs(cd_dir) or not cwd else mod.join(cwd, cd_dir)
    p = path.strip()
    if not mod.isabs(p) and base:
        p = mod.join(base, p)
    return mod.normpath(p).replace("\\", "/") if (mod.isabs(p) or base) else p.replace("\\", "/")


def to_request(raw: str, platform: str = sys.platform) -> Request:
    text = raw.replace("\x00", "")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        return Request(agent=AGENT, event="pre_tool", empty=True, failure="empty payload")
    try:
        d = json.loads(text)
    except ValueError:
        return Request(agent=AGENT, event="pre_tool", failure="payload is not JSON")
    if not isinstance(d, dict):
        return Request(agent=AGENT, event="pre_tool", failure="payload is not a JSON object")
    common = dict(agent=AGENT, cwd=_s(d.get("cwd")), session_id=_s(d.get("session_id")),
                  transcript_path=_s(d.get("transcript_path")), tool_name=_s(d.get("tool_name")))
    event = d.get("hook_event_name")
    if event == "SessionStart":
        return Request(event="session_start", kind="none", source=_s(d.get("source")), **common)
    if event != "PreToolUse":
        return Request(event="other", kind="other", **common)
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    cmd = ti.get("command")
    name = common["tool_name"]
    try:
        if name == "apply_patch":
            if not isinstance(cmd, str) or not cmd:
                return Request(event="pre_tool", failure="apply_patch without patch text", **common)
            ops = patch_paths.parse_patch(cmd)
            return Request(event="pre_tool", kind="write", command=cmd,
                           paths=tuple(_resolve(p, common["cwd"], None, platform) for _, p in ops),
                           ops=tuple(o for o, _ in ops), **common)
        if name == "Bash":
            if not isinstance(cmd, str):
                return Request(event="pre_tool", failure="shell call without a command string", **common)
            shell = "powershell" if platform.startswith("win") else "bash"
            sp = patch_paths.shell_patch(cmd)
            if sp is None:
                return Request(event="pre_tool", kind="shell", shell=shell, command=cmd, **common)
            body, cd_dir = sp
            ops = patch_paths.parse_patch(body)
            return Request(event="pre_tool", kind="shell", shell=shell, command=cmd,
                           paths=tuple(_resolve(p, common["cwd"], cd_dir, platform) for _, p in ops),
                           ops=tuple(o for o, _ in ops), **common)
    except patch_paths.PatchParseError as e:
        return Request(event="pre_tool", failure="unreadable patch: {}".format(e), **common)
    if not name.startswith("mcp__") and isinstance(cmd, str):
        # An unknown tool carrying a command string: guard it as a shell call, in case a future Codex renames
        # its shell tool. The engine still records it as unhooked.
        shell = "powershell" if platform.startswith("win") else "bash"
        return Request(event="pre_tool", kind="shell", shell=shell, command=cmd, **common)
    kind = "mcp" if name.startswith("mcp__") else "other"     # MEASURED Task 0: mcp__<server>__<tool>
    return Request(event="pre_tool", kind=kind, mcp_tool=name if kind == "mcp" else "",
                   mcp_args=json.dumps(ti, sort_keys=True) if kind == "mcp" else "", **common)


def render(v: Verdict) -> str:
    if v.decision == "allow":
        if not v.context:
            return ""
        body = {"hookEventName": "PreToolUse", "additionalContext": _cap(v.context)}
    else:
        reason = v.reason if v.decision == "deny" else (
            ASK_PREFIX + v.reason if v.decision == "ask" else
            "GUARD INERT: the engine returned an unknown decision ({!r}); refusing.".format(v.decision))
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": _cap(reason)}
    return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)
