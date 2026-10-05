"""Claude Code <-> skyguard. Translation only (spec section 5): native payload in, native reply out."""
from __future__ import annotations

import json


def _jq_r(v) -> str:
    """`jq -r '<path> // empty'`: null/false/missing -> "", strings raw, everything else as JSON."""
    if v is None or v is False:
        return ""
    if isinstance(v, str):
        return v
    if v is True:
        return "true"
    if isinstance(v, (dict, list)):
        return json.dumps(v, indent=2, ensure_ascii=False)
    return json.dumps(v)


def _sub(v) -> str:
    """`$(jq -r ...)`: bash command substitution drops NUL bytes and strips ALL trailing newlines."""
    return _jq_r(v).replace("\x00", "").rstrip("\n")


# The shell tools Claude Code runs, and the dialect each one's command is written in.
# `Monitor` (MEASURED, Claude Code 2.1.289) also runs a shell command, carried in tool_input.command like Bash, but its
# payload names no dialect, so it is judged as the unknown dialect ("" = bash AND PowerShell, spec R5), never guessed.
SHELLS = {"Bash": "bash", "PowerShell": "powershell"}

# Tools that write a file but carry its path under another key (MEASURED 2.1.289: NotebookEdit sends `notebook_path`
# and no `file_path`). A non-string value is no path: the engine then sees "" and refuses (W-INERT-NOPATH).
PATH_KEYS = {"NotebookEdit": "notebook_path"}


def _json_type(v, present: bool) -> str:
    if not present:
        return "missing"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    return "array" if isinstance(v, list) else "object"


def _file_path(tool_name: str, ti: dict) -> str:
    key = PATH_KEYS.get(tool_name)
    if key is None:
        return _sub(ti.get("file_path"))
    if key in ti:
        # notebook_path is the key the tool writes. Present but empty / not a string -> "" -> refused; it never falls
        # back to file_path (that could judge a path the tool does not write).
        v = ti[key]
        return _sub(v) if isinstance(v, str) and v != "" else ""
    # Absent: fall back to file_path (the coordinator's cross-install finding: read file_path, then notebook_path).
    fp = ti.get("file_path")
    return _sub(fp) if isinstance(fp, str) else ""


def _is_ws_monitor(tool_name: str, ti: dict) -> bool:
    """Monitor's WebSocket form: tool_input {ws: {url, protocols}, description, timeout_ms} and NO `command` key.
    Evidence: the DOCUMENTED Monitor tool schema (ws {url, protocols}; it cannot be combined with command; a command
    Monitor "runs in the same shell environment as Bash"), plus the coordinator's report from a second install.
    UNMEASURED here -- no captured fixture. A ws Monitor runs no shell command, so the shell rules have nothing to judge.
    Strict: ws must be an object with a non-empty string `url`, and `command` must be absent (ws beside any command key,
    ws as a string, ws without a url, or neither key all stay on the fail-closed path)."""
    ws = ti.get("ws")
    return (tool_name == "Monitor" and isinstance(ws, dict) and isinstance(ws.get("url"), str) and ws["url"] != ""
            and "command" not in ti)


def extract(payload_text: str) -> dict:
    """The fields the engine reads. Plan 5 Task 6 added:
      cwd           the payload's top-level `cwd`, RAW ('/c/x', 'C:\\x' and 'C:/x' all occur; targets normalises it);
                    "" when absent or not a string. Task 0 (M5a) measured it equal to the shell's real cwd.
      shell         "bash" / "powershell" from tool_name, "" for any other tool (R5: unknown = both dialects);
      mcp_input     tool_input itself for an `mcp__*` tool (a dict), else {};
      command_type  the JSON type of tool_input.command ("string", "number", "null", "missing", ...), so a rule can
                    tell a real command from a non-string one that `command` renders as JSON text."""
    t = payload_text.replace("\x00", "")          # $(cat) drops NUL before jq ever sees it
    empty = t.rstrip("\r\n") == ""                 # [ -z "$INPUT" ]
    try:
        d = json.loads(t) if t.strip() else None
    except ValueError:
        d = None
    d = d if isinstance(d, dict) else {}
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    tool_name = "unknown" if d.get("tool_name") in (None, False) else _sub(d.get("tool_name"))
    cwd = d.get("cwd")
    return {
        "empty": empty,
        "tool_name": tool_name,
        "command": _sub(ti.get("command")),
        "ws_monitor": _is_ws_monitor(tool_name, ti),
        "command_type": _json_type(ti.get("command"), "command" in ti),
        "file_path": _file_path(tool_name, ti),
        "source": _sub(d.get("source")),
        "cwd": cwd.replace("\x00", "") if isinstance(cwd, str) else "",
        "shell": SHELLS.get(tool_name, ""),
        "mcp_input": ti if tool_name.startswith("mcp__") else {},
        "session_id": _sub(d.get("session_id")),
        "transcript_path": _sub(d.get("transcript_path")),
    }


def render(v) -> str:
    if v.decision in ("deny", "ask"):
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": v.decision,
                                      "permissionDecisionReason": v.reason}}
    elif v.context:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": v.context}}
    else:
        out = {}
    return json.dumps(out, ensure_ascii=False)


def to_request(p: dict, kind: str):
    """`p` is extract()'s dict. The payload cwd is the shell's real cwd for Bash and PowerShell (Plan 5 T0)."""
    from ..request import Request
    # shell: the dialect from tool_name (Bash -> bash, PowerShell -> powershell); a shell tool that names none (Monitor)
    # is None here and "" to the rules = BOTH dialects (spec R5), never a guessed "bash".
    return Request(agent="claude", event="pre_tool", kind=kind,
                   shell=(p.get("shell") or None) if kind == "shell" else None,
                   command=p["command"], paths=(p["file_path"],) if p["file_path"] else (),
                   cwd=p.get("cwd", ""), session_id=p.get("session_id", ""),
                   tool_name=p["tool_name"], empty=p["empty"])
