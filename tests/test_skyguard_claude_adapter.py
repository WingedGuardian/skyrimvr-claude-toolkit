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
    assert p["file_path"] == str(ti.get("file_path", "")).rstrip("\n")
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
    payload = b'{"tool_input":{"file_path":"C:/x/a\xff.psc"}}'
    r = subprocess.run([sys.executable, RUN, "claude", "protect-files", "--project", str(tmp_path)],
                       input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.decode("utf-8"))
    assert "PAPYRUS SCRIPT" in out["hookSpecificOutput"]["additionalContext"]


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
