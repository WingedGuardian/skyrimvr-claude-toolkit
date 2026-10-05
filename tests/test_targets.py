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
    ("/proc/cygdrive/c/x", "c:/x"),
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


@pytest.mark.parametrize("cmd,dialect", [
    ("cd /c/GOG*/The*Skyrim*; rm -rf Data", "bash"),
    ("cd '" + GAME[:-3] + "'*; rm -rf Data", "bash"),
    ("cd '" + GAME[:-3] + "'?R; rm -rf Data", "bash"),
    ("Set-Location '" + w(GAME[:-3]) + "*'; Remove-Item -Recurse Data", "powershell"),
])
def test_i3_a_glob_in_the_cd_target_makes_the_directory_unknown(cmd, dialect):
    """I3: resolve() keeps a glob in the LAST component; a relative word joined onto it read as resolved+outside."""
    e = _exec(cmd, dialect)[0]
    assert targets.exec_cwd(e, "C:/work", ENV) is None
    assert targets.resolve_operand(e, "Data", "C:/work", ENV) is None


def test_i3_a_literal_cd_still_resolves():
    e = _exec("cd '" + GAME + "'; rm -rf Data")[0]
    assert targets.exec_cwd(e, "C:/work", ENV) == GAMEN


@pytest.mark.parametrize("word", ["@p", "@args", "@splat_1"])
def test_i5_a_powershell_splat_is_unresolvable(word):
    assert targets.resolve(word, GAME, "powershell", ENV) is None


def test_i5_an_at_word_in_bash_is_a_file_name():
    assert targets.resolve("@p", GAME, "bash", ENV) == GAMEN + "/@p"


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
    (GAMEN + "/scripts/__pycache__/x.pyc", "scratch"),
    (GAMEN + "/scripts/__pycache__", "scratch"),
    (GAMEN + "/tools/__pycache__", "scratch"),                            # Task 7 fix round 1: Python regenerates it
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


# ------------------------------------------------------------------------------------------------ fix round 1
# Adversarial review of Task 6 (plan5-t6-review/adv.py, adv2.py, cfg.py): every wrong row is a test here.
PARENT = "C:/GOG Games"


@pytest.mark.parametrize("word,cwd,dialect", [
    ("C:/GOG Games", GAME, "bash"),
    ("C:/GOG Games/", GAME, "powershell"),
    ("C:/", GAME, "cmd"),
    ("/c", GAME, "bash"),
    ("/c/", GAME, "bash"),
    (".", PARENT, "bash"),
    ("..", GAME, "powershell"),
    ("C:/GOG Games/*", GAME, "bash"),
    ("C:/GOG*", GAME, "bash"),
    ("C:/GOG Games/The Elder*", GAME, "powershell"),
    ("C:/GOG Games/The Elder Scrolls V Skyrim V?", GAME, "cmd"),
    ("C:/GOG Games/[T]he Elder Scrolls V Skyrim VR", GAME, "bash"),
    ("C:/GOG Games/{The Elder Scrolls V Skyrim VR,x}", GAME, "bash"),
    ("C:/GOG Games/{TES,The Elder Scrolls V Skyrim VR}", GAME, "bash"),
    ("*", PARENT, "bash"),
    ("The*", PARENT, "powershell"),
    ("D:/MO2", GAME, "bash"),
    ("D:/*", GAME, "bash"),
    ("d:/profiles", GAME, "cmd"),
])
def test_a_path_that_contains_or_globs_a_protected_root_covers_it(word, cwd, dialect):
    """CRITICAL (review item 1): deleting an ANCESTOR of the install, or a glob that matches it, deletes the
    install. zone() alone says "outside" for these, so Task 7 needs covers_protected()."""
    p = targets.resolve(word, cwd, dialect, ENV)
    assert p is not None, word
    assert targets.covers_protected(p, ROOTS), (word, p)


