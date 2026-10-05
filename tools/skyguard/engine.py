"""The one dispatch point between requests and rules.

Every agent's verdicts go through here, so a rule-module signature change (Plan 5: cwd=, shell=) is made
in one place. Rule modules are called, never edited, from this file.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

from . import backup, codes, mcp_rules, paths as _paths, shell_rules, snapshot, state as _state, write_rules
from .verdict import Advice, Verdict

_RANK = {"evaluated": 0, "partial": 1, "failed": 2}
NO_PATHS = ("GUARD INERT: a write request named no file, so the write rules checked NOTHING. Allowing "
            "silently would be indistinguishable from deciding this is fine.")
NO_VERDICTS = "GUARD INERT: the engine combined zero verdicts, so nothing was checked."


def evaluate_shell(req, roots, audit) -> Verdict:
    """Plan 5: the rules read the payload cwd (RAW) and the dialect; a request that names none ("" / None) is
    parsed as bash AND PowerShell (spec R5)."""
    return codes.classify(shell_rules.decide(req.command, roots, audit, cwd=req.cwd or "", shell=req.shell or ""))


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
    # kind "mcp": the DevBench judge (evaluate_mcp) is wired for Claude only; on Codex an MCP call is logged as
    # unhooked and allowed (docs/guard-coverage.md X4). Wiring it is a behaviour change, left to its own task.
    return Verdict()


def _unhooked(req) -> bool:
    """A call no Codex rule guards by name: record it (never for a call already refused)."""
    if req.failure or req.event != "pre_tool":
        return False
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


SHADOW_HEAD = "Guards OFF: this would have been denied ({})."
_LINE_MAX = 190
# Plan 6 Task 11 (R7, R8): guards off never lifts the lock. Without this an agent could take `guards off` (a D6 lift it
# may take) and then run `lock disable` with no ask -- a persistent off nobody approved -- or unlock a vanilla file
# outside the CLI. These refusals stand under shadow mode, logged as `SHADOW <code> kept`.
NEVER_SHADOW = frozenset(("shell.S-LIFT-PERMANENT", "shell.S-LIFT-RAW", "shell.S-LIFT-UNLOCKCMD"))


def _reject_marker(project) -> Path:
    return Path(project) / ".claude" / "backups" / ".last-state-reject"


def _state_reject_is_new(project, reason) -> bool:
    """STATE-REJECTED is logged when the reason CHANGES, not on every hook call: the last reason is persisted."""
    try:
        marker = _reject_marker(project)
        try:
            if marker.read_text(encoding="utf-8") == reason:
                return False
        except OSError:
            pass
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(reason, encoding="utf-8")
    except OSError:
        pass          # cannot persist: log every time rather than never
    return True


def _state_reject_clear(project) -> None:
    """A readable state ends the episode, so the same fault later is news again."""
    try:
        _reject_marker(project).unlink()
    except OSError:
        pass


def shadow(v, project, session_id, telemetry, audit, now=None) -> Verdict:
    """R8, shadow mode: the ONE place a refusal can be turned into an allow. Called on the finished verdict
    (after combine, after the side effects) by every agent's decision path.

    A deny/ask becomes `allow` + a two-line context naming the rule only when an HONOURED `guards` lift exists for
    THIS session (state.honoured: session-bound, after that session's latest start, unexpired). Never converted:
      * a GUARD INERT refusal (status "failed", or an INERT/NO_ rule code) -- a broken engine cannot read the state
        it would need to honour;
      * a refusal no rule claims (code "unknown");
      * anything when the state is unreadable or forged (the reader returns guards-on and a reason, logged here);
      * anything when this function itself fails (the original refusal stands).
    Every conversion writes `SHADOW <rule>` to the audit log FIRST (a raising append: if the line cannot be written the
    refusal stands), and while a guards lift is honoured EVERY call writes
    one (`SHADOW allow`, `SHADOW <rule> kept`), so an honoured lift can never be silent. The lock and Codex `.rules` are outside the engine."""
    try:
        refusal = v.decision in ("deny", "ask")
        if refusal:
            v = codes.classify(v)
        st = _state.read(str(Path(project) / ".skyguard" / "state.json"))
        if st.rejected:
            if _state_reject_is_new(project, st.reason):
                audit.log("skyguard", "STATE-REJECTED", st.reason)
            return v
        _state_reject_clear(project)
        if not st.lifts:
            return v
        start = _state.session_start(telemetry, session_id) if telemetry else None
        now = time.time() if now is None else now
        if not [x for x in _state.honoured(st, session_id, now, start) if x["kind"] == "guards"]:
            return v
        # From here a guards lift is honoured: EVERY call writes a SHADOW line (spec; closes Table M's silence row).
        if not refusal:
            audit.log_strict("skyguard", "SHADOW", "allow")
            return v
        if v.code in NEVER_SHADOW:                          # Plan 6 Task 11: guards off never lifts the lock
            audit.log_strict("skyguard", "SHADOW", "{} kept ({}; a lift rule)".format(v.code, v.decision))
            return v
        if v.status == "failed" or codes._inert(v.code) or v.code == "unknown":
            audit.log_strict("skyguard", "SHADOW", "{} kept ({})".format(v.code, v.decision))
            return v
        first = (v.reason.split("\n", 1)[0] if v.reason else "")[:_LINE_MAX]
        context = SHADOW_HEAD.format(v.code) + ("\n" + first if first else "")
        audit.log_strict("skyguard", "SHADOW", "{} ({})".format(v.code, v.decision))   # raises -> the refusal stands
        return Verdict("allow", "", context, v.status, v.code)
    except Exception:  # noqa: BLE001 -- shadow mode must never be able to break or loosen a decision by failing
        return v


def pre_tool(req, project, roots_fn, audit, telemetry="", now=None) -> Verdict:
    """All Codex PreToolUse work: the rules, then the side effects, then shadow mode on the finished verdict."""
    if req.failure:
        audit.log("codex-pre-tool", "REFUSE", req.failure)
        v = _failed(INERT_TRANSLATE.format(req.failure), "codex.TRANSLATE")
    elif req.event != "pre_tool":
        v = _failed(INERT_TRANSLATE.format("unexpected event {!r}".format(req.event)), "codex.TRANSLATE")
    else:
        v = _rules(req, roots_fn, audit)
    v = side_effects(req, project, audit, v)
    return shadow(v, project, req.session_id, telemetry, audit, now=now)
