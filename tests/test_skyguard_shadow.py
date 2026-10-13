"""Plan 6 Task 9 (R8): `guards off` is ENGINE SHADOW MODE.

Rules still evaluate. A deny verdict becomes allow + context `Guards OFF: this would have been denied ({rule}).`, an ask
one `Guards OFF: this would have asked you first ({rule}); it ran without asking.`
(+ the rule's own first line) when, and only when, an HONOURED `guards` lift exists for THIS session. A `GUARD INERT`
refusal (a broken engine) is never converted; backups/snapshots still run; every conversion writes a `SHADOW <rule>`
audit line. The state reader (Task 7) decides what is honoured; this file tests what the engine does with it.

The Claude path is exercised through the real `run.py` over the guard corpus (every deny/ask row), the Codex path
through `engine.pre_tool`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import cli, engine, paths, state  # noqa: E402
from skyguard.adapters import codex  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

H = 3600.0
NOW = 1_800_000_000.0
SID = "sess-shadow"
HEAD = "Guards OFF: this would have been denied ({}).".format
# Plan 8 T6 (T4 claims audit K1): a shadowed ASK says what happened -- it ran without the confirmation -- not "denied".
ASK_HEAD = "Guards OFF: this would have asked you first ({}); it ran without asking.".format


def _lift(**kw):
    lift = {"kind": "guards", "reason": "test", "session_id": SID, "agent": "codex",
            "created": NOW - H, "expires_at": NOW + H}
    lift.update(kw)
    return lift


def _env(tmp_path, lifts=None, start=NOW - 2 * H, session=SID, persistent=None):
    """-> (project, telemetry dir). The state file and the session's start line, as the real writers make them."""
    project, hb = tmp_path / "proj", tmp_path / "hb"
    project.mkdir(exist_ok=True)
    if lifts is not None:
        state.write(str(project / ".skyguard" / "state.json"),
                    {"v": 1, "lifts": lifts, "persistent": persistent or {}})
    if start is not None:
        f = hb / "sessions" / (session + ".log")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{:.6f} start startup transcript.jsonl\n".format(start), encoding="utf-8")
    return project, hb


def _deny(code="write.DENY_PLUGIN", status="evaluated", decision="deny"):
    return Verdict(decision, "Blocked: first line of the rule.\nSecond line.", "", status, code)


def _shadow(tmp_path, v, **kw):
    project, hb = tmp_path / "proj", tmp_path / "hb"
    return engine.shadow(v, str(project), kw.pop("session", SID), str(hb), Audit(str(project)), now=NOW, **kw)


def _log(tmp_path):
    p = tmp_path / "proj" / ".claude" / "backups" / "AUDIT_LOG.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---- the conversion --------------------------------------------------------------------------------------------

def test_an_honoured_lift_turns_a_deny_into_allow_with_the_two_line_context(tmp_path):
    _env(tmp_path, [_lift()])
    v = _shadow(tmp_path, _deny())
    assert (v.decision, v.reason) == ("allow", "")
    assert v.context == HEAD("write.DENY_PLUGIN") + "\nBlocked: first line of the rule."
    assert v.code == "write.DENY_PLUGIN" and v.status == "evaluated"


def test_an_ask_is_converted_too(tmp_path):
    _env(tmp_path, [_lift()])
    v = _shadow(tmp_path, _deny(code="shell.ASK_X", decision="ask"))
    assert v.decision == "allow" and v.context.startswith(ASK_HEAD("shell.ASK_X"))
    assert "denied" not in v.context and len(v.context.split("\n")) <= 2


def test_a_long_first_line_keeps_the_context_within_the_message_contract(tmp_path):
    _env(tmp_path, [_lift()])
    v = _shadow(tmp_path, Verdict("deny", "x" * 500, "", "evaluated", "write.DENY_PLUGIN"))
    lines = v.context.split("\n")
    assert len(lines) == 2 and all(len(x) <= 200 for x in lines)


def test_an_allowed_call_is_returned_untouched_but_logged_while_a_lift_is_honoured(tmp_path):
    """l2-shadow-every-call: spec wins -- silence is closed by a SHADOW line on EVERY call, allowed ones too."""
    _env(tmp_path, [_lift()])
    adv = Verdict("allow", "", "advice", "evaluated", "write.ADV_X")
    assert _shadow(tmp_path, adv) is adv
    plain = Verdict()
    assert _shadow(tmp_path, plain) is plain
    lines = [x for x in _log(tmp_path).splitlines() if "SHADOW" in x]
    assert len(lines) == 2 and all(x.endswith("SHADOW: allow") for x in lines), lines


