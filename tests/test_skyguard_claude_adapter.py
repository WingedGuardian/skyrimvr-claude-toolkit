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


@pytest.mark.parametrize("text", ["not json", "[1,2]", '{"tool_input": "x"}', "", "   "])
def test_malformed_gives_empty_fields(text):
    p = claude.extract(text)
    assert p["command"] == "" and p["file_path"] == "" and p["source"] == ""
    assert p["tool_name"] == "unknown"
    assert p["empty"] == (text == "")


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
    assert r.returncode == 0 and json.loads(r.stdout) is not None
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


def test_internal_error_exits_1_with_empty_stdout(tmp_path):
    code = ("import sys\n"
            "sys.path.insert(0, %r)\n"
            "from skyguard import run, shell_rules\n"
            "def boom(*a, **k):\n    raise RuntimeError('x')\n"
            "shell_rules.decide = boom\n"
            "try:\n    sys.exit(run.main(['claude','protect-bash','--root','C:/x','--project',%r]))\n"
            "except SystemExit:\n    raise\n"
            "except BaseException:\n    sys.exit(1)\n") % (str(REPO / "tools"), str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code],
                       input=json.dumps({"tool_input": {"command": "ls"}}).encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 1 and r.stdout == b""


def test_internal_error_via_script_entry(tmp_path):
    # a project path that cannot be a directory -> failure must still leave stdout empty
    r = subprocess.run([sys.executable, RUN, "claude", "protect-bash", "--root", "C:/x", "--project", str(tmp_path)],
                       input=b"\xff\xfe not json", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode in (0, 1)
    if r.returncode == 1:
        assert r.stdout == b""
