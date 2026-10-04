"""Entry point the per-agent hook wrappers call: run.py <agent> <role> --root .. --root-win .. --project ..

Exits 1 with NOTHING on stdout on any internal failure, so a wrapper can tell "the engine decided"
from "the engine broke" -- the second must never read as allow (spec section 5.1)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skyguard import backup, mcp_rules, paths, session, shell_rules, snapshot, write_rules  # noqa: E402
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
                                     "snapshot-before-tool", "session-kb-guard", "protect-mcp"])
    ap.add_argument("--root", default="")
    ap.add_argument("--root-win", default="")
    ap.add_argument("--project", required=True)
    ap.add_argument("--bash", default="bash")
    a = ap.parse_args(argv)
    adapter = ADAPTERS[a.agent]
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    p = adapter.extract(raw)
    audit = Audit(a.project)
    # The declared roots file, read from the WINDOWS root when there is one (native python cannot open /c/...).
    paths_file = Path(a.root_win or a.root) / ".claude" / paths.PATHS_FILE
    if a.role == "protect-bash":
        # Task 12: a command that is not a string (an array, number, object, boolean) is no command the guard can
        # judge -- `command` renders it as JSON text, which the rules would then read as if the agent had typed it.
        # (An empty or whitespace-only STRING is refused by shell_rules.decide itself.)
        if p.get("ws_monitor"):
            # Task 13b: a WebSocket Monitor runs no command (no `command` key at all), so there is nothing to judge.
            v = Verdict()
        elif p["command_type"] != "string":
            v = shell_rules.decide("", paths.Roots("", ""), audit)
        else:
            try:
                env = paths.read_config(paths_file, dict(os.environ))
            except (OSError, ValueError):
                env = None
            if env is None:
                v = Verdict("deny", ENV_UNREADABLE)
            else:
                # Plan 4 merge point: its adapter registry supplies cwd/shell for Codex (shell="powershell", E3).
                v = shell_rules.decide(p["command"], paths.roots(a.root, a.root_win, env), audit,
                                       cwd=p.get("cwd", ""), shell=p.get("shell", ""))
    elif a.role == "protect-files":
        # Task 9: W-PAPYRUS and W-INSTALL decide on where the file RESOLVES, so this role needs the roots too. Its
        # rules are notes plus a name-based deny, so an unreadable paths file costs only the declared roots (the
        # install this hook runs from is still known); it is not a reason to refuse an edit.
        try:
            env = paths.read_config(paths_file, dict(os.environ))
        except (OSError, ValueError):
            env = None
        roots = paths.roots(a.root, a.root_win, env if env is not None else {})
        v = write_rules.decide(p["file_path"], audit, roots=roots, cwd=p.get("cwd", ""))
    elif a.role == "protect-mcp":
        # Task 11: the DevBench MCP tools. Only the save-exists check reads the declared config dirs, so an unreadable
        # paths file costs that one answer (it then asks); it is not a reason to refuse every call.
        try:
            env = paths.read_config(paths_file, dict(os.environ))
        except (OSError, ValueError):
            env = None
        roots = paths.roots(a.root, a.root_win, env if env is not None else {})
        v = mcp_rules.decide(p["tool_name"], p["mcp_input"], audit, roots=roots)
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
