"""A POSIX host has one root: `/x` is absolute there in every dialect (Linux CI run 38187755448).

The guard read a PowerShell/cmd `/x` (and an apply_patch `/x`) as "the root of the cwd's drive", which is right on
Windows and names NOTHING on Linux: there every such operand resolved to None (unknown), so a write or delete under a
POSIX install was allowed unless the command text happened to name a root. Reachable by a real user: Claude Code's
PowerShell tool runs on Linux with pwsh, and `python -c "open('/.../Saves/x.ess','wb')"` through the Bash tool went
the same way (its code is judged in both dialects). These tests run on any OS by flipping paths.POSIX_HOST; the paths
are POSIX strings that never touch the disk.
"""
import json
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, paths, targets  # noqa: E402
from skyguard.adapters import claude, codex  # noqa: E402
from skyguard.audit import Audit  # noqa: E402

GAME = "/home/u/Games/SkyrimVR"
CFG = "/home/u/cfg"


@pytest.fixture()
def posix(monkeypatch):
    monkeypatch.setattr(paths, "POSIX_HOST", True)


@pytest.fixture()
def windows(monkeypatch):
    monkeypatch.setattr(paths, "POSIX_HOST", False)


def _roots():
    return paths.roots(GAME, "", {"SKYRIM_CONFIG_DIR": CFG})


def test_a_powershell_absolute_posix_path_resolves_on_a_posix_host(posix):
    assert targets.resolve(GAME + "/Data/x.esp", "/home/u", "powershell", {}) == (GAME + "/Data/x.esp")
    assert targets.resolve(GAME + "/Data/x.esp", "", "powershell", {}) == (GAME + "/Data/x.esp")   # no cwd at all
    assert paths.norm_abs(GAME + "/Data", msys=False) == GAME + "/Data"


def test_on_windows_a_slash_path_is_still_the_cwd_drives_root(windows):
    assert targets.resolve("/x/y", "C:/work", "powershell", {}) == "c:/x/y"
    assert targets.resolve("/x/y", "", "powershell", {}) is None           # no cwd: which drive is unknown
    assert paths.norm_abs("/x", msys=False) is None


@pytest.mark.parametrize("root", ["/c/profile", "/x/profile", "/a"])
def test_single_letter_posix_root_and_native_file_use_the_same_zone(posix, root):
    roots = paths.roots(GAME, "", {"SKYRIM_CONFIG_DIR": root})
    assert paths.norm_abs(root, msys=True) == root
    target = targets.resolve(root + "/Saves/Existing.ess", "/home/u", "cmd", {})
    assert targets.zone(target, roots) == "config"


def test_git_bash_drive_alias_is_still_windows_specific(windows):
    assert paths.norm_abs("/c/profile", msys=True) == "c:/profile"


def _claude(tool, command):
    p = claude.extract(json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": "/home/u",
                                   "tool_input": {"command": command}}))
    audit = Audit(GAME)
    v = engine.evaluate_shell(claude.to_request(p, "shell"), _roots(), audit)
    return v.decision, {rid for rid, _ in audit.fired}


def _save_targets(tool, command):
    """The saves the call changes, as the save policy reads them (saves.py): (path, op). Nothing exists on disk here,
    so no backup is due; what matters is that the guard SEES the save under a POSIX install."""
    p = claude.extract(json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": "/home/u",
                                   "tool_input": {"command": command}}))
    v = engine.evaluate_shell(claude.to_request(p, "shell"), _roots(), Audit(GAME))
    return [(t.path, t.op) for t in v.saves.change.targets] if v.saves is not None else []


def test_a_powershell_save_overwrite_is_seen_on_a_posix_host(posix):
    got = _save_targets("PowerShell", "Set-Content '{}/Saves/Existing.ess' x".format(CFG))
    assert got == [(CFG + "/Saves/Existing.ess", "write")]


def test_a_python_save_overwrite_through_bash_is_seen_on_a_posix_host(posix):
    cmd = "python -c \"open('{}/Saves/Existing.ess','wb').write(b'x')\"".format(CFG)
    assert _save_targets("Bash", cmd) == [(CFG + "/Saves/Existing.ess", "write")]


def test_a_powershell_delete_in_the_install_is_denied_on_a_posix_host(posix):
    assert _claude("PowerShell", "Remove-Item -Recurse '{}/Data'".format(GAME)) == ("deny", {"S-DEL-INSTALL"})


def test_an_apply_patch_delete_by_absolute_posix_path_is_denied_on_a_posix_host(posix):
    raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "/home/u",
                      "tool_input": {"command": "*** Begin Patch\n*** Delete File: {}/SkyrimVR.exe\n*** End Patch"
                                     .format(GAME)}})
    audit = Audit(GAME)
    v = engine.pre_tool(codex.to_request(raw, "linux"), GAME, _roots, audit)
    assert (v.decision, {rid for rid, _ in audit.fired}) == ("deny", {"S-DEL-INSTALL"})