def test_an_allowed_call_writes_no_shadow_line_without_an_honoured_lift(tmp_path):
    _env(tmp_path, [_lift(session_id="someone-else")])
    _shadow(tmp_path, Verdict())
    _env(tmp_path, [_lift(expires_at=NOW - 1)])
    _shadow(tmp_path, Verdict())
    assert "SHADOW" not in _log(tmp_path)


def test_a_kept_inert_refusal_is_still_logged_while_a_lift_is_honoured(tmp_path):
    _env(tmp_path, [_lift()])
    v = _shadow(tmp_path, _deny(code="engine.EMPTY_COMBINE", status="failed"))
    assert v.decision == "deny"
    line = [x for x in _log(tmp_path).splitlines() if "SHADOW" in x]
    assert len(line) == 1 and "engine.EMPTY_COMBINE" in line[0] and "kept" in line[0]


def test_an_unclassifiable_refusal_is_not_converted(tmp_path):
    _env(tmp_path, [_lift()])
    v = _shadow(tmp_path, Verdict("deny", "some text no rule owns"))
    assert v.decision == "deny" and v.code == "unknown"


# ---- GUARD INERT is never converted (R8) -----------------------------------------------------------------------

def test_a_failed_status_stays_a_deny_even_with_an_honoured_lift(tmp_path):
    """l2-shadow-inert-still-denies: a broken engine cannot read the state it would need to honour."""
    _env(tmp_path, [_lift()])
    v = _shadow(tmp_path, _deny(code="engine.EMPTY_COMBINE", status="failed"))
    assert (v.decision, v.status) == ("deny", "failed")


def test_an_inert_rule_code_stays_a_deny_even_when_the_status_reads_evaluated(tmp_path):
    """l2-shadow-inert-code-still-denies: the code clause, separately from the status clause."""
    _env(tmp_path, [_lift()])
    for code in ("shell.S-INERT-NOCMD", "write.W-INERT-NOPATH", "write.NO_PATHS"):
        v = _shadow(tmp_path, _deny(code=code))
        assert v.decision == "deny", code


def test_every_real_inert_refusal_through_the_codex_engine_still_denies(tmp_path):
    project, hb = _env(tmp_path, [_lift()])
    for raw in ("not json", json.dumps({"hook_event_name": "Stop"})):
        req = codex.to_request(raw, "win32")._replace(session_id=SID)
        v = engine.pre_tool(req, str(project), lambda: paths.roots(str(project), "", {}), Audit(str(project)),
                            telemetry=str(hb), now=NOW)
        assert (v.decision, v.status) == ("deny", "failed")


# ---- the lift must be honoured: session-bound, unexpired, unforged ----------------------------------------------

def test_no_state_means_the_deny_stands(tmp_path):
    _env(tmp_path, None)
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_another_sessions_lift_is_not_honoured(tmp_path):
    """l2-shadow-session-bound"""
    _env(tmp_path, [_lift(session_id="someone-else")])
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_a_lift_is_not_honoured_by_a_session_that_never_started(tmp_path):
    _env(tmp_path, [_lift()], start=None)
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_a_lift_older_than_the_sessions_latest_start_is_not_honoured(tmp_path):
    _env(tmp_path, [_lift(created=NOW - 3 * H)], start=NOW - 2 * H)
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_an_expired_lift_is_not_honoured(tmp_path):
    _env(tmp_path, [_lift(expires_at=NOW - 1)])
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_a_lock_lift_does_not_switch_the_guards_off(tmp_path):
    _env(tmp_path, [_lift(kind="lock")])
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_a_forged_lock_disabled_cannot_silence_a_deny(tmp_path):
    """The open gap (forged persistent.lock_disabled) is not widened: persistence of the LOCK flag is not shadow mode."""
    _env(tmp_path, [], persistent={"lock_disabled": {"since": NOW - H, "by_session": SID}})
    assert _shadow(tmp_path, _deny()).decision == "deny"


def test_an_unverifiable_state_reads_as_guards_on_with_a_log_line(tmp_path):
    project, _hb = _env(tmp_path, [_lift()])
    sf = project / ".skyguard" / "state.json"
    sf.write_text(sf.read_text().replace('"reason": "test"', '"reason": "hand edited"'), encoding="utf-8")
    assert _shadow(tmp_path, _deny()).decision == "deny"
    assert "STATE-REJECTED" in _log(tmp_path)


