"""Captured hook payloads become test fixtures; nothing identifying may survive the scrub."""
import importlib.util
import json
import os
import subprocess

from conftest import BASH, REPO

BS = chr(92)  # backslash, spelled this way to keep escapes out of this file (as conftest does)
HOME = "C:\\Users\\Moona"


def _scrub():
    spec = importlib.util.spec_from_file_location("scrub", REPO / "tools" / "payload-capture" / "scrub.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.scrub


RAW = {
    "session_id": "01a0f357-e906", "turn_id": "01a0f357-e95d", "tool_use_id": "exec-eb6",
    "transcript_path": "C:\\Users\\Moona\\.codex\\sessions\\rollout.jsonl",
    "cwd": "C:\\Users\\Moona\\AppData\\Local\\Temp\\spike", "hook_event_name": "PreToolUse",
    "model": "gpt-5.6-sol", "permission_mode": "bypassPermissions", "tool_name": "Bash",
    "tool_input": {"command": "New-Item -ItemType File C:/Users/Moona/x.flag; ls /c/Users/Moona"},
}


def test_ids_and_paths_are_replaced_and_shape_kept():
    s = _scrub()(RAW, HOME)
    assert set(s) == set(RAW)
    assert s["session_id"] == s["turn_id"] == s["tool_use_id"] == "<id>"
    assert s["transcript_path"] == "<path>" and s["cwd"] == "<path>"
    assert s["tool_name"] == "Bash" and s["hook_event_name"] == "PreToolUse"


def test_scrub_removes_home_in_every_spelling():
    s = _scrub()(RAW, HOME)
    dumped = json.dumps(s)
    for spelling in ("Moona", "Users\\\\Moona", "Users/Moona", "/c/Users/Moona"):
        assert spelling not in dumped, spelling
    assert "<HOME>/x.flag" in s["tool_input"]["command"]


def test_scrub_is_idempotent():
    once = _scrub()(RAW, HOME)
    assert _scrub()(once, HOME) == once


def _capture(stdin, env_dir):
    env = dict(os.environ)
    env.pop("PAYLOAD_CAPTURE_DIR", None)
    if env_dir is not None:
        env["PAYLOAD_CAPTURE_DIR"] = str(env_dir)
    return subprocess.run([BASH, str(REPO / "tools" / "payload-capture" / "capture-hook.sh")],
                          input=stdin, capture_output=True, text=True, env=env, timeout=60)


def test_capture_hook_saves_the_payload_verbatim_and_never_speaks(tmp_path):
    payload = '{"tool_name": "Bash", "tool_input": {"command": "echo hi"}}'
    r = _capture(payload, tmp_path / "raw")
    assert r.returncode == 0 and r.stdout == ""      # a capture hook must never influence a decision
    files = list((tmp_path / "raw").glob("*.json"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8") == payload


def test_capture_hook_is_inert_without_a_capture_dir(tmp_path):
    r = _capture('{"a": 1}', None)
    assert r.returncode == 0 and r.stdout == ""


def test_every_id_field_is_scrubbed_including_new_ones():
    """Agents add id fields over time (Claude 2.1.287 added prompt_id); any *_id key is an id."""
    s = _scrub()({**RAW, "prompt_id": "1742d334-dc53", "future_thing_id": "x"}, HOME)
    assert s["prompt_id"] == "<id>" and s["future_thing_id"] == "<id>"


def test_paths_under_the_session_cwd_lose_the_cwd():
    """The capture project's own path (with the session id in it) must not survive inside file paths."""
    cwd = "C:/Users/Moona/AppData/Local/Temp/claude/sess-123/capclaude".replace("/", BS)
    raw = {**RAW, "cwd": cwd, "tool_name": "Write",
           "tool_input": {"file_path": cwd + BS + "notes.txt", "content": "a"}}
    s = _scrub()(raw, HOME)
    assert s["tool_input"]["file_path"] == "<cwd>" + BS + "notes.txt"
    assert "sess-123" not in json.dumps(s)


def test_posix_homes_are_scrubbed():
    """Linux and macOS contributors capture too; their home must not survive either."""
    linux = _scrub()({"tool_input": {"command": "cat /home/moona/.bashrc"}}, "/home/moona")
    mac = _scrub()({"tool_input": {"command": "ls /Users/moona/x"}}, "/Users/moona")
    assert linux["tool_input"]["command"] == "cat <HOME>/.bashrc"
    assert mac["tool_input"]["command"] == "ls <HOME>/x"


def test_posix_cwd_is_scrubbed_from_paths():
    raw = {"cwd": "/home/moona/scratch/cap", "tool_input": {"file_path": "/home/moona/scratch/cap/notes.txt"}}
    s = _scrub()(raw, "/home/moona")
    assert s["tool_input"]["file_path"] == "<cwd>/notes.txt"
