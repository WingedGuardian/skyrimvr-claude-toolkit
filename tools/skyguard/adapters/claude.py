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


def extract(payload_text: str) -> dict:
    t = payload_text.replace("\x00", "")          # $(cat) drops NUL before jq ever sees it
    empty = t.rstrip("\r\n") == ""                 # [ -z "$INPUT" ]
    try:
        d = json.loads(t) if t.strip() else None
    except ValueError:
        d = None
    d = d if isinstance(d, dict) else {}
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    return {
        "empty": empty,
        "tool_name": "unknown" if d.get("tool_name") in (None, False) else _sub(d.get("tool_name")),
        "command": _sub(ti.get("command")),
        "file_path": _sub(ti.get("file_path")),
        "source": _sub(d.get("source")),
        "cwd": _sub(d.get("cwd")),
        "session_id": _sub(d.get("session_id")),
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
    return Request(agent="claude", event="pre_tool", kind=kind, shell="bash" if kind == "shell" else None,
                   command=p["command"], paths=(p["file_path"],) if p["file_path"] else (),
                   cwd=p.get("cwd", ""), session_id=p.get("session_id", ""),
                   tool_name=p["tool_name"], empty=p["empty"])
