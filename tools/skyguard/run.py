"""Entry point the per-agent hook wrappers call: run.py <agent> <role> --root .. --root-win .. --project ..

Exits 1 with NOTHING on stdout on any internal failure, so a wrapper can tell "the engine decided"
from "the engine broke" -- the second must never read as allow (spec section 5.1). The one exception is
the in-process watchdog: its report goes to stdout and starts `Timeout (`, so a bash wrapper can tell
"the watchdog ended a working interpreter" from "the interpreter is broken" (and skip a re-probe).

`--nonce N` (the bash wrappers pass it on a FALLBACK interpreter) prefixes the answer with the line
`skyguard-nonce:N`: the run itself proves it is this engine, so a remembered fallback needs no probe."""
from __future__ import annotations

import argparse
import faulthandler
import os
import sys
import time
from pathlib import Path

_T0 = time.monotonic()        # the engine's time budget counts from (about) here

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skyguard import backup, codes, engine, health, paths, recovery, session, snapshot  # noqa: E402
from skyguard.adapters import claude, codex  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

ADAPTERS = {"claude": claude, "codex": codex}
CODEX_ROLES = ("pre-tool", "session-start")
DECISION_ROLES = ("protect-bash", "protect-files", "protect-mcp")


ENV_UNREADABLE = (
    "GUARD INERT: protect-bash.sh found skyrim-paths.env but could not read it, so it cannot tell "
    "which directories are the Skyrim install. Refusing rather than guarding the wrong set. "
    "Fix or delete that file (re-run setup.sh)."
)


_NONCE = []   # --nonce: echoed ahead of the (single) answer


def _emit(text: str) -> None:
    """One write of UTF-8 bytes, so a console code page cannot raise and no partial output can escape."""
    if _NONCE:
        text = "skyguard-nonce:{}\n".format(_NONCE.pop()) + text
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


_ARGS = []   # the parsed args, so the last-resort handler below can still name the hook that broke


def _telemetry(a, v) -> None:
    """Write the `evaluated` record. A telemetry failure never changes the verdict (health.record swallows it)."""
    if a.invocation and a.telemetry:
        health.record(a.telemetry, a.role, "evaluated", a.invocation,
                      "{} {} {}".format(v.status, v.decision, v.code or "-"))
        health.trim_latency(a.telemetry, a.role)


def _session_line(a, session_id, tool="", source=None, transcript=None, hook=None) -> None:
    """Per-session record for the canary's coverage check (health.py). Written BEFORE the engine decides, so a
    call the engine denies still counts as seen. Never changes the verdict: any failure is swallowed."""
    try:
        if not a.telemetry:
            return
        if transcript is not None:
            health.session_record(a.telemetry, session_id, "start", source or "-", transcript)
        elif hook:
            health.session_record(a.telemetry, session_id, hook, tool or "-")
        elif a.role in health.COUNTING_HOOKS[a.agent]:
            health.session_record(a.telemetry, session_id, a.role, tool or "-")
    except Exception:  # noqa: BLE001 -- telemetry must never reach the decision path
        pass


def _engine_budget() -> float:
    try:
        b = float(os.environ.get("SKYGUARD_ENGINE_TIMEOUT", "20"))
    except ValueError:
        return 20.0
    return b if b > 0 else 20.0


def _declared_env(a):
    """The install's declared roots (skyrim-paths.env at the root, else the legacy .claude/ one: paths.read_install),
    or None when the file it reads exists but cannot be read. No root reads no file (deferred item 9)."""
    try:
        cfg = paths.read_install(a.root_win or a.root, dict(os.environ))
        # rt-fix-b: hooks run from a git worktree's copy: the main tree's paths file (untracked, so absent from the
        # worktree) declares the config and MO2 folders too. Union, as R3 does for every root.
        main = paths.main_worktree(a.root_win or a.root)
        if main:
            cfg = paths.merge_configs(cfg, paths.read_install(main, {}))
        return cfg
    except (OSError, ValueError):
        return None


SNAPSHOT_MARGIN = 3.0         # seconds left unused before the engine budget's watchdog


def _session_snapshot(project, source, audit) -> None:
    """The SessionStart snapshot (Plan 6 Task 14): the first-ever baseline, time-boxed to snapshot.BASELINE_BOX -- and
    never past the engine budget minus a margin, because the watchdog would end the process and take the session's
    banners and preferences with it. Never raises: a failed snapshot must not cost the session its context."""
    box = min(snapshot.BASELINE_BOX, _T0 + _engine_budget() - SNAPSHOT_MARGIN - time.monotonic())
    try:
        snapshot.session_start(project, source, audit, box=box)
    except Exception:
        audit.log("snapshot-before-tool", "FAILED", "the session-start snapshot raised -- no snapshot was taken")


def _recovery_deadline() -> float:
    """Task 15: a recovery snapshot (a save copy) ends this far before the engine budget's watchdog."""
    return _T0 + _engine_budget() - SNAPSHOT_MARGIN