def test_a_failure_inside_shadow_mode_keeps_the_original_deny(tmp_path, monkeypatch):
    _env(tmp_path, [_lift()])
    monkeypatch.setattr(engine._state, "honoured", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert _shadow(tmp_path, _deny()).decision == "deny"


# ---- every conversion is logged --------------------------------------------------------------------------------

def test_every_converted_verdict_writes_a_shadow_audit_line_naming_the_rule(tmp_path):
    """l2-shadow-logs"""
    _env(tmp_path, [_lift()])
    _shadow(tmp_path, _deny())
    _shadow(tmp_path, _deny(code="write.DENY_OTHER"))
    lines = [x for x in _log(tmp_path).splitlines() if "SHADOW" in x]
    assert len(lines) == 2 and not any("kept" in x for x in lines)
    assert "write.DENY_PLUGIN" in lines[0] and "write.DENY_OTHER" in lines[1]


def test_a_refusal_that_stands_writes_no_shadow_line(tmp_path):
    _env(tmp_path, None)
    _shadow(tmp_path, _deny())
    assert "SHADOW" not in _log(tmp_path)


# ---- side effects run (Codex engine path) ---------------------------------------------------------------------

def _patch(body):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "C:/G", "session_id": SID,
                       "tool_input": {"command": "*** Begin Patch\n" + body + "\n*** End Patch"}})


def _pre_tool(project, hb, raw):
    req = codex.to_request(raw, sys.platform)  # host platform: on Linux CI an absolute POSIX path under a Windows adapter is a mix no host makes (py3.13 joins it onto C:)
    return engine.pre_tool(req, str(project), lambda: paths.roots(str(project), "", {}), Audit(str(project)),
                           telemetry=str(hb), now=NOW)


def test_a_codex_plugin_write_is_allowed_with_the_shadow_context_under_a_lift(tmp_path):
    project, hb = _env(tmp_path, [_lift()])
    v = _pre_tool(project, hb, _patch("*** Add File: Data/a.esp\n+x"))
    assert v.decision == "allow" and v.context.startswith(HEAD("write.DENY_PLUGIN"))


def test_the_same_codex_call_without_a_lift_is_still_denied(tmp_path):
    """The control: without it, the test above could pass because the call was never denied at all."""
    project, hb = _env(tmp_path, None)
    assert _pre_tool(project, hb, _patch("*** Add File: Data/a.esp\n+x")).decision == "deny"


def test_the_backup_of_a_shadowed_delete_is_still_taken(tmp_path):
    """l2-shadow-side-effects-run"""
    project, hb = _env(tmp_path, [_lift()])
    f = project / "Data" / "a.esp"
    f.parent.mkdir()
    f.write_text("plugin")
    v = _pre_tool(project, hb, _patch("*** Delete File: {}".format(str(f).replace("\\", "/"))))
    assert v.decision == "allow" and "Guards OFF" in v.context
    assert any(p.name.endswith("a.esp") for p in (project / ".claude" / "backups").iterdir())


