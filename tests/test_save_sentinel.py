"""The PostToolUse save sentinel (tools/skyguard/save_sentinel.py over modding_harness/sentinel.py).

Fake saves only, in tmp_path; the environment's Documents points into tmp_path and the OS Known Folder is switched
off (SKYGUARD_NO_KNOWN_FOLDERS), so no real profile folder is ever listed."""
from types import SimpleNamespace
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


class _FakeLock:
    """modding_harness.save_lock with the Windows calls replaced by recorders."""
    def __init__(self, elevated=False):
        self.locked_files, self.elevated = [], elevated

    def install(self, monkeypatch):
        sl = save_sentinel.save_lock
        monkeypatch.setattr(sl, "supported", lambda: True)
        monkeypatch.setattr(sl, "is_elevated", lambda: self.elevated)
        monkeypatch.setattr(sl, "user_sid", lambda: "S-1-5-21-1")
        monkeypatch.setattr(sl, "status_map", lambda folder, exts: {})
        monkeypatch.setattr(sl, "is_locked", lambda p: True)
        monkeypatch.setattr(sl, "lock_folder", lambda p, sid: None)
        monkeypatch.setattr(sl, "lock_file", lambda p, sid: self.locked_files.append(os.path.basename(p)))
        return self


def _switch(project, enabled):
    s = save_sentinel.lock_switch(project)
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")


def _old_pair(folder, stem):
    a = _pair(folder, stem)
    for p in (a, a.with_suffix(".skse")):
        os.utime(p, (500.0, 500.0))
    return a


def test_locking_off_locks_nothing(world, monkeypatch):
    project, home, env = world
    fake = _FakeLock().install(monkeypatch)
    _old_pair(home / "Documents" / "My Games" / "Skyrim VR" / "Saves", "A")
    _look(project, env, 1000.0)
    _look(project, env, 1010.0)
    assert fake.locked_files == []


def test_locking_on_locks_settled_saves_once_and_leaves_rotating_slots(world, monkeypatch):
    project, home, env = world
    fake = _FakeLock().install(monkeypatch)
    folder = home / "Documents" / "My Games" / "Skyrim VR" / "Saves"
    _old_pair(folder, "Save1")
    _old_pair(folder, "Autosave1")
    _switch(project, True)
    text, loud = _look(project, env, 1000.0)
    assert sorted(fake.locked_files) == ["Save1.ess", "Save1.skse"] and not loud, text
    fake.locked_files.clear()
    _look(project, env, 1010.0)
    assert fake.locked_files == []


def test_switching_locking_off_is_raised_once(world, monkeypatch):
    project, home, env = world
    _FakeLock().install(monkeypatch)
    _switch(project, True)
    _look(project, env, 1000.0)
    _switch(project, False)
    text, loud = _look(project, env, 1010.0)
    assert loud and "switched OFF" in text
    text, loud = _look(project, env, 1020.0)
    assert "switched OFF" not in text


def test_an_elevated_agent_is_warned_while_locking_is_on(world, monkeypatch):
    project, home, env = world
    _FakeLock(elevated=True).install(monkeypatch)
    _switch(project, True)
    text, loud = _look(project, env, 1000.0)
    assert loud and "runs as administrator" in text
    text, loud = _look(project, env, 1010.0)
    assert "runs as administrator" not in text


# ---- what the shared core's hardening asks of this caller
def _folders(tmp_path, monkeypatch):
    project, saves = tmp_path / "p", tmp_path / "Saves"
    project.mkdir()
    saves.mkdir()
    monkeypatch.setattr(save_sentinel, "save_folders", lambda roots, env: [str(saves)])
    monkeypatch.setattr(save_sentinel, "game_running", lambda: False)
    monkeypatch.setattr(save_sentinel, "marked_folders", lambda project: [])
    return project


def test_a_record_held_by_another_look_compares_nothing_and_keeps_the_baseline(tmp_path, monkeypatch):
    project = _folders(tmp_path, monkeypatch)
    monkeypatch.setattr(save_sentinel.sentinel, "load_state", lambda path, now=None: {"busy": True})
    text, loud = save_sentinel.report_text(str(project), None, {})
    assert "held by another process" in text and not loud
    assert not save_sentinel.state_path(str(project)).exists()


def test_a_lost_record_is_raised_to_the_user(tmp_path, monkeypatch):
    project = _folders(tmp_path, monkeypatch)
    monkeypatch.setattr(save_sentinel.sentinel, "load_state", lambda path, now=None: {"unreadable": True, "why": "it was deleted"})
    text, loud = save_sentinel.report_text(str(project), None, {})
    assert "it was deleted" in text and loud


def test_a_look_that_cannot_be_recorded_still_reports(tmp_path, monkeypatch):
    project = _folders(tmp_path, monkeypatch)

    def refuse(path, state):
        raise PermissionError("held")
    monkeypatch.setattr(save_sentinel.sentinel, "save_state", refuse)
    text, _ = save_sentinel.report_text(str(project), None, {})
    assert "could not be recorded (PermissionError)" in text


def test_a_save_that_cannot_be_locked_is_raised_once_per_failure(tmp_path, monkeypatch):
    project = tmp_path / "p"
    project.mkdir()

    def refuse(*_a, **_k):
        raise ValueError("no")
    monkeypatch.setattr(save_sentinel.save_lock, "read_config", lambda path: {"enabled": True})
    monkeypatch.setattr(save_sentinel.save_lock, "supported", lambda: True)
    monkeypatch.setattr(save_sentinel.save_lock, "is_elevated", lambda: False)
    monkeypatch.setattr(save_sentinel.save_lock, "auto_lock", refuse)
    profile = SimpleNamespace(save_roots=[], extensions=(".ess",))
    state = {}
    notes, loud = save_sentinel.lock_notes(str(project), state, {}, profile, 1000.0)
    assert "NOT protected" in notes[0] and "ValueError" in notes[0] and loud
    assert save_sentinel.lock_notes(str(project), state, {}, profile, 1001.0) == ([], False)
