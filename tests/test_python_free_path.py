"""The "no Python on PATH" environment the hook tests build -- and the wrappers' SKYGUARD_NO_SYSTEM_PYTHON knob.

The first native-Linux CI run (38178385999) failed 36 hook tests on every Python: the tests dropped each PATH
directory that offers a python, and on Linux that is /usr/bin -- where cat, mkdir and tr live too. Every hook
then read an EMPTY payload (`INPUT=$(cat)` -> "cat: command not found") and refused with "GUARD INERT ...
received no file path". A test-environment defect, not a product one: no real Linux PATH lacks cat.

These tests exercise the Linux shape on any platform through conftest.python_free_path's `mirror` seam.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from conftest import python_free_path
from test_python_fallback import (MISSING,_esp_write, _failed, _fire, _install, _reason, _working)

BLOCKED = "Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."


def _entries(path: str) -> dict:
    """name -> directory, for every entry reachable on `path` (first one wins, as in a lookup)."""
    seen = {}
    for d in path.split(os.pathsep):
        if d and Path(d).is_dir():
            for n in os.listdir(d):
                seen.setdefault(n, d)
    return seen


def test_a_dropped_directory_keeps_its_other_commands(tmp_path):
    """The Linux /usr/bin shape: python3 BESIDE cat. The python must go; cat must stay reachable."""
    usr_bin, other = tmp_path / "usr-bin", tmp_path / "other"
    for d, names in ((usr_bin, ("python3", "python3.12", "cat", "mkdir")), (other, ("tool",))):
        d.mkdir()
        for n in names:
            (d / n).write_text("#!/bin/sh\n", encoding="utf-8")
    path = python_free_path(os.pathsep.join([str(usr_bin), str(other)]), mirror=True, base=tmp_path / "m")
    reach = _entries(path)
    assert "cat" in reach and "mkdir" in reach, (path, sorted(reach))
    assert "tool" in reach
    assert not any(n.startswith("python") for n in reach), sorted(reach)
    assert str(usr_bin) not in path.split(os.pathsep)


def test_without_the_mirror_a_dropped_directory_takes_everything_with_it(tmp_path):
    """The Windows default (Pythons live in their own directories): no mirror, the directory just goes."""
    d = tmp_path / "pydir"
    d.mkdir()
    for n in ("python.exe", "cat"):
        (d / n).write_text("x", encoding="utf-8")
    assert python_free_path(str(d), mirror=False) == ""


def test_the_real_path_keeps_every_command_the_hooks_use():
    """On THIS machine's PATH: every helper the wrappers fork that is reachable now stays reachable, and no
    python is. (Red on the Linux runner before the fix: /usr/bin, and cat with it, was dropped.)"""
    clean = python_free_path()
    for cmd in ("cat", "mkdir", "tr", "sleep", "env", "bash"):
        if shutil.which(cmd):
            assert shutil.which(cmd, path=clean), f"{cmd} lost from the python-free PATH: {clean}"
    for name in ("python", "python3", "py"):
        assert shutil.which(name, path=clean) is None, (name, shutil.which(name, path=clean))


def test_no_python_env_switches_the_system_locations_off():
    """The tests that mean "no Python at all" must also take /usr/bin/python3 off the table on Linux."""
    from conftest import no_python_env
    env = no_python_env()
    assert env["SKYGUARD_NO_SYSTEM_PYTHON"] == "1"
    assert shutil.which("python3", path=env["PATH"]) is None


def _system_python_install(tmp_path: Path) -> tuple:
    """A wrapper whose fixed system locations name a WORKING python in tmp_path (the runner's /usr/bin/python3
    stand-in), so the knob can be exercised on any platform."""
    root = _install(tmp_path, MISSING)
    sysbin = tmp_path / "sysbin"
    _working(sysbin, "python3")
    stand_in = (sysbin / "python3").as_posix()
    hook = root / ".claude" / "hooks" / "protect-files.sh"
    text = hook.read_text(encoding="utf-8")
    assert text.count("for d in /usr/bin/python3 /usr/local/bin/python3; do") == 1
    hook.write_text(text.replace("/usr/bin/python3 /usr/local/bin/python3", stand_in), encoding="utf-8",
                    newline="\n")
    return root, stand_in


def test_the_fixed_system_location_rescues_a_missing_python(tmp_path):
    """Knob unset: the chain reaches the fixed location (as on any Linux box) and the hook still decides."""
    root, stand_in = _system_python_install(tmp_path)
    r = _fire(root, [], "protect-files.sh", _esp_write(root), {"SKYGUARD_NO_SYSTEM_PYTHON": ""})
    assert _reason(r).startswith(BLOCKED), r.stdout + r.stderr
    assert f"used {stand_in}" in _failed(root, "protect-files")


def test_the_knob_skips_the_fixed_system_location(tmp_path):
    """SKYGUARD_NO_SYSTEM_PYTHON=1: the same install finds nothing, so "no Python at all" is testable on Linux."""
    root, _ = _system_python_install(tmp_path)
    r = _fire(root, [], "protect-files.sh", _esp_write(root), {"SKYGUARD_NO_SYSTEM_PYTHON": "1"})
    reason = _reason(r)
    assert "GUARD INERT" in reason and "cannot run Python" in reason, reason