def test_a_partial_status_survives_the_conversion(tmp_path, monkeypatch):
    project, hb = _env(tmp_path, [_lift()])
    monkeypatch.setattr(engine.backup, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    v = _pre_tool(project, hb, _patch("*** Delete File: Data/a.esp"))
    assert (v.decision, v.status) == ("allow", "partial")


# ---- the Claude path: every deny/ask row of the guard corpus, through the real run.py ---------------------------

def _rows():
    sys.path.insert(0, str(REPO / "scripts" / "guard-corpus"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    rec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rec)
    legacy = rec.load_rows(REPO / "tests" / "guard_corpus" / "expected.jsonl")
    intended = rec.load_rows(REPO / "tests" / "guard_corpus" / "intended.jsonl")
    gone = {r["supersedes"] for r in intended if r.get("supersedes")}
    rows = [r for r in legacy if r["id"] not in gone] + intended
    roles = ("protect-bash.sh", "protect-files.sh", "protect-mcp.sh")
    return rec, [r for r in rows if r["decision"] in ("deny", "ask") and r["hook"] in roles
                 and isinstance(r["payload"], dict)]


REC, CORPUS = _rows()
_NEVER_SHADOW_IDS = {c.split(".", 1)[1] for c in engine.NEVER_SHADOW}


@pytest.fixture(scope="module")
def corpus_run():
    if REC.needs_missing_jq(REPO / ".claude" / "hooks"):
        pytest.skip("the shipped hooks still need jq")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        installs = {n: REC.make_install(td / "inst", REPO / ".claude" / "hooks", n) for n in REC.LAYOUTS}
        hb = td / "hb"
        now = time.time()
        for sid in ("on-sess", "off-sess"):
            f = hb / "sessions" / (sid + ".log")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("{:.6f} start startup t.jsonl\n".format(now - 60), encoding="utf-8")
        for inst in installs.values():
            state.write(str(inst / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": [
                {"kind": "guards", "reason": "corpus", "session_id": "off-sess", "agent": "t", "created": now - 30,
                 "expires_at": now + H}]})
        yield installs, hb


def _fire(install, hb, row, sid):
    """run.py's main(), in-process (a subprocess per row made this file take 18 minutes): same argv, same stdin and
    stdout bytes, the environment the recorder gives a hook."""
    import faulthandler
    import io
    from skyguard import run
    payload = dict(REC.row_payload(row, install), session_id=sid)
    argv = ["claude", row["hook"][:-3], "--root", install.as_posix(),
            "--root-win", install.as_posix() if os.name == "nt" else "", "--project", str(install),
            "--telemetry", str(hb)]
    saved_env, saved_io = dict(os.environ), (sys.stdin, sys.stdout)
    for k in REC.ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(REC.home_env(install))

    class _Std:
        def __init__(self, buf):
            self.buffer = buf

    out = io.BytesIO()
    sys.stdin, sys.stdout = _Std(io.BytesIO(json.dumps(payload).encode("utf-8"))), _Std(out)
    try:
        code = run.main(argv)
    finally:
        faulthandler.cancel_dump_traceback_later()
        sys.stdin, sys.stdout = saved_io
        os.environ.clear()
        os.environ.update(saved_env)
    assert code == 0
    text = out.getvalue().decode("utf-8").strip()
    # An allow may print JSON with no hookSpecificOutput (Linux CI run 38187755448: a row whose input only Windows
    # produces is allowed there). That is "not refused on this machine", which the caller already skips.
    return json.loads(text).get("hookSpecificOutput", {}) if text else {}


def test_the_corpus_has_refusals_to_shadow():
    assert len(CORPUS) >= 100, len(CORPUS)


def test_every_denied_or_asked_corpus_row_is_shadowed_except_the_inert_ones(corpus_run):
    installs, hb = corpus_run
    problems, inert, converted, kept = [], 0, 0, 0
    for row in CORPUS:
        inst = installs[row["layout"]]
        base = _fire(inst, hb, row, "on-sess")
        if base.get("permissionDecision") not in ("deny", "ask"):
            continue                                     # the corpus row is not refused on this machine: not ours
        reason = base["permissionDecisionReason"]
        got = _fire(inst, hb, row, "off-sess")
        if set(row.get("rules") or ()) & _NEVER_SHADOW_IDS:
            kept += 1                                    # Plan 6 Task 11: guards off never lifts the lock (R7, R8)
            if got.get("permissionDecision") != base["permissionDecision"] or got.get("permissionDecisionReason") != reason:
                problems.append((row["id"], "a lift rule was shadowed", got))
            continue
        if reason.startswith("GUARD INERT"):
            inert += 1
            if got.get("permissionDecision") != "deny" or got.get("permissionDecisionReason") != reason:
                problems.append((row["id"], "INERT row was changed", got))
            continue
        ctx = got.get("additionalContext", "")
        head = ("Guards OFF: this would have been denied (" if base["permissionDecision"] == "deny" else
                "Guards OFF: this would have asked you first (")
        if "permissionDecision" in got or not ctx.startswith(head):
            problems.append((row["id"], "not shadowed", got))
        elif reason.split("\n")[0][:190] not in ctx:
            problems.append((row["id"], "context lacks the rule's first line", ctx))
        else:
            converted += 1
    assert not problems, problems[:5]
    assert converted >= 100 and inert >= 1 and kept >= 10, (converted, inert, kept)


def test_the_codex_hook_entry_point_honours_the_lift(tmp_path):
    """l2-shadow-codex-wired: run.py's codex pre-tool role passes its --telemetry dir through to the engine."""
    import subprocess
    now = time.time()
    project = tmp_path / "proj"
    hb = tmp_path / "hb"
    state.write(str(project / ".skyguard" / "state.json"), {"v": 1, "persistent": {}, "lifts": [
        {"kind": "guards", "reason": "t", "session_id": SID, "agent": "codex", "created": now - 30,
         "expires_at": now + H}]})
    f = hb / "sessions" / (SID + ".log")
    f.parent.mkdir(parents=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format(now - 60), encoding="utf-8")
    argv = [sys.executable, "-B", str(REPO / "tools" / "skyguard" / "run.py"), "codex", "pre-tool", "--root", str(project),
            "--project", str(project), "--telemetry", str(hb)]
    r = subprocess.run(argv, input=_patch("*** Add File: Data/a.esp\n+x").encode("utf-8"), capture_output=True, timeout=60)
    body = r.stdout.decode("utf-8")
    assert r.returncode == 0 and body.startswith("SKYGUARD-OK"), (r.returncode, body, r.stderr[-200:])
    assert "permissionDecision" not in body and "Guards OFF: this would have been denied" in body


# ---- the CLI: guards off | on | status --------------------------------------------------------------------------

def _hb(project, agent="claude"):
    return Path(project) / ".claude" / "backups" / ".hook-heartbeat" / agent


def _start(project, session=SID, agent="claude", when=None):
    f = _hb(project, agent) / "sessions" / (session + ".log")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{:.6f} start startup t.jsonl\n".format((time.time() - 60) if when is None else when), encoding="utf-8")


_ENVS = ("SKYGUARD_LOCK_SANDBOX", "SKYGUARD_SESSION_ID", "SKYGUARD_AGENT", "CLAUDE_PROJECT_DIR")


def _cli_raw(argv, env=None):
    import io
    saved = {k: os.environ.get(k) for k in _ENVS}
    for k in _ENVS:
        os.environ.pop(k, None)
    os.environ.update(env or {})
    out = io.StringIO()
    try:
        code = cli.main(argv, out)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return code, out.getvalue()


def _cli(tmp_path, *args, session=SID, env=None, start=True, flag="--project"):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    if start and session:
        _start(root, session)
    argv = ["guards"] + list(args) + [flag, str(root)] + (["--session", session] if session else [])
    code, text = _cli_raw(argv, dict({"SKYGUARD_LOCK_SANDBOX": str(tmp_path)}, **(env or {})))
    return code, text, root / ".skyguard" / "state.json"


def test_guards_off_records_a_session_bound_expiring_guards_lift(tmp_path):
    code, text, sf = _cli(tmp_path, "off")
    assert code == 0, text
    lifts = state.read(str(sf)).lifts
    assert len(lifts) == 1 and lifts[0]["kind"] == "guards" and lifts[0]["session_id"] == SID
    assert 0 < lifts[0]["expires_at"] - lifts[0]["created"] <= state.MAX_LIFT_SECONDS
    lines = text.strip().splitlines()
    assert len(lines) <= 2 and all(len(x) <= 200 for x in lines)
    assert "a refusal or a confirmation prompt becomes an allow" in lines[0], lines[0]   # K1: asks are shadowed too


def test_guards_off_needs_a_session_id(tmp_path):
    code, text, sf = _cli(tmp_path, "off", session=None)
    assert code == 2 and not sf.exists() and "session" in text.lower()


def test_guards_off_is_idempotent_per_session_and_keeps_other_state(tmp_path):
    root = tmp_path / "root"
    now = time.time()
    other = {"kind": "lock", "reason": "r", "session_id": "keep-me", "agent": "a", "created": now, "expires_at": now + H}
    state.write(str(root / ".skyguard" / "state.json"),
                {"v": 1, "lifts": [other], "persistent": {"lock_disabled": {"since": now, "by_session": "u"}}})
    _cli(tmp_path, "off")
    _cli(tmp_path, "off")
    st = state.read(str(root / ".skyguard" / "state.json"))
    assert [x["kind"] for x in st.lifts].count("guards") == 1 and other in st.lifts
    assert st.lock_disabled and st.lock_disabled["by_session"] == "u"


def test_guards_on_removes_every_guards_lift_and_nothing_else(tmp_path):
    _cli(tmp_path, "off")
    _cli(tmp_path, "off", session="other-sess")
    root = tmp_path / "root"
    st = state.read(str(root / ".skyguard" / "state.json"))
    lock_lift = {"kind": "lock", "reason": "r", "session_id": SID, "agent": "a", "created": time.time(),
                 "expires_at": time.time() + H}
    state.write(str(root / ".skyguard" / "state.json"), {"v": 1, "lifts": st.lifts + [lock_lift], "persistent": {}})
    code, text, sf = _cli(tmp_path, "on")
    assert code == 0, text
    assert [x["kind"] for x in state.read(str(sf)).lifts] == ["lock"]


def test_guards_status_reports_without_changing_anything(tmp_path):
    _cli(tmp_path, "off")
    sf = tmp_path / "root" / ".skyguard" / "state.json"
    before = sf.read_bytes()
    code, text, _ = _cli(tmp_path, "status")
    assert code == 0 and SID in text and sf.read_bytes() == before
    code, text, _ = _cli(tmp_path, "on")
    code, text, _ = _cli(tmp_path, "status")
    assert code == 0 and "on" in text.lower()


def test_guards_without_a_subcommand_is_a_usage_error(tmp_path):
    code, text, _ = _cli(tmp_path)
    assert code == 2 and "usage" in text.lower()


def test_guards_refuses_a_state_folder_that_is_outside_the_sandbox(tmp_path):
    from skyguard import lock
    out, sandbox = tmp_path / "elsewhere", tmp_path / "sbx"
    out.mkdir()
    sandbox.mkdir()
    _start(out)
    code, text = _cli_raw(["guards", "off", "--project", str(out), "--session", SID],
                          {"SKYGUARD_LOCK_SANDBOX": str(sandbox)})
    assert code == 2 and "sandbox" in text and not (out / ".skyguard" / "state.json").exists()
    assert lock.SandboxViolation  # the refusal is the lock module's own choke, not a second one


# ---- the lift is written where the engine reads it, and only when it can be honoured ---------------------------

def test_guards_off_without_a_visible_session_start_writes_nothing_and_says_so(tmp_path):
    """The lift would never be honoured; a dead lift must not look like success."""
    code, text, sf = _cli(tmp_path, "off", start=False)
    lines = text.strip().splitlines()
    assert code == 2 and not sf.exists() and len(lines) == 2 and all(len(x) <= 200 for x in lines), text
    assert "start" in lines[0].lower() and SID in lines[0]


# ---- `guards off --reason` (CHANGELOG v4.0.0 known limit "Guards-off reason", fixed before the tag) --------------

def test_guards_off_records_the_reason_given_with_reason(tmp_path):
    code, text, sf = _cli(tmp_path, "off", "--reason", "testing the decoy write")
    assert code == 0, text
    assert [x["reason"] for x in state.read(str(sf)).lifts] == ["testing the decoy write"]


def test_guards_off_without_a_reason_still_records_guards_off(tmp_path):
    code, text, sf = _cli(tmp_path, "off")
    assert code == 0 and [x["reason"] for x in state.read(str(sf)).lifts] == ["guards off"], text


_BAD_REASONS = {"newline": "two" + chr(10) + "lines", "cr": "carriage" + chr(13) + "return", "tab": "tab" + chr(9) + "here",
                "nul": "nul" + chr(0), "del": "del" + chr(127), "c1-nel": "nel" + chr(0x85),
                "line-sep": "sep" + chr(0x2028) + "line", "para-sep": "sep" + chr(0x2029) + "para",
                "too-long": "x" * 121, "blank": "   "}


@pytest.mark.parametrize("bad", list(_BAD_REASONS.values()), ids=list(_BAD_REASONS))
def test_guards_off_refuses_a_reason_that_is_not_one_short_line_and_writes_nothing(tmp_path, bad):
    code, text, sf = _cli(tmp_path, "off", "--reason", bad)
    assert code == 2 and not sf.exists(), text
    assert "reason" in text.lower() and all(len(x) <= 200 for x in text.splitlines())


def test_a_reason_of_exactly_the_cap_is_accepted(tmp_path):
    code, text, sf = _cli(tmp_path, "off", "--reason", "y" * 120)
    assert code == 0 and state.read(str(sf)).lifts[0]["reason"] == "y" * 120, text


def test_a_reason_is_refused_for_on_and_status(tmp_path):
    for action in ("on", "status"):
        code, text, _ = _cli(tmp_path, action, "--reason", "why")
        assert code == 2 and "reason" in text.lower(), (action, text)


def test_a_start_line_of_another_agents_heartbeat_dir_counts_when_the_agent_is_unnamed_but_not_a_wrong_named_one(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _start(root, agent="codex")
    code, _t, _s = _cli(tmp_path, "off", start=False)
    assert code == 0                                           # agent unnamed: either heartbeat dir is accepted
    code, text, _s = _cli(tmp_path, "off", start=False, env={"SKYGUARD_AGENT": "claude"})
    assert code == 2                                           # claude named: only claude's dir is looked in


def test_guards_off_refuses_a_project_that_is_not_a_folder_with_two_lines(tmp_path):
    code, text = _cli_raw(["guards", "off", "--project", str(tmp_path / "nope"), "--session", SID],
                          {"SKYGUARD_LOCK_SANDBOX": str(tmp_path)})
    lines = text.strip().splitlines()
    assert code == 2 and len(lines) == 2 and not (tmp_path / "nope").exists(), text
    assert "does not exist" in lines[0]


def test_the_project_defaults_to_the_agents_project_dir_and_root_stays_a_synonym(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _start(root)
    code, text = _cli_raw(["guards", "off", "--session", SID],
                          {"SKYGUARD_LOCK_SANDBOX": str(tmp_path), "CLAUDE_PROJECT_DIR": str(root)})
    assert code == 0, text
    assert state.read(str(root / ".skyguard" / "state.json")).lifts
    code, text, sf = _cli(tmp_path, "on", flag="--root")
    assert code == 0 and not state.read(str(sf)).lifts


def _through_run(project, sid, tool="Write", path="Data/a.esp"):
    import subprocess
    payload = {"tool_name": tool, "session_id": sid, "tool_input": {"file_path": path}}
    argv = [sys.executable, "-B", str(REPO / "tools" / "skyguard" / "run.py"), "claude", "protect-files",
            "--root", str(project), "--project", str(project), "--telemetry", str(_hb(project))]
    r = subprocess.run(argv, input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr[-200:]
    text = r.stdout.decode("utf-8").strip()
    return json.loads(text)["hookSpecificOutput"] if text else {}


def test_guards_off_then_a_call_through_run_py_with_the_same_project_is_converted(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert _through_run(root, SID).get("permissionDecision") == "deny"          # the control
    code, text, _sf = _cli(tmp_path, "off")
    assert code == 0, text
    got = _through_run(root, SID)
    assert "permissionDecision" not in got and got["additionalContext"].startswith("Guards OFF: this would have")
    assert _through_run(root, "another-session").get("permissionDecision") == "deny"   # session-bound end to end
    _cli(tmp_path, "on")
    assert _through_run(root, SID).get("permissionDecision") == "deny"


# ---- review fix round (Task 9) ----------------------------------------------------------------------------------

def test_a_refusal_is_not_converted_when_its_shadow_line_cannot_be_written(tmp_path):
    """l2-shadow-line-must-land: Audit.raw swallows OSError, so a converted verdict could be silent."""
    project, _hb = _env(tmp_path, [_lift()])
    (project / ".claude" / "backups" / "AUDIT_LOG.txt").mkdir(parents=True)        # appending to it raises
    v = _shadow(tmp_path, _deny())
    assert v.decision == "deny"
    ok = _shadow(tmp_path, Verdict())                      # an allow stays an allow either way
    assert ok.decision == "allow"


def _two_starts(tmp_path, first, second):
    f = tmp_path / "hb" / "sessions" / (SID + ".log")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{:.6f} start startup t\n{:.6f} start resume t\n".format(first, second), encoding="utf-8")
    return f


def test_a_forged_early_start_line_cannot_revive_a_pre_resume_lift(tmp_path):
    """l2-state-start-max: the session start is the MAX of its start lines, not whichever is appended last."""
    f = _two_starts(tmp_path, NOW - 5 * H, NOW - H)                  # the lift below (NOW - 2h) predates the resume
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("0.000001 start resume forged\n")
    assert state.session_start(str(tmp_path / "hb"), SID) == NOW - H
    project, _hb = _env(tmp_path, [_lift(created=NOW - 2 * H)], start=None)
    assert _shadow(tmp_path, _deny()).decision == "deny"


# one SHADOW line per tool call: only the decision roles log

def _run_role(project, role, payload):
    import faulthandler
    import io
    from skyguard import run
    hb = _hb(project)
    argv = ["claude", role, "--root", project.as_posix(), "--root-win", project.as_posix() if os.name == "nt" else "",
            "--project", str(project), "--telemetry", str(hb)]
    saved = (sys.stdin, sys.stdout)

    class _Std:
        def __init__(self, buf):
            self.buffer = buf

    out = io.BytesIO()
    sys.stdin, sys.stdout = _Std(io.BytesIO(json.dumps(payload).encode("utf-8"))), _Std(out)
    try:
        assert run.main(argv) == 0
    finally:
        faulthandler.cancel_dump_traceback_later()
        sys.stdin, sys.stdout = saved


def _shadow_lines(project):
    p = project / ".claude" / "backups" / "AUDIT_LOG.txt"
    return [x for x in p.read_text(encoding="utf-8").splitlines() if "SHADOW" in x] if p.exists() else []


def test_a_tool_call_logs_one_shadow_line_not_one_per_hook(tmp_path):
    """l2-shadow-decision-roles-only: backup-before-edit / snapshot-before-tool pass no verdict worth logging."""
    project = tmp_path / "root"
    project.mkdir()
    _start(project)
    code, text, _sf = _cli(tmp_path, "off")
    assert code == 0, text
    (project / "notes").mkdir()
    (project / "notes" / "a.txt").write_text("x")
    edit = {"tool_name": "Edit", "session_id": SID, "tool_input": {"file_path": str(project / "notes" / "a.txt")}}
    for role in ("protect-files", "backup-before-edit"):
        _run_role(project, role, edit)
    assert len(_shadow_lines(project)) == 1, _shadow_lines(project)
    bash = {"tool_name": "Bash", "session_id": SID, "tool_input": {"command": "echo hi"}}
    for role in ("protect-bash", "snapshot-before-tool"):
        _run_role(project, role, bash)
    assert len(_shadow_lines(project)) == 2


# STATE-REJECTED is logged when the reason changes, not on every hook call

def _break(project, how):
    sf = project / ".skyguard" / "state.json"
    if how == "sum":
        sf.write_text(sf.read_text().replace('"reason": "test"', '"reason": "hand edited"'), encoding="utf-8")
    else:
        sf.write_text("garbage", encoding="utf-8")


def _rejects(tmp_path):
    return [x for x in _log(tmp_path).splitlines() if "STATE-REJECTED" in x]


def test_a_state_rejection_is_logged_once_per_reason_not_once_per_call(tmp_path):
    """l2-shadow-reject-once"""
    project, _hb = _env(tmp_path, [_lift()])
    _break(project, "sum")
    for _ in range(3):
        _shadow(tmp_path, Verdict())
    assert len(_rejects(tmp_path)) == 1
    _break(project, "garbage")                                    # a different reason is news
    _shadow(tmp_path, Verdict())
    _shadow(tmp_path, Verdict())
    assert len(_rejects(tmp_path)) == 2
    _env(tmp_path, [_lift()])                                      # repaired ...
    _shadow(tmp_path, Verdict())
    _break(project, "garbage")                                    # ... and broken again: logged again
    _shadow(tmp_path, Verdict())
    assert len(_rejects(tmp_path)) == 3


def test_guards_on_over_an_unverifiable_state_says_so_in_two_lines(tmp_path):
    root = tmp_path / "root"
    _cli(tmp_path, "off")
    _break(root, "garbage")
    code, text, sf = _cli(tmp_path, "on")
    lines = text.strip().splitlines()
    assert code == 0 and 1 <= len(lines) <= 2 and "verif" in text.lower() and "unparseable" in text, text
    st = state.read(str(sf))
    assert not st.rejected and st.lifts == []
    code, text, _ = _cli(tmp_path, "on")                           # a clean state: the ordinary one-liner
    assert "verif" not in text.lower()


# ---- Plan 6 final fix pass (A-I3 / C-I1 ruling, A-M1) -----------------------------------------------------------

def _off_and_hook(tmp_path):
    code, text, sf = _cli(tmp_path, "off")
    assert code == 0, text
    root = tmp_path / "root"
    lift = state.read(str(sf)).lifts[0]
    hb = _hb(root)

    def hook(now):
        return engine.shadow(_deny(), str(root), SID, str(hb), Audit(str(root)), now=now)
    return lift, hook


def test_a_guards_off_lift_ends_shadow_mode_at_its_expiry(tmp_path):
    """Fake clock on the CLI's own lift: a second before expires_at the deny is shadowed, a second after it stands."""
    lift, hook = _off_and_hook(tmp_path)
    assert hook(lift["expires_at"] - 1).decision == "allow"
    assert hook(lift["expires_at"] + 1).decision == "deny"


def _status(tmp_path):
    code, text, _ = _cli(tmp_path, "status", start=False)
    assert code == 0, text
    return text


def test_a_compact_does_not_end_guards_off_and_status_agrees(tmp_path):
    """A-M1 (R9: compact changes nothing): run.py writes a `start compact` line for a compaction, which used to end the
    lift in the engine while `guards status` still said OFF. Now neither changes: the engine ignores compact lines."""
    lift, hook = _off_and_hook(tmp_path)
    f = _hb(tmp_path / "root") / "sessions" / (SID + ".log")
    with open(str(f), "a", encoding="utf-8") as fh:
        fh.write("{:.6f} start compact t.jsonl\n".format(lift["created"] + 5))
    assert hook(lift["created"] + 10).decision == "allow"
    assert "Guards OFF" in _status(tmp_path)


def test_a_resume_after_the_lift_still_ends_it(tmp_path):
    """The twin: a `resume` start line after the lift is a new start, so the lift is not honoured (and session start
    clears it, so status then agrees)."""
    lift, hook = _off_and_hook(tmp_path)
    f = _hb(tmp_path / "root") / "sessions" / (SID + ".log")
    with open(str(f), "a", encoding="utf-8") as fh:
        fh.write("{:.6f} start resume t.jsonl\n".format(lift["created"] + 5))
    assert hook(lift["created"] + 10).decision == "deny"
