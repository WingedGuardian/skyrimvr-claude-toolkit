"""tests/spin_reaper.py ends a spinning process within its deadline, and leaves one that exits by itself alone."""
from __future__ import annotations

import subprocess
import sys
import time

from conftest import REPO



def _wait(p, limit):
    try:
        return p.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        return None


def _spinner(tmp_path, seconds, marker, own_exit=None):
    """A python that arms the reaper on itself, then spins (or exits after own_exit seconds)."""
    body = "import time\n"
    body += "time.sleep({})\n".format(own_exit) if own_exit is not None else "while True:\n    pass\n"
    code = ("import sys\nsys.path.insert(0, {!r})\nimport spin_reaper\nspin_reaper.arm({!r}, {!r})\n").format(
        str(REPO / "tests"), seconds, str(marker)) + body
    script = tmp_path / "spin.py"
    script.write_text(code, encoding="utf-8")
    return subprocess.Popen([sys.executable, str(script)])


def _marker_lines(marker, n, limit=10):
    end = time.time() + limit
    while time.time() < end:
        if marker.is_file():
            lines = marker.read_text(encoding="utf-8").split()
            if len(lines) >= n:
                return lines
        time.sleep(0.1)
    return marker.read_text(encoding="utf-8").split() if marker.is_file() else []


def test_a_spinning_process_is_ended_at_the_deadline(tmp_path):
    marker = tmp_path / "m.txt"
    p = _spinner(tmp_path, 1, marker)
    t = time.time()
    code = _wait(p, 20)
    if code is None:                                  # the reaper failed: never leave the spinner behind
        p.kill()
        p.wait()
    assert code is not None, "the reaper did not end the spinning process"
    assert time.time() - t < 15
    assert "terminated" in _marker_lines(marker, 3)


def test_a_target_that_exits_at_once_is_still_the_reapers_own(tmp_path):
    """Review M1: the reaper must never look its target up by pid (a reused pid could be another process). The
    parent hands it a handle (Windows) or pidfd (Linux) before the reaper starts, so a target that exits before the
    reaper is even running still reads as `exited`, never `gone`."""
    marker = tmp_path / "m.txt"
    p = _spinner(tmp_path, 10, marker, own_exit=0)
    assert _wait(p, 20) == 0
    lines = _marker_lines(marker, 3)
    assert lines[-1:] == ["exited"], lines
    assert lines[1] in ("handle", "pidfd"), lines          # by reference, never by pid, on the platforms we test


def test_a_process_that_exits_by_itself_is_left_alone(tmp_path):
    marker = tmp_path / "m.txt"
    p = _spinner(tmp_path, 10, marker, own_exit=0.5)
    assert _wait(p, 20) == 0
    lines = _marker_lines(marker, 3)
    assert "exited" in lines and "terminated" not in lines
