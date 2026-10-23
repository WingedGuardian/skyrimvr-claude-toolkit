"""OpenCode V1 source contract; actual host enforcement requires qualification."""
import json
import sys
import time
from pathlib import Path

from modding_harness import contracts, transport, telemetry
from .. import patch_paths, portable, prefs


def handle(raw, project, event="pre"):
    try:
        action = transport.normalize("opencode", raw, shell_patch=patch_paths.shell_patch)
        if event == "session":
            text = prefs.render(project)
            return {"v": 1, "decision": "advise" if text else "allow", "inert": False,
                    "reason": None, "context": text or None}
        if event == "post":
            result = contracts.Result(validation="not_run")
            telemetry.Telemetry(project).record(action, "post", result, source="Skyrim OpenCode V1 adapter")
            context = ("Skyrim validation did not run: this OpenCode route has no qualified post-operation validator."
                       if action.surface in ("files", "shell", "mcp") else None)
            return {"v": 1, "decision": "advise" if context else "allow", "inert": False,
                    "reason": None, "context": context}
        if event != "pre":
            raise ValueError("unknown adapter event")
        outcome, count = portable.check(action, project, deadline=time.monotonic() + 20)
        result = outcome.result
        notes = "\n".join(result.warnings) if result.evaluation != "unguarded" or telemetry.announce(count) else ""
        if result.preservation == "verified":
            notes += "\nSave backup verified in the permanent neutral store before this call."
        return {"v": 1, "decision": "deny" if result.decision in ("deny", "ask") else "advise" if notes else "allow",
                "inert": result.evaluation == "failed", "reason": result.reason or None,
                "context": notes.strip() or None}
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        return {"v": 1, "decision": "deny", "inert": True,
                "reason": "Skyrim OpenCode adapter failed: " + type(exc).__name__, "context": None}


def main(root=None):
    root = Path(root or Path(__file__).resolve().parents[3]).resolve()
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    result = handle(sys.stdin.buffer.read(1024 * 1024 + 1), root, event)
    sys.stdout.buffer.write(("SKYGUARD-OPENCODE " + json.dumps(result, ensure_ascii=True) + "\n").encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
