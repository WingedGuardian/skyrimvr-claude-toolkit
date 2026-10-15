"""The Codex adapter (translation only) and the engine's Codex roles (pre_tool, side_effects)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, paths  # noqa: E402
from skyguard.adapters import codex  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

RUN = REPO / "tools" / "skyguard" / "run.py"
FIXTURES = sorted(p for p in (REPO / "tests" / "fixtures" / "payloads" / "codex").rglob("*.json") if p.name != "EXPECTED.json")


def _pre(name, tool_input, cwd="C:/G"):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": name, "cwd": cwd, "tool_input": tool_input})


def _patch(body, cwd="C:/G"):
    return _pre("apply_patch", {"command": "*** Begin Patch\n" + body + "\n*** End Patch"}, cwd)


# ---- render ---------------------------------------------------------------------------------------

def test_ask_never_reaches_codex():
    for decision in ("ask", "allow", "deny", "bogus"):
        out = codex.render(Verdict(decision, "r", "c"))
        assert '"ask"' not in out and '"allow"' not in out


def test_ask_becomes_a_deny_that_says_why():
    h = json.loads(codex.render(Verdict("ask", "ReSaver --apply mutates a save")))["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert h["permissionDecisionReason"].startswith("NEEDS YOUR CONFIRMATION")
    assert "ReSaver --apply" in h["permissionDecisionReason"]
    assert "type ! followed by" in h["permissionDecisionReason"]   # Task 0: `!` fires no PreToolUse


def test_an_unknown_decision_is_refused_not_allowed():
    h = json.loads(codex.render(Verdict("bogus", "x")))["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"


def test_plain_allow_prints_nothing():
    assert codex.render(Verdict()) == ""


def test_allow_with_advice_is_context_only():
    h = json.loads(codex.render(Verdict("allow", "", "advice")))["hookSpecificOutput"]
    assert h == {"hookEventName": "PreToolUse", "additionalContext": "advice"}


def test_long_context_is_capped_with_a_visible_note():
    out = json.loads(codex.render(Verdict("allow", "", "x" * 20000)))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert len(ctx.encode("utf-8")) <= codex.CONTEXT_LIMIT
    assert "truncated" in ctx


def test_the_cap_counts_bytes_not_characters():
    ctx = json.loads(codex.render(Verdict("allow", "", "\u00e9" * 5000)))["hookSpecificOutput"]["additionalContext"]
    assert len(ctx.encode("utf-8")) <= codex.CONTEXT_LIMIT and "truncated" in ctx


def test_the_context_limit_is_the_measured_one():
    assert codex.CONTEXT_LIMIT == 8000 and codex.INSTRUCTION_LIMIT_BYTES == 32768


# ---- to_request -----------------------------------------------------------------------------------

def test_session_start_maps_with_its_source():
    r = codex.to_request(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "cwd": "C:/G"}))
    assert (r.event, r.kind, r.source, r.failure) == ("session_start", "none", "compact", "")


def test_bash_is_a_shell_request_with_the_platform_shell():
    raw = _pre("Bash", {"command": "Get-Date"}, "C:\\G")
    r = codex.to_request(raw, "win32")
    assert (r.kind, r.shell, r.command, r.cwd, r.paths) == ("shell", "powershell", "Get-Date", "C:\\G", ())
    assert codex.to_request(raw, "linux").shell == "bash"


def test_a_relative_patch_path_resolves_against_the_session_cwd():
    raw = _pre("apply_patch", {"command": "*** Begin Patch\n*** Add File: ..\\x.esp\n+a\n*** End Patch"},
               "C:\\G\\Data\\Scripts")
    assert codex.to_request(raw, "win32").paths == ("C:/G/Data/x.esp",)


def test_apply_patch_maps_to_a_write_with_ops():
    r = codex.to_request(_patch("*** Update File: Data/a.ini\n@@\n-a\n+b\n*** Delete File: Data/b.txt"), "win32")
    assert (r.kind, r.paths, r.ops) == ("write", ("C:/G/Data/a.ini", "C:/G/Data/b.txt"), ("update", "delete"))


def test_a_move_yields_both_paths():
    r = codex.to_request(_patch("*** Update File: a.txt\n*** Move to: b.txt\n@@\n-a\n+b"), "win32")
    assert r.ops == ("move_from", "move_to") and r.paths == ("C:/G/a.txt", "C:/G/b.txt")


def test_a_patch_through_the_shell_carries_its_paths():
    cmd = "cd Data && apply_patch <<'P'\n*** Begin Patch\n*** Delete File: Skyrim.esm\n*** End Patch\nP"
    r = codex.to_request(_pre("Bash", {"command": cmd}), "win32")
    assert (r.kind, r.paths, r.ops) == ("shell", ("C:/G/Data/Skyrim.esm",), ("delete",))


def test_posix_resolution_uses_posix_rules():
    r = codex.to_request(_patch("*** Add File: x.txt", "/home/u/p"), "linux")
    assert r.paths == ("/home/u/p/x.txt",)


def test_mcp_tools_map_to_mcp_and_others_to_other():
    r = codex.to_request(_pre("mcp__probe__echo", {"text": "hi"}))
    assert (r.kind, r.mcp_tool, r.failure) == ("mcp", "mcp__probe__echo", "")
    assert json.loads(r.mcp_args) == {"text": "hi"}
    r = codex.to_request(_pre("collaborationspawn_agent", {}))
    assert (r.kind, r.mcp_tool, r.failure) == ("other", "", "")


@pytest.mark.parametrize("raw", [
    "", "   \n", "not json", "[]", "null",
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":5}}',
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{}}',
    '{"hook_event_name":"PreToolUse","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\\n*** End Patch"}}',
    '{"hook_event_name":"PreToolUse","tool_name":"apply_patch","tool_input":{}}',
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"apply_patch <<P\\nfoo\\nP"}}',
])
def test_untranslatable_payloads_carry_a_failure(raw):
    assert codex.to_request(raw, "win32").failure


@pytest.mark.parametrize("fx", FIXTURES, ids=lambda p: p.name)
def test_every_committed_codex_fixture_translates_and_renders(fx):
    raw = fx.read_text(encoding="utf-8")
    r = codex.to_request(raw, "win32")
    assert not r.failure, r
    assert r.agent == "codex" and r.event in ("pre_tool", "session_start")
    out = codex.render(Verdict("deny", "x"))
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- engine.pre_tool ------------------------------------------------------------------------------

def _run(tmp_path, raw, platform=sys.platform):
    # The HOST platform (Linux CI run 38187755448): these requests name real tmp_path files, and an absolute POSIX
    # path under the Windows adapter is a mix no host makes (py3.13 ntpath joins it onto the C:/G cwd).
    req = codex.to_request(raw, platform)
    return engine.pre_tool(req, str(tmp_path), lambda: paths.roots(str(tmp_path), "", {}), Audit(str(tmp_path)))


def test_a_plugin_write_is_denied(tmp_path):
    v = _run(tmp_path, _patch("*** Add File: Data/a.esp\n+x"))
    assert (v.decision, v.code) == ("deny", "write.DENY_PLUGIN")


def test_an_untouched_ordinary_write_is_allowed(tmp_path):
    v = _run(tmp_path, _patch("*** Add File: notes/a.txt\n+x"))
    assert v.decision == "allow" and v.status == "evaluated"


def test_an_update_of_an_existing_skse_ini_gets_advice_and_a_backup(tmp_path):
    ini = tmp_path / "Data" / "SKSE" / "Plugins" / "x.ini"
    ini.parent.mkdir(parents=True)
    ini.write_text("a=1\n")
    raw = _patch("*** Update File: {}\n@@\n-a=1\n+a=2".format(str(ini).replace("\\", "/")))
    v = _run(tmp_path, raw)
    assert v.decision == "allow" and v.code == "write.ADV_SKSE_CONFIG" and v.context   # Plan 5 rewrote the text
    assert any(p.name.endswith("x.ini") for p in (tmp_path / ".claude" / "backups").iterdir())


def test_a_delete_of_an_existing_file_is_backed_up(tmp_path):
    f = tmp_path / "notes" / "gone.txt"
    f.parent.mkdir()
    f.write_text("keep me")
    _run(tmp_path, _patch("*** Delete File: {}".format(str(f).replace("\\", "/"))))
    assert any(p.name.endswith("gone.txt") for p in (tmp_path / ".claude" / "backups").iterdir())


def test_an_add_takes_no_backup(tmp_path):
    f = tmp_path / "notes" / "new.txt"
    f.parent.mkdir()
    f.write_text("already there")
    _run(tmp_path, _patch("*** Add File: {}\n+x".format(str(f).replace("\\", "/"))))
    bdir = tmp_path / ".claude" / "backups"
    assert not bdir.exists() or not any(p.name.endswith("new.txt") for p in bdir.iterdir())


def test_a_backup_is_taken_even_when_the_write_is_denied(tmp_path):
    f = tmp_path / "Data" / "a.esp"
    f.parent.mkdir()
    f.write_text("plugin")
    v = _run(tmp_path, _patch("*** Delete File: {}".format(str(f).replace("\\", "/"))))
    assert v.decision == "deny"
    assert any(p.name.endswith("a.esp") for p in (tmp_path / ".claude" / "backups").iterdir())


def test_a_shell_patch_is_checked_as_shell_and_as_write(tmp_path):
    cmd = "cd Data && apply_patch <<'P'\n*** Begin Patch\n*** Add File: z.esp\n+x\n*** End Patch\nP"
    v = _run(tmp_path, _pre("Bash", {"command": cmd}))
    assert (v.decision, v.code) == ("deny", "write.DENY_PLUGIN")


def test_a_plain_shell_call_is_evaluated(tmp_path):
    v = _run(tmp_path, _pre("Bash", {"command": "Get-Date"}))
    assert v.decision == "allow" and v.status == "evaluated"


def test_an_untranslatable_request_is_a_failed_deny(tmp_path):
    v = _run(tmp_path, "not json")
    assert (v.decision, v.status, v.code) == ("deny", "failed", "codex.TRANSLATE")
    assert v.reason.startswith("GUARD INERT") and "not JSON" in v.reason


def test_an_unexpected_event_is_a_failed_deny(tmp_path):
    v = _run(tmp_path, json.dumps({"hook_event_name": "Stop"}))
    assert (v.decision, v.status, v.code) == ("deny", "failed", "codex.TRANSLATE")


def test_an_mcp_tool_is_allowed_and_recorded(tmp_path):
    v = _run(tmp_path, _pre("mcp__probe__echo", {"text": "hi"}))
    assert (v.decision, v.status) == ("allow", "evaluated")
    lines = (tmp_path / ".claude" / "backups" / "codex-unhooked-tools.log").read_text().splitlines()
    assert len(lines) == 1 and "mcp__probe__echo" in lines[0]


def test_an_unknown_tool_is_allowed_and_recorded(tmp_path):
    v = _run(tmp_path, _pre("collaborationspawn_agent", {}))
    assert v.decision == "allow"
    assert "collaborationspawn_agent" in (tmp_path / ".claude" / "backups" / "codex-unhooked-tools.log").read_text()


def test_a_shell_call_is_not_recorded_as_unhooked(tmp_path):
    _run(tmp_path, _pre("Bash", {"command": "Get-Date"}))
    assert not (tmp_path / ".claude" / "backups" / "codex-unhooked-tools.log").exists()


def test_a_side_effect_exception_is_partial_and_keeps_the_decision(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(engine.snapshot, "run", boom)
    v = _run(tmp_path, _pre("Bash", {"command": "Get-Date"}))
    assert (v.decision, v.status) == ("allow", "partial")


def test_a_backup_exception_is_partial_and_the_deny_stands(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.backup, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    v = _run(tmp_path, _patch("*** Delete File: Data/a.esp"))
    assert (v.decision, v.status, v.code) == ("deny", "partial", "write.DENY_PLUGIN")


def test_one_failing_side_effect_does_not_skip_the_next(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(engine.snapshot, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(engine.backup, "run", lambda *a, **k: calls.append(a[2]))
    cmd = "apply_patch <<'P'\n*** Begin Patch\n*** Delete File: n/a.txt\n*** End Patch\nP"
    _run(tmp_path, _pre("Bash", {"command": cmd}))
    assert calls == ["C:/G/n/a.txt"]


def test_unreadable_install_env_refuses_a_shell_call(tmp_path):
    def bad():
        raise engine.RootsUnavailable("env unreadable")
    req = codex.to_request(_pre("Bash", {"command": "Get-Date"}), "win32")
    v = engine.pre_tool(req, str(tmp_path), bad, Audit(str(tmp_path)))
    assert (v.decision, v.status) == ("deny", "failed") and v.reason == "env unreadable"


# ---- run.py roles ---------------------------------------------------------------------------------

def _cli(tmp_path, agent, role, stdin):
    return subprocess.run([sys.executable, str(RUN), agent, role, "--project", str(tmp_path),
                           "--root", str(tmp_path), "--bash", "bash"],
                          input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)


def test_cli_pre_tool_allow_prints_the_sentinel_only(tmp_path):
    r = _cli(tmp_path, "codex", "pre-tool", _pre("Bash", {"command": "Get-Date"}))
    assert r.returncode == 0 and r.stdout.decode().replace("\r", "") == "SKYGUARD-OK\n"


def test_cli_pre_tool_deny_prints_sentinel_then_deny_json(tmp_path):
    r = _cli(tmp_path, "codex", "pre-tool", _patch("*** Add File: Data/a.esp\n+x"))
    head, body = r.stdout.decode().split("\n", 1)
    assert r.returncode == 0 and head == "SKYGUARD-OK"
    assert json.loads(body)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cli_empty_stdin_is_a_deny_not_a_crash(tmp_path):
    r = _cli(tmp_path, "codex", "pre-tool", "")
    head, body = r.stdout.decode().split("\n", 1)
    assert head == "SKYGUARD-OK" and "GUARD INERT" in body


def test_cli_session_start_prints_the_banner(tmp_path):
    r = _cli(tmp_path, "codex", "session-start", json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    out = r.stdout.decode().replace("\r", "")
    assert r.returncode == 0 and out.startswith("SKYGUARD-OK\n" + codex.BANNER)


def test_cli_codex_roles_are_refused_for_claude_and_vice_versa(tmp_path):
    for agent, role in (("claude", "pre-tool"), ("claude", "session-start"), ("codex", "protect-bash")):
        r = _cli(tmp_path, agent, role, "{}")
        assert r.returncode == 1 and r.stdout == b"", (agent, role)


# ---- fix round 1 ----------------------------------------------------------------------------------

def test_session_body_is_capped_keeping_banner_and_preferences():
    prefs = "USER PREFERENCES (from KNOWLEDGEBASE.local.md, section `## My preferences`) -- follow these all session:\nbe terse"
    out = codex.session_body("kb line\n" * 5000 + "\n" + prefs)
    assert len(out.encode("utf-8")) <= codex.CONTEXT_LIMIT
    assert out.startswith(codex.BANNER) and out.endswith("be terse") and "truncated" in out


def test_a_short_session_body_is_untouched():
    assert codex.session_body("hello") == codex.BANNER + "\nhello"
    assert codex.session_body("") == codex.BANNER


def test_an_oversized_preferences_block_is_still_capped():
    out = codex.session_body("USER PREFERENCES (from x) -- y:\n" + "p" * 20000)
    assert len(out.encode("utf-8")) <= codex.CONTEXT_LIMIT and out.startswith(codex.BANNER)


def test_run_session_start_output_is_capped(tmp_path, monkeypatch, capsys):
    from skyguard import run
    monkeypatch.setattr(run.session, "run_status", lambda *a, **k: ("noise line\n" * 3000,"evaluated", "session"))
    sent = []
    monkeypatch.setattr(run, "_emit", sent.append)
    a = run.argparse.Namespace(project=str(tmp_path), bash="bash", role="session-start", invocation="", telemetry="",
                               root="", root_win="")     # T8 fix round 1: the session start reads the guard's root
    assert run._codex(a, json.dumps({"hook_event_name": "SessionStart", "source": "startup"})) == 0
    assert sent[0].startswith("SKYGUARD-OK\n" + codex.BANNER)
    assert len(sent[0].encode("utf-8")) <= codex.CONTEXT_LIMIT + len("SKYGUARD-OK\n")


def test_an_unknown_tool_with_a_command_string_gets_the_shell_rules(tmp_path):
    v = _run(tmp_path, _pre("exec_command", {"command": "rm -rf " + str(tmp_path).replace(chr(92), "/")}))
    assert v.decision == "deny" and v.code.startswith("shell."), v
    r = codex.to_request(_pre("exec_command", {"command": "Get-Date"}), "win32")
    assert r.kind == "shell" and r.command == "Get-Date" and r.tool_name == "exec_command"
    assert "exec_command" in (tmp_path / ".claude" / "backups" / "codex-unhooked-tools.log").read_text()


def test_an_unknown_tool_without_a_command_stays_other():
    assert codex.to_request(_pre("weird", {"x": 1})).kind == "other"
    assert codex.to_request(_pre("mcp__s__t", {"command": "rm -rf /"})).kind == "mcp"


def test_a_refused_call_is_not_logged_as_an_unhooked_tool(tmp_path):
    _run(tmp_path, "not json")
    _run(tmp_path, json.dumps({"hook_event_name": "Stop"}))
    assert not (tmp_path / ".claude" / "backups" / "codex-unhooked-tools.log").exists()


def test_an_argv_list_command_is_guarded_as_a_shell_request():
    r = codex.to_request(_pre("exec_command", {"command": ["git", "status"]}), "linux")
    assert r.kind == "shell" and r.command == "git status" and r.shell == "bash"
    r = codex.to_request(_pre("Bash", {"command": ["Get-Date", "-Format", "o"]}), "win32")
    assert r.kind == "shell" and r.shell == "powershell" and r.command == "Get-Date -Format o"


def test_an_argv_list_is_quoted_so_one_element_stays_one_argument():
    bash = codex.to_request(_pre("Bash", {"command": ["echo", "a b;c"]}), "linux").command
    assert bash == "echo 'a b;c'"
    ps = codex.to_request(_pre("Bash", {"command": ["echo", "a b;c", "it's"]}), "win32").command
    assert ps == "echo 'a b;c' 'it''s'"


@pytest.mark.parametrize("cmd", [[], ["rm", 5], ["rm", None], [["rm"]], ["rm", {"a": 1}]])
def test_an_argv_list_with_a_non_string_element_is_refused(cmd):
    assert codex.to_request(_pre("exec_command", {"command": cmd}), "win32").failure
    assert codex.to_request(_pre("Bash", {"command": cmd}), "win32").failure


def test_an_argv_list_command_gets_the_shell_rules(tmp_path):
    v = _run(tmp_path, _pre("exec_command", {"command": ["rm", "-rf", str(tmp_path).replace(chr(92), "/")]}))
    assert v.decision == "deny" and v.code.startswith("shell."), v


def test_the_unreadable_env_text_is_agent_neutral():
    assert "protect-bash.sh" not in codex.ENV_UNREADABLE and "GUARD INERT" in codex.ENV_UNREADABLE


def test_a_leading_bom_is_ignored():
    r = codex.to_request("\ufeff" + _pre("Bash", {"command": "Get-Date"}), "win32")
    assert not r.failure and r.command == "Get-Date"


def test_cli_env_unreadable_text_is_agent_neutral(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "skyrim-paths.env").write_bytes(b"\xff\xfe\x00bad")
    r = _cli(tmp_path, "codex", "pre-tool", _pre("Bash", {"command": "Get-Date"}))
    body = r.stdout.decode().split("\n", 1)[1]
    reason = json.loads(body)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "GUARD INERT" in reason and "protect-bash.sh" not in reason


# ---- merge of Plans 4+5 ------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [12, 1.5, True, False, None, {"a": 1}], ids=["int", "float", "true", "false", "null", "object"])
@pytest.mark.parametrize("tool", ["Bash", "exec_command"])
def test_a_scalar_non_string_command_is_refused_before_the_rules(tool, cmd, tmp_path):
    """Plan 5 Task 12 on Codex: a command that is neither a string nor an argv list is no command the guard can
    judge. Left as kind 'other' (an unknown tool) it was ALLOWED unexamined; it must be a failed deny, decided
    before any rule runs."""
    r = codex.to_request(_pre(tool, {"command": cmd}), "win32")
    assert r.failure and r.kind != "shell", r
    v = _run(tmp_path, _pre(tool, {"command": cmd}))
    assert (v.decision, v.status, v.code) == ("deny", "failed", "codex.TRANSLATE"), v


def test_an_unknown_tool_with_no_command_key_is_still_other():
    """The twin: only a PRESENT command of the wrong type is refused; a tool that carries no command is not a shell."""
    assert codex.to_request(_pre("weird", {"x": 1}), "win32").kind == "other"
    assert not codex.to_request(_pre("weird", {"x": 1}), "win32").failure


@pytest.mark.parametrize("cmd,code", [
    ('Remove-Item -Recurse -Force "{g}/Data/meshes"', "shell.S-DEL-INSTALL"),
    ('rm -rf "{g}/Data/meshes"', "shell.S-DEL-INSTALL"),
    ("Remove-Item -Recurse Data", "shell.S-DEL-INSTALL"),                       # relative to the session cwd
    ('Remove-Item "{c}/Skyrim.ini"', "shell.S-DEL-CONFIG"),
    ('rm "{c}/SkyrimPrefs.ini"', "shell.S-DEL-CONFIG"),   # (`rm -f` is ambiguous in PowerShell: it does not run)
])
def test_the_codex_instructions_claim_deleting_in_game_or_config_is_refused(tmp_path, cmd, code):
    """agent/instructions/codex.md (-> AGENTS.md): "deleting in the game or config folders is refused". Re-verified
    against the MERGED engine (Plan 5 rewrote the delete rules) through the Codex path, PowerShell dialect."""
    game = tmp_path / "Skyrim VR"
    cfg = tmp_path / "My Games" / "Skyrim VR"
    (game / "Data" / "meshes").mkdir(parents=True)
    cfg.mkdir(parents=True)
    g, c = str(game).replace(chr(92), "/"), str(cfg).replace(chr(92), "/")
    req = codex.to_request(_pre("Bash", {"command": cmd.format(g=g, c=c)}, cwd=g), "win32")
    v = engine.pre_tool(req, str(tmp_path), lambda: paths.roots(g, "", {"SKYRIM_CONFIG_DIR": c}), Audit(str(tmp_path)))
    assert (v.decision, v.code) == ("deny", code), v
    assert json.loads(codex.render(v))["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- DevBench on Codex (gap X4; Task 17): DENY-class outcomes only ---------------------------------------------------

DEVBENCH_TOOLS = ["mcp__devbench-vr__console", "mcp__devbench__console", "mcp__devbench_vr__console"]


def _unhooked_log(tmp_path):
    f = tmp_path / ".claude" / "backups" / "codex-unhooked-tools.log"
    return f.read_text() if f.exists() else ""


@pytest.mark.parametrize("tool", DEVBENCH_TOOLS)
def test_a_devbench_smp_reset_is_denied_on_codex(tmp_path, tool):
    v = _run(tmp_path, _pre(tool, {"action": "exec", "command": "smp reset"}))
    assert (v.decision, v.status, v.code) == ("deny", "evaluated", "shell.S-DEVBENCH-CRASH")
    out = json.loads(codex.render(v))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "smp reset" in out["permissionDecisionReason"]


def test_a_judged_devbench_call_is_not_logged_as_unhooked(tmp_path):
    _run(tmp_path, _pre("mcp__devbench-vr__console", {"action": "exec", "command": "smp reset"}))
    _run(tmp_path, _pre("mcp__devbench-vr__inspect", {"kind": "state"}))
    assert _unhooked_log(tmp_path) == ""


def test_an_ordinary_devbench_call_is_allowed_silently(tmp_path):
    v = _run(tmp_path, _pre("mcp__devbench-vr__inspect", {"kind": "state"}))
    assert (v.decision, v.context, v.status) == ("allow", "", "evaluated")
    v = _run(tmp_path, _pre("mcp__devbench-vr__console", {"action": "exec", "command": "player.additem f 1"}))
    assert (v.decision, v.context) == ("allow", "")


def test_only_devbench_servers_are_judged_on_codex(tmp_path):
    v = _run(tmp_path, _pre("mcp__probe__console", {"action": "exec", "command": "smp reset"}))
    assert (v.decision, v.context) == ("allow", "")
    assert "mcp__probe__console" in _unhooked_log(tmp_path)


def _run_with_saves(tmp_path, raw):
    cfg = tmp_path / "cfg"
    (cfg / "Saves").mkdir(parents=True)
    (cfg / "Saves" / "Existing.ess").write_bytes(b"x")
    req = codex.to_request(raw, "win32")
    audit = Audit(str(tmp_path))
    v = engine.pre_tool(req, str(tmp_path), lambda: paths.roots(str(tmp_path), "", {"SKYRIM_CONFIG_DIR": str(cfg)}), audit)
    return v, audit


def test_a_devbench_save_over_an_existing_save_is_backed_up_then_allowed_on_codex(tmp_path):
    """The save policy (user ruling 2026-10-13): Codex cannot ask, so the old ask was a refusal there. Now the save is
    backed up and verified by the pre-tool hook (saves.enforce), and only then is the call allowed, on Codex too."""
    v, audit = _run_with_saves(tmp_path, _pre("mcp__devbench-vr__game", {"action": "save", "name": "Existing"}))
    assert v.decision == "allow" and v.code == "shell.S-SAVE-BACKUP" and "Existing" in v.context
    assert ("S-SAVE-BACKUP", "advise") in audit.fired
    from skyguard import save_store
    store = save_store.store(tmp_path)
    manifests = list((store.root / "transactions").glob("*.json"))
    assert len(manifests) == 1
    members = [member for group in store.load(manifests[0].stem)["groups"] for member in group["members"]]
    assert [Path(member["path"]).name for member in members if not member["missing"]] == ["Existing.ess"]
    assert [Path(member["path"]).name for member in members if member["missing"]] == ["Existing.skse"]
    assert "permissionDecision" not in json.loads(codex.render(v) or "{}").get("hookSpecificOutput", {})


def test_a_devbench_save_to_a_new_name_is_silent_on_codex(tmp_path):
    v, _ = _run_with_saves(tmp_path, _pre("mcp__devbench-vr__game", {"action": "save", "name": "Brand New"}))
    assert (v.decision, v.context) == ("allow", "")


def test_cli_a_devbench_reset_is_denied_through_the_real_entry(tmp_path):
    r = _cli(tmp_path, "codex", "pre-tool", _pre("mcp__devbench-vr__console", {"action": "exec", "command": "smp reset"}))
    head, body = r.stdout.decode().split("\n", 1)
    assert r.returncode == 0 and head == "SKYGUARD-OK"
    assert json.loads(body)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unreadable_devbench_arguments_are_refused_as_inert_on_codex(tmp_path):
    """Merge of rt-fix-b: arguments the engine cannot read (the adapter always sends JSON, so only a broken Request gets
    here) are an INERT refusal -- the guard checked nothing -- not an allow (Task 17's first cut allowed them)."""
    req = codex.to_request(_pre("mcp__devbench-vr__console", {"command": "smp reset"}), "win32")
    req = req._replace(mcp_args="{not json")
    v = engine.pre_tool(req, str(tmp_path), lambda: paths.roots(str(tmp_path), "", {}), Audit(str(tmp_path)))
    assert (v.decision, v.status, v.code) == ("deny", "failed", "codex.TRANSLATE")
    assert "unreadable MCP arguments" in v.reason
