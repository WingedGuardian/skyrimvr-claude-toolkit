"""skyguard Claude adapter and run.py entry point."""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard.adapters import claude  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

RUN = str(REPO / "tools" / "skyguard" / "run.py")
FIX = REPO / "tests" / "fixtures" / "payloads" / "claude"


@pytest.mark.parametrize("f", sorted(FIX.glob("*.json")), ids=lambda p: p.name)
def test_plan1_fixtures_extract(f):
    text = f.read_text(encoding="utf-8")
    d = json.loads(text)
    p = claude.extract(text)
    ti = d.get("tool_input") or {}
    assert not p["empty"]
    assert p["command"] == str(ti.get("command", "")).rstrip("\n")
    path_key = "notebook_path" if d.get("tool_name") == "NotebookEdit" else "file_path"     # Task 13b
    assert p["file_path"] == str(ti.get(path_key, "")).rstrip("\n")
    assert p["tool_name"] == (d.get("tool_name") or "unknown")


def test_command_trailing_newlines_are_stripped():
    p = claude.extract(json.dumps({"tool_input": {"command": "ls\n\n\n"}}))
    assert p["command"] == "ls"


@pytest.mark.parametrize("text", ["not json", "[1,2]", '{"tool_input": "x"}', "", "   ", "\n", "\n\n", "\r\n",
                                  "\x00"])
def test_malformed_gives_empty_fields(text):
    p = claude.extract(text)
    assert p["command"] == "" and p["file_path"] == "" and p["source"] == ""
    assert p["tool_name"] == "unknown"
    assert p["empty"] == (text.replace("\x00", "").rstrip("\r\n") == "")


@pytest.mark.parametrize("text", ["", "\n", "\n\n", "\r\n", "\x00"])
def test_blank_payloads_are_empty(text):
    assert claude.extract(text)["empty"] is True


def test_whitespace_only_is_not_empty():
    assert claude.extract("   ")["empty"] is False


def test_nul_inside_json_string_is_joined():
    p = claude.extract('{"tool_input": {"file_path": "a\x00b"}}')
    assert p["file_path"] == "ab"


def test_tool_name_empty_string_stays_empty():
    assert claude.extract('{"tool_name": ""}')["tool_name"] == ""
    assert claude.extract('{"tool_name": null}')["tool_name"] == "unknown"
    assert claude.extract("{}")["tool_name"] == "unknown"


@pytest.mark.parametrize("raw,want", [("0", "0"), ("0.0", "0.0"), ("false", "unknown"), ("null", "unknown")])
def test_tool_name_falls_back_only_where_jq_does(raw, want):
    """`jq -r '.tool_name // "unknown"'` falls back on null and false ONLY. Python's `0 in (None, False)` is True
    (0 == False), so a numeric zero used to become "unknown" where the bash hook printed "0"."""
    assert claude.extract('{"tool_name": %s}' % raw)["tool_name"] == want


def test_numeric_command_is_its_json_text():
    assert claude.extract('{"tool_input": {"command": 12}}')["command"] == "12"
    assert claude.extract('{"tool_input": {"command": 1.5}}')["command"] == "1.5"


def test_nul_bytes_are_dropped():
    p = claude.extract(json.dumps({"tool_name": "Ed\u0000it", "tool_input": {"file_path": "C:/x/y.psc\u0000",
                                                                         "command": "a\u0000b"},
                                   "source": "s\u0000"}))
    assert p["file_path"] == "C:/x/y.psc" and p["command"] == "ab"
    assert p["tool_name"] == "Edit" and p["source"] == "s"


