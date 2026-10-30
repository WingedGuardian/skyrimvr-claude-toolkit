"""The PostToolUse save sentinel (tools/skyguard/save_sentinel.py over modding_harness/sentinel.py).

Fake saves only, in tmp_path; the environment's Documents points into tmp_path and the OS Known Folder is switched
off (SKYGUARD_NO_KNOWN_FOLDERS), so no real profile folder is ever listed."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from skyguard import paths, save_sentinel  # noqa: E402
from conftest import install_hooks  # noqa: E402
from test_hooks import _bash  # noqa: E402


def _pair(folder, stem, data=b"E"):
    folder.mkdir(parents=True, exist_ok=True)
    for ext in (".ess", ".skse"):
        (folder / (stem + ext)).write_bytes(data)
    return folder / (stem + ".ess")


def _bump(path, data):
    path.write_bytes(data)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


@pytest.fixture
def world(tmp_path, monkeypatch):
    project = tmp_path / "game"
    project.mkdir()
    home = tmp_path / "home"
    env = {"USERPROFILE": str(home), "HOME": str(home), "SKYGUARD_NO_KNOWN_FOLDERS": "1"}
    monkeypatch.setattr(save_sentinel, "game_running", lambda: False)
    return project, home, env


def _look(project, env, now):
    return save_sentinel.report_text(project, paths.roots(str(project), str(project), {}), env, now=now)


def test_every_existing_save_folder_is_found_including_the_ini_override(world):
    project, home, env = world
    vr = home / "Documents" / "My Games" / "Skyrim VR"
    gog = home / "Documents" / "My Games" / "Skyrim Special Edition GOG"
    (vr / "Saves").mkdir(parents=True)
    (gog / "Moved").mkdir(parents=True)
    (gog / "SkyrimCustom.ini").write_text("[General]\nsLocalSavePath=Moved\\\n", encoding="latin-1")
    found = {os.path.normcase(str(p)) for p in save_sentinel.save_folders(paths.roots("", "", {}), env)}
    assert found == {os.path.normcase(str(vr / "Saves")), os.path.normcase(str(gog / "Moved"))}


def test_an_unbacked_change_is_reported_once_and_loudly(world):
    project, home, env = world
    a = _pair(home / "Documents" / "My Games" / "Skyrim VR" / "Saves", "A")
    assert _look(project, env, 1000.0) == ("", False)              # first look: the baseline
    _bump(a, b"changed")
    text, loud = _look(project, env, 1010.0)
    assert loud and "modified: " + str(a) in text and "Recycle Bin" in text
    assert _look(project, env, 1020.0) == ("", False)


def test_while_the_game_runs_it_is_noted_not_raised(world, monkeypatch):
    project, home, env = world
    a = _pair(home / "Documents" / "My Games" / "Skyrim VR" / "Saves", "A")
    _look(project, env, 1000.0)
    _bump(a, b"changed")
    monkeypatch.setattr(save_sentinel, "game_running", lambda: True)
    text, loud = _look(project, env, 1010.0)
    assert not loud and "may be the game itself" in text


def test_a_marked_folder_under_the_project_is_watched(world):
    project, home, env = world
    kept = project / "evidence" / "baseline"
    b = _pair(kept / "deep", "B")
    (kept / save_sentinel.MARKER).write_text("kept", encoding="utf-8")
    _look(project, env, 1000.0)
    b.unlink()
    text, loud = _look(project, env, 1010.0)
    assert "removed: " + str(b) in text


def test_render_is_a_post_tool_use_answer():
    assert save_sentinel.render("", False) == ""
    quiet = json.loads(save_sentinel.render("x", False))
    assert quiet == {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "x"}}
    assert json.loads(save_sentinel.render("x", True))["systemMessage"] == "x"


def test_the_wrapper_end_to_end(tmp_path):
    """Through the shipped bash wrapper and run.py: a baseline, then the report on stdout."""
    root = install_hooks(tmp_path / "install", names={"save-sentinel.sh"})
    home = tmp_path / "home"
    a = _pair(home / "Documents" / "My Games" / "Skyrim VR" / "Saves", "A")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), USERPROFILE=str(home), HOME=str(home),
               SKYGUARD_NO_KNOWN_FOLDERS="1")
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        env.pop(k, None)
    hook = str(root / ".claude" / "hooks" / "save-sentinel.sh")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {}})
    first = subprocess.run([_bash(), hook], input=payload, capture_output=True, text=True, env=env, timeout=60)
    assert first.returncode == 0 and first.stdout.strip() == "", first.stderr
    _bump(a, b"changed")
    second = subprocess.run([_bash(), hook], input=payload, capture_output=True, text=True, env=env, timeout=60)
    assert second.returncode == 0
    answer = json.loads(second.stdout)
    assert answer["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "modified: " + str(a) in answer["hookSpecificOutput"]["additionalContext"]
