#!/usr/bin/env python3
"""Skyrim's offline generic Action v1 entry; host adapters own blocking transport."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
from modding_harness import contracts, generic, installation, telemetry
from skyguard import portable, save_store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--transcript", default="")
    check.add_argument("--preview", action="store_true", help="read-only policy/scope report; never execution authority")
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--session-id", required=True)
    sub.add_parser("capabilities")
    sub.add_parser("preferences")
    installation.add_arguments(sub)
    sub.add_parser("save-list")
    restore = sub.add_parser("save-restore")
    restore.add_argument("transaction")
    destination = restore.add_mutually_exclusive_group()
    destination.add_argument("--to", type=Path)
    destination.add_argument("--in-place", action="store_true")
    restore.add_argument("--path", action="append", default=[])
    restore.add_argument("--host", default="generic")
    restore.add_argument("--session-id", default="")
    restore.add_argument("--transcript", default="")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    exit_status = 0
    try:
        if args.command == "coverage":
            result = telemetry.Telemetry(root).report(args.session_id)
        elif args.command == "preferences":
            from modding_harness import preferences
            result = {"context": preferences.render(root),
                      "scope": "explicit My preferences section only; unclassified notes remain notes"}
        elif args.command in installation.COMMANDS:
            from skyguard import ordered_upgrade, upgrade_render
            result = installation.handle(args, root, local_files=(*ordered_upgrade.POLICY_FILES, ".claude/hooks/test_protect_save_access.py",
                "skyrim-paths.env", ".claude/skyrim-paths.env"), candidate_preparer=upgrade_render.prepare,
                candidate_validator=upgrade_render.validate, review_builder=ordered_upgrade.proposal)
        elif args.command == "save-list":
            from modding_harness import workspace
            target = save_store.store(root)
            rows, unreadable = workspace.summaries(target)
            result = {"transactions": rows, "unreadable": unreadable, "destination": str(target.root),
                      "blob_verification": "runs for selected saves on restore",
                      "legacy_recovery": "tools/skyguard.sh snapshot list --kind save"}
        elif args.command == "save-restore":
            result = save_store.restore_result(root, args.transaction, args.path, args.to, args.in_place,
                                               host=args.host, session_id=args.session_id, transcript=args.transcript,
                                               command=json.dumps(sys.argv[1:] if argv is None else argv))
        elif args.command == "capabilities":
            result = {"api_version": 1, "profile": "skyrim", "unknown_policy": "warn and allow",
                      "host_enforcement": "unmeasured; qualify the actual host transport and review hooks",
                      "surfaces": {"files": "Python rules", "shell": "Bash/PowerShell rules", "mcp": "DevBench only"},
                      "local_policy_upgrade": "upgrade-stage --integrate-local-policy; exact reviewed Windows sources/groups, explicit settings choices; host trust remains separate",
                      "save_recovery": "save-list/save-restore; guarded in-place; both legacy formats remain readable via snapshot"}
        else:
            action = generic.parse(sys.stdin.buffer.read(1024 * 1024 + 1))
            outcome, count = portable.check(action, root, transcript=args.transcript, deadline=time.monotonic() + 20,
                                             preview=args.preview)
            result = portable.reply(outcome, count)
            if args.preview:
                result.update(preview=True, execution_authorized=False, runtime_writes=False)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if args.command in installation.COMMANDS:
            result, exit_status = installation.failure(args.command, exc, "Skyrim adapter failed")
        else:
            result = {"api_version": 1, "result": contracts.Result("deny", "failed", reason=
                      "Skyrim adapter failed: " + type(exc).__name__).as_dict(), "transactions": []}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=True) + "\n").encode("ascii"))
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
