"""skyguard.writes (Plan 5 Task 8, E21): shell writes and copies decided on the executed program and its target.

Two halves, as in test_skyguard_deletes:
  - every hand-authored t08 intended row, fired IN-PROCESS (decision, rule trace, exact text), parametrized by row id
    so a mutation can name the one row it must turn red;
  - the helpers, one behaviour per test: which programs write, which words are their targets, what a file is.
"""
import json
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, shellparse, targets, write_rules, writes  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
INSTALL = "C:/t7/Skyrim VR"           # stands in for the corpus's temp install (`<install>`)
LAYOUT_ENV = {
    "declared": {"SKYRIM_GAME_ROOT": GAME},
    "config": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_CONFIG_DIR": "D:/Profiles/MO2/profiles/Default"},
    "mo2": {"SKYRIM_GAME_ROOT": GAME, "SKYRIM_MO2_MODS": "D:/MO2/mods", "SKYRIM_MO2_OVERWRITE": "D:/MO2/overwrite"},
}
SHELL = {"Bash": "bash", "PowerShell": "powershell"}
BS = chr(92)


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "T8"]


ROWS = _rows()


def decide(command, layout="declared", cwd="", shell="bash"):
    r = paths.roots(INSTALL, "", LAYOUT_ENV[layout])
    a = audit.Audit("")
    a.raw = lambda line: None                             # no audit log on disk from a unit test
    v = shell_rules.decide(command, r, a, cwd=cwd, shell=shell, env=os.environ)
    if v.decision == "allow":
        got = ("advise", v.context) if v.context else ("allow", "")
    else:
        got = (v.decision, v.reason)
    return got, {rid for rid, _ in a.fired}