@pytest.mark.parametrize("word,cwd", [
    ("C:/GOG Games/Other", GAME), ("C:/GOG Games/Th?", GAME), ("C:/GOG Games/[X]he Elder Scrolls V Skyrim VR", GAME),
    ("C:/GOG Games/{a,b}", GAME), ("C:/Temp/*", GAME), ("x*", PARENT), ("D:/MO2/other", GAME), ("/tmp/*", GAME),
    ("//srv/share", GAME), (GAME + "/Data/x", GAME),
])
def test_a_path_that_does_not_reach_a_protected_root_does_not_cover_it(word, cwd):
    assert not targets.covers_protected(targets.resolve(word, cwd, "bash", ENV), ROOTS), word


def test_covers_protected_includes_the_root_itself_and_documents_ancestors():
    assert targets.covers_protected(GAMEN, ROOTS)
    assert targets.covers_protected("c:/Users/me/Documents", ROOTS)          # holds My Games/Skyrim*
    assert targets.covers_protected("c:/Users/me/Documents/My Games", ROOTS)
    assert not targets.covers_protected("c:/Users/me/Documents/x", ROOTS)


def test_covers_protected_is_linear_on_a_long_glob():
    import time
    t0 = time.process_time()
    targets.covers_protected("c:/" + "*a" * 20000 + "b", ROOTS)
    assert time.process_time() - t0 < 1.0


@pytest.mark.parametrize("word", [
    "C:/GOGGAM~1/THEELD~1/Data", "C:/GOGGAM~1/THEELD~1", "C:/GOG Games/THEELD~1", "c:/progra~2/x",
])
def test_an_8dot3_short_name_is_unresolvable(word):
    """IMPORTANT (item 2): the install's real short name is C:/GOGGAM~1/THEELD~1. It cannot be expanded without the
    filesystem, so it is unresolved (stricter), never "outside"."""
    for d in ("bash", "powershell", "cmd"):
        assert targets.resolve(word, GAME, d, ENV) is None, (word, d)


def test_a_short_name_cwd_makes_relative_words_unresolvable():
    assert targets.resolve("Data/x", w("C:/GOGGAM~1/THEELD~1"), "powershell", ENV) is None
    assert targets.exec_cwd(_exec("rm x")[0], w("C:/GOGGAM~1/THEELD~1"), ENV) is None


@pytest.mark.parametrize("word", [
    w("//localhost/c$/GOG Games/The Elder Scrolls V Skyrim VR/Data"),
    w("//127.0.0.1/C$/GOG Games/The Elder Scrolls V Skyrim VR"),
    w("//?/UNC/localhost/c$/GOG Games/The Elder Scrolls V Skyrim VR"),
    w("//BILLY/c$/GOG Games/The Elder Scrolls V Skyrim VR"),
    w("Microsoft.PowerShell.Core/FileSystem:://localhost/c$/GOG Games/The Elder Scrolls V Skyrim VR"),
])
def test_a_loopback_admin_share_is_the_drive(word):
    env = dict(ENV, COMPUTERNAME="BILLY")
    for d in ("powershell", "cmd"):
        p = targets.resolve(word, GAME, d, env)
        assert p is not None and targets.zone(p, ROOTS) == "install", (word, d, p)


def test_an_ipv6_loopback_admin_share_is_the_drive_in_cmd_and_unresolvable_in_powershell():
    """`[` is a PowerShell wildcard in a directory component, so there it is unresolved (stricter)."""
    word = w("//[::1]/c$/GOG Games/The Elder Scrolls V Skyrim VR")
    assert targets.zone(targets.resolve(word, GAME, "cmd", ENV), ROOTS) == "install"
    assert targets.resolve(word, GAME, "powershell", ENV) is None


def test_another_hosts_admin_share_is_unresolvable_and_a_plain_share_is_outside():
    assert targets.resolve(w("//other/c$/x"), GAME, "cmd", dict(ENV, COMPUTERNAME="BILLY")) is None
    assert targets.resolve(w("//srv/share/x"), GAME, "cmd", ENV) == "//srv/share/x"
    assert targets.resolve(w("//./c$"), GAME, "cmd", ENV) is None


@pytest.mark.parametrize("word", [
    GAME + "::$INDEX_ALLOCATION", GAME + ":$I30:$INDEX_ALLOCATION/Data", GAME + "/Data/x.esp:stream",
    GAME + "/Data/x.esp::$DATA",
])
def test_an_ntfs_stream_suffix_is_stripped(word):
    p = targets.resolve(word, GAME, "cmd", ENV)
    assert p is not None and targets.zone(p, ROOTS) == "install" and ":" not in p[2:], (word, p)


