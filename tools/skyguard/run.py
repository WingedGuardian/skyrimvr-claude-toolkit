"""Entry point the per-agent hook wrappers call: run.py <agent> <role> --root .. --root-win .. --project ..

Exits 1 with NOTHING on stdout on any internal failure, so a wrapper can tell "the engine decided"
from "the engine broke" -- the second must never read as allow (spec section 5.1)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skyguard import backup, paths, session, shell_rules, snapshot, write_rules  # noqa: E402
from skyguard.adapters import claude  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

ADAPTERS = {"claude": claude}


ENV_UNREADABLE = (
    "GUARD INERT: protect-bash.sh found .claude/skyrim-paths.env but could not read it, so it cannot tell "
    "which directories are the Skyrim install. Refusing rather than guarding the wrong set. "
    "Fix or delete that file (re-run setup.sh)."
)


def _emit(text: str) -> None:
    """One write of UTF-8 bytes, so a console code page cannot raise and no partial output can escape."""
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=sorted(ADAPTERS))
    ap.add_argument("role", choices=["protect-bash", "protect-files", "backup-before-edit",
                                     "snapshot-before-tool", "session-kb-guard"])
    ap.add_argument("--root", default="")
    ap.add_argument("--root-win", default="")
    ap.add_argument("--project", required=True)
    ap.add_argument("--bash", default="bash")
    a = ap.parse_args(argv)
    adapter = ADAPTERS[a.agent]
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    p = adapter.extract(raw)
    audit = Audit(a.project)
    if a.role == "protect-bash":
        if not p["command"]:
            v = shell_rules.decide("", paths.Roots("", ""), audit)
        else:
            try:
                env = paths.read_env(Path(a.root_win or a.root) / ".claude" / "skyrim-paths.env", dict(os.environ))
            except (OSError, ValueError):
                env = None
            if env is None:
                v = Verdict("deny", ENV_UNREADABLE)
            else:
                v = shell_rules.decide(p["command"], paths.roots(a.root, a.root_win, env), audit)
    elif a.role == "protect-files":
        v = write_rules.decide(p["file_path"], audit)
    elif a.role == "backup-before-edit":
        backup.run(a.project, p["tool_name"], p["file_path"], p["empty"], audit)
        v = Verdict()
    elif a.role == "snapshot-before-tool":
        snapshot.run(a.project, p["command"], p["empty"], audit)
        v = Verdict()
    else:
        out = session.run(a.project, "" if p["empty"] else p["source"], a.bash, audit)
        _emit(out)
        return 0
    _emit(adapter.render(v))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(1)
