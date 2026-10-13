"""The `skyguard` command line: `lock`, `guards` and `doctor` (Plan 6 Task 8).

    python tools/skyguard lock <lock|unlock [file...]|status [--json]|disable|enable> [--root R] [--session S]
    python tools/skyguard guards <on|off|status> [--root R] [--session S] [--reason TEXT]   shadow mode (R8)
    python tools/skyguard doctor [--root R] [--json]  the hook health report (tools/skyguard/health.py)
    python tools/skyguard snapshot <list|restore <id> [--path F] [--to DIR | --in-place]> [--project P]
                                                     script and recovery snapshots (tools/skyguard/snapcli.py)

Exit codes follow the X4 contract: 0 verified, 1 partial or disagreeing, 2 refused or unreadable, 3 unsupported.
"""
from __future__ import annotations

import sys

USAGE = "usage: skyguard <lock|guards|doctor|snapshot> ... (see tools/skyguard/cli.py)"
GUARDS_USAGE = "usage: guards <off|on|status> [--project P] [--session S] [--reason TEXT (off only)]"
REASON_MAX = 120          # characters; skydoctor shows 60 of them and the session-start banner 80, both sanitised


def _reason_problem(text: str) -> str:
    """'' when `text` is a usable `guards off --reason`: one line, no control or line-separator characters, not blank,
    at most REASON_MAX characters. The text is stored as given; every reader (skydoctor, the session-start banner)
    still passes it through session._clean, so a recorded reason stays data, never an instruction."""
    if not text.strip():
        return "the reason is blank"
    if len(text) > REASON_MAX:
        return "the reason is longer than %d characters" % REASON_MAX
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in "\u2028\u2029" for c in text):
        return "the reason must be one line of plain text (no line breaks, tabs or control characters)"
    return ""


def _clip(s: str, n: int = 190) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _guards_write(root, keep, add=None, now=None):
    """Rewrite state.json through state.write (the only sanctioned writer): `keep(lift)` filters the existing lifts,
    `add` is appended. Expired lifts are dropped here too, so the file cannot grow without bound. The lock's
    persistent block is carried through untouched."""
    import time
    from pathlib import Path
    from . import lock, state
    now = time.time() if now is None else now
    sf = lock._skyguard_dir(root) / "state.json"
    lock._assert_sandbox(sf)
    with state.locked(sf):                                  # read-modify-write under the one state lock
        cur = state.read(str(sf))
        lifts = [x for x in cur.lifts if x["expires_at"] > now and keep(x)] + ([add] if add else [])
        persistent = {"lock_disabled": cur.lock_disabled} if cur.lock_disabled else {}
        state.write(str(sf), {"v": 1, "lifts": lifts, "persistent": persistent})


