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

from conftest import REPO, GAME_ROOT

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, shellparse, targets, write_rules, writes  # noqa: E402

GAME = GAME_ROOT
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
    ("sed -bi s/a/b/ f", "bash", [["f"]]),                    # P8-SED: binary + in-place is still a write
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
    # fix-limits 14: /XF and /XD take a LIST of exclusions (up to the next option); they are never copied
    ("robocopy src dst /XF *.esp *.esm /E", "cmd", []),
    ("robocopy src dst c.txt /XF a.esp /XD Data /E", "cmd", [("dst/c.txt", ("c.txt",))]),
    ("robocopy src dst /xd Data Scripts c.txt", "cmd", []),
    ("robocopy src dst /XF a.esp /IF c.txt", "cmd", [("dst/c.txt", ("c.txt",))]),
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
                "If that file exists, tar and -Force replace it; zip, 7z and -Update change what is inside it.")


@pytest.mark.parametrize("command,shell,name", [
    ('Compress-Archive -Path C:/Temp/x -DestinationPath "<i>/Data/out.zip"', "powershell", "out.zip"),
    ('Compress-Archive C:/Temp/x "<i>/Data/SKSE/Plugins/a.zip" -Force', "powershell", "a.zip"),
    ('7z a "<i>/Data/out.7z" C:/Temp/x', "bash", "out.7z"),
    ('7z d "<i>/Data/out.7z" old.txt', "bash", "out.7z"),
    ('7z rn "<i>/Data/out.7z" a.txt b.txt', "bash", "out.7z"),
    ('zip "<i>/Data/out.zip" C:/Temp/x', "bash", "out.zip"),
    ('tar -cf "<i>/Data/out.tar" C:/Temp/x', "bash", "out.tar"),
], ids=["compress-archive", "compress-archive-positional", "7z", "7z-delete", "7z-rename", "zip", "tar"])
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


# ---- P8-SED: is sed in binary mode? (S-SED-CRLF). Each case MEASURED with Git for Windows' sed 4.9 on a CRLF file.
@pytest.mark.parametrize("argv,binary", [
    (["-i", "-b", "s/a/b/", "f"], True),
    (["--binary", "-i", "s/a/b/", "f"], True),
    (["-bi", "s/a/b/", "f"], True),
    (["-nbi", "s/a/b/p", "f"], True),
    (["-i", "s/a/b/", "-b", "f"], True),             # GNU getopt permutes
    (["-i", "s/a/b/", "f"], False),
    (["-ib", "s/a/b/", "f"], False),                 # -i's backup suffix "b", not binary
    (["-i", "-es/b/c/", "f"], False),                # an attached -e script
    (["-i", "-f", "-b", "f"], False),                # -f's value: a script file named -b
    (["-i", "--", "s/a/b/", "-b"], False),           # after --, an operand
], ids=["b", "binary-long", "bi-cluster", "nbi-cluster", "permuted", "none", "ib-suffix", "e-attached", "f-valued",
        "dashdash"])
def test_sed_binary(argv, binary):
    assert writes.sed_binary(argv) is binary
