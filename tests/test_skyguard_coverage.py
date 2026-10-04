"""Plan 4 Task 13: the canary reports Codex trust and per-surface hook coverage (D14).

Coverage compares what an agent's own transcript says RAN with what the hook says it SAW. The transcripts here
are the scrubbed Task 0 excerpts (tests/fixtures/codex/0.160.0/rollout-*.jsonl, tests/fixtures/claude/
transcript-p.jsonl). There is no committed TUI excerpt, so the TUI twin is derived from an exec excerpt by
rewriting `originator` to the MEASURED TUI value (`codex-tui`, with `source` `vscode`: the user's probe ran it
inside VS Code's terminal). Every Codex config used lives in tmp_path -- the user's ~/.codex is never read.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codex_trust as ct  # noqa: E402
from skyguard import health, transcripts  # noqa: E402

FX = REPO / "tests" / "fixtures"
CODEX_FX = FX / "codex" / "0.160.0"
EXEC = CODEX_FX / "rollout-exec.jsonl"                  # 2 calls: one shell, one apply_patch
EXEC_DENY = CODEX_FX / "rollout-exec-hook-deny.jsonl"   # a blocked call: no item at all
EXEC_MCP = CODEX_FX / "rollout-exec-mcp.jsonl"
EXEC_PATCH = CODEX_FX / "rollout-exec-apply-patch.jsonl"  # 5 FileChange items
CLAUDE_P = FX / "claude" / "transcript-p.jsonl"        # 1 Bash tool_use
TEMPLATE = (REPO / "agent" / "targets" / "codex" / "hooks.json").read_text(encoding="utf-8")
SID = "019a0000-aaaa-bbbb-cccc-000000000001"


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------
def _rewrite(src: Path, dst: Path, fn) -> Path:
    rows = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
    fn(rows)
    dst.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return dst


def _surface(tmp_path, originator, source="exec", name="tw.jsonl"):
    def fn(rows):
        rows[0]["payload"]["originator"] = originator
        rows[0]["payload"]["source"] = source
    return _rewrite(EXEC, tmp_path / name, fn)


def _rename_item(tmp_path, old, new):
    def fn(rows):
        for r in rows:
            it = r.get("payload", {}).get("item") if r.get("type") == "event_msg" else None
            if isinstance(it, dict) and it.get("type") == old:
                it["type"] = new
    return _rewrite(EXEC, tmp_path / "renamed.jsonl", fn)


def _sessions(root: Path, agent: str) -> Path:
    d = root / ".claude" / "backups" / ".hook-heartbeat" / agent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session(root, agent, sid, transcript, calls, hook=None, start=True, t0=1_800_000_000.0):
    """Write a session file: a start line, then one line per (tool) in `calls`."""
    hook = hook or health.COUNTING_HOOKS[agent][0]
    f = _sessions(root, agent) / "sessions" / (sid + ".log")
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if start:
        lines.append("{} start startup {}".format(t0, transcript))
    for i, tool in enumerate(calls):
        lines.append("{} {} {}".format(t0 + 1 + i, hook, tool))
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _cov(root, agent, last=5):
    return health.coverage(root, agent, last)


def _codex_root(tmp_path: Path, name="Skyrim VR (x86)") -> Path:
    root = tmp_path / name
    (root / ".codex").mkdir(parents=True)
    win = str(root.resolve()).replace(chr(92), "/")
    text = TEMPLATE.replace("{{CODEX_ROOT_POSIX}}", "/x" + win).replace("{{CODEX_ROOT}}", win)
    (root / ".codex" / "hooks.json").write_text(text, encoding="utf-8", newline="\n")
    return root


def _config(tmp_path, root, trusted: bool, name="config.toml") -> Path:
    exp = ct.expected_entries(root / ".codex" / "hooks.json")
    lines = ['model = "x"', "", "[hooks.state]", ""]
    if trusted:
        for k, h in exp.items():
            lines += ["[hooks.state.'{}']".format(k), 'trusted_hash = "{}"'.format(h), ""]
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


NOW = 1_800_000_100.0


def _complete(root, agent, hook, t=NOW - 10, inv="1.1-1"):
    d = _sessions(root, agent)
    (d / "{}.invoked".format(hook)).write_text("{} {}\n".format(t - 0.2, inv), encoding="utf-8")
    (d / "{}.received".format(hook)).write_text("{} {} bytes=40\n".format(t - 0.1, inv), encoding="utf-8")
    (d / "{}.evaluated".format(hook)).write_text("{} {} evaluated allow allow\n".format(t, inv), encoding="utf-8")
    (d / "{}.emitted".format(hook)).write_text("{} {} ok\n".format(t + 0.01, inv), encoding="utf-8")


def _report(root, cfg, **kw):
    out = io.StringIO()
    code = health.report(root, codex_config=cfg, now=NOW, out=out, **kw)
    return code, out.getvalue()


# --------------------------------------------------------------------------------------------------
# the parsers
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path,counts", [
    (EXEC, {"Bash": 1, "apply_patch": 1}),
    (EXEC_PATCH, {"apply_patch": 5}),
    (EXEC_MCP, {"mcp__probe__echo": 1}),
    (EXEC_DENY, {}),            # the blocked call left no item: the rollout under-counts, never over-counts
])
def test_codex_calls_are_counted_from_the_typed_items_not_the_js_text(path, counts):
    assert transcripts.count_calls("codex", path) == ("exec", counts)


def test_claude_tool_use_blocks_are_counted_and_the_entrypoint_is_the_surface():
    assert transcripts.count_calls("claude", CLAUDE_P) == ("claude:sdk-cli", {"Bash": 1})


def test_the_tui_surface_is_read_from_originator(tmp_path):
    assert transcripts.count_calls("codex", _surface(tmp_path, "codex-tui", "vscode"))[0] == "tui"


def test_the_surface_is_never_inferred_from_the_source_field(tmp_path):
    """MEASURED: the TUI probe recorded source=vscode. An exec session reporting source=vscode is still exec."""
    assert transcripts.count_calls("codex", _surface(tmp_path, "codex_exec", "vscode"))[0] == "exec"
    assert transcripts.count_calls("codex", _surface(tmp_path, "codex-tui", "exec"))[0] == "tui"


@pytest.mark.parametrize("originator", ["Codex Desktop", "codex_vscode", "codex-ide"])
def test_an_unprobed_surface_is_reported_unclaimed(tmp_path, originator):
    surface = transcripts.count_calls("codex", _surface(tmp_path, originator))[0]
    assert surface == "unclaimed:" + originator


@pytest.mark.parametrize("item", ["Reasoning", "ContextCompaction"])
def test_measured_non_tool_items_are_recognised_not_unmeasurable(tmp_path, item):
    """MEASURED (Task 0 TUI rollout, Task 14 exec runs): Codex writes Reasoning and ContextCompaction items. They are
    not tool calls, so they must neither count as calls nor make a normal session unmeasurable."""
    rollout = _rename_item(tmp_path, "AgentMessage", item)
    assert transcripts.count_calls("codex", rollout) == ("exec", {"Bash": 1, "apply_patch": 1})


def test_a_renamed_item_type_is_unmeasurable_not_zero_calls(tmp_path):
    with pytest.raises(transcripts.Unmeasurable):
        transcripts.count_calls("codex", _rename_item(tmp_path, "CommandExecution", "ShellRun"))


def test_a_codex_mcp_item_without_a_tool_is_unmeasurable(tmp_path):
    def fn(rows):
        for r in rows:
            it = r.get("payload", {}).get("item") if r.get("type") == "event_msg" else None
            if isinstance(it, dict) and it.get("type") == "McpToolCall":
                del it["tool"]
    with pytest.raises(transcripts.Unmeasurable):
        transcripts.count_calls("codex", _rewrite(EXEC_MCP, tmp_path / "m.jsonl", fn))


@pytest.mark.parametrize("body", ["", "not json\n", "[1, 2]\n", '{"type": "response_item", "payload": {}}\n',
                                  '{"type": "session_meta", "payload": {"source": "exec"}}\n'])
def test_other_codex_shapes_are_unmeasurable(tmp_path, body):
    f = tmp_path / "x.jsonl"
    f.write_text(body, encoding="utf-8")
    with pytest.raises(transcripts.Unmeasurable):
        transcripts.count_calls("codex", f)


def test_a_missing_transcript_is_unmeasurable(tmp_path):
    with pytest.raises(transcripts.Unmeasurable):
        transcripts.count_calls("codex", tmp_path / "nope.jsonl")


def test_a_claude_transcript_without_an_entrypoint_is_unmeasurable(tmp_path):
    def fn(rows):
        for r in rows:
            r.pop("entrypoint", None)
    with pytest.raises(transcripts.Unmeasurable):
        transcripts.count_calls("claude", _rewrite(CLAUDE_P, tmp_path / "c.jsonl", fn))


# --------------------------------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------------------------------
def test_coverage_is_healthy_when_every_call_has_a_session_line(tmp_path):
    _session(tmp_path, "codex", SID, EXEC, ["Bash", "apply_patch"])
    assert _cov(tmp_path, "codex") == [(SID, "exec", 2, 2, "HEALTHY")]


def test_coverage_is_degraded_when_one_call_has_no_line(tmp_path):
    """The E24 shape: a surface that ran a call and never fired PreToolUse."""
    _session(tmp_path, "codex", SID, EXEC, ["Bash"])
    assert _cov(tmp_path, "codex") == [(SID, "exec", 1, 2, "DEGRADED")]


def test_a_blocked_call_leaves_a_line_but_no_item_so_it_is_not_counted_as_called(tmp_path):
    _session(tmp_path, "codex", SID, EXEC_DENY, ["Bash"])
    assert _cov(tmp_path, "codex") == [(SID, "exec", 1, 0, "NOT EXERCISED")]


def test_no_calls_is_not_exercised(tmp_path):
    _session(tmp_path, "codex", SID, EXEC_DENY, [])
    assert _cov(tmp_path, "codex") == [(SID, "exec", 0, 0, "NOT EXERCISED")]


def test_coverage_is_unmeasurable_on_an_unknown_transcript_shape(tmp_path):
    twin = _rename_item(tmp_path, "CommandExecution", "ShellRun")
    _session(tmp_path, "codex", SID, twin, ["Bash", "apply_patch"])
    assert _cov(tmp_path, "codex")[0][4] == "UNMEASURABLE"


def test_coverage_is_unmeasurable_when_the_transcript_is_gone(tmp_path):
    _session(tmp_path, "codex", SID, tmp_path / "deleted.jsonl", ["Bash"])
    assert _cov(tmp_path, "codex")[0][4] == "UNMEASURABLE"


def test_coverage_is_unmeasurable_without_a_start_line(tmp_path):
    _session(tmp_path, "codex", SID, EXEC, ["Bash", "apply_patch"], start=False)
    assert _cov(tmp_path, "codex")[0][4] == "UNMEASURABLE"


def test_coverage_groups_by_surface(tmp_path):
    tui = _surface(tmp_path, "codex-tui", "vscode")
    _session(tmp_path, "codex", "s-exec", EXEC, ["Bash", "apply_patch"], t0=1_800_000_000.0)
    _session(tmp_path, "codex", "s-tui", tui, ["Bash"], t0=1_800_000_100.0)
    rows = {r[0]: r for r in _cov(tmp_path, "codex")}
    assert rows["s-exec"][1:] == ("exec", 2, 2, "HEALTHY")
    assert rows["s-tui"][1:] == ("tui", 1, 2, "DEGRADED")      # the TUI twin ran two calls, one was seen


def test_only_the_pre_tool_guard_counts_as_seeing_a_codex_call(tmp_path):
    _session(tmp_path, "codex", SID, EXEC, ["Bash", "apply_patch"], hook="session-start")
    assert _cov(tmp_path, "codex")[0][4] == "DEGRADED"


def test_claude_coverage_is_judged_per_covered_tool(tmp_path):
    _session(tmp_path, "claude", SID, CLAUDE_P, ["Bash"])
    assert _cov(tmp_path, "claude") == [(SID, "claude:sdk-cli", 1, 1, "HEALTHY")]
    # a guard line for a DIFFERENT tool cannot make up for the unseen Bash call
    _session(tmp_path, "claude", SID, CLAUDE_P, ["Edit"])
    assert _cov(tmp_path, "claude")[0][4] == "DEGRADED"
    # and a hook that is not a guard (backup, snapshot) does not count
    _session(tmp_path, "claude", SID, CLAUDE_P, ["Bash"], hook="backup-before-edit")
    assert _cov(tmp_path, "claude")[0][4] == "DEGRADED"


def test_claude_tools_the_installed_matchers_do_not_cover_are_not_counted(tmp_path):
    def fn(rows):
        for r in rows:
            c = (r.get("message") or {}).get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        b["name"] = "PowerShell"
    twin = _rewrite(CLAUDE_P, tmp_path / "ps.jsonl", fn)
    _session(tmp_path, "claude", SID, twin, [])
    assert _cov(tmp_path, "claude")[0][2:] == (0, 0, "NOT EXERCISED")


def test_coverage_reads_only_the_newest_sessions(tmp_path):
    for i in range(7):
        f = _session(tmp_path, "codex", "s{}".format(i), EXEC_DENY, [])
        import os
        os.utime(str(f), (1_700_000_000 + i, 1_700_000_000 + i))
    assert [r[0] for r in _cov(tmp_path, "codex", last=3)] == ["s6", "s5", "s4"]


# --------------------------------------------------------------------------------------------------
# session records (engine side)
# --------------------------------------------------------------------------------------------------
def test_session_records_round_trip_a_transcript_path_with_spaces(tmp_path):
    d = tmp_path / "hb"
    where = "C:/Users/Some One/.codex/sessions/rollout 1.jsonl"
    assert health.session_record(d, SID, "start", "startup", where)
    assert health.session_record(d, SID, "pre-tool", "Bash")
    transcript, hooked = health._read_session(d / "sessions" / (SID + ".log"))
    assert transcript == where and hooked == {"pre-tool": {"Bash": 1}}


def test_a_session_id_that_is_not_a_plain_name_writes_nothing(tmp_path):
    for bad in ("../escape", "a/b", "", "a b", ".hidden", "x" * 200, None):
        assert health.session_record(tmp_path / "hb", bad, "pre-tool", "Bash") is False
    assert not (tmp_path / "hb").exists() or not any((tmp_path / "hb").rglob("*.log"))
    assert not (tmp_path / "escape.log").exists()


def test_only_the_twenty_newest_session_files_are_kept(tmp_path):
    import os
    d = tmp_path / "hb"
    for i in range(25):
        assert health.session_record(d, "s{:02d}".format(i), "start", "startup", "p")
        f = d / "sessions" / "s{:02d}.log".format(i)
        os.utime(str(f), (1_700_000_000 + i, 1_700_000_000 + i))
    health.prune_sessions(d)
    kept = sorted(p.stem for p in (d / "sessions").glob("*.log"))
    assert len(kept) == health.SESSIONS_KEEP == 20 and kept[0] == "s05" and kept[-1] == "s24"


def _run_engine(root: Path, argv, payload: dict):
    cmd = [sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), *argv, "--project", str(root),
           "--root", str(root), "--root-win", str(root).replace(chr(92), "/"),
           "--invocation", "1.9-1", "--telemetry", str(_sessions(root, argv[0]))]
    return subprocess.run(cmd, input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=120)


def test_the_engine_records_a_codex_session_and_its_calls(tmp_path):
    root = _codex_root(tmp_path)
    base = {"session_id": SID, "transcript_path": "C:/t/rollout.jsonl", "cwd": str(root).replace(chr(92), "/")}
    r = _run_engine(root, ["codex", "session-start"], dict(base, hook_event_name="SessionStart", source="startup"))
    assert r.returncode == 0, r.stderr
    r = _run_engine(root, ["codex", "pre-tool"], dict(base, hook_event_name="PreToolUse", tool_name="Bash",
                                                      tool_input={"command": "echo hi"}))
    assert r.returncode == 0, r.stderr
    f = _sessions(root, "codex") / "sessions" / (SID + ".log")
    lines = [ln.split(None, 3) for ln in f.read_text(encoding="utf-8").splitlines()]
    assert [ln[1:] for ln in lines] == [["start", "startup", "C:/t/rollout.jsonl"], ["pre-tool", "Bash"]]


def _append(f, line):
    with open(f, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def test_coverage_excludes_refused_calls_from_hooked(tmp_path):
    """A refused call leaves no transcript item, so counting its hook record as 'hooked' let one refusal hide
    one unguarded call: hooked 2 / called 2 HEALTHY. The refusal is recorded and subtracted."""
    f = _session(tmp_path, "codex", SID, EXEC, ["Bash", "mcp__x"])
    _append(f, "1800000009.0 refused mcp__x")
    assert _cov(tmp_path, "codex") == [(SID, "exec", 1, 2, "DEGRADED")]


def test_a_refusal_alone_does_not_make_a_session_degraded(tmp_path):
    f = _session(tmp_path, "codex", SID, EXEC, ["Bash", "apply_patch", "Bash"])
    _append(f, "1800000009.0 refused Bash")
    assert _cov(tmp_path, "codex") == [(SID, "exec", 2, 2, "HEALTHY")]


def test_the_engine_records_a_refusal_in_the_session_line_and_an_allow_does_not(tmp_path):
    root = _codex_root(tmp_path)
    base = {"session_id": SID, "transcript_path": "C:/t/r.jsonl", "cwd": str(root).replace(chr(92), "/"),
            "hook_event_name": "PreToolUse", "tool_name": "Bash"}
    _run_engine(root, ["codex", "pre-tool"], dict(base, tool_input={"command": "echo hi"}))
    f = _sessions(root, "codex") / "sessions" / (SID + ".log")
    assert "refused" not in f.read_text(encoding="utf-8")
    _run_engine(root, ["codex", "pre-tool"], dict(base, tool_input={
        "command": "reg delete HKLM\\SOFTWARE\\WOW6432Node\\Bethesda Softworks /f"}))
    assert [ln.split()[1:] for ln in f.read_text(encoding="utf-8").splitlines()] == [
        ["pre-tool", "Bash"], ["pre-tool", "Bash"], ["refused", "Bash"]]


def test_a_denied_call_is_still_recorded_as_seen(tmp_path):
    root = _codex_root(tmp_path)
    p = {"session_id": SID, "transcript_path": "C:/t/r.jsonl", "cwd": str(root).replace(chr(92), "/"),
         "hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "reg delete HKLM\\SOFTWARE\\WOW6432Node\\Bethesda Softworks /f"}}
    r = _run_engine(root, ["codex", "pre-tool"], p)
    assert b"deny" in r.stdout
    assert "pre-tool Bash" in (_sessions(root, "codex") / "sessions" / (SID + ".log")).read_text(encoding="utf-8")


def test_the_engine_records_a_claude_guard_call_but_not_a_backup_call(tmp_path):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    p = {"session_id": SID, "transcript_path": "C:/t/c.jsonl", "tool_name": "Bash",
         "tool_input": {"command": "echo hi"}, "cwd": str(root)}
    assert _run_engine(root, ["claude", "protect-bash"], p).returncode == 0
    assert _run_engine(root, ["claude", "snapshot-before-tool"], p).returncode == 0
    text = (_sessions(root, "claude") / "sessions" / (SID + ".log")).read_text(encoding="utf-8")
    assert [ln.split()[1:] for ln in text.splitlines()] == [["protect-bash", "Bash"]]


# --------------------------------------------------------------------------------------------------
# the report: trust block, never-invoked, exit codes
# --------------------------------------------------------------------------------------------------
def test_an_untrusted_install_says_codex_hooks_are_not_running_and_exits_1(tmp_path):
    root = _codex_root(tmp_path)
    code, text = _report(root, _config(tmp_path, root, trusted=False))
    assert "Codex hooks NOT RUNNING (untrusted or unapproved)" in text
    assert code == 1


def test_trusted_and_proven_exits_0(tmp_path):
    root = _codex_root(tmp_path)
    for h in health.CODEX_HOOKS:
        _complete(root, "codex", h)
    _session(root, "codex", SID, EXEC, ["Bash", "apply_patch"])
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert code == 0, text
    assert "all Codex hooks trusted" in text and "exec" in text and "hooked   2 / called   2  HEALTHY" in text


def test_approved_but_never_invoked_is_reported_and_exits_1(tmp_path):
    """AllSigned group policy and WSL-hosted Codex: the entry never starts, so there is no record at all.
    That must not read HEALTHY, and must not read as a merely quiet NOT EXERCISED."""
    root = _codex_root(tmp_path)
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert text.count("NEVER INVOKED") >= 2 and "approved, but it has never run" in text
    assert "HEALTHY" not in text.split("RESULT")[0].split("COVERAGE")[0]
    assert code == 1


def test_a_silent_pre_tool_hook_is_not_never_invoked_when_session_start_ran(tmp_path):
    """One hook with records proves the entry starts; a session with no tool call has simply not run pre-tool."""
    root = _codex_root(tmp_path)
    _complete(root, "codex", "session-start")
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert "NEVER INVOKED" not in text.replace("never-invoked: 0", "")
    assert code == 0


def test_an_untrusted_install_is_never_reported_never_invoked(tmp_path):
    root = _codex_root(tmp_path)
    json_out = io.StringIO()
    health.report(root, codex_config=_config(tmp_path, root, trusted=False), now=NOW, out=json_out, as_json=True)
    rows = json.loads(json_out.getvalue())["rows"]
    assert {r["state"] for r in rows} == {"NOT EXERCISED"}


def test_degraded_coverage_folds_into_exit_1(tmp_path):
    root = _codex_root(tmp_path)
    for h in health.CODEX_HOOKS:
        _complete(root, "codex", h)
    _session(root, "codex", SID, EXEC, ["Bash"])
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert code == 1 and "DEGRADED" in text and "DEGRADED COVERAGE" in text


def test_unmeasurable_coverage_is_printed_and_cannot_prove_ok(tmp_path):
    root = _codex_root(tmp_path)
    for h in health.CODEX_HOOKS:
        _complete(root, "codex", h)
    _session(root, "codex", SID, tmp_path / "deleted.jsonl", ["Bash"])
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert code == 2 and "UNMEASURABLE" in text


def test_an_unreadable_codex_config_is_unknown_and_exits_2(tmp_path):
    root = _codex_root(tmp_path)
    for h in health.CODEX_HOOKS:
        _complete(root, "codex", h)
    code, text = _report(root, tmp_path / "no-such-config.toml")
    assert code == 2 and "UNKNOWN" in text


def test_a_modified_hook_exits_1(tmp_path):
    root = _codex_root(tmp_path)
    exp = ct.expected_entries(root / ".codex" / "hooks.json")
    (k1, h1), (k2, _h2) = sorted(exp.items())
    cfg = tmp_path / "config.toml"
    cfg.write_text("[hooks.state]\n[hooks.state.'{}']\ntrusted_hash = \"{}\"\n[hooks.state.'{}']\n"
                   "trusted_hash = \"sha256:{}\"\n".format(k1, h1, k2, "0" * 64), encoding="utf-8")
    code, text = _report(root, cfg)
    assert code == 1 and "modified" in text


def test_an_unrendered_install_is_not_configured_never_untrusted(tmp_path):
    """setup.sh on a non-Windows host leaves .codex/hooks.json holding {{CODEX_ROOT}}: nothing was set up."""
    root = tmp_path / "linux-install"
    (root / ".codex").mkdir(parents=True)
    (root / ".codex" / "hooks.json").write_text(TEMPLATE, encoding="utf-8", newline="\n")
    rep = ct.trust_report(root / ".codex" / "hooks.json", tmp_path / "none.toml", root, windows=False)
    assert rep["overall"] == "unrunnable" and rep["unrendered"] is True
    assert all(r["status"] != "untrusted" for r in rep["hooks"])
    code, text = _report(root, _config(tmp_path, _codex_root(tmp_path / "other"), trusted=False))
    assert "NOT CONFIGURED" in text and "NOT RUNNING" not in text and "NEVER INVOKED" not in text
    assert code == 0, text


def test_a_rendered_install_is_not_marked_unrendered(tmp_path):
    root = _codex_root(tmp_path)
    assert ct.trust_report(root / ".codex" / "hooks.json", _config(tmp_path, root, True), root)["unrendered"] is False


def test_the_json_report_carries_trust_and_coverage(tmp_path):
    root = _codex_root(tmp_path)
    _session(root, "codex", SID, EXEC, ["Bash", "apply_patch"])
    out = io.StringIO()
    health.report(root, codex_config=_config(tmp_path, root, True), now=NOW, out=out, as_json=True)
    d = json.loads(out.getvalue())
    assert d["trust"]["overall"] == "trusted"
    assert d["coverage"]["codex"][0]["state"] == "HEALTHY" and d["coverage_counts"]["HEALTHY"] == 1


def test_the_report_runs_as_a_script_too(tmp_path):
    root = _codex_root(tmp_path)
    cfg = _config(tmp_path, root, trusted=False)
    p = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "health.py"), "report", "--root", str(root),
                        "--codex-config", str(cfg)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 1 and "NOT RUNNING" in p.stdout, p.stdout + p.stderr


def test_the_report_only_claims_exec_and_tui(tmp_path):
    root = _codex_root(tmp_path)
    _session(root, "codex", SID, _surface(tmp_path, "Codex Desktop"), ["Bash", "apply_patch"])
    code, text = _report(root, _config(tmp_path, root, True))
    assert "unclaimed:Codex Desktop" in text and "claimed surfaces: exec, tui" in text


def test_a_root_the_hooks_cannot_start_from_fails_the_canary_even_when_approved(tmp_path):
    root = _codex_root(tmp_path, "Skyrim 100%")
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert "CANNOT RUN" in text and code == 1


def test_a_streamed_claude_tool_use_is_counted_once(tmp_path):
    rows = [json.loads(line) for line in CLAUDE_P.read_text(encoding="utf-8").splitlines() if line.strip()]
    dup = [r for r in rows if r.get("type") == "assistant"
           and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in r["message"]["content"])]
    out = tmp_path / "dup.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in rows + dup) + "\n", encoding="utf-8")
    assert transcripts.count_calls("claude", out)[1] == {"Bash": 1}


# --------------------------------------------------------------------------------------------------
# fix round 1: false-green paths
# --------------------------------------------------------------------------------------------------
def test_a_rollout_with_content_but_no_recognised_item_is_unmeasurable(tmp_path):
    """A TUI that renames item_completed would otherwise read as zero calls = NOT EXERCISED."""
    def fn(rows):
        for r in rows:
            if r.get("type") == "event_msg" and r["payload"].get("type") == "item_completed":
                r["payload"]["type"] = "item_done"
    with pytest.raises(transcripts.Unmeasurable):
        transcripts.count_calls("codex", _rewrite(EXEC, tmp_path / "evt.jsonl", fn))


def test_a_tiny_rollout_with_no_items_is_just_a_quiet_session(tmp_path):
    f = tmp_path / "tiny.jsonl"
    rows = [json.loads(line) for line in EXEC.read_text(encoding="utf-8").splitlines() if line.strip()][:3]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert transcripts.count_calls("codex", f) == ("exec", {})


def test_session_start_alone_does_not_prove_the_codex_guard_runs(tmp_path):
    root = _codex_root(tmp_path)
    _complete(root, "codex", "session-start")
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert "RESULT: OK --" not in text and "pre-tool has not completed an evaluation" in text
    assert code == 0


def test_stale_hooks_do_not_read_as_ok(tmp_path):
    root = _codex_root(tmp_path)
    for h in health.CODEX_HOOKS:
        _complete(root, "codex", h, t=NOW - 10 * 86400)
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    assert "RESULT: OK --" not in text and "STALE (no recent completed evaluation): codex pre-tool" in text


def test_unmeasurable_coverage_exits_2_and_the_result_names_it(tmp_path):
    root = _codex_root(tmp_path)
    for h in health.CODEX_HOOKS:
        _complete(root, "codex", h)
    _session(root, "codex", SID, tmp_path / "deleted.jsonl", ["Bash"])
    code, text = _report(root, _config(tmp_path, root, trusted=True))
    result = text.split("RESULT:")[1]
    assert code == 2 and "UNMEASURABLE" in result and "codex session " + SID[:13] in result


def test_an_unrendered_codex_keeps_exit_0_but_the_result_says_not_configured(tmp_path):
    root = tmp_path / "claude-only"
    (root / ".codex").mkdir(parents=True)
    (root / ".codex" / "hooks.json").write_text(TEMPLATE, encoding="utf-8", newline="\n")
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "hooks" / "protect-bash.sh").write_text("#", encoding="utf-8")
    _complete(root, "claude", "protect-bash")
    code, text = _report(root, tmp_path / "none.toml")
    result = text.split("RESULT:")[1]
    assert code == 0 and "Codex not configured" in result and not result.strip().startswith("OK")
