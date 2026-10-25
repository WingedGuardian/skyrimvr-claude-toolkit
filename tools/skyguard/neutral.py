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

from . import codes, engine, mcp_rules, saves
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
    "GUARD INERT: the adapter's engine call found skyrim-paths.env but could not read it, so it cannot "
    "tell which directories are the Skyrim install. Refusing rather than guarding the wrong set. "
    "Fix or delete that file (re-run setup.sh).")
TELEMETRY_ROLE = "neutral-pre-tool"   # never "pre-tool": that file is the Codex hook's record
OWN_ENTRY_AGENTS = ("claude", "codex")
# kind "other": no rule guards an arbitrary tool. Allowed (blocking every unknown tool would make an adapted agent
# unusable), but coded and labelled so a reader can never take it for a verdict.
UNJUDGED = "NOT JUDGED: this tool kind is not guarded; WARN AND ALLOW"
# kind "mcp" to a server the MCP rules do not judge (only DevBench is): allowed as before, labelled the same way,
# never status "evaluated", which an adapter would log as checked.
UNJUDGED_MCP = "NOT JUDGED: only a DevBench MCP server is guarded; WARN AND ALLOW"
# The shell rules' own INERT texts name protect-bash.sh (Plan 5's rule modules are not edited here); the neutral
# role keeps their CODE and replaces their wording.
NO_COMMAND = ("GUARD INERT: the adapter sent a shell request with no command, so the toolkit checked nothing.\n"
              "Investigate the adapter's request before retrying.")
NO_ROOT = ("GUARD INERT: the toolkit cannot tell which folder is the Skyrim install, so its delete rules checked "
           "nothing.\nRe-run setup.sh from your Skyrim folder.")
REWORD = {"shell.NO_COMMAND_DENY": NO_COMMAND, "shell.NO_ROOT_DENY": NO_ROOT}
_BOM = chr(0xFEFF)


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


def _unique(pairs):
    """object_pairs_hook: a repeated key is refused at EVERY nesting level. json keeps the last value silently, so
    `"kind":"other", ..., "kind":"write"` would otherwise be judged as whichever one the adapter did not mean."""
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise BadRequest("duplicate field {}".format(_names([k])))
        seen.add(k)
    return dict(pairs)


def _loads(text: str, what: str, not_json: str):
    try:
        return json.loads(text, object_pairs_hook=_unique)
    except BadRequest:
        raise
    except RecursionError:
        raise BadRequest("{} is too deeply nested".format(what))
    except ValueError:
        raise BadRequest(not_json)


def mcp_args(text: str) -> dict:
    """mcp_args is JSON object TEXT ("" = no arguments); anything else is refused, never judged as "no args"."""
    if text == "":
        return {}
    obj = _loads(text, "mcp_args", "mcp_args must be a JSON object text")
    if not isinstance(obj, dict):
        raise BadRequest("mcp_args must be a JSON object text")
    return obj


def parse(raw: str) -> Request:
    text = raw.replace("\x00", "")
    if text.startswith(_BOM):
        text = text[1:]
    d = _loads(text, "request", "request is not JSON")
    if not isinstance(d, dict):
        raise BadRequest("request is not a JSON object")
    extra = sorted(k for k in d if k not in FIELDS)
    if extra:
        raise BadRequest("unknown field {}".format(_names(extra)))
    if type(d.get("v")) is not int or d.get("v") != 1:   # exactly int 1: not True, not 1.0, not "1"
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
    agent = _str(d, "agent")
    if agent.strip().lower() in OWN_ENTRY_AGENTS:
        # The engine keys Codex-only behaviour (the unhooked-tools log) on agent == "codex".
        raise BadRequest("agent must not be claude or codex: those agents have their own entry")
    margs = _str(d, "mcp_args")
    mcp_args(margs)
    return Request(agent=agent or "neutral", event="pre_tool", kind=kind, shell=shell,
                   command=_str(d, "command"), paths=tuple(paths), ops=tuple(ops),
                   mcp_tool=_str(d, "mcp_tool"), mcp_args=margs, cwd=_str(d, "cwd"),
                   tool_name=_str(d, "tool_name") or "neutral", session_id=_str(d, "session_id"))


def render(v: Verdict) -> str:
    if v.decision not in DECISIONS:
        v = Verdict("deny", BAD_DECISION, status="failed", code="neutral.BAD_DECISION")
    status = v.status if v.status in STATUSES else "failed"
    return json.dumps({"v": 1, "decision": v.decision, "reason": v.reason, "context": v.context,
                       "status": status, "code": v.code}, ensure_ascii=True, separators=(",", ":"))


def _soft_roots(roots_fn):
    """The DevBench save check reads one optional fact from the roots; an unreadable paths file costs that answer
    (it then asks), never a refusal of every call -- as on Claude's protect-mcp role."""
    try:
        return roots_fn()
    except engine.RootsUnavailable:
        return None


def _decide(req: Request, project: str, roots_fn, audit) -> Verdict:
    if req.kind == "other":
        v = engine.pre_tool(req, project, roots_fn, audit)
        return v._replace(context=UNJUDGED if v.context else "", code="neutral.UNJUDGED")
    if req.kind == "mcp":
        if req.mcp_tool not in ("", "unknown") and not mcp_rules.judged_server(req.mcp_tool):
            v = engine.pre_tool(req, project, roots_fn, audit)
            return v._replace(context=UNJUDGED_MCP if v.context else "", code="neutral.UNJUDGED_MCP")
        # The same judge Claude's protect-mcp role uses (engine.evaluate_mcp -> mcp_rules), and the same save policy:
        # an existing save the call overwrites is backed up first (portable.check), or the call is refused.
        v = engine.evaluate_mcp(req.mcp_tool, mcp_args(req.mcp_args), audit, roots=_soft_roots(roots_fn))
        from . import portable
        return portable.legacy_pre_tool(req, project, roots_fn, audit, precomputed=v)
    v = codes.classify(engine.pre_tool(req, project, roots_fn, audit))
    text = REWORD.get(v.code)
    return v._replace(reason=text) if text and v.decision == "deny" else v


def handle(raw, project: str, roots_fn, audit) -> Tuple[str, Verdict]:
    """raw: the request as bytes (decoded here, strictly: a bad byte is refused, never replaced) or as text."""
    try:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise BadRequest("request is not UTF-8")
        req = parse(raw)
    except BadRequest as e:
        audit.log("neutral", "REFUSE", str(e))
        v = Verdict("deny", BAD.format(e), status="failed", code="neutral.BAD_REQUEST")
    else:
        v = _decide(req, project, roots_fn, audit)
    return OK + "\n" + render(v) + "\n", v