# ---- fix-unclear 4b: a write target held in a same-command literal is judged as that literal (Exec.subst)
_G = GAME + "/Data/"
_CFG = "C:/Users/u/Documents/My Games/Skyrim VR/"
_VAR_PAIRS = [   # (shell, direct, variable form)
    ("bash", 'echo x > "' + _G + 'X.esp"', 'P="' + _G + 'X.esp"; echo x > "$P"'),
    ("bash", 'echo x >> "' + _G + 'X.bsa"', 'P="' + _G + 'X.bsa"; echo x >> "$P"'),
    ("bash", 'echo x | tee "' + _G + 'X.esm"', 'P="' + _G + 'X.esm"; echo x | tee "$P"'),
    ("bash", 'cp a.esp "' + _G + 'X.esp"', 'P="' + _G + 'X.esp"; cp a.esp "$P"'),
    ("bash", 'mv b.esp "' + _G + 'X.esp"', 'P="' + _G + 'X.esp"; mv b.esp "$P"'),
    ("bash", 'echo x > "' + _G + 'X.esp"', 'D="' + GAME + '"; F=X.esp; echo x > "$D/Data/$F"'),
    ("bash", 'echo x > "' + _CFG + 'Saves/a.ess"', 'S="' + _CFG + 'Saves/a.ess"; echo x > "$S"'),
    ("bash", 'echo x >> "' + _CFG + 'Skyrim.ini"', 'F="' + _CFG + 'Skyrim.ini"; echo x >> "$F"'),
    ("bash", "echo x >> plugins.txt", 'F=plugins.txt; echo x >> "$F"'),
    ("bash", 'echo x > "' + _G + 'notes.txt"', 'F="' + _G + 'notes.txt"; echo x > "$F"'),
    ("bash", "caprica -o Out.psc a.pex", 'P=Out.psc; caprica -o "$P" a.pex'),
    ("bash", 'Champollion "' + INSTALL + '/Data/Scripts/a.pex"',
     'P="' + INSTALL + '/Data/Scripts/a.pex"; Champollion "$P"'),
    ("bash", 'git -C "' + INSTALL + '" checkout -- .', 'R="' + INSTALL + '"; git -C "$R" checkout -- .'),
    ("powershell", "Set-Content '" + _G + "X.esp' x", "$p = '" + _G + "X.esp'; Set-Content $p x"),
    ("powershell", "'x' | Out-File '" + _G + "X.esp'", "$p = '" + _G + "X.esp'; 'x' | Out-File $p"),
    ("powershell", "'x' > '" + _G + "X.esp'", "$p = '" + _G + "X.esp'; 'x' > $p"),
    ("powershell", "Copy-Item a.esp '" + _G + "X.esp'", "$p = '" + _G + "X.esp'; Copy-Item a.esp $p"),
    ("powershell", "Set-Content '" + _CFG + "Saves/a.ess' x", "$p = '" + _CFG + "Saves/a.ess'; Set-Content $p x"),
]


_VAR_IDS = ['redirect-esp', 'append-bsa', 'tee-esm', 'cp-esp', 'mv-esp', 'joined-esp', 'redirect-save', 'append-ini', 'append-loadorder', 'redirect-install-file', 'psc-out', 'champollion', 'git-discard', 'ps-set-content-esp', 'ps-out-file-esp', 'ps-redirect-esp', 'ps-copy-item-esp', 'ps-set-content-save']


def _save_targets(command, shell):
    """The saves a command changes (the save policy, saves.py). Its decision depends on whether the save exists on
    THIS disk, so a save pair is compared on the save it finds, not on the decision."""
    r = paths.roots(INSTALL, "", LAYOUT_ENV["declared"])
    a = audit.Audit("")
    a.raw = lambda line: None
    v = shell_rules.decide(command, r, a, cwd="C:/w", shell=shell, env=os.environ)
    return tuple(v.saves.change.targets) if v.saves is not None else ()


@pytest.mark.parametrize("shell,direct,var", _VAR_PAIRS, ids=_VAR_IDS)
def test_a_same_command_literal_target_is_judged_as_its_literal(shell, direct, var):
    saved = _save_targets(direct, shell)
    if "Saves/" in direct:
        assert saved, ("the direct form must be caught, or this pair proves nothing", direct)
        assert _save_targets(var, shell) == saved, var
    else:
        want = decide(direct, cwd="C:/w", shell=shell)
        assert want[0][0] != "allow", ("the direct form must be caught, or this pair proves nothing", direct)
        assert decide(var, cwd="C:/w", shell=shell) == want, var


@pytest.mark.parametrize("shell,command", [
    ("bash", 'echo x > "$P"'),                                     # never assigned here
    ("bash", 'if true; then P=X.esp; fi; echo x > "$P"'),          # assigned in a branch
    ("bash", 'P=a.txt; P=X.esp; echo x > "$P"'),                   # written twice
    ("bash", 'cp a.esp "$P"'),
    ("powershell", "Set-Content $p x"),
    ("powershell", "'x' > $p"),
])
def test_an_unresolvable_variable_target_behaves_as_before(shell, command):
    assert decide(command, cwd="C:/w", shell=shell)[0][0] == "allow", command


