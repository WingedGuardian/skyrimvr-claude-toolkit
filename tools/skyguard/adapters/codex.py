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
import re
import shlex
import sys

from .. import patch_paths
from ..request import Request
from ..verdict import Verdict
from modding_harness.transport import execution_cwd_known

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
    "GUARD INERT: the Codex hook found skyrim-paths.env but could not read it, so it cannot tell "
    "which directories are the Skyrim install. Refusing rather than guarding the wrong set. "
    "Fix or delete that file (re-run setup.sh).")


_SAFE_PS = re.compile(r"[A-Za-z0-9_@%+=:,./\-]+\Z")
# Where a non-MCP tool carries shell text: `command` (Codex 0.160.0 normalises its shell to Bash {command}, MEASURED),
# `cmd` (the unified-exec argument name, ASSUMED from the Codex schema), `chars` (write_stdin: keystrokes into a
# running process, possibly a shell; whether Codex fires PreToolUse for it is UNMEASURED on 0.160.0).
# Plan 8 T6 (rt-fix-b concern 4, routes s130): `script` too -- a command under it was allowed unread.
_COMMAND_KEYS = ("command", "cmd", "chars", "script")


def _quote(arg: str, shell: str) -> str:
    """One argv element as one shell word. Single quotes in both shells: bash closes and reopens around an
    embedded quote, PowerShell doubles it."""
    if arg and (shlex.quote(arg) == arg if shell == "bash" else _SAFE_PS.match(arg)):
        return arg
    if shell == "bash":
        return shlex.quote(arg)
    return "'" + arg.replace("'", "''") + "'"


def _argv_text(cmd, shell: str):
    """A command given as an argv LIST -> one shell request text, or None when it is not a non-empty list of
    strings. Left as 'other' it would be allowed unexamined, so the caller fails closed on None."""
    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
        return None
    return " ".join(_quote(x, shell) for x in cmd)


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
    p = path
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
    except RecursionError:          # Codex red team F8: say why, rather than refusing by an engine crash
        return Request(agent=AGENT, event="pre_tool", failure="payload nested too deeply")
    if not isinstance(d, dict):
        return Request(agent=AGENT, event="pre_tool", failure="payload is not a JSON object")
    common = dict(agent=AGENT, cwd=_s(d.get("cwd")), session_id=_s(d.get("session_id")),
                  transcript_path=_s(d.get("transcript_path")), tool_name=_s(d.get("tool_name")))
    event = d.get("hook_event_name")
    if event == "SessionStart":
        return Request(event="session_start", kind="none", source=_s(d.get("source")), **common)
    if event != "PreToolUse":
        return Request(event="other", kind="other", **common)
    if not isinstance(d.get("cwd"), str) or not d["cwd"].strip():
        # routes / Codex red team F4: every captured Codex PreToolUse carries cwd (12 of 12 fixtures). Without one,
        # no relative path resolves, so a relative delete would pass as an advice: a malformed payload, refused.
        return Request(event="pre_tool", failure="payload has no cwd", **common)
    name = common["tool_name"]
    is_mcp = name.startswith("mcp__")
    raw_ti = d.get("tool_input")
    if raw_ti is not None and not isinstance(raw_ti, dict) and not is_mcp:
        # Codex red team F3b (m25): a bare-string or list tool_input carries a call the guard cannot place.
        return Request(event="pre_tool", failure="tool_input is not an object", **common)
    ti = raw_ti if isinstance(raw_ti, dict) else {}
    if not is_mcp and isinstance(ti.get("workdir"), str) and ti["workdir"].strip():
        # routes red team (s132-s144): a shell call's own working directory, when the payload carries one, is where
        # its relative paths land (README: `workdir` is unmeasured; read it when present, never assume it absent).
        common["cwd"] = _resolve(ti["workdir"], common["cwd"], None, platform)
    shell = "powershell" if platform.startswith("win") else "bash"
    try:
        if name.lower() in ("apply_patch", "applypatch"):        # m35: `Apply_Patch` is still a patch
            cmd = ti.get("command")
            if not isinstance(cmd, str) or not cmd:
                return Request(event="pre_tool", failure="apply_patch without patch text", **common)
            return _patch_request(cmd, common, platform)
        if is_mcp:                                             # MEASURED Task 0: mcp__<server>__<tool>
            # The arguments as sent (a list or a string too): the DevBench judge reads every string in them.
            return Request(event="pre_tool", kind="mcp", mcp_tool=name,
                           mcp_args=json.dumps({} if raw_ti is None else raw_ti, sort_keys=True), **common)
        keys = [k for k in _COMMAND_KEYS if k in ti]
        if len(keys) > 1:
            # Only one field can be judged as THE command; the other would run unread. No measured Codex tool sends
            # two (every captured Bash payload carries `command` alone), so the call is refused, not guessed at.
            return Request(event="pre_tool", failure="tool call with more than one command field ({})".format(
                ", ".join(keys)), **common)
        key = keys[0] if keys else None
        if key is None:
            body = next((v for v in ti.values() if isinstance(v, str) and patch_paths.has_patch_body(v)), None)
            if body is not None:                               # a patch under a key no tool here is known to use
                return _patch_request(body, common, platform)
            return Request(event="pre_tool", kind="other", **common)
        cmd = ti[key]
        if isinstance(cmd, list):
            cmd = _argv_text(cmd, shell)
            if cmd is None:
                return Request(event="pre_tool", failure="shell call with an argv list that is not strings",
                               **common)
        if not isinstance(cmd, str):
            # Merge of Plans 4+5 (Plan 5 Task 12): a PRESENT command that is a number, boolean, null or object is no
            # command the guard can judge; as kind 'other' it would be allowed unexamined. Refused before any rule.
            return Request(event="pre_tool", failure="tool call whose command is not a string or an argv list",
                           **common)
        if key == "chars" and not cmd.strip():
            # write_stdin with nothing but Enter (or nothing: Codex polls a running process this way) types no command.
            return Request(event="pre_tool", kind="other", **common)
        common["execution_cwd_known"] = execution_cwd_known(AGENT, d, platform=platform)
        # Bash, and any other tool carrying a command (`exec_command {cmd}`, write_stdin `chars` typed into a live
        # shell, a future renamed shell tool): the shell rules, and a patch run through it is a patch.
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


def _patch_request(text: str, common: dict, platform: str) -> Request:
    ops = patch_paths.parse_patch(text)
    return Request(event="pre_tool", kind="write", command=text,
                   paths=tuple(_resolve(p, common["cwd"], None, platform) for _, p in ops),
                   ops=tuple(o for o, _ in ops), **common)


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
