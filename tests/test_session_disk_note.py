"""Session start shows the last disk-housekeeping note and launches the next sweep detached (tools/skyguard/run.py).

Both agents' session-start roles go through run.py, so one choke point serves Claude Code and Codex.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

RUN = REPO / "tools" / "skyguard" / "run.py"
NOTE = "DISK: removed 3 leftover temp dir(s), free space +2.0 GiB -- python tools/housekeep.py report"

sys.path.insert(0, str(REPO / "tools"))
from skyguard import run  # noqa: E402


def _start(tmp_path, agent, role, payload):
    return subprocess.run([sys.executable, str(RUN), agent, role, "--project", str(tmp_path), "--root",
                           str(tmp_path), "--bash", "bash", "--telemetry", str(tmp_path / "hb")],
                          input=payload.encode("utf-8"), capture_output=True, timeout=60)


def _note(proj, text=NOTE):
    p = proj / run.DISK_NOTE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n", encoding="utf-8")


def test_the_claude_session_start_shows_the_disk_note(tmp_path):
    _note(tmp_path)
    r = _start(tmp_path, "claude", "session-kb-guard", '{"session_id": "s1", "source": "startup"}')
    assert r.returncode == 0, r.stderr
    assert NOTE in r.stdout.decode("utf-8")


def test_the_codex_session_start_shows_the_disk_note(tmp_path):
    _note(tmp_path)
    r = _start(tmp_path, "codex", "session-start", json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    assert r.returncode == 0, r.stderr
    assert NOTE in r.stdout.decode("utf-8")


def test_no_note_adds_nothing_to_the_banner(tmp_path):
    with_note = tmp_path / "a"
    without = tmp_path / "b"
    with_note.mkdir()
    without.mkdir()
    _note(with_note)                                                # twin: the same start with a note does show it
    payload = '{"session_id": "s1", "source": "startup"}'
    assert "DISK:" not in _start(without, "claude", "session-kb-guard", payload).stdout.decode("utf-8")
    assert "DISK:" in _start(with_note, "claude", "session-kb-guard", payload).stdout.decode("utf-8")


def _markers(monkeypatch, *keep):
    """Exactly the named test markers set (the launcher sets HARNESS_FIXTURE_ROOT too, which would mask a clause)."""
    for k in ("PYTEST_CURRENT_TEST", "HARNESS_FIXTURE_ROOT", "HOUSEKEEP_REAL_BIN_IN_TESTS", "SKYGUARD_HOUSEKEEP_SWEEP"):
        monkeypatch.delenv(k, raising=False)
    for k in keep:
        monkeypatch.setenv(k, "1")


@pytest.mark.parametrize("marker", ["PYTEST_CURRENT_TEST", "HARNESS_FIXTURE_ROOT"])
def test_a_sweep_is_launched_detached_outside_tests_and_never_inside_them(monkeypatch, marker):
    """Only for the installed toolkit itself (the project whose tools/ holds this run.py): here, REPO. Each test
    marker alone stops the launch; HOUSEKEEP_REAL_BIN_IN_TESTS (housekeep's one opt-in) lets it through."""
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    _markers(monkeypatch, marker)
    run._disk_note(str(REPO))
    assert launched == [], "under the test harness the hook must not sweep the real temp dir"
    monkeypatch.setenv("HOUSEKEEP_REAL_BIN_IN_TESTS", "1")         # twin: what a real session does
    run._disk_note(str(REPO))
    (argv, kw), = launched
    assert argv[-3:] == [str(REPO / "tools" / "housekeep.py"), "session", str(REPO / run.DISK_NOTE)]
    assert kw["stdout"] == subprocess.DEVNULL and (kw["creationflags"] if os.name == "nt" else kw["start_new_session"])


def test_no_sweep_for_a_project_that_is_not_this_toolkit(tmp_path, monkeypatch):
    """A hook run against a test fixture or another copy never launches THIS copy's sweep (coordinator finding)."""
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    _markers(monkeypatch, "HOUSEKEEP_REAL_BIN_IN_TESTS")           # as a real session: only the project differs
    run._disk_note(str(tmp_path))
    assert launched == []
    run._disk_note(str(REPO))                                       # twin: the toolkit itself does launch
    assert len(launched) == 1


def test_the_launch_guard_and_the_bin_refusal_read_the_same_markers(monkeypatch):
    """review2 B-tools L2: one set of test markers and ONE opt-in for both guards (the launch and the shell call)."""
    sys.path.insert(0, str(REPO / "tools"))
    import housekeep
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    for keep in [(), ("PYTEST_CURRENT_TEST",), ("HARNESS_FIXTURE_ROOT",),
                 ("PYTEST_CURRENT_TEST", "HOUSEKEEP_REAL_BIN_IN_TESTS"),
                 ("HARNESS_FIXTURE_ROOT", "SKYGUARD_SWEEP_IN_TESTS")]:     # the retired second opt-in counts for nothing
        monkeypatch.delenv("SKYGUARD_SWEEP_IN_TESTS", raising=False)
        _markers(monkeypatch, *keep)
        del launched[:]
        run._disk_note(str(REPO))
        assert bool(launched) is not housekeep.under_test_harness(), keep


def test_the_switch_stops_the_session_sweep(monkeypatch):
    """Helpers that clean the environment (no PYTEST_CURRENT_TEST) rely on SKYGUARD_HOUSEKEEP_SWEEP=off."""
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    _markers(monkeypatch, "HOUSEKEEP_REAL_BIN_IN_TESTS")
    monkeypatch.setenv("SKYGUARD_HOUSEKEEP_SWEEP", "off")
    run._disk_note(str(REPO))
    assert launched == []
