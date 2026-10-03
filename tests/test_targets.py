"""targets: operand resolution from the payload cwd, and protection zones (Plan 5 Task 6).

Review focus 3: Windows vs MSYS spellings, `..`, `~`, drive-relative `C:foo`, UNC paths and a `cd` inside the
command. A wrong answer here is a silent allow once the delete rules sit on it (Task 7), so every spelling is
pinned, and None ("could be anywhere") is pinned as distinct from "outside".
"""
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import paths, shellparse, targets  # noqa: E402

BS = chr(92)
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
GAMEN = "c:/GOG Games/The Elder Scrolls V Skyrim VR"
ENV = {"HOME": "/c/Users/me", "USERPROFILE": "C:" + BS + "Users" + BS + "me"}
CWD_WIN = GAME.replace("/", BS)


def w(s):
    """Write a Windows path with '/' in the test source and get backslashes, so no escape can eat one."""
    return s.replace("/", BS)


@pytest.mark.parametrize("word,want", [
    (w("C:/a/b"), "c:/a/b"),
    ("C:/a/b", "c:/a/b"),
    ("/c/a/b", "c:/a/b"),
    (w("c:/A/B/../b"), "c:/A/b"),
    (w("c:/A/./B//b/"), "c:/A/B/b"),
    ("C:/a/../../..", "c:/"),
    ("C:/", "c:/"),
    (w("//server/share/x"), "//server/share/x"),
    ("//server/share/x/../y", "//server/share/y"),
    (w("//?/C:/x"), "c:/x"),
    (w("//?/UNC/srv/share/x"), "//srv/share/x"),
    ("C:foo", None),
    ("C:", None),
    ("/cygdrive/d/x", "d:/x"),
    ("/mnt/c/x", "c:/x"),
    ("/tmp/x", "/tmp/x"),
    ("C:/Data./x", "c:/Data/x"),          # Windows drops a component's trailing dots and spaces
])
def test_absolute_spellings_bash(word, want):
    assert targets.resolve(word, "", "bash", ENV) == want


@pytest.mark.parametrize("dialect", ["bash", "powershell", "cmd"])
def test_absolute_windows_path_is_dialect_independent(dialect):
    assert targets.resolve(w("C:/a/b"), "", dialect, ENV) == "c:/a/b"
    assert targets.resolve("C:foo", CWD_WIN, dialect, ENV) is None


@pytest.mark.parametrize("word,dialect,want", [
    ("~/x", "bash", "c:/Users/me/x"),
    ("~", "bash", "c:/Users/me"),
    ("$HOME/x", "bash", "c:/Users/me/x"),
    ("${USERPROFILE}/x", "bash", "c:/Users/me/x"),
    ("~/x", "powershell", "c:/Users/me/x"),
    (w("$env:USERPROFILE/x"), "powershell", "c:/Users/me/x"),
    (w("${env:USERPROFILE}/x"), "powershell", "c:/Users/me/x"),
    (w("$Env:UserProfile/x"), "powershell", "c:/Users/me/x"),
    (w("$HOME/x"), "powershell", "c:/Users/me/x"),
    (w("%USERPROFILE%/x"), "cmd", "c:/Users/me/x"),
    (w("%userprofile%/x"), "cmd", "c:/Users/me/x"),
])
def test_home_family(word, dialect, want):
    assert targets.resolve(word, "", dialect, ENV) == want


@pytest.mark.parametrize("word,dialect", [
    ("$X/a", "bash"), ("${X:-a}/b", "bash"), ("$(pwd)/a", "bash"), ("~user/x", "bash"), ("~+/x", "bash"),
    ("$x/a", "powershell"), ("$env:TEMP/a", "powershell"), ("%TEMP%/a", "cmd"),
    ("%i", "cmd"), ("%~dp0/x", "cmd"), ("%1", "cmd"),
    ("-", "bash"), ("", "bash"),
    ("a/*/b", "bash"), ("a/?/b", "powershell"), ("a/[x]/b", "bash"), ("{a,b}/c", "bash"),
    (w("HKLM:/SOFTWARE/Bethesda"), "powershell"),
])
def test_unresolvable_is_none(word, dialect):
    assert targets.resolve(word, CWD_WIN, dialect, ENV) is None


def test_glob_in_the_last_component_keeps_its_parent():
    assert targets.resolve("Data/*.esp", GAME, "bash", ENV) == GAMEN + "/Data/*.esp"


def test_home_unset_is_none_not_relative():
    assert targets.resolve("~/x", CWD_WIN, "bash", {}) is None
    assert targets.resolve("$HOME/x", CWD_WIN, "bash", {}) is None


@pytest.mark.parametrize("cwd", [CWD_WIN, GAME, "/c/GOG Games/The Elder Scrolls V Skyrim VR",
                                 "/c/GOG Games/The Elder Scrolls V Skyrim VR/"])
@pytest.mark.parametrize("dialect", ["bash", "powershell", "cmd"])
def test_relative_word_resolves_against_the_payload_cwd(cwd, dialect):
    word = w("Data/x") if dialect != "bash" else "Data/x"
    assert targets.resolve(word, cwd, dialect, ENV) == GAMEN + "/Data/x"
    assert targets.resolve("../x", cwd, dialect, ENV) == "c:/GOG Games/x"
    assert targets.resolve(".", cwd, dialect, ENV) == GAMEN


def test_relative_word_without_a_cwd_is_none():
    for cwd in ("", "relative/dir", "C:rel"):
        assert targets.resolve("Data/x", cwd, "bash", ENV) is None