def test_render_shapes():
    assert claude.render(Verdict()) == "{}"
    assert json.loads(claude.render(Verdict("deny", "no"))) == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "no"}}
    assert json.loads(claude.render(Verdict("ask", "hm")))["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert json.loads(claude.render(Verdict("allow", "", "ctx"))) == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "additionalContext": "ctx"}}


def _run(role, payload, tmp_path, env_extra=None, args=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, RUN, "claude", role, "--root", "C:/GOG Games/Fake", "--root-win", "C:" + chr(92) + "GOG Games" + chr(92) + "Fake",
         "--project", str(tmp_path)] + (args or []),
        input=payload.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def test_run_protect_bash(tmp_path):
    r = _run("protect-bash", json.dumps({"tool_input": {"command": "echo hi"}}), tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout) == {}
    r = _run("protect-bash", "", tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_protect_files(tmp_path):
    r = _run("protect-files", json.dumps({"tool_input": {"file_path": "C:/GOG Games/Fake/Data/a.esp"}}), tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout) is not None


def test_run_backup_and_snapshot_silent_allow(tmp_path):
    for role in ("backup-before-edit", "snapshot-before-tool"):
        r = _run(role, json.dumps({"tool_name": "Edit", "tool_input": {"command": "ls"}}), tmp_path)
        assert r.returncode == 0 and r.stdout == b"{}"


def test_run_session_guard_compact_and_missing(tmp_path):
    r = _run("session-kb-guard", json.dumps({"source": "compact"}), tmp_path)
    assert r.returncode == 0 and r.stdout == b""
    r = _run("session-kb-guard", json.dumps({"source": "startup"}), tmp_path)
    assert r.returncode == 0 and b"KB GUARD: NOT RUN" in r.stdout


def test_run_nonascii_output_on_cp1252_console(tmp_path):
    env = {"PYTHONIOENCODING": "cp1252"}
    env_clean = dict(os.environ)
    env_clean.pop("PYTHONIOENCODING", None)
    payload = json.dumps({"tool_input": {"command": "rm C:/GOG Games/Fake/caf\u00e9\ufffd\u2603.esp"}})
    r = subprocess.run(
        [sys.executable, "-X", "utf8=0", RUN, "claude", "protect-bash", "--root", "C:/GOG Games/Fake",
         "--project", str(tmp_path)],
        input=payload.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(env_clean, **env))
    assert r.returncode == 0, r.stderr
    json.loads(r.stdout.decode("utf-8"))


def test_run_nonascii_reason_roundtrips_as_utf8(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    payload = json.dumps({"tool_input": {"file_path": "C:/GOG Games/Fake/Data/caf\u00e9\ufffd.esp"}})
    r = subprocess.run([sys.executable, "-X", "utf8=0", RUN, "claude", "protect-files", "--project", str(tmp_path)],
                       input=payload.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    assert r.returncode == 0, r.stderr
    r.stdout.decode("utf-8")


def test_raw_invalid_utf8_byte_is_replaced_not_fatal(tmp_path):
    # Task 9: W-PAPYRUS fires on a script under the install's Data/Scripts, so the hook is given an install root.
    payload = b'{"tool_input":{"file_path":"C:/x/Data/Scripts/a\xff.psc"}}'
    r = subprocess.run([sys.executable, RUN, "claude", "protect-files", "--root", "C:/x", "--project", str(tmp_path)],
                       input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.decode("utf-8"))
    assert "a script change in a\ufffd.psc" in out["hookSpecificOutput"]["additionalContext"]


def test_internal_error_exits_1_with_empty_stdout(tmp_path):
    import shutil
    pkg = tmp_path / "skyguard"
    shutil.copytree(REPO / "tools" / "skyguard", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    proj = tmp_path / "proj"
    proj.mkdir()
    cmd = [sys.executable, str(pkg / "run.py"), "claude", "protect-bash", "--root", "C:/x", "--project", str(proj)]
    payload = json.dumps({"tool_input": {"command": "ls"}}).encode()
    control = subprocess.run(cmd, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert control.returncode == 0 and control.stdout        # the copy works before it is broken
    with open(pkg / "shell_rules.py", "a", encoding="utf-8", newline="\n") as f:
        f.write("\n\ndef decide(*a, **k):\n    raise RuntimeError('forced')\n")
    r = subprocess.run(cmd, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 1 and r.stdout == b""


# ---------------------------------------------------------------- Plan 5 Task 6: cwd, shell, mcp_input, command_type

@pytest.mark.parametrize("f", sorted(FIX.glob("*.json")), ids=lambda p: p.name)
def test_fixtures_carry_cwd_shell_and_mcp_input(f):
    d = json.loads(f.read_text(encoding="utf-8"))
    p = claude.extract(f.read_text(encoding="utf-8"))
    assert p["cwd"] == (d["cwd"] if isinstance(d.get("cwd"), str) else "")
    assert p["shell"] == {"Bash": "bash", "PowerShell": "powershell"}.get(d.get("tool_name"), "")
    if str(d.get("tool_name", "")).startswith("mcp__"):
        assert p["mcp_input"] == d["tool_input"] and p["mcp_input"]
    else:
        assert p["mcp_input"] == {}


@pytest.mark.parametrize("cwd", ["/c/Users/x", "C:" + chr(92) + "x", "C:/x"])
def test_cwd_is_passed_raw(cwd):
    assert claude.extract(json.dumps({"tool_name": "Bash", "cwd": cwd}))["cwd"] == cwd


@pytest.mark.parametrize("cwd", [None, 12, ["C:/x"], {"a": 1}])
def test_a_non_string_cwd_is_empty(cwd):
    assert claude.extract(json.dumps({"tool_name": "Bash", "cwd": cwd}))["cwd"] == ""


@pytest.mark.parametrize("ti,want", [
    ({"command": "ls"}, "string"), ({"command": ""}, "string"), ({"command": 12}, "number"),
    ({"command": True}, "boolean"), ({"command": None}, "null"), ({"command": ["ls"]}, "array"),
    ({"command": {"a": 1}}, "object"), ({}, "missing"),
])
def test_command_type(ti, want):
    assert claude.extract(json.dumps({"tool_name": "Bash", "tool_input": ti}))["command_type"] == want


def test_run_passes_the_shell_dialect(tmp_path):
    """Wiring proof: a python heredoc that only MENTIONS a ReSaver write is allowed when the engine is told the
    dialect is bash, but asks under the unknown dialect (bash AND PowerShell, where the PowerShell reading cannot
    parse it and the R4 text fallback applies). run.py must therefore be passing shell."""
    cmd = "python - <<'EOF'\nprint('resaver-cli.sh reset-havok x.ess --apply')\nEOF"
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import shell_rules
    assert shell_rules.resaver_apply(cmd, "") and not shell_rules.resaver_apply(cmd, "bash")
    r = _run("protect-bash", json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}), tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout) == {}


def test_run_reports_a_malformed_paths_file_by_key(tmp_path):
    root = tmp_path / "Skyrim"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "skyrim-paths.env").write_text('SKYRIM_CONFIG_DIR="C:/Users/$X/My Games"\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SKYRIM_")}
    r = subprocess.run(
        [sys.executable, RUN, "claude", "protect-bash", "--root", root.as_posix(), "--root-win", str(root),
         "--project", str(tmp_path)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "SKYRIM_CONFIG_DIR" in out["permissionDecisionReason"]


# ---------------------------------------------------------------- Plan 5 Task 13b: Monitor and NotebookEdit

def _verdict(role, payload, tmp_path):
    r = _run(role, json.dumps(payload), tmp_path)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


FAKE_ROOT = "C:/GOG Games/Fake"


def test_monitor_fixture_command_is_extracted_with_the_unknown_dialect():
    """MEASURED (Claude Code 2.1.289): Monitor carries its command in tool_input.command, like Bash. The payload does not
    name a dialect, so the engine is given "" (unknown = judge as bash AND PowerShell), the conservative reading."""
    text = (FIX / "claude-PreToolUse-Monitor-1.json").read_text(encoding="utf-8")
    p = claude.extract(text)
    assert p["tool_name"] == "Monitor" and p["command"] == "echo hi" and p["command_type"] == "string"
    assert p["shell"] == ""


def test_monitor_that_deletes_the_game_root_is_denied_like_bash(tmp_path):
    cmd = 'rm -rf "%s"' % FAKE_ROOT
    outs = {tool: _verdict("protect-bash", {"tool_name": tool, "tool_input": {"command": cmd}}, tmp_path)
            for tool in ("Bash", "Monitor")}
    assert _decision(outs["Bash"]) == "deny" and _decision(outs["Monitor"]) == "deny"
    assert outs["Monitor"] == outs["Bash"]


def test_monitor_benign_command_is_allowed(tmp_path):
    assert _verdict("protect-bash", {"tool_name": "Monitor", "tool_input": {"command": "echo hi"}}, tmp_path) == {}


@pytest.mark.parametrize("ti", [{}, {"command": 12}, {"command": ["rm", "x"]}, {"command": ""}, {"command": "   "}])
def test_monitor_without_a_real_command_fails_closed(ti, tmp_path):
    out = _verdict("protect-bash", {"tool_name": "Monitor", "tool_input": ti}, tmp_path)
    assert _decision(out) == "deny"
    assert len(out["hookSpecificOutput"]["permissionDecisionReason"].splitlines()) <= 2


def test_notebook_fixture_path_comes_from_notebook_path():
    """MEASURED (Claude Code 2.1.289): NotebookEdit carries `notebook_path`, with no `file_path` at all."""
    text = (FIX / "claude-PreToolUse-NotebookEdit-1.json").read_text(encoding="utf-8")
    d = json.loads(text)
    assert "file_path" not in d["tool_input"]
    assert claude.extract(text)["file_path"] == d["tool_input"]["notebook_path"]


@pytest.mark.parametrize("path", [FAKE_ROOT + "/Data/a.esp", FAKE_ROOT + "/Data/SKSE/Plugins/x.ini",
                                  FAKE_ROOT + "/Data/Scripts/Source/a.psc", "C:/scratch/nb.ipynb"])
def test_notebook_edit_gets_the_same_verdict_as_write_on_that_path(path, tmp_path):
    w = _verdict("protect-files", {"tool_name": "Write", "tool_input": {"file_path": path}}, tmp_path)
    n = _verdict("protect-files", {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": path}}, tmp_path)
    assert n == w


def test_notebook_edit_on_a_protected_path_is_denied_and_in_scratch_allowed(tmp_path):
    bad = _verdict("protect-files", {"tool_name": "NotebookEdit",
                                     "tool_input": {"notebook_path": FAKE_ROOT + "/Data/a.esp"}}, tmp_path)
    assert _decision(bad) == "deny"
    ok = _verdict("protect-files", {"tool_name": "NotebookEdit",
                                    "tool_input": {"notebook_path": "C:/scratch/nb.ipynb"}}, tmp_path)
    assert ok == {}


@pytest.mark.parametrize("ti", [{}, {"notebook_path": 12}, {"notebook_path": ["a"]}, {"notebook_path": ""},
                                {"notebook_path": None, "file_path": 12},
                                {"notebook_path": "", "file_path": "C:/scratch/nb.ipynb"},      # present-but-bad: no fallback
                                {"notebook_path": 12, "file_path": "C:/scratch/nb.ipynb"},
                                {"notebook_path": None, "file_path": "C:/scratch/nb.ipynb"}])
def test_notebook_edit_without_a_string_path_fails_closed(ti, tmp_path):
    out = _verdict("protect-files", {"tool_name": "NotebookEdit", "tool_input": ti}, tmp_path)
    assert _decision(out) == "deny"
    assert len(out["hookSpecificOutput"]["permissionDecisionReason"].splitlines()) <= 2


def test_notebook_edit_is_backed_up_before_the_edit(tmp_path):
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}", encoding="utf-8")
    r = _run("backup-before-edit", json.dumps({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(nb)}}), tmp_path)
    assert r.returncode == 0
    assert len(list((tmp_path / ".claude" / "backups").glob("*nb.ipynb"))) == 1


def test_notebook_edit_reads_notebook_path_first_then_file_path():
    """notebook_path is what the tool writes, so it wins over a stray file_path; file_path is only the fallback."""
    both = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "C:/a/nb.ipynb", "file_path": "C:/b/x.ipynb"}}
    assert claude.extract(json.dumps(both))["file_path"] == "C:/a/nb.ipynb"
    absent = {"tool_name": "NotebookEdit", "tool_input": {"file_path": "C:/b/x.ipynb"}}
    assert claude.extract(json.dumps(absent))["file_path"] == "C:/b/x.ipynb"
    only_fp = {"tool_name": "NotebookEdit", "tool_input": {"file_path": "C:/b/x.ipynb"}}
    assert claude.extract(json.dumps(only_fp))["file_path"] == "C:/b/x.ipynb"


# ---- Monitor's WebSocket form: {ws: {url}, description, timeout_ms}, no `command` (coordinator report; UNMEASURED here)

WS = {"url": "ws://127.0.0.1:8921/events"}


def test_websocket_monitor_is_allowed(tmp_path):
    ti = {"ws": WS, "description": "watch", "timeout_ms": 1000}
    assert _verdict("protect-bash", {"tool_name": "Monitor", "tool_input": ti}, tmp_path) == {}


@pytest.mark.parametrize("ti", [
    {"description": "d"},                              # neither command nor ws
    {"ws": "ws://x/y"},                                # ws as a string
    {"ws": ["ws://x/y"]}, {"ws": None}, {"ws": 5},     # ws of any other type
    {"ws": WS, "command": ""},                         # ws beside an empty command
    {"ws": WS, "command": None},                       # ws beside a null command
    {"ws": WS, "command": 12},                         # ws beside a non-string command
    {"ws": {}}, {"ws": {"url": ""}}, {"ws": {"url": 5}}, {"ws": {"protocols": ["x"]}},   # ws object without a real url
])
def test_monitor_ws_twins_still_deny(ti, tmp_path):
    out = _verdict("protect-bash", {"tool_name": "Monitor", "tool_input": ti}, tmp_path)
    assert _decision(out) == "deny"


def test_monitor_ws_beside_a_real_command_judges_the_command(tmp_path):
    ti = {"ws": WS, "command": 'rm -rf "%s"' % FAKE_ROOT}
    assert _decision(_verdict("protect-bash", {"tool_name": "Monitor", "tool_input": ti}, tmp_path)) == "deny"


def test_ws_form_only_exempts_monitor(tmp_path):
    for tool in ("Bash", "PowerShell"):
        out = _verdict("protect-bash", {"tool_name": tool, "tool_input": {"ws": WS}}, tmp_path)
        assert _decision(out) == "deny", tool


@pytest.mark.parametrize("cwd", ["/c/Users/x", "C:\\x"])
def test_to_request_carries_cwd_raw(cwd):
    """Request.cwd is the agent's cwd string, unnormalized: MSYS and backslash forms pass through."""
    p = claude.extract(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": cwd}))
    assert claude.to_request(p, "shell").cwd == cwd


def test_run_judges_monitor_in_the_unknown_dialect(tmp_path):
    """Merge of Plans 4+5: Plan 4's to_request hard-coded shell="bash". Monitor names no dialect, so the engine must
    get "" (bash AND PowerShell, R5): the same python heredoc that is allowed as Bash asks as Monitor."""
    cmd = "python - <<'EOF'\nprint('resaver-cli.sh reset-havok x.ess --apply')\nEOF"
    p = claude.extract(json.dumps({"tool_name": "Monitor", "tool_input": {"command": cmd}}))
    assert claude.to_request(p, "shell").shell is None
    bash = _run("protect-bash", json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}), tmp_path)
    mon = _run("protect-bash", json.dumps({"tool_name": "Monitor", "tool_input": {"command": cmd}}), tmp_path)
    assert json.loads(bash.stdout) == {}
    # The save policy (2026-10-13): the ReSaver --apply the PowerShell reading sees names a save it cannot place (no
    # cwd), so it cannot be backed up first -- refused (it used to ask).
    assert json.loads(mon.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
