"""Skyrim policy bridge for the shared synchronous dispatcher.

The Python rules still decide game policy. Local restrictions run before the
shared store preserves bytes or permission is emitted. Legacy entrypoints stay
available while each new host path is qualified.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from modding_harness import contracts, dispatch, profiles, telemetry
from . import codes, engine, local_policy, mcp_rules, paths, saves
from .audit import Audit
from .request import Request
from .verdict import Verdict


def request(action, transcript=""):
    ops = []
    for index, operation in enumerate(action.operations):
        ops.append("move_from" if operation == "move" else "delete" if operation == "delete" else
                   "move_to" if index and action.operations[index - 1] == "move" else "update")
    kind = {"files": "write", "shell": "shell", "mcp": "mcp"}.get(action.surface, "other")
    if kind == "write":
        kept = [(path, op) for path, op, operation in zip(action.paths, ops, action.operations) if operation != "read"]
        paths_, ops = zip(*kept) if kept else ((), ())
        if not kept:
            kind = "other"
    else:
        paths_ = action.paths
    raw = action.provenance.get("raw_payload", {})
    arguments = raw.get("args", raw.get("tool_input", {})) if isinstance(raw, dict) else {}
    arguments = action.provenance.get("arguments", arguments)
    return Request(agent=action.host, event="pre_tool", kind=kind, shell=action.shell,
                   command=action.command, paths=tuple(paths_), ops=tuple(ops),
                   mcp_tool=action.tool if kind == "mcp" else "", mcp_args=json.dumps(arguments),
                   cwd=action.cwd, tool_name=action.tool, session_id=action.session_id,
                   transcript_path=transcript)


def mutations(change):
    counted = saves.count(change)
    if counted.unknown:
        raise ValueError("save identity could not be resolved")
    return tuple(dispatch.Mutation(pair.ess, op) for pair in saves._existing(counted) for op in pair.ops), counted


def check(action, project, *, transcript="", deadline=None, local_restrictions=(), helper=False, _legacy=None):
    audit = _legacy.audit if _legacy else Audit(str(project))
    req = _legacy.request if _legacy else request(action, transcript)
    last = [Verdict()]
    known = req.kind in ("shell", "write") or req.kind == "mcp" and mcp_rules.judged_server(req.mcp_tool)
    if _legacy:
        roots = _legacy.roots or paths.Roots("", "")
    elif known:
        config = paths.read_install(str(project), os.environ)
        main = paths.main_worktree(str(project))
        if main:
            config = paths.merge_configs(config, paths.read_install(main, {}))
        roots = paths.roots(str(project), str(project), config)
        if roots.malformed:
            raise ValueError("protection roots could not be read")
    else:
        roots = paths.Roots("", "")
    if known:
        folders = [Path(folder) / "Saves" for folder in roots.config_dirs]
        fallback = mcp_rules.saves_folder(roots, os.environ)
        if fallback:
            folders.append(Path(fallback))
        profile = profiles.skyrim(folders)
    else:
        profile = profiles.skyrim()

    def policy(_action):
        if not _legacy:
            engine.hold_if_shadowing(str(project), audit)
        value = codes.classify(_legacy.value if _legacy else engine._rules(req, lambda: roots, audit))
        last[0] = value
        plan = dispatch.SavePlan()
        if value.saves is not None:
            change, rest = value.saves
            if change.unknown:
                return dispatch.Evaluation(contracts.Result("deny", "partial", "failed", reason=value.reason))
            changed, counted = mutations(change)
            plan = dispatch.SavePlan(changed, bool(counted.patterns), helper=helper, recheck=
                (lambda: mutations(change)[0]) if counted.patterns or helper else None)
            value = rest
        # Resolve the toolkit's existing, session-bound shadow policy exactly
        # once. A later shadow pass must not relax a refused preservation.
        value = engine.shadow(codes.classify(value), str(project), req.session_id,
                              _legacy.telemetry if _legacy else "", audit, now=_legacy.now if _legacy else None)
        last[0] = value
        if req.tool_name == "write_stdin" and req.kind == "other" and value.decision == "allow":
            result = contracts.Result(evaluation="partial", reason=
                "no command submitted in this payload; fresh stdin interception remains unqualified")
        elif not known and value.decision == "allow" and value.status != "failed":
            result = contracts.unknown_tool(action)
            if value.context:
                result = replace(result, warnings=result.warnings + (value.context,))
        else:
            result = contracts.Result(value.decision, value.status, reason=value.reason,
                                      warnings=(value.context,) if value.context else ())
        return dispatch.Evaluation(result, plan)

    restrictions = (local_policy.restriction(project, deadline), *local_restrictions)
    outcome = dispatch.Dispatcher(project, profile, policy, restrictions).pre(action, transcript, deadline)
    result = outcome.result
    # Ordinary config/script snapshots remain non-blocking and distinct from
    # the permanent save preservation that has already decided this call.
    value = Verdict(result.decision, result.reason, "\n".join(result.warnings),
                    "partial" if result.evaluation == "unguarded" else result.evaluation, last[0].code)
    side = (engine.side_effects(req, str(project), audit, value, lambda: roots, deadline)
            if not _legacy or _legacy.side_effects else value)
    if side.status == "partial" and result.evaluation == "evaluated":
        result = replace(result, evaluation="partial")
    if _legacy:
        _legacy.verdict = side
    if outcome.transactions:
        audit.log("portable-pre-tool", "SAVE-BACKUP", "verified neutral transactions: " + ", ".join(outcome.transactions))
    if result.decision in ("deny", "ask"):
        audit.log("portable-pre-tool", "REFUSED", result.reason)
    count = telemetry.Telemetry(project).record(action, "pre", result, source="Skyrim portable profile")
    return replace(outcome, result=result), count


@dataclass
class _Legacy:
    request: Request
    value: Verdict
    roots: object
    audit: object
    telemetry: str
    now: object
    side_effects: bool = True
    verdict: object = None


def legacy_pre_tool(req, project, roots_fn, audit, telemetry_path="", now=None, deadline=None,
                    *, precomputed=None, side_effects=True, metadata=None):
    """Keep the qualified host parser/rendering while sharing dispatch ordering."""
    engine.hold_if_shadowing(project, audit)
    captured = []
    def roots():
        if not captured:
            captured.append(roots_fn())
        return captured[0]
    if precomputed is not None:
        value = codes.classify(precomputed)
        engine._soft_roots(roots)
    elif req.failure or req.event != "pre_tool":
        value = engine._failed(engine.INERT_TRANSLATE.format(req.failure or "unexpected event"), "codex.TRANSLATE")
    else:
        value = codes.classify(engine._rules(req, roots, audit))
    try:
        arguments = json.loads(req.mcp_args) if req.mcp_args else {}
    except ValueError:
        arguments = {}
    metadata = metadata or {}
    action = contracts.Action(req.agent, req.tool_name, {"write": "files", "shell": "shell", "mcp": "mcp"}.get(req.kind, "unknown"),
                              cwd=req.cwd, shell=req.shell or "", session_id=req.session_id,
                              tool_call_id=metadata.get("tool_use_id", ""), host_version=metadata.get("host_version", "unknown"),
                              command=req.command or " ".join(req.paths),
                              paths=tuple(req.paths), operations=tuple("move" if op == "move_from" else
                              "delete" if op == "delete" else "write" for op in req.ops),
                              provenance={"arguments": arguments, "parser": "qualified legacy Request bridge"})
    if len(action.paths) != len(action.operations):
        action = replace(action, operations=("write",) * len(action.paths))
    context = _Legacy(req, value, captured[0] if captured else None, audit, telemetry_path, now, side_effects)
    outcome, count = check(action, project, transcript=req.transcript_path, deadline=deadline, _legacy=context)
    result = outcome.result
    answer = context.verdict
    if result.preservation == "verified" and value.saves is not None:
        change, _rest = value.saves
        counted = saves.count(change)
        note = saves._render("S-SAVE-BACKUP", (("file", saves._phrase([pair.stem for pair in saves._existing(counted)])),
                                               ("op", saves.NOTE_BEFORE)))
        answer = saves._with_note(answer, note)
        audit.rule("portable-pre-tool", "S-SAVE-BACKUP", "allow", note)
        if value.decision == "deny":
            audit.log("portable-pre-tool", "SAVE-APPROVED", "canonical typed grant verified; saves preserved before permission")
    elif result.decision == "deny" and result.preservation == "required":
        from modding_harness import approval
        grants = [line for line in result.reason.splitlines() if line.startswith(approval.KEY + " ")]
        reason = value.reason + "\nIf approved, the user types:\n" + "\n".join(grants)
        if req.agent not in ("claude", "codex") or not req.session_id:
            reason += "\nThis host/session has no qualified approval reader; bulk changes remain refused."
        answer = answer._replace(code=codes.rule_code("S-SAVE-APPROVAL"),
                                 reason=reason)
    elif result.preservation == "failed":
        # An unresolved save is a policy refusal with its original explanation,
        # not a failed attempt to copy already identified save bytes.
        if not (value.saves is not None and value.saves.change.unknown):
            reason = saves._render("S-SAVE-BACKUP-FAILED", (("file", "the saves"), ("op", result.reason)))
            answer = answer._replace(code=codes.rule_code("S-SAVE-BACKUP-FAILED"), reason=reason)
    if result.evaluation == "unguarded" and not telemetry.announce(count):
        answer = answer._replace(context="")
    return codes.classify(answer)


def reply(outcome, count=None):
    output = {"api_version": 1, "result": outcome.result.as_dict(), "transactions": list(outcome.transactions)}
    if outcome.result.evaluation == "unguarded":
        output.update(unknown_occurrences=count, announce_unknown=telemetry.announce(count))
    return output