@pytest.mark.parametrize("dialect", ["powershell", "cmd"])
def test_root_relative_takes_the_cwd_drive(dialect):
    assert targets.resolve(w("/x/y"), "D:/work", dialect, ENV) == "d:/x/y"
    assert targets.resolve("/x/y", "D:/work", dialect, ENV) == "d:/x/y"


def test_pwd_and_cd_variables_are_the_cwd():
    assert targets.resolve("$PWD/Data", GAME, "bash", ENV) == GAMEN + "/Data"
    assert targets.resolve("%CD%/Data", GAME, "cmd", ENV) == GAMEN + "/Data"
    assert targets.resolve("$pwd/Data", GAME, "powershell", ENV) == GAMEN + "/Data"


def test_powershell_provider_prefix():
    assert targets.resolve(w("FileSystem::C:/a"), "", "powershell", ENV) == "c:/a"
    assert targets.resolve(w("Microsoft.PowerShell.Core/FileSystem::C:/a"), "", "powershell", ENV) == "c:/a"


def _exec(cmd, dialect="bash"):
    return [e for e in shellparse.parse(cmd, dialect).execs if e.program not in ("cd", "set-location")]


def test_cd_before_absolute():
    e = _exec('cd "' + GAME + '" && rm -rf Data/x')[0]
    assert targets.resolve_operand(e, "Data/x", "D:/elsewhere", ENV) == GAMEN + "/Data/x"


def test_cd_before_relative_joins_the_payload_cwd():
    e = _exec("cd Data && rm x")[0]
    assert targets.resolve_operand(e, "x", GAME, ENV) == GAMEN + "/Data/x"
    e = _exec("Set-Location Data; Remove-Item x", "powershell")[0]
    assert targets.resolve_operand(e, "x", CWD_WIN, ENV) == GAMEN + "/Data/x"


def test_cd_before_unknowable_is_none():
    e = _exec("cd $X && rm x")[0]
    assert targets.exec_cwd(e, GAME, ENV) is None
    assert targets.resolve_operand(e, "x", GAME, ENV) is None


def test_no_cd_uses_the_payload_cwd():
    e = _exec("rm x")[0]
    assert targets.exec_cwd(e, CWD_WIN, ENV) == GAMEN


# ------------------------------------------------------------------------------------------------ zones
MO2 = "D:/MO2/mods"
ROOTS = paths.roots(GAME, "", {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_CONFIG_DIR": "D:/Profiles/Default",
                               "SKYRIM_MO2_MODS": MO2, "SKYRIM_MO2_OVERWRITE": "D:/MO2/overwrite"})


@pytest.mark.parametrize("p,want", [
    (GAMEN + "/Data/x.esp", "install"),
    (GAMEN.upper(), "install"),
    (GAMEN, "install"),
    ("c:/GOG Games/The Elder Scrolls V Skyrim VR2/x", "outside"),        # a sibling, not a child
    ("c:/GOG Games", "outside"),
    ("d:/profiles/default/skyrim.ini", "config"),
    ("c:/Users/me/Documents/My Games/Skyrim VR/SkyrimVR.ini", "config"),   # legacy literal, any Skyrim* folder
    ("c:/Users/me/Documents/My Games/SkyrimX", "config"),
    ("c:/Users/me/Documents/My Games/Oblivion/x", "outside"),
    ("d:/mo2/mods/SomeMod/x.esp", "mo2"),
    ("d:/mo2/overwrite", "mo2"),
    ("/tmp/x", "outside"),
    ("//server/share/x", "outside"),
    (GAMEN + "/tools/__pycache__/x.pyc", "scratch"),
    (GAMEN + "/tools/__pycache__", "scratch"),
    (GAMEN + "/node_modules/a/b", "scratch"),
    (GAMEN + "/.pytest_cache", "scratch"),
    (GAMEN + "/.claude/backups", "install"),                              # R8: .claude stays protected
    (GAMEN + "/tools/__pycache__x/y", "install"),
])
def test_zone(p, want):
    assert targets.zone(p, ROOTS) == want


def test_mo2_nested_inside_the_install_wins():
    r = paths.roots(GAME, "", {"SKYRIM_MO2_MODS": GAME + "/MO2/mods"})
    assert targets.zone(GAMEN + "/MO2/mods/A/x.esp", r) == "mo2"
    assert targets.zone(GAMEN + "/MO2/profiles/x", r) == "install"


def test_install_nested_inside_mo2_wins():
    r = paths.roots("D:/Nolvus/STOCK GAME", "", {"SKYRIM_MO2_MODS": "D:/Nolvus"})
    assert targets.zone("d:/Nolvus/STOCK GAME/Data/x", r) == "install"


def test_scratch_only_applies_inside_the_install():
    assert targets.zone("d:/mo2/mods/A/__pycache__/x", ROOTS) == "mo2"
    assert targets.zone("c:/elsewhere/__pycache__/x", ROOTS) == "outside"


def test_scratch_globs_are_validated_data():
    assert paths.SCRATCH_NAMES == ("__pycache__", "node_modules", ".pytest_cache")
    with pytest.raises(ValueError):
        paths._scratch_names(["**/*.pyc"])


def test_roots_dirs_are_normalised_and_deduped():
    r = paths.roots("/c/GOG Games/The Elder Scrolls V Skyrim VR", CWD_WIN, {"SKYRIM_GAME_ROOT": GAME + "/"})
    assert r.install_dirs == (GAMEN,)
