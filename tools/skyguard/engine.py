"""The one dispatch point between requests and rules.

Every agent's verdicts go through here, so a rule-module signature change (Plan 5: cwd=, shell=) is made
in one place. Rule modules are called, never edited, from this file.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from typing import Iterable, Optional

from . import (backup, codes, deletes, mcp_rules, paths as _paths, rules, shell_rules, shellparse, snapshot, targets,
               write_rules)
from .verdict import Advice, Verdict

# apply_patch ops that take a file away from where it is (a Move only when it leaves that folder).
DELETE_OPS = ("delete", "move_from")

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


def patch_target(path: str, cwd: str) -> Optional[str]:
    """Where an apply_patch path lands, read LITERALLY: apply_patch expands nothing (`$HOME/x` and `~/x` are folder
    names under the cwd), and `/x` is the root of the cwd's drive (Codex runs Windows paths). None when unknowable
    (no usable cwd for a relative path, a drive-relative `C:x`, an 8.3 short name)."""
    p = path.strip().replace("\\", "/")
    a = _paths.norm_abs(p, msys=False)
    if a is None and not re.match(r"[A-Za-z]:", p):
        base = targets.normalise_cwd(cwd)
        if base and p.startswith("/"):
            root = targets._drive_root(base)
            a = _paths.norm_abs(root + p.lstrip("/")) if root else None
        elif base:
            a = _paths.norm_abs(_paths._join(base, p))
    return targets._final(a, os.environ)


def _patch_delete(path: str, dest: Optional[str], roots, cwd: str, audit) -> Optional[Verdict]:
    """routes red team (F2): an apply_patch Delete, or a Move whose destination leaves the folder, removes `path`
    from where it is. Judged like the shell delete rules (deletes.protected_kind): into the install, config or MO2
    -> S-DEL-*; a path the guard cannot place that reaches one -> S-DEL-UNRESOLVED. Scratch (R8) and outside pass."""
    a = patch_target(path, cwd)
    if a is None:
        if deletes.raw_reaches(path, targets.normalise_cwd(cwd), "cmd", roots):
            rid, file = "S-DEL-UNRESOLVED", ""
        else:
            return None
    else:
        kind = deletes.protected_kind(a, roots)
        if not kind:
            return None
        if dest is not None:
            b = patch_target(dest, cwd)
            if b is not None and targets.zone(b, roots) == targets.zone(a, roots):
                return None                       # a rename inside the same folder: the W- rules judge both names
        rid, file = deletes.RULE_OF[kind], posixpath.basename(a.rstrip("/")) or a
    reason = rules.message(rid, file=file) if "{file}" in rules.RULES[rid]["template"] else rules.message(rid)
    audit.rule("protect-files", rid, "deny", reason)
    return Verdict("deny", reason, code=codes.rule_code(rid))


def evaluate_writes(req, audit, roots=None) -> Verdict:
    if not req.paths:
        audit.log("protect-files", "REFUSE", "write request without paths")
        return Verdict("deny", NO_PATHS, status="failed", code="engine.NO_PATHS")
    out = []
    ops = tuple(req.ops) + ("",) * (len(req.paths) - len(req.ops))
    for i, (p, op) in enumerate(zip(req.paths, ops)):
        if op in DELETE_OPS and roots is not None:
            dest = req.paths[i + 1] if op == "move_from" and i + 1 < len(req.paths) else None
            v = _patch_delete(p, dest, roots, req.cwd or "", audit)
            if v is not None:
                out.append(v)
                continue
        out.append(evaluate_write(p, audit, roots=roots, cwd=req.cwd or ""))
    return combine(out)


INERT_TRANSLATE = ("GUARD INERT: the agent's hook could not read this call ({}), so it checked NOTHING. "
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


def _write_roots(req, roots_fn):
    """The roots for a patch's paths. Soft (advice only) unless the patch deletes or moves a file away: the delete
    rule is a deny, so an unreadable or malformed paths file then refuses, exactly as a shell delete does."""
    if not any(op in DELETE_OPS for op in req.ops):
        return _soft_roots(roots_fn), None
    try:
        roots = roots_fn()
    except RootsUnavailable as e:
        return None, _failed(str(e), "run.ENV_UNREADABLE")
    if roots.malformed:
        file, key = roots.malformed[0]
        return None, _failed(rules.message("S-INERT-CONFIG", file=file, op=key), "run.ENV_MALFORMED")
    return roots, None


def _rules(req, roots_fn, audit) -> Verdict:
    if req.kind == "write":
        roots, refused = _write_roots(req, roots_fn)
        return refused or evaluate_writes(req, audit, roots=roots)
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
            roots, refused = _write_roots(req, roots_fn)
            v = combine([v, refused or evaluate_writes(req, audit, roots=roots)])
        return v
    if req.kind == "mcp":
        # rt-fix-b (routes s115/s116) = gap X4 (Plan 5 Task 17): the DevBench judge Claude's protect-mcp uses, on Codex
        # too. Any other MCP server is allowed by mcp_rules and still logged as unhooked. An ask (S-DEVBENCH-SAVE)
        # stays an ask, which the Codex render turns into a refusal naming the confirmation (adapters/codex.py
        # ASK_PREFIX), as every other ask on Codex does. Merge of rt-fix-b into plan5: this replaces Task 17's
        # ask-to-advice downgrade (the stricter of the two; see merge-fixes-report).
        try:
            args = json.loads(req.mcp_args) if req.mcp_args else {}
        except ValueError:
            return _failed(INERT_TRANSLATE.format("unreadable MCP arguments"), "codex.TRANSLATE")
        return evaluate_mcp(req.mcp_tool, args, audit, roots=_soft_roots(roots_fn) or _paths.Roots("", ""))
    return Verdict()


def _unhooked(req) -> bool:
    """A call no Codex rule guards by name: record it (never for a call already refused)."""
    if req.failure or req.event != "pre_tool":
        return False
    if req.agent != "codex":
        return False   # Plan 7: the neutral role's calls are judged by kind, not by a Codex tool name
    if req.kind == "mcp" and mcp_rules.is_devbench(req.mcp_tool):
        return False                    # Task 17: judged by name (the DevBench judge above), not an unhooked call
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
