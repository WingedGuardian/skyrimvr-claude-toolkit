"""Session charter and ledger at SessionStart (2026-10-27): the shared core's charter (the session's origin) and its
open ledger are appended after the knowledgebase guard and preferences at every source, within the hosts' budgets."""
from __future__ import annotations

import json
import sys
from unittest import mock

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import session  # noqa: E402
from modding_harness import charter, ledger  # noqa: E402

SID = "0b7c6733-5bc4-4b6f-af5e-8c64ed2ce4fe"


def _seed(project):
    charter.capture_origin(json.dumps({"session_id": SID, "prompt": "Port the mod to VR"}).encode(), str(project))
    ledger.main(["--workspace", str(project), "--session", SID, "add", "--", "Rebuild the ESP"])


def test_the_charter_and_open_ledger_follow_the_guard_text(tmp_path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    out = session.with_charter("KB GUARD: ok\nUSER PREFERENCES: x", str(tmp_path), SID)
    assert out.startswith("KB GUARD: ok\nUSER PREFERENCES: x\nSESSION CHARTER")
    assert "> Port the mod to VR" in out
    assert "[Ledger open: 1" in out and "Rebuild the ESP" in out


def test_no_session_id_leaves_the_text_alone(tmp_path):
    assert session.with_charter("KB GUARD: ok", str(tmp_path), "") == "KB GUARD: ok"


def test_a_charter_that_does_not_fit_becomes_a_pointer(tmp_path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    out = session.with_charter("P" * 7000, str(tmp_path), SID)
    assert "list the open ledger yourself. Ledger commands, run as" in out
    assert "--session={}".format(SID) in out
    assert ".agent-sessions/{}.md".format(SID) in out
    assert "Port the mod to VR" not in out
    assert len(out.encode("utf-8")) < 8000


def test_a_broken_core_is_reported_in_one_line(tmp_path):
    with mock.patch.object(charter, "render", side_effect=RuntimeError("boom")):
        out = session.with_charter("KB GUARD: ok", str(tmp_path), SID)
    assert out.splitlines()[-1].startswith("SESSION CHARTER: NOT INJECTED")


# ---- tools/session-charter.sh: the per-prompt hook (UserPromptSubmit), outside skyguard ------------------------------
import os  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash")


def _install(root, python):
    """A minimal install: the hook, the shared core, and skyrim-paths.env naming `python` the way setup.sh's sq() does."""
    (root / "tools").mkdir()
    shutil.copy2(REPO / "tools" / "session-charter.sh", root / "tools" / "session-charter.sh")
    shutil.copytree(REPO / "tools" / "modding_harness", root / "tools" / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    quoted = "'" + str(python).replace("'", "'\\''") + "'"
    (root / "skyrim-paths.env").write_text("SKYRIM_GAME_ROOT='x'\nSKYGUARD_PYTHON=" + quoted + "\n", encoding="utf-8")


def _counting_python(tmp_path):
    """An interpreter shim whose name holds a quote (the unquoting is exercised) and that counts its starts."""
    d = tmp_path / "bin"
    d.mkdir()
    shim = d / "py'thon"
    shim.write_text('#!/bin/sh\necho x >> "{}"\nexec "{}" "$@"\n'.format(d / "starts", sys.executable), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return shim, d / "starts"


def _prompt(root, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([BASH, str(root / "tools" / "session-charter.sh")], input=raw, capture_output=True,
                          cwd=str(root), env=env, timeout=120)


def _starts(counter):
    return len(counter.read_text().splitlines()) if counter.exists() else 0


@needs_bash
def test_first_prompt_records_the_origin_and_later_prompts_skip_python(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    shim, counter = _counting_python(tmp_path)
    _install(root, shim)
    r = _prompt(root, {"session_id": SID, "prompt": "Port the mod to VR"})
    assert r.returncode == 0 and r.stdout == b"", r
    assert "> Port the mod to VR" in (root / ".agent-sessions" / (SID + ".md")).read_text(encoding="utf-8")
    assert _starts(counter) == 1
    r = _prompt(root, {"session_id": SID, "prompt": "and the textures"})
    assert r.returncode == 0 and r.stdout == b""
    assert _starts(counter) == 1, "a session with a charter and no ledger started Python"


@needs_bash
def test_an_open_ledger_row_is_listed_on_every_prompt(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, sys.executable)
    _prompt(root, {"session_id": SID, "prompt": "Port the mod to VR"})
    ledger.main(["--workspace", str(root), "--session", SID, "add", "--", "Rebuild the ESP"])
    r = _prompt(root, {"session_id": SID, "prompt": "next"})
    assert r.returncode == 0
    out = r.stdout.decode()
    assert out.startswith("[Ledger open: 1") and "Rebuild the ESP" in out


@needs_bash
@pytest.mark.parametrize("payload", [b"", b"not json", b'{"prompt": "no id"}',
                                     json.dumps({"session_id": "../escape", "prompt": "x"}).encode()])
def test_a_bad_payload_prints_nothing_and_writes_nothing(tmp_path, payload):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, sys.executable)
    r = _prompt(root, payload)
    assert (r.returncode, r.stdout) == (0, b"")
    assert not (root / ".agent-sessions").exists()


@needs_bash
@pytest.mark.parametrize("env_text", [None, "SKYGUARD_PYTHON=''\n", "SKYGUARD_PYTHON='/no/such/python'\n"])
def test_no_usable_interpreter_is_silent(tmp_path, env_text):
    root = tmp_path / "game"
    root.mkdir()
    _install(root, sys.executable)
    if env_text is None:
        (root / "skyrim-paths.env").unlink()
    else:
        (root / "skyrim-paths.env").write_text(env_text, encoding="utf-8")
    r = _prompt(root, {"session_id": SID, "prompt": "x"})
    assert (r.returncode, r.stdout) == (0, b"")


def test_the_hook_is_registered_for_every_prompt_and_not_as_a_guard():
    settings = json.loads((REPO / "agent" / "targets" / "claude" / "settings.json").read_text(encoding="utf-8"))
    (group,) = settings["hooks"]["UserPromptSubmit"]
    (entry,) = group["hooks"]
    assert entry["command"] == 'bash "$CLAUDE_PROJECT_DIR/tools/session-charter.sh"'
    assert entry["timeout"] >= 30
    assert not (REPO / ".claude" / "hooks" / "session-charter.sh").exists()


def _protect_bash(root, command):
    payload = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}, "cwd": str(root)}
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    r = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "skyguard" / "run.py"), "claude", "protect-bash",
                        "--project", str(root), "--root", str(root), "--root-win", str(root).replace("\\", "/")],
                       input=json.dumps(payload).encode(), capture_output=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.decode() or "{}")


@pytest.mark.parametrize("verb", ["add -- 'Rebuild the ESP'", "list", "mission -- 'Port the mod to VR'",
                                  "update L123456 --status done --evidence 'built and loaded in game'"])
def test_the_guard_lets_the_session_run_the_printed_ledger_commands(tmp_path, verb):
    """The session changes its ledger only through the command ledger.commands() prints. If protect-bash refused it,
    the ledger would stay empty while every prompt said it was there."""
    root = tmp_path / "game"
    root.mkdir()
    _install(root, sys.executable)
    (root / "skyrim-paths.env").unlink()    # the fixture's dummy game root would make the guard fail closed (inert)
    printed = ledger.commands(str(root), SID)
    command = printed.split("`")[1] + " " + verb
    assert _protect_bash(root, command) == {}, command
    # control: the same engine on the same root does refuse a destructive command
    assert _protect_bash(root, "rm -rf Data")["hookSpecificOutput"]["permissionDecision"] == "deny"
