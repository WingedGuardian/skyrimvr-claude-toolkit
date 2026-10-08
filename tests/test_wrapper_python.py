"""The skyguard / skydoctor wrappers use the interpreter setup.sh recorded in <root>/skyrim-paths.env (Plan 8 T7).

Order: $SKYGUARD_PYTHON, then the file's SKYGUARD_PYTHON (read as data, only when that file exists), then probing.
The stub interpreter lives in a folder with a space, so "one word" is exercised; it prints what it was asked to run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASH, REPO

WIN = sys.platform == "win32"
BASH_WRAPPERS = {"skyguard.sh": "/skyguard lock status", "skydoctor": "/skyguard/doctor.py"}
CMD_WRAPPERS = {"skyguard.cmd": "tools" + chr(92) + 'skyguard" lock status', "skydoctor.cmd": 'doctor.py" lock status'}


def _install(tmp_path: Path, file_value: str | None) -> Path:
    inst = tmp_path / "inst"
    (inst / "tools").mkdir(parents=True)
    for name in list(BASH_WRAPPERS) + list(CMD_WRAPPERS):
        shutil.copyfile(REPO / "tools" / name, inst / "tools" / name)
    if file_value is not None:
        (inst / "skyrim-paths.env").write_text(
            "SKYRIM_GAME_ROOT='%s'\nSKYGUARD_PYTHON='%s'\n" % (inst.as_posix(), file_value), encoding="utf-8",
            newline="\n")
    return inst


def _stub(tmp_path: Path, tag: str, cmd: bool = False) -> Path:
    d = tmp_path / ("Py Dir " + tag)
    d.mkdir()
    if cmd:
        f = d / "fakepy.cmd"
        f.write_text("@echo %s %%*\r\n" % tag, encoding="ascii")
    else:
        f = d / "fakepy"
        f.write_text('#!/bin/bash\necho "%s $*"\n' % tag, encoding="utf-8", newline="\n")
        f.chmod(0o755)
    return f


def _bash(inst, name, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "SKYGUARD_PYTHON"}
    env.update(env_extra or {})
    return subprocess.run([BASH, (inst / "tools" / name).as_posix(), "lock", "status"], capture_output=True,
                          text=True, errors="replace", timeout=120, env=env)


@pytest.mark.parametrize("name", sorted(BASH_WRAPPERS))
def test_the_bash_wrappers_use_the_recorded_interpreter(tmp_path, name):
    stub = _stub(tmp_path, "FILEPY")
    r = _bash(_install(tmp_path, stub.as_posix()), name)
    assert "FILEPY -B" in r.stdout and BASH_WRAPPERS[name] in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("name", sorted(BASH_WRAPPERS))
def test_the_environment_still_wins_over_the_file(tmp_path, name):
    stub, env_stub = _stub(tmp_path, "FILEPY"), _stub(tmp_path, "ENVPY")
    r = _bash(_install(tmp_path, stub.as_posix()), name, {"SKYGUARD_PYTHON": env_stub.as_posix()})
    assert "ENVPY -B" in r.stdout and "FILEPY" not in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("name", sorted(BASH_WRAPPERS))
@pytest.mark.parametrize("value", ["C:/nowhere/python.exe", ""], ids=["missing", "empty"])
def test_a_recorded_interpreter_that_does_not_exist_is_skipped(tmp_path, name, value):
    r = _bash(_install(tmp_path, value), name)
    assert "FILEPY" not in r.stdout and "nowhere" not in r.stdout + r.stderr, r.stdout + r.stderr


@pytest.mark.skipif(not WIN, reason="the .cmd twins run on Windows")
@pytest.mark.parametrize("name", sorted(CMD_WRAPPERS))
def test_the_cmd_twins_use_the_recorded_interpreter(tmp_path, name):
    stub = _stub(tmp_path, "FILEPY", cmd=True)
    inst = _install(tmp_path, stub.as_posix())
    env = {k: v for k, v in os.environ.items() if k != "SKYGUARD_PYTHON"}
    comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    r = subprocess.run([comspec, "/d", "/c", str(inst / "tools" / name), "lock", "status"], capture_output=True,
                       text=True, errors="replace", timeout=120, env=env)
    assert "FILEPY -B" in r.stdout and CMD_WRAPPERS[name] in r.stdout, r.stdout + r.stderr
