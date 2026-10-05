"""The agent-neutral engine entry: spec section 5's request/verdict contract over stdin/stdout.

An adapter for ANY agent (written in any language, living outside this package) builds the request below,
runs `run.py neutral pre-tool`, and renders the verdict as its agent's hard block. See ADAPTING.md.

Strictness is the point: an adapter that mistypes a field must be told, loudly, not silently allowed.
A request that cannot be understood becomes a DENY with status=failed (the toolkit checked nothing), never
an exception and never an allow.
"""
from __future__ import annotations

import json
from typing import Tuple

from . import codes, engine
from .request import Request
from .verdict import DECISIONS, STATUSES, Verdict

OK = "SKYGUARD-OK"
KINDS = ("shell", "write", "mcp", "other")
SHELLS = ("bash", "powershell")
OPS = ("add", "update", "delete", "move_from", "move_to", "write", "edit")
FIELDS = frozenset(("v", "agent", "event", "kind", "shell", "command", "paths", "ops", "mcp_tool",
                    "mcp_args", "cwd", "tool_name", "session_id"))
BAD = ("GUARD INERT: the adapter's request was refused ({}), so the toolkit checked NOTHING. "
       "This is a refusal, not a decision.")
BAD_DECISION = "GUARD INERT: the engine produced an unknown decision; refusing."
# run.py's ENV_UNREADABLE names a Claude hook and adapters.codex's names the Codex hook; an adapted agent is
# neither, so the neutral role carries its own wording (Task 0 measurement note, section 6).
ENV_UNREADABLE = (
    "GUARD INERT: the adapter's engine call found .claude/skyrim-paths.env but could not read it, so it cannot "
    "tell which directories are the Skyrim install. Refusing rather than guarding the wrong set. "
    "Fix or delete that file (re-run setup.sh).")


class BadRequest(ValueError):
    """The request is not a valid contract request; the message names the field."""


def _str(d: dict, key: str) -> str:
    v = d.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise BadRequest("{} must be a string".format(key))
    return v


def _names(keys) -> str:
    """Unknown key names, as one short line: a key is adapter input, so it may hold a newline or be huge."""
    return ", ".join(repr(k)[:40] for k in keys[:5]) + (", ..." if len(keys) > 5 else "")


def parse(raw: str) -> Request:
    text = raw.replace("\x00", "")
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        d = json.loads(text)
    except ValueError:
        raise BadRequest("request is not JSON")
    if not isinstance(d, dict):
        raise BadRequest("request is not a JSON object")
    extra = sorted(k for k in d if k not in FIELDS)
    if extra:
        raise BadRequest("unknown field {}".format(_names(extra)))
    if isinstance(d.get("v"), bool) or d.get("v") != 1:
        raise BadRequest("v must be 1")
    if d.get("event") != "pre_tool":
        raise BadRequest("event must be pre_tool")
    kind = d.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise BadRequest("kind must be one of {}".format(", ".join(KINDS)))
    shell = d.get("shell")
    if shell is not None:
        if kind != "shell":
            raise BadRequest("shell is only for kind shell")
        if not isinstance(shell, str) or shell not in SHELLS:
            raise BadRequest("shell must be one of {} or null".format(", ".join(SHELLS)))
    paths = d.get("paths", [])
    if not isinstance(paths, list):
        raise BadRequest("paths must be a list")
    if not all(isinstance(p, str) and p for p in paths):
        raise BadRequest("paths must be a list of non-empty strings")
    ops = d.get("ops")
    if ops is None:
        ops = ["update"] * len(paths)
    if not (isinstance(ops, list) and len(ops) == len(paths)
            and all(isinstance(o, str) and o in OPS for o in ops)):
        raise BadRequest("ops must be parallel to paths, each one of {}".format(", ".join(OPS)))
    return Request(agent=_str(d, "agent") or "neutral", event="pre_tool", kind=kind, shell=shell,
                   command=_str(d, "command"), paths=tuple(paths), ops=tuple(ops),
                   mcp_tool=_str(d, "mcp_tool"), mcp_args=_str(d, "mcp_args"), cwd=_str(d, "cwd"),
                   tool_name=_str(d, "tool_name") or "neutral", session_id=_str(d, "session_id"))


def render(v: Verdict) -> str:
    if v.decision not in DECISIONS:
        v = Verdict("deny", BAD_DECISION, status="failed", code="neutral.BAD_DECISION")
    status = v.status if v.status in STATUSES else "failed"
    return json.dumps({"v": 1, "decision": v.decision, "reason": v.reason, "context": v.context,
                       "status": status, "code": v.code}, ensure_ascii=True, separators=(",", ":"))


def handle(raw: str, project: str, roots_fn, audit) -> Tuple[str, Verdict]:
    try:
        req = parse(raw)
    except BadRequest as e:
        audit.log("neutral", "REFUSE", str(e))
        v = Verdict("deny", BAD.format(e), status="failed", code="neutral.BAD_REQUEST")
    else:
        v = codes.classify(engine.pre_tool(req, project, roots_fn, audit))
    return OK + "\n" + render(v) + "\n", v
