#!/usr/bin/env python3
"""Skyrim's offline generic Action v1 entry; host adapters own blocking transport."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
from modding_harness import contracts, generic, telemetry
from skyguard import portable, save_store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--transcript", default="")
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--session-id", required=True)
    sub.add_parser("capabilities")
    sub.add_parser("preferences")
    upgrade = sub.add_parser("upgrade-preview")
    upgrade.add_argument("--candidate", required=True, type=Path)
    upgrade.add_argument("--previous", type=Path)
    upgrade.add_argument("--installed-root", type=Path)
    stage = sub.add_parser("upgrade-stage")
    stage.add_argument("--candidate", required=True, type=Path)
    stage.add_argument("--previous", type=Path)
    stage.add_argument("--installed-root", type=Path)
    stage.add_argument("--source", required=True, type=Path)
    stage.add_argument("--destination", required=True, type=Path)
    stage.add_argument("--owned-command", action="append", default=[])
    stage.add_argument("--local-file", action="append", default=[])
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
        elif args.command in ("upgrade-preview", "upgrade-stage"):
            from modding_harness import transport, upgrade
            def read_manifest(path):
                with path.open("rb") as stream:
                    return transport.load(stream.read(1024 * 1024 + 1))
            candidate = read_manifest(args.candidate)
            previous = read_manifest(args.previous) if args.previous else None
            if args.command == "upgrade-preview":
                result = upgrade.preview(args.installed_root or root, candidate, previous, deadline=time.monotonic() + 20)
            else:
                bundle = upgrade.stage_review_bundle(args.installed_root or root, args.source, args.destination,
                    candidate, previous, owned_commands=args.owned_command,
                    local_files=(".claude/hooks/protect-save-access.py", ".claude/hooks/save-access-entry.ps1",
                                 ".claude/hooks/restore-save-backup.py", ".claude/hooks/test_protect_save_access.py", *args.local_file))
                result = {"bundle": str(args.destination.resolve()), "complete": True,
                          "collisions": bundle["plan"]["collisions"], "application_authorized": False,
                          "ordered_dispatch_review_required": bundle["ordered_dispatch_review_required"]}
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
                      "save_recovery": "save-list/save-restore; guarded in-place; both legacy formats remain readable via snapshot"}
        else:
            action = generic.parse(sys.stdin.buffer.read(1024 * 1024 + 1))
            outcome, count = portable.check(action, root, transcript=args.transcript, deadline=time.monotonic() + 20)
            result = portable.reply(outcome, count)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        if args.command == "upgrade-stage":
            exit_status = 2
        result = {"api_version": 1, "result": contracts.Result("deny", "failed", reason=
                  "Skyrim adapter failed: " + type(exc).__name__).as_dict(), "transactions": []}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=True) + "\n").encode("ascii"))
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
