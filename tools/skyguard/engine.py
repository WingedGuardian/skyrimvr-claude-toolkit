"""The one dispatch point between requests and rules.

Every agent's verdicts go through here, so a rule-module signature change (Plan 5: cwd=, shell=) is made
in one place. Rule modules are called, never edited, from this file.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import time
from pathlib import Path
from typing import Iterable, Optional

from . import (backup, codes, deletes, mcp_rules, paths as _paths, recovery, rules, self_rules, shell_rules, shellparse,
               snapshot, state as _state, targets, write_rules)
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
    """Plan 5 Task 11: the DevBench MCP tools (a server name containing devbench); any other MCP server is allowed by
    mcp_rules."""
    return codes.classify(mcp_rules.decide(tool_name, args, audit, roots=roots))


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    vs = list(verdicts)
    if not vs:   # nothing was evaluated; an empty fold must never read as "allow"
        return Verdict("deny", NO_VERDICTS, status="failed", code="engine.EMPTY_COMBINE")
    worst = max((v.status for v in vs), key=lambda s: _RANK.get(s, 2), default="evaluated")
    # Plan 6 final fix (lane A C1): within a decision a NEVER_SHADOW refusal outranks the others (an apply_patch that
    # deletes an install file and then adds .skyguard/state.json is W-SELF-STATE, not S-DEL-INSTALL), and one that a
    # stronger refusal outranks is carried beside it (Verdict.never), for shadow mode to return.
    kept = sorted((n for n in (_never_of(v) for v in vs) if n is not None), key=lambda n: n.decision != "deny")
    for want in ("deny", "ask"):
        hits = [v for v in vs if v.decision == want]
        if hits:
            top = next((v for v in hits if _is_never(v)), hits[0])
            if not _is_never(top) and top.never is None and kept:
                top = top._replace(never=kept[0])
            return top._replace(status=worst)
    advice, seen = Advice(), set()
    for v in vs:
        for item in [v.context] if v.context else []:
            if item not in seen:
                seen.add(item)
                advice.add(item)
    codes_seen = [v.code for v in vs if v.context]
    return Verdict("allow", "", advice.joined(), worst, codes_seen[0] if codes_seen else "allow")


def _is_never(v: Verdict) -> bool:
    return v.decision in ("deny", "ask") and codes.classify(v).code in NEVER_SHADOW


def _never_of(v: Verdict):
    """The NEVER_SHADOW refusal `v` is or carries (Verdict.never), classified; None for neither."""
    if _is_never(v):
        return codes.classify(v)
    n = v.never
    return codes.classify(n) if isinstance(n, Verdict) and n.decision in ("deny", "ask") else None


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
    # Merge of master into plan6 (2026-10-05): a delete or move-away of the guards' state is W-SELF-STATE (the write rule
    # below decides it), never S-DEL-INSTALL -- guards off shadows S-DEL-*, and it never shadows W-SELF-STATE
    # (NEVER_SHADOW). Plan 6 orders the shell rules the same way (shell_rules.decide: S-SELF-STATE before the deletes).
    if (self_rules.kind_of(a, roots, os.environ) if a is not None else self_rules.text_kind(path)) == self_rules.STATE:
        return None
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
    # Plan 8 T6 (rt-fix-b concern 6): the rule's own hook (S-DEL-* -> protect-bash, the same label the shell delete
    # rules write and the namespace of its verdict code), not protect-files.
    audit.rule(rules.RULES[rid]["hook"], rid, "deny", reason)
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


def side_effects(req, project, audit, v, roots_fn=None, deadline=None) -> Verdict:
    """Snapshot and backups, whatever the decision (as on Claude, where those hooks run beside the guard).
    Each step has its own try: one failing never skips the next and never changes the decision. Task 15: the recovery
    snapshot (load order, INIs, the named save) goes first, inside `deadline` (a time.monotonic time)."""
    partial = False
    if req.kind == "shell":
        try:
            recovery.run(project, req.command, False, audit, _soft_roots(roots_fn) if roots_fn else None,
                         cwd=req.cwd or "", shell=req.shell or "", agent=req.agent, session_id=req.session_id,
                         deadline=deadline)
        except Exception:
            partial = True
            audit.log(recovery.HOOK, "FAILED", "the recovery snapshot raised -- none was taken")
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
                backup.run(project, "apply_patch", p, False, audit, req.agent, req.session_id)
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
# Plan 8 T6 (T4 audit K1): a shadowed ASK ran without its confirmation prompt; "denied" was not what happened.
SHADOW_ASK_HEAD = "Guards OFF: this would have asked you first ({}); it ran without asking."
_LINE_MAX = 190
# Plan 6 Task 11 (R7, R8): guards off never lifts the lock. Without this an agent could take `guards off` (a D6 lift it
# may take) and then run `lock disable` with no ask -- a persistent off nobody approved -- or unlock a vanilla file
# outside the CLI. These refusals stand under shadow mode, logged as `SHADOW <code> kept`.
NEVER_SHADOW = frozenset(("shell.S-LIFT-PERMANENT", "shell.S-LIFT-RAW", "shell.S-LIFT-UNLOCKCMD"))
# Plan 6 Task 12 (R12): nor does it open the state file. Shadowed, a `guards off` would let the agent then write
# .skyguard/state.json directly -- a persistent disable or an extended lift that nobody approved.
NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE", "write.W-SELF-STATE"))


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


def _relock_due(project, st, now, audit) -> None:
    """Plan 6 final fix (A-I3 / C-I1 ruling): the first guarded call after a lock lift's expires_at relocks the files
    it opened (lock.relock_expired) and logs it; session start stays the backstop. Costs a time compare per lift while
    none is due; never changes or breaks the decision (a failure is logged, never raised)."""
    if not any(x["kind"] == "lock" and _state.lift_expired(x, now) for x in st.lifts):
        return
    from . import lock
    try:
        res = lock.relock_expired(project, now)
    except _state.StateBusy:
        audit.log("skyguard", "RELOCK", "skipped: the state file is busy; the next guarded call retries")
        return
    except (Exception, lock.SandboxViolation) as e:  # noqa: BLE001 -- the call's decision must stand
        audit.log("skyguard", "RELOCK", "FAILED: an expired lock lift could not be relocked ({})".format(
            e.__class__.__name__))
        return
    if res is None:
        return
    names = res.data.get("relocked", [])
    if names:
        what = "relocked {} file(s): {}".format(len(names), ", ".join(names[:5]) + (" ..." if len(names) > 5 else ""))
    else:
        what = res.lines[0] if res.lines else "nothing relocked"
    audit.log("skyguard", "RELOCK", "lock lift expired: " + what + ("" if res.exit == 0 else " (exit {})".format(res.exit)))


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
        now = time.time() if now is None else now
        _relock_due(project, st, now, audit)          # Plan 6 final fix: a lock lift's expiry relocks (A-I3 / C-I1)
        start = _state.session_start(telemetry, session_id) if telemetry else None
        if not [x for x in _state.honoured(st, session_id, now, start) if x["kind"] == "guards"]:
            return v
        # From here a guards lift is honoured: EVERY call writes a SHADOW line (spec; closes Table M's silence row).
        if not refusal:
            audit.log_strict("skyguard", "SHADOW", "allow")
            return v
        if v.code in NEVER_SHADOW:                          # Plan 6 Task 11: guards off never lifts the lock
            audit.log_strict("skyguard", "SHADOW", "{} kept ({}; a lift rule)".format(v.code, v.decision))
            return v
        # Plan 6 final fix (lane A C1): the call ALSO carries a NEVER_SHADOW refusal that a decoy outranked. Shadow mode
        # would allow the decoy and with it the whole command, so the call is refused by THAT rule, and the log says so.
        kept = _never_of(v)
        if kept is not None:
            audit.log_strict("skyguard", "SHADOW", "{} kept ({}; a lift rule, beside {})".format(
                kept.code, kept.decision, v.code))
            return kept._replace(status=v.status, never=None)
        if v.status == "failed" or codes._inert(v.code) or v.code == "unknown":
            audit.log_strict("skyguard", "SHADOW", "{} kept ({})".format(v.code, v.decision))
            return v
        first = (v.reason.split("\n", 1)[0] if v.reason else "")[:_LINE_MAX]
        head = SHADOW_ASK_HEAD if v.decision == "ask" else SHADOW_HEAD
        context = head.format(v.code) + ("\n" + first if first else "")
        audit.log_strict("skyguard", "SHADOW", "{} ({})".format(v.code, v.decision))   # raises -> the refusal stands
        return Verdict("allow", "", context, v.status, v.code)
    except Exception:  # noqa: BLE001 -- shadow mode must never be able to break or loosen a decision by failing
        return v


def pre_tool(req, project, roots_fn, audit, telemetry="", now=None, deadline=None) -> Verdict:
    """All Codex PreToolUse work: the rules, then the side effects, then shadow mode on the finished verdict."""
    if req.failure:
        audit.log("codex-pre-tool", "REFUSE", req.failure)
        v = _failed(INERT_TRANSLATE.format(req.failure), "codex.TRANSLATE")
    elif req.event != "pre_tool":
        v = _failed(INERT_TRANSLATE.format("unexpected event {!r}".format(req.event)), "codex.TRANSLATE")
    else:
        v = _rules(req, roots_fn, audit)
    v = side_effects(req, project, audit, v, roots_fn, deadline)
    return shadow(v, project, req.session_id, telemetry, audit, now=now)