def _recovery(a, p, audit, roots) -> None:
    """Task 15: the load order, INIs and the named save, before the command (snapshot role) or the DevBench call
    (protect-mcp). Never raises and never changes a verdict."""
    try:
        if a.role == "protect-mcp":
            recovery.mcp(a.project, p["tool_name"], p["mcp_input"], audit, roots, agent="claude",
                         session_id=p.get("session_id", ""), deadline=_recovery_deadline())
        else:
            recovery.run(a.project, p["command"], p["empty"], audit, roots, cwd=p.get("cwd", ""),
                         shell=p.get("shell", ""), agent="claude", session_id=p.get("session_id", ""),
                         deadline=_recovery_deadline())
    except Exception:
        audit.log(recovery.HOOK, "FAILED", "the recovery snapshot raised -- none was taken")


def _codex(a, raw: str) -> int:
    """Codex roles. Output is SKYGUARD-OK + body: the wrapper tells "decided" (marker present) from "broke"
    (exit 1, nothing on stdout) -- only a JSON deny blocks on Codex, so the wrapper owns the failure deny."""
    audit = Audit(a.project)
    req = codex.to_request(raw)
    if a.role == "session-start":
        _session_line(a, req.session_id, source=req.source, transcript=req.transcript_path)
        out, status, code = session.run_status(a.project, req.source, a.bash, audit, root=a.root_win or a.root)
        _session_snapshot(a.project, req.source, audit)
        _telemetry(a, Verdict("allow", "", "", status, code))
        _emit("SKYGUARD-OK\n" + codex.session_body(out))
        return 0

    _session_line(a, req.session_id, tool=req.tool_name)

    def roots_fn():
        env = _declared_env(a)
        if env is None:
            raise engine.RootsUnavailable(codex.ENV_UNREADABLE)
        return paths.roots(a.root, a.root_win, env)

    v = codes.classify(engine.pre_tool(req, a.project, roots_fn, audit, telemetry=a.telemetry,
                                       deadline=_recovery_deadline()))
    _telemetry(a, v)
    if v.decision != "allow":
        # A refused call leaves no item in the agent's transcript, so the canary must not count its hook
        # record as "hooked" (health._coverage_rows subtracts these).
        _session_line(a, req.session_id, tool=req.tool_name, hook="refused")
    _emit("SKYGUARD-OK\n" + codex.render(v))
    return 0