@pytest.mark.parametrize("p,want", [
    (GAMEN + "/tools/zedit/app_src/node_modules/x", "install"),     # tool RUNTIME (zEdit), not scratch
    (GAMEN + "/tools/xelib/node_modules/koffi", "install"),
    (GAMEN + "/TOOLS/__pycache__", "scratch"),                      # Task 7 fix round 1 ruling
    (GAMEN + "/tools/xelib/__pycache__/x.pyc", "scratch"),
    (GAMEN + "/tools/x/.pytest_cache", "install"),                  # only __pycache__ under tools/
    (GAMEN + "/Data/__pycache__", "install"),
    (GAMEN + "/Data/scripts/node_modules", "install"),
    (GAMEN + "/node_modules/x", "scratch"),
    (GAMEN + "/scripts/__pycache__/a.pyc", "scratch"),
    (GAMEN + "/tests/.pytest_cache", "scratch"),
])
def test_scratch_never_covers_tools_or_data(p, want):
    """RULING (item 4, R8): node_modules under the install's tools/ is tool runtime (zEdit app_src/node_modules,
    tools/xelib/node_modules with koffi); Data/ is the game. Task 7 fix round 1 refines it: a __pycache__ under
    tools/ IS scratch (Python regenerates it); nothing else under tools/ is."""
    assert targets.zone(p, ROOTS) == want


@pytest.mark.parametrize("pat,name,want", [
    ("*", "anything", True), ("the*", "the elder", True), ("*vr", "skyrim vr", True), ("t?e", "the", True),
    ("[st]he", "the", True), ("[!t]he", "the", False), ("[^t]he", "she", True), ("[a-u]he", "the", True),
    ("[x-z]he", "the", False), ("[the", "[the", True), ("{a,the}", "the", True), ("x{a,b}", "the", False),
    ("{a,{b,the}}", "the", False), ("*e*e*", "the elder", True), ("*z*", "the elder", False), ("", "", True),
])
def test_component_glob_matcher(pat, name, want):
    assert targets._comp_matches(pat, name) == want


def test_an_overlong_glob_component_is_treated_as_star():
    assert targets._comp_matches("*" * 2000 + "x", "the")
    assert not targets._comp_matches("y" * 2000, "the")             # no glob char: plain comparison


# ------------------------------------------------------------------------------------------------ fix round 2
def test_a_root_with_literal_glob_characters_covers_itself():
    """A protected root named `D:/Mods [MO2]/mods` or `E:/M{a,b}` must match ITSELF: literal equality is tested
    before the component is read as a glob."""
    r = paths.roots(GAME, "", {"SKYRIM_MO2_MODS": "D:/Mods [MO2]/mods", "SKYRIM_MO2_OVERWRITE": "E:/M{a,b}/ow"})
    assert targets.covers_protected("d:/Mods [MO2]", r)
    assert targets.covers_protected("d:/Mods [MO2]/mods", r)
    assert targets.covers_protected("e:/M{a,b}", r)
    assert targets.covers_protected("e:/M{a,b}/ow", r)


@pytest.mark.parametrize("word", ["{C:/GOG*,x}", "{x,C:/GOG Games}", "a{b,c/d}e", "{x,/c/GOG Games}"])
def test_a_brace_group_holding_a_separator_is_unresolvable(word):
    """bash `{C:/GOG*,x}` expands to an ABSOLUTE path; read literally it looked relative (fix round 2)."""
    assert targets.resolve(word, GAME, "bash", ENV) is None


def test_a_brace_group_without_a_separator_still_resolves():
    assert targets.resolve("{a,b}", GAME, "bash", ENV) == GAMEN + "/{a,b}"


def test_claude_dir_is_never_scratch():
    assert targets.zone(GAMEN + "/.claude/node_modules/x", ROOTS) == "install"
    assert targets.zone(GAMEN + "/.claude/hooks/__pycache__", ROOTS) == "install"
