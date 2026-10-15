"""Offline adapter contract and a toy third-game integration, without named hosts.

Game toolkits supply a policy callback to `handle`. The example CLI implements
only the toy file policy: it never claims to enforce a game's shell/MCP rules.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import profiles
from .contracts import Action, Result, unknown_tool
from .dispatch import Dispatcher, Evaluation, Mutation, SavePlan
from .preservation import contains
from .telemetry import Telemetry, announce

FIELDS = frozenset(Action.__dataclass_fields__)
STRINGS = FIELDS - {"paths", "operations", "provenance", "api_version"}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate request field")
        result[key] = value
    return result


def parse(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError("adapter request exceeds 1 MiB")
    data = json.loads(raw, object_pairs_hook=_object)
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("request must be a version 1 action with known fields")
    if type(data.get("api_version")) is not int or data["api_version"] != 1:
        raise ValueError("api_version must be integer 1")
    for key in STRINGS:
        if key in data and not isinstance(data[key], str):
            raise ValueError(key + " must be text")
    if not all(data.get(key, "").strip() for key in ("host", "tool", "surface")):
        raise ValueError("host, tool and surface are required")
    for key in ("paths", "operations"):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError(key + " must be an array of nonempty strings")
        data[key] = tuple(values)
    if len(data["paths"]) != len(data["operations"]):
        raise ValueError("paths and operations must be parallel")
    if any(op not in ("read", "write", "delete", "move") for op in data["operations"]):
        raise ValueError("unrecognized operation")
    if "provenance" in data and not isinstance(data["provenance"], dict):
        raise ValueError("provenance must be an object")
    # A relative path cannot silently use the hook process's directory.
    cwd = data.get("cwd", "")
    if cwd and not os.path.isabs(cwd):
        raise ValueError("cwd must be absolute")
    if any(not os.path.isabs(path) for path in data["paths"]) and not cwd:
        raise ValueError("relative paths require an absolute cwd")
    data["paths"] = tuple(str(Path(cwd, p).absolute()) for p in data["paths"])
    return Action(**data)


def file_policy(profile):
    """Example policy factory: exact file operations and whole-root protection.

    Parsing a shell command or a game helper belongs in the toolkit's policy.
    The profile is passed in, demonstrating a third game without core changes.
    """
    def evaluate(action):
        if action.surface != "files":
            return Evaluation(unknown_tool(action))
        mutations = []
        for path, op in zip(action.paths, action.operations):
            if op == "read":
                continue
            if any(contains(path, root) for root in profile.save_roots):
                return Evaluation(Result("deny", "evaluated", reason="whole save-folder mutation is refused"))
            if any(contains(root, path) for root in profile.save_roots):
                if any(c in path for c in "*?["):
                    return Evaluation(Result(evaluation="evaluated"), SavePlan(unresolved="submit a profile-resolved wildcard set"))
                if any(Path(path).name.lower().endswith(ext) for ext in profile.extensions):
                    mutations.append(Mutation(path, op))
        return Evaluation(Result(evaluation="evaluated"), SavePlan(tuple(mutations)))
    return evaluate


def handle(raw, workspace, profile, policy, local_restrictions=(), transcript="", preview=False, deadline=None):
    try:
        action = parse(raw)
    except (ValueError, UnicodeError, RecursionError):
        # Do not echo a payload that may contain credentials or private data.
        return {"api_version": 1, "result": Result("deny", "failed", reason="malformed version 1 adapter action").as_dict(),
                "transactions": [], "telemetry": "unavailable"}
    outcome = Dispatcher(workspace, profile, policy, local_restrictions).pre(
        action, transcript, deadline, preserve=not preview)
    count = Telemetry(workspace).record(action, "preview" if preview else "pre", outcome.result,
                                        source="generic adapter")
    response = {"api_version": 1, "result": outcome.result.as_dict(), "transactions": list(outcome.transactions),
                "telemetry": "recorded" if count is not None else "unavailable", "preview": preview}
    if outcome.result.evaluation == "unguarded":
        response["unknown_occurrences"] = count
        response["announce_unknown"] = announce(count)
    return response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("toy-check", help="toy third-game file policy; JSON Action on stdin")
    check.add_argument("--save-root", required=True, type=Path)
    check.add_argument("--transcript", default="")
    check.add_argument("--preview", action="store_true")
    report = sub.add_parser("coverage")
    report.add_argument("--session-id", required=True)
    sub.add_parser("capabilities")
    args = parser.parse_args(argv)
    if args.command == "coverage":
        output = Telemetry(args.workspace).report(args.session_id)
    elif args.command == "capabilities":
        output = {"api_version": 1, "profile": "toy example", "runtime": "offline stdlib Python 3.10-3.13",
                  "surfaces": {"files": "library fixtures", "shell": "unguarded", "mcp": "unguarded"},
                  "host_enforcement": "unmeasured; install and qualify an adapter separately",
                  "unknown_policy": "warn and allow", "approval_readers": ["qualified Claude transcript", "interactive Codex transcript"],
                  "trust": "installation does not grant hook trust"}
    else:
        profile = profiles.toy([args.save_root.resolve()])
        output = handle(sys.stdin.buffer.read(1024 * 1024 + 1), args.workspace, profile, file_policy(profile),
                        transcript=args.transcript, preview=args.preview)
    sys.stdout.buffer.write((json.dumps(output, ensure_ascii=True) + "\n").encode("ascii"))
    return 0  # Structured decision transport; host adapters own blocking semantics.


if __name__ == "__main__":
    raise SystemExit(main())