def _neutral(a, raw: bytes) -> int:
    """Role pre-tool for an adapter this repo has no module for (ADAPTING.md). Output is SKYGUARD-OK + a
    verdict line; the adapter owns the failure deny, exactly as the Codex wrappers do."""
    from skyguard import neutral
    if a.role != "pre-tool":
        return 1
    # Telemetry (and the last-resort `failed` record in __main__, which reads this same object) is written under
    # its own name, so a neutral call can never overwrite or be read as the Codex hook's pre-tool record.
    a.role = neutral.TELEMETRY_ROLE
    audit = Audit(a.project)

    def roots_fn():
        env = _declared_env(a)
        if env is None:
            raise engine.RootsUnavailable(neutral.ENV_UNREADABLE)
        return paths.roots(a.root, a.root_win, env)

    text, v = neutral.handle(raw, a.project, roots_fn, audit)
    _telemetry(a, v)
    _emit(text)
    return 0


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) == 2 and args[0] == "--selftest":
        # Reaching here means every engine module above imported. The wrappers' fallback chain uses this
        # to prove a candidate interpreter can run the ENGINE, not merely that it exits 0.
        if sys.version_info[:2] < (3, 8):
            return 1
        _emit("skyguard-selftest:{}\n".format(args[1]))
        return 0
    # Hard time limit, armed before anything can run long. faulthandler's watchdog is a C thread that does
    # not need the GIL, so it fires even inside a catastrophic regex; exit=True ends the process with
    # status 1 (the wrapper's "engine failed" deny). The agent discards a deny that arrives after ITS
    # timeout, so we must end first. The report goes to STDOUT (it starts "Timeout ("): no wrapper passes a
    # failed run's stdout on, and the bash wrappers read it to skip re-probing an interpreter that has
    # just proved it runs the engine.
    try:
        faulthandler.dump_traceback_later(_engine_budget(), exit=True, file=sys.stdout)
    except (OSError, ValueError, AttributeError, RuntimeError):
        pass   # no usable stdout: run unwatched rather than not at all (the wrapper's own limit still bounds it)
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=sorted(ADAPTERS) + ["neutral"])
    ap.add_argument("role", choices=["protect-bash", "protect-files", "backup-before-edit",
                                     "snapshot-before-tool", "session-kb-guard", "protect-mcp"]
                    + list(CODEX_ROLES))
    ap.add_argument("--root", default="")
    ap.add_argument("--root-win", default="")
    ap.add_argument("--project", required=True)
    ap.add_argument("--bash", default="bash")
    ap.add_argument("--invocation", default="")
    ap.add_argument("--telemetry", default="")
    ap.add_argument("--nonce", default="")
    a = ap.parse_args(argv)
    _ARGS.append(a)
    if a.nonce:
        _NONCE.append(a.nonce)
    if a.agent == "neutral":
        # Plan 7: the agent-neutral role, for an adapter living outside this package (no ADAPTERS module).
        # _neutral refuses every role but pre-tool with exit 1 (broken wiring, never a decision).
        return _neutral(a, sys.stdin.buffer.read())   # bytes: neutral decodes strictly (a bad byte is refused)
    if (a.agent == "codex") != (a.role in CODEX_ROLES):
        return 1      # a role the agent does not have: broken wiring, never a decision
    adapter = ADAPTERS[a.agent]
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    if a.agent == "codex":
        return _codex(a, raw)
    p = adapter.extract(raw)
    if a.role == "session-kb-guard":
        _session_line(a, p["session_id"], source=p["source"], transcript=p["transcript_path"])
    else:
        _session_line(a, p["session_id"], tool=p["tool_name"])
    audit = Audit(a.project)
    decides = a.role in DECISION_ROLES
    if decides:                      # fix-limits 2: shadow() below writes this call's rule lines as one record
        engine.hold_if_shadowing(a.project, audit)
    if a.role == "protect-bash":
        req = adapter.to_request(p, "shell")
        if p.get("ws_monitor"):
            # Plan 5 Task 13b: a WebSocket Monitor runs no command (no `command` key at all): nothing to judge.
            v = Verdict()
        elif p["command_type"] != "string" or not p["command"]:
            # Plan 5 Task 12: a command that is not a string (array, number, object, boolean) is no command the
            # guard can judge -- `command` renders it as JSON text, which the rules would read as if typed. An
            # empty or whitespace-only STRING is refused by shell_rules.decide itself.
            v = engine.evaluate_shell(req._replace(command=""), paths.Roots("", ""), audit)
        else:
            env = _declared_env(a)
            if env is None:
                v = Verdict("deny", ENV_UNREADABLE, status="failed", code="run.ENV_UNREADABLE")
            else:
                # The payload's cwd and the tool's dialect reach the rules through the engine (Plan 4/5 handshake).
                v = engine.evaluate_shell(req, paths.roots(a.root, a.root_win, env), audit)
    elif a.role == "protect-files":
        # Plan 5 Task 9: W-PAPYRUS and W-INSTALL decide on where the file RESOLVES, so this role needs the roots
        # too. Its rules are notes plus a name-based deny, so an unreadable paths file costs only the declared
        # roots (the install this hook runs from is still known); it is not a reason to refuse an edit.
        env = _declared_env(a)
        roots = paths.roots(a.root, a.root_win, env if env is not None else {})
        v = engine.evaluate_write(p["file_path"], audit, roots=roots, cwd=p.get("cwd", ""))
    elif a.role == "protect-mcp":
        # Plan 5 Task 11: the DevBench MCP tools. Only the save-exists check reads the declared config dirs, so an
        # unreadable paths file costs that one answer (it then asks); it is not a reason to refuse every call.
        env = _declared_env(a)
        roots = paths.roots(a.root, a.root_win, env if env is not None else {})
        v = engine.evaluate_mcp(p["tool_name"], p["mcp_input"], audit, roots=roots)
        _recovery(a, p, audit, roots)          # Task 15: an existing save DevBench overwrites is kept first
    elif a.role == "backup-before-edit":
        backup.run(a.project, p["tool_name"], p["file_path"], p["empty"], audit, "claude", p.get("session_id", ""))
        v = Verdict()
    elif a.role == "snapshot-before-tool":
        env = _declared_env(a)                 # Task 15: the save first (the bigger copy gets the budget), then scripts
        _recovery(a, p, audit, None if env is None else paths.roots(a.root, a.root_win, env))
        snapshot.run(a.project, p["command"], p["empty"], audit)
        v = Verdict()
    else:
        out, status, code = session.run_status(a.project, "" if p["empty"] else p["source"], a.bash, audit,
                                              root=a.root_win or a.root)
        _session_snapshot(a.project, "" if p["empty"] else p["source"], audit)
        _telemetry(a, Verdict(status=status, code=code))
        session.export_env(p["session_id"], "claude")   # later Bash calls get SKYGUARD_SESSION_ID (Task 10)
        _emit(out)
        return 0
    if a.role in DECISION_ROLES:     # one SHADOW line per tool call: backup / snapshot hooks make no decision
        v = engine.shadow(v, a.project, p["session_id"], a.telemetry, audit)   # R8: the one exit
    v = codes.classify(v)
    _telemetry(a, v)
    _emit(adapter.render(v))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:
        if _ARGS and _ARGS[0].invocation and _ARGS[0].telemetry:
            health.record(_ARGS[0].telemetry, _ARGS[0].role, "failed", _ARGS[0].invocation,
                          "engine {}".format(e.__class__.__name__))
        sys.exit(1)