def test_the_parser_substitutes_redirect_targets_too():
    from skyguard import deletes
    e = shellparse.parse('P=X.esp; echo x > "$P"', "bash").execs[-1]
    assert ("$P", "X.esp") in e.subst, e
    assert deletes.substituted(e).redirects == ((">", "X.esp"),)
    ps = shellparse.parse("$p = 'X.esp'; 'x' > $p", "powershell").execs[-1]
    assert deletes.substituted(ps).redirects == ((">", "X.esp"),), ps


# ---- fix-limits 9: a file named by inline interpreter code is judged where it lands, as a shell write to it is -------
_PYW = "python -c \"open('{}', 'w').write('x')\""
_NODEW = "node -e \"require('fs').writeFileSync('{}', 'x')\""


@pytest.mark.parametrize("target,cwd,shell_form", [
    ("Data/SKSE/Plugins/x.ini", INSTALL, "echo x > Data/SKSE/Plugins/x.ini"),        # class by where it lands
    ("Data/notes.txt", INSTALL, "echo x > Data/notes.txt"),                          # any file in the install
    ("notes.txt", INSTALL + "/Data", "echo x > notes.txt"),                          # relative to the cwd
    ("C:/w/notes.txt", INSTALL, "echo x > C:/w/notes.txt"),                          # outside: nothing
    ("C:/Temp/x.esp", "C:/w", "echo x > C:/Temp/x.esp"),                            # a plugin's raw write: anywhere
    ("plugins.txt", "C:/w", "echo x > plugins.txt"),                                  # the load order, by name
])
@pytest.mark.parametrize("form", [_PYW, _NODEW], ids=["python", "node"])
def test_an_interpreter_literal_write_is_judged_like_the_shell_write_to_the_same_place(target, cwd, shell_form, form):
    want = decide(shell_form, cwd=cwd)
    assert decide(form.format(target), cwd=cwd) == want, (target, want)


def test_an_interpreter_literal_write_follows_the_cd_before_it():
    want = decide("echo x > Data/notes.txt", cwd=INSTALL)
    assert want[1] == {"S-WRITE-INSTALL"}
    assert decide("cd Data && " + _PYW.format("notes.txt"), cwd=INSTALL) == want


# --------------------------------------------------------------------------- S-WRITE-ROOT (batch-3 ruling)

B3_ROWS = [r for r in (json.loads(x) for x in (REPO / "tests" / "guard_corpus" / "intended.jsonl")
                       .read_text(encoding="utf-8").split("\n") if x.strip()) if r["task"] == "B3"]


def test_there_are_b3_rows_for_each_shape_and_control():
    assert len(B3_ROWS) >= 18 and sum(r["decision"] == "allow" for r in B3_ROWS) >= 8


@pytest.mark.parametrize("row", B3_ROWS, ids=[r["id"] for r in B3_ROWS])
def test_b3_row(row):
    """The S-WRITE-ROOT intended rows, in-process (the corpus test fires the same rows through the real wrapper)."""
    p = row["payload"]
    fill = lambda s: s.replace("<install>", INSTALL)      # noqa: E731
    got, fired = decide(fill(p["tool_input"]["command"]), row["layout"], fill(p.get("cwd", "")),
                        SHELL[p["tool_name"]])
    assert (got, fired) == ((row["decision"], row["text"]), set(row["rules"]))


def test_a_same_command_variable_naming_a_root_file_is_resolved():
    got, fired = decide('T="' + INSTALL + '/openvr_api.dll"; cp x.dll "$T"')
    assert fired == {"S-WRITE-ROOT"} and "openvr_api.dll" in got[1]


def test_a_root_write_in_interpreter_code_is_noted():
    got, fired = decide('python -c "open(\'' + INSTALL + '/dxgi.dll\', \'wb\').write(b\'x\')"')
    assert fired == {"S-WRITE-ROOT"}, (got, fired)


def test_a_write_in_data_keeps_its_own_note_and_gets_no_root_note():
    got, fired = decide('echo x > "' + INSTALL + '/Data/notes.txt"')
    assert fired == {"S-WRITE-INSTALL"}
