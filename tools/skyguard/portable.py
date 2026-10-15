"""Skyrim policy bridge for the shared synchronous dispatcher.

The Python rules still decide game policy. Local restrictions run before the
shared store preserves bytes or permission is emitted. Legacy entrypoints stay
available while each new host path is qualified.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
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


def check(action, project, *, transcript="", deadline=None, local_restrictions=(), helper=False):
    audit = Audit(str(project))
    req = request(action, transcript)
    last = [Verdict()]
    known = req.kind in ("shell", "write") or req.kind == "mcp" and mcp_rules.judged_server(req.mcp_tool)
    if known:
        config = paths.read_install(str(project), os.environ)
        main = paths.main_worktree(str(project))
        if main:
            config = paths.merge_configs(config, paths.read_install(main, {}))
        roots = paths.roots(str(project), str(project), config)
        if roots.malformed:
            raise ValueError("protection roots could not be read")
        folders = [Path(folder) / "Saves" for folder in roots.config_dirs]
        fallback = mcp_rules.saves_folder(roots, os.environ)
        if fallback:
            folders.append(Path(fallback))
        profile = profiles.skyrim(folders)
    else:
        roots = paths.Roots("", "")
        profile = profiles.skyrim()

    def policy(_action):
        value = codes.classify(engine._rules(req, lambda: roots, audit))
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
        value = engine.shadow(codes.classify(value), str(project), req.session_id, "", audit)
        last[0] = value
        if not known:
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
    side = engine.side_effects(req, str(project), audit, value, lambda: roots, deadline)
    if side.status == "partial" and result.evaluation == "evaluated":
        result = replace(result, evaluation="partial")
    if outcome.transactions:
        audit.log("portable-pre-tool", "SAVE-BACKUP", "verified neutral transactions: " + ", ".join(outcome.transactions))
    count = telemetry.Telemetry(project).record(action, "pre", result, source="Skyrim portable profile")
    return replace(outcome, result=result), count


def reply(outcome, count=None):
    output = {"api_version": 1, "result": outcome.result.as_dict(), "transactions": list(outcome.transactions)}
    if outcome.result.evaluation == "unguarded":
        output.update(unknown_occurrences=count, announce_unknown=telemetry.announce(count))
    return output