def test_there_are_t08_rows():
    assert len(ROWS) >= 40, len(ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_t08_row(row):
    p = row["payload"]
    fill = (lambda s: s.replace("<install>", INSTALL)) if row.get("payload_note") == "install-relative" else (lambda s: s)
    got, fired = decide(fill(p["tool_input"]["command"]), row["layout"], fill(p.get("cwd", "")),
                        SHELL[p["tool_name"]])
    assert got == (row["decision"], row["text"])
    assert fired == set(row["rules"])


# ------------------------------------------------------------------------------------------- targets
def _w(cmd, shell="bash"):
    return [writes.write_targets(e) for e in shellparse.parse(cmd, shell).execs]


def _c(cmd, shell="bash"):
    return [writes.copy_targets(e) for e in shellparse.parse(cmd, shell).execs]


@pytest.mark.parametrize("cmd,shell,want", [
    ("echo x > a 2>&1", "bash", [["a"]]),
    ("echo x >| a 2> b", "bash", [["a", "b"]]),
    ("echo x &> a", "bash", [["a"]]),
    ("ls > /dev/null 2>NUL", "bash", [[]]),
    ("echo hi *> log.txt", "powershell", [["log.txt"]]),
    ("cat < a.esp", "bash", [[]]),
    ("tee -a x y", "bash", [["x", "y"]]),
    ("Tee-Object -FilePath x", "powershell", [["x"]]),
    ("Add-Content -Path a -Value b", "powershell", [["a"]]),
    ("Clear-Content x", "powershell", [["x"]]),
    ("New-Item -ItemType File x -Value y", "powershell", [["x"]]),
    ("Out-File x -Encoding utf8", "powershell", [["x"]]),
    ("sed -i s/a/b/ f g", "bash", [["f", "g"]]),
    ("sed -i.bak -e s/a/b/ f", "bash", [["f"]]),
    ("sed -Ei s/a/b/ f", "bash", [["f"]]),
    ("sed --in-place=.b s/a/b/ f", "bash", [["f"]]),
    ("sed s/a/b/ f", "bash", [[]]),
    ("sed -n p f", "bash", [[]]),
    ("dd if=a of=b bs=1", "bash", [["b"]]),
    ("ls", "bash", [[]]),
])
def test_write_targets(cmd, shell, want):
    assert _w(cmd, shell)[-len(want):] == want


@pytest.mark.parametrize("cmd,shell,want", [
    ("cp a b", "bash", [("b", ("a",))]),
    ("cp -r a b c/", "bash", [("c/", ("a", "b"))]),
    ("cp -t d a b", "bash", [("d/", ("a", "b"))]),
    ("cp --target-directory=d a", "bash", [("d/", ("a",))]),
    ("mv -f -- -x y", "bash", [("y", ("-x",))]),
    ("cp a", "bash", []),
    ("Copy-Item -Path a -Destination b", "powershell", [("b", ("a",))]),
    ("Copy-Item a b -Force", "powershell", [("b", ("a",))]),
    ("Move-Item a", "powershell", [(".", ("a",))]),
    ("copy /y a b", "cmd", [("b", ("a",))]),
    ("robocopy src dst a.esp b.esp /MT", "cmd", [("dst/a.esp", ("a.esp",)), ("dst/b.esp", ("b.esp",))]),
    ("[IO.File]::Copy('a','b')", "powershell", [("b", ("a",))]),
])
def test_copy_targets(cmd, shell, want):
    got = [t for ts in _c(cmd, shell) for t in ts]
    assert got == want


# ------------------------------------------------------------------------------------------- classes
@pytest.mark.parametrize("path,want", [
    ("x.esp", "plugin"), ("X.ESM", "plugin"), ("a/b.esl", "plugin"), ("x.bsa ", "plugin"), ("x.ba2.", "plugin"),
    ("x.esp.bak", ""), ("x.esp.yaml", ""), ("plugins.txt", "loadorder"), ("loadorder.txt", "loadorder"),
    ("SkyrimPrefs.ini", "ini"), ("C:/g/Data/SKSE/Plugins/x.ini", "skse-ini"), ("Data/SKSE/Plugins/x.ini", ""),
    ("notes.txt", ""),
])
def test_file_class(path, want):
    assert targets.file_class(path) == want


def test_write_rules_share_the_classifier():
    """One classifier for both guards: the Edit/Write guard reads the same patterns this module does."""
    src = (REPO / "tools" / "skyguard" / "write_rules.py").read_text(encoding="utf-8")
    for name in ("PLUGIN_FILE", "SKYRIM_INI", "SKSE_INI", "LOADORDER"):
        assert "targets." + name in src, name


# ------------------------------------------------------------------------------------------- where
def _roots(layout="mo2"):
    return paths.roots(INSTALL, "", LAYOUT_ENV[layout])


@pytest.mark.parametrize("path,want", [
    (INSTALL.lower() + "/data/my.esp", True),
    (INSTALL.lower() + "/data/sub/my.esp", False),         # the game loads plugins from Data/ itself only
    (INSTALL.lower() + "/my.esp", False),
    (INSTALL.lower() + "/.claude/backups/my.esp", False),
    ("d:/mo2/mods/m/x.esp", True),
    ("d:/mo2/overwrite/x.esp", True),
    ("c:/users/u/x.esp", False),
])
def test_game_loads(path, want):
    assert writes.game_loads(path, _roots()) is want


@pytest.mark.parametrize("path,want", [
    (INSTALL.lower() + "/data/meshes/x.nif", True),
    (INSTALL.lower() + "/_claude_output/x.txt", False),
    (INSTALL.lower() + "/tools/x.js", False),
    (INSTALL.lower() + "/data/__pycache__/x.pyc", True),   # Data/ is never scratch (R8)
    ("d:/mo2/mods/m/x.txt", True),
    ("c:/users/u/x.txt", False),
])
def test_in_install_area(path, want):
    assert writes.in_install_area(path, _roots()) is want


def test_an_unresolved_destination_in_a_data_folder_still_notes():
    got, fired = decide('cp a.esp "$G/Data/a.esp"', cwd="C:/w")
    assert fired == {"S-COPY-PLUGIN"}
    got, fired = decide('cp a.esp "$G/Backup/a.esp"', cwd="C:/w")
    assert fired == set()


def test_a_deny_drops_the_write_notes():
    got, fired = decide("echo x > a.esp; echo y >> plugins.txt", cwd="C:/w")
    assert got[0] == "deny" and fired == {"S-WRITE-PLUGIN"}


def test_several_targets_name_one_and_count_the_rest():
    got, _ = decide("echo x > a.esp; echo y > b.esp; echo z > a.esp", cwd="C:/w")
    assert got[1].startswith("Blocked: this writes a.esp and 1 more as raw text")


# Plan 8 T6 (rt-fix-b concern 3, deferred row 54): an archive writer onto a file in the install got the redirect
# note ("a > redirect empties it first"), which is false for it. It gets its own two-line note.
ARCHIVE_NOTE = ("Note: this writes the archive {} in the Skyrim install.\n"
                "If that file exists, tar and -Force replace it; zip, 7z and -Update add to it.")


@pytest.mark.parametrize("command,shell,name", [
    ('Compress-Archive -Path C:/Temp/x -DestinationPath "<i>/Data/out.zip"', "powershell", "out.zip"),
    ('Compress-Archive C:/Temp/x "<i>/Data/SKSE/Plugins/a.zip" -Force', "powershell", "a.zip"),
    ('7z a "<i>/Data/out.7z" C:/Temp/x', "bash", "out.7z"),
    ('zip "<i>/Data/out.zip" C:/Temp/x', "bash", "out.zip"),
    ('tar -cf "<i>/Data/out.tar" C:/Temp/x', "bash", "out.tar"),
], ids=["compress-archive", "compress-archive-positional", "7z", "zip", "tar"])
def test_an_archive_writer_in_the_install_gets_the_archive_note(command, shell, name):
    got, fired = decide(command.replace("<i>", INSTALL), shell=shell)
    assert (got, fired) == (("advise", ARCHIVE_NOTE.format(name)), {"S-WRITE-ARCHIVE"})


def test_a_redirect_in_the_install_keeps_the_redirect_note():
    """The twin: only the archive writers moved; a redirect into the same folder is still S-WRITE-INSTALL."""
    got, fired = decide('echo hi > "' + INSTALL + '/Data/out.txt"')
    assert fired == {"S-WRITE-INSTALL"} and "redirect" in got[1]


def test_a_redirect_on_an_archive_writer_keeps_the_redirect_note():
    """Only the ARCHIVE the program writes gets the archive note; its own redirect is still a redirect."""
    got, fired = decide('7z a "' + INSTALL + '/Data/out.7z" C:/Temp/x > "' + INSTALL + '/Data/log.txt"')
    assert fired == {"S-WRITE-ARCHIVE", "S-WRITE-INSTALL"}
    assert "out.7z" in got[1] and "log.txt in the Skyrim install; a > redirect empties it first" in got[1]


def test_an_archive_writer_outside_the_install_is_silent():
    assert decide("Compress-Archive -Path C:/Temp/x -DestinationPath C:/Temp/out.zip", shell="powershell") == \
        (("allow", ""), set())


def test_an_archive_writer_onto_a_plugin_name_is_still_denied():
    got, fired = decide('Compress-Archive C:/Temp/x -DestinationPath "' + INSTALL + '/Data/x.bsa"', shell="powershell")
    assert got[0] == "deny" and fired == {"S-WRITE-PLUGIN"}


def test_the_shell_write_deny_matches_the_write_tool_on_the_same_name():
    """A file the Write tool refuses as a plugin is refused through the shell too, and vice versa."""
    a = audit.Audit("")
    a.raw = lambda line: None
    for name in ("X.esp", "X.ESM", "x.bsa ", "x.esp.bak", "x.esp.yaml"):
        tool = write_rules.decide(GAME + "/Data/" + name, a).decision == "deny"
        shell = decide('echo x > "' + name + '"', cwd="C:/w")[0][0] == "deny"
        assert tool is shell, name
