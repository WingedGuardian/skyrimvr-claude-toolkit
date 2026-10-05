"""The one dispatch point between requests and rules.

Every agent's verdicts go through here, so a rule-module signature change (Plan 5: cwd=, shell=) is made
in one place. Rule modules are called, never edited, from this file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from . import backup, codes, mcp_rules, paths as _paths, shell_rules, shellparse, snapshot, write_rules
from .verdict import Advice, Verdict

_RANK = {"evaluated": 0, "partial": 1, "failed": 2}
NO_PATHS = ("GUARD INERT: a write request named no file, so the write rules checked NOTHING. Allowing "
            "silently would be indistinguishable from deciding this is fine.")
NO_VERDICTS = "GUARD INERT: the engine combined zero verdicts, so nothing was checked."


def evaluate_shell(req, roots, audit) -> Verdict:
    """Plan 5: the rules read the payload cwd (RAW) and the dialect; a request that names none ("" / None) is
    parsed as bash AND PowerShell (spec R5)."""
    command, shell = req.command, req.shell or ""
    try:
        parsed = shellparse.parse(command, shell) if isinstance(command, str) else None
    except Exception:
        parsed = None                  # decide() parses (and fails) as before; the answer below is partial either way
    v = codes.classify(shell_rules.decide(command, roots, audit, cwd=req.cwd or "", shell=shell, parsed=parsed))
    # D-P4-2 (Plan 4 Verdict.status, Plan 5 Task 17): the rules ran, but on a command the parser could not read
    # completely (a quote or heredoc it could not close) or whose program is a variable. The answer stands; the
    # telemetry must not call it a full evaluation. A failed status (an INERT refusal) is never lowered.
    if v.status == "evaluated" and (parsed is None or not parsed.ok or any(e.program == "?" for e in parsed.execs)):
        v = v._replace(status="partial")
    return v


def evaluate_write(path: str, audit, roots=None, cwd: str = "") -> Verdict:
    """Plan 5 Task 9: W-PAPYRUS / W-INSTALL decide on where the file RESOLVES (roots + cwd). roots=None keeps the
    legacy text tests (more notes, never fewer denies)."""
    return codes.classify(write_rules.decide(path, audit, roots=roots, cwd=cwd))


def evaluate_mcp(tool_name: str, args, audit, roots=None) -> Verdict:
    """Plan 5 Task 11: the DevBench MCP tools (mcp__devbench*); any other MCP server is allowed by mcp_rules."""
    return codes.classify(mcp_rules.decide(tool_name, args, audit, roots=roots))


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    vs = list(verdicts)
    if not vs:   # nothing was evaluated; an empty fold must never read as "allow"
        return Verdict("deny", NO_VERDICTS, status="failed", code="engine.EMPTY_COMBINE")
    worst = max((v.status for v in vs), key=lambda s: _RANK.get(s, 2), default="evaluated")
    for want in ("deny", "ask"):
        for v in vs:
            if v.decision == want:
                return v._replace(status=worst)
    advice, seen = Advice(), set()
    for v in vs:
        for item in [v.context] if v.context else []:
            if item not in seen:
                seen.add(item)
                advice.add(item)
    codes_seen = [v.code for v in vs if v.context]
    return Verdict("allow", "", advice.joined(), worst, codes_seen[0] if codes_seen else "allow")


def evaluate_writes(req, audit, roots=None) -> Verdict:
    if not req.paths:
        audit.log("protect-files", "REFUSE", "write request without paths")
        return Verdict("deny", NO_PATHS, status="failed", code="engine.NO_PATHS")
    return combine(evaluate_write(p, audit, roots=roots, cwd=req.cwd or "") for p in req.paths)


INERT_TRANSLATE = ("GUARD INERT: the Codex hook could not read this call ({}), so it checked NOTHING. "
                   "This is a refusal, not a verdict.")
_BACKUP_OPS = ("update", "delete", "move_from")
UNHOOKED_LOG = "codex-unhooked-tools.log"


class RootsUnavailable(Exception):
    """roots_fn could not tell which directories are the install; the message is the refusal text."""


def _failed(reason: str, code: str) -> Verdict:
    return Verdict("deny", reason, status="failed", code=code)


def _soft_roots(roots_fn):
    """The roots for rules that only ADVISE on location (writes) or read one optional fact (the DevBench save
    check): an unreadable paths file costs those answers, never a refusal (as on Claude, Plan 5 Task 9).
    None = the legacy text tests."""
    try:
        return roots_fn()
    except RootsUnavailable:
        return None


def _rules(req, roots_fn, audit) -> Verdict:
    if req.kind == "write":
        return evaluate_writes(req, audit, roots=_soft_roots(roots_fn))
    if req.kind == "shell":
        if not req.command:
            v = evaluate_shell(req, _paths.Roots("", ""), audit)
        else:
            try:
                roots = roots_fn()
            except RootsUnavailable as e:
                return _failed(str(e), "run.ENV_UNREADABLE")
            v = evaluate_shell(req, roots, audit)
        if req.paths:
            v = combine([v, evaluate_writes(req, audit, roots=_soft_roots(roots_fn))])
        return v
    if req.kind == "mcp" and mcp_rules.is_devbench(req.mcp_tool):
        return _codex_devbench(req, roots_fn, audit)
    # Any other MCP server, and every other tool: logged as unhooked and allowed (docs/guard-coverage.md X4, C11).
    return Verdict()


def _codex_devbench(req, roots_fn, audit) -> Verdict:
    """Plan 5 Task 17 (gap X4): the DevBench judge Claude uses, on Codex, for its DENY outcomes only. An ASK outcome
    (S-DEVBENCH-SAVE) stays an allow on Codex -- Codex fails an ask open anyway, so this adds no refusal -- and its text
    is handed to the model as advice. Unreadable arguments are not a refusal either (as on Claude)."""
    try:
        args = json.loads(req.mcp_args) if req.mcp_args else None
    except ValueError:
        args = None
    v = evaluate_mcp(req.mcp_tool, args, audit, roots=_soft_roots(roots_fn))
    if v.decision == "ask":
        return v._replace(decision="allow", reason="", context=v.reason)
    return v


def _unhooked(req) -> bool:
    """A call no Codex rule guards by name: record it (never for a call already refused)."""
    if req.failure or req.event != "pre_tool":
        return False
    if req.kind == "mcp" and mcp_rules.is_devbench(req.mcp_tool):
        return False                    # judged by name (_codex_devbench): not an unhooked call
    return req.kind in ("mcp", "other") or (req.kind == "shell" and req.tool_name != "Bash")


def side_effects(req, project, audit, v) -> Verdict:
    """Snapshot and backups, whatever the decision (as on Claude, where those hooks run beside the guard).
    Each step has its own try: one failing never skips the next and never changes the decision."""
    partial = False
    if req.kind == "shell":
        try:
            snapshot.run(project, req.command, False, audit)
        except Exception:
            partial = True
            audit.log("snapshot-before-tool", "FAILED", "snapshot raised -- no snapshot was taken")
    if req.kind in ("shell", "write"):
        for p, op in zip(req.paths, req.ops):
            if op not in _BACKUP_OPS:
                continue
            try:
                backup.run(project, "apply_patch", p, False, audit)
            except Exception:
                partial = True
                audit.log("backup-before-edit", "FAILED", "backup of {} raised -- NO BACKUP EXISTS".format(p))
    if _unhooked(req):
        try:
            d = Path(project) / ".claude" / "backups"
            d.mkdir(parents=True, exist_ok=True)
            with open(str(d / UNHOOKED_LOG), "ab") as fh:
                line = "{} {} {}\n".format(audit.stamp(), req.kind, " ".join(req.tool_name.split()))
                fh.write(line.encode("utf-8", "replace"))
        except Exception:
            partial = True
    return v._replace(status="partial") if partial and v.status == "evaluated" else v


def pre_tool(req, project, roots_fn, audit) -> Verdict:
    """All Codex PreToolUse work: the rules, then the side effects."""
    if req.failure:
        audit.log("codex-pre-tool", "REFUSE", req.failure)
        v = _failed(INERT_TRANSLATE.format(req.failure), "codex.TRANSLATE")
    elif req.event != "pre_tool":
        v = _failed(INERT_TRANSLATE.format("unexpected event {!r}".format(req.event)), "codex.TRANSLATE")
    else:
        v = _rules(req, roots_fn, audit)
    return side_effects(req, project, audit, v)