def guards_main(argv, out) -> int:
    """`guards off`: shadow mode for THIS session (R8, a D6 lift: an hour, session-bound, never persistent).
    `guards on` removes every guards lift. `guards status` reports. Exit: 0 done, 2 refused or unreadable."""
    import argparse
    import os
    import time
    from . import lock, state
    ap = argparse.ArgumentParser(prog="skyguard guards", add_help=False, allow_abbrev=False)
    ap.add_argument("action", nargs="?", choices=("off", "on", "status"))
    ap.add_argument("--project", "--root", dest="project", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--reason", default=None)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        print(GUARDS_USAGE, file=out)
        return 2
    if not a.action:
        print(GUARDS_USAGE, file=out)
        return 2
    if a.reason is not None:
        problem = "--reason is taken only by `guards off`" if a.action != "off" else _reason_problem(a.reason)
        if problem:
            print(_clip("Refused: %s; nothing was changed." % problem), file=out)
            print(GUARDS_USAGE, file=out)
            return 2
    # The engine reads <--project>/.skyguard/state.json, and the hooks pass --project "$CLAUDE_PROJECT_DIR": resolve
    # the same folder here (explicit flag, else the agent's own project dir, else this toolkit's root).
    root = a.project or os.environ.get("CLAUDE_PROJECT_DIR") or lock.default_root()
    if not os.path.isdir(root):
        print(_clip("The project folder %r does not exist, so there is no state to write." % os.path.basename(str(root))),
              file=out)
        print("Pass --project <the install root> (the folder the hooks run with); nothing was changed.", file=out)
        return 2
    session = a.session or os.environ.get(lock.ENV_SESSION) or None
    if lock.reserved_session(session):
        for line in lock.RESERVED_LINES:
            print(line, file=out)
        return 2
    now = time.time()
    try:
        if a.action == "status":
            st = state.read(str(__import__("pathlib").Path(root) / ".skyguard" / "state.json"))
            live = [x for x in st.lifts if x["kind"] == "guards" and not state.lift_expired(x, now)]   # as the engine
            if st.rejected:
                print(_clip("Guards ON: the state file cannot be trusted (%s), so it is ignored." % st.reason), file=out)
            elif not live:
                print("Guards ON: no shadow-mode lift is recorded.", file=out)
            else:
                ends = max(x["expires_at"] for x in live)
                print(_clip("Guards OFF (shadow mode) recorded for session(s): %s." % ", ".join(
                    sorted({x["session_id"] for x in live})[:3])), file=out)
                print("Each is honoured only inside its own session; the latest expires in %d min." % max(
                    0, int((ends - now) // 60)), file=out)
            return 0
        if a.action == "on":
            st0 = state.read(os.path.join(root, ".skyguard", "state.json"))
            _guards_write(root, lambda x: x["kind"] != "guards", now=now)
            if st0.rejected:
                print(_clip("The state file could not be verified (%s); it was replaced by a fresh one." % st0.reason),
                      file=out)
                print("Guards are ON. Any lift it held is gone, including a lock lift.", file=out)
            else:
                print("Guards ON: every shadow-mode lift is cleared.", file=out)
            return 0
        if not lock._valid_session(session):
            print("No session id: pass --session <id> (or set SKYGUARD_SESSION_ID) so the lift is bound to it.", file=out)
            print("Without one the lift could not be honoured by any session, so nothing was recorded.", file=out)
            return 2
        agent = os.environ.get(lock.ENV_AGENT, "")
        hb_root = os.path.join(root, ".claude", "backups", ".hook-heartbeat")
        seen = [d for d in ((agent,) if agent in ("claude", "codex") else ("claude", "codex"))
                if state.session_start(os.path.join(hb_root, d), session) is not None]
        if not seen:
            print(_clip("No session start for %s is visible under .claude/backups/.hook-heartbeat of that project." % session),
                  file=out)
            print("A lift would never be honoured; check the session id, --project and SKYGUARD_AGENT. Nothing was written.",
                  file=out)
            return 2
        lift = {"kind": "guards", "reason": a.reason.strip() if a.reason is not None else "guards off", "session_id": session,
                "agent": os.environ.get(lock.ENV_AGENT, "cli") or "cli", "created": now,
                "expires_at": now + lock.LIFT_SECONDS}
        _guards_write(root, lambda x: not (x["kind"] == "guards" and x["session_id"] == session), add=lift, now=now)
    except lock.SandboxViolation as e:
        print("REFUSED (sandbox): " + _clip(str(e)), file=out)
        return 2
    except lock.Refused as e:
        print(_clip(str(e)), file=out)
        return 2
    except (ValueError, OSError) as e:
        print("Could not write the guards state (%s); nothing changed." % e.__class__.__name__, file=out)
        return 2
    print(_clip("Guards OFF for this session (%d min): a refusal or a confirmation prompt becomes an allow plus a warning, and is "
                "logged as SHADOW."
                % int(lock.LIFT_SECONDS // 60)), file=out)
    print("GUARD INERT refusals and the file lock still apply. `guards on` ends it.", file=out)
    return 0


def main(argv=None, out=None) -> int:
    out = sys.stdout if out is None else out
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=out)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "lock":
        from . import lock
        return lock.main(rest, out)
    if cmd == "guards":
        return guards_main(rest, out)
    if cmd == "snapshot":
        from . import snapcli
        return snapcli.main(rest, out)
    if cmd == "doctor":
        from . import health
        from . import lock as _lock
        if "--root" not in rest:
            rest = rest + ["--root", _lock.default_root()]
        try:
            return health.main(["report"] + rest)
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 2
    print(USAGE, file=out)
    return 2
