"""v4.0.0 release review, 2026-11-02: what the guard counted as a save change or a delete. Each row was allowed at
bc246b8 (reproduced through engine.evaluate_shell), and each control pins the ordinary command that must stay allowed.
Commands are only judged here, never run."""
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, codes, engine, paths, saves  # noqa: E402
from skyguard.request import Request  # noqa: E402

EXT, CO = ".ess", ".skse"


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    base = tmp_path_factory.mktemp("gaps").resolve()
    inst, sv = base / "Game", base / "My Games" / "Skyrim VR" / "Saves"
    for d in (inst / "Data", inst / "tools", sv, base / "bk", base / "other"):
        d.mkdir(parents=True)
    for n in "abcd":
        (sv / (n + EXT)).write_bytes(b"x")
        (sv / (n + CO)).write_bytes(b"y")
        (base / "bk" / (n + EXT)).write_bytes(b"z")
    (inst / "Data" / "Skyrim.esm").write_bytes(b"m")
    B = base.as_posix()
    (inst / "skyrim-paths.env").write_text(
        "SKYRIM_GAME_ROOT='{}/Game'\nSKYRIM_CONFIG_DIR='{}/My Games/Skyrim VR'\n".format(B, B), encoding="utf-8")
    env = paths.read_install(inst.as_posix(), {})
    roots = paths.roots(inst.as_posix(), inst.as_posix(), env)

    def judge(cmd, shell="bash", cwd=None):
        text = cmd.replace("$S", '"{}/My Games/Skyrim VR/Saves"'.format(B)).replace("$B", B).replace("$E", EXT)
        req = Request(agent="claude", event="pre_tool", kind="shell", shell=shell, command=text,
                      cwd=cwd or inst.as_posix(), session_id="s", tool_name="Bash")
        return engine.evaluate_shell(req, roots, audit.Audit(inst.as_posix()))
    yield judge


def _pairs(v):
    return len(saves._existing(saves.count(v.saves.change))) if v.saves is not None else 0


# ---- more than two saves must reach the typed approval, whatever spelling names them
@pytest.mark.parametrize("cmd,shell", [
    ("Move-Item -Path $S/a$E,$S/b$E,$S/c$E -Destination $B/bk", "powershell"),
    ("Set-Content $S/a$E,$S/b$E,$S/c$E x", "powershell"),
    ("Copy-Item $B/bk/a$E,$B/bk/b$E,$B/bk/c$E $S -Force", "powershell"),
    ("rm -f $S/{a,b,c}$E", "bash"),
    ("D=$S; rm -f \"$D\"/{a,b,c}$E", "bash"),
    ("rm -f $S/{a..c}$E", "bash"),
    ("cp $B/bk/* $S/", "bash"),
    ("cp -r $B/bk/. $S", "bash"),
    ("mv $B/bk/* $S/", "bash"),
    ("rsync -a $B/bk/ $S/", "bash"),
    ("tar -xf $B/x.tar -C $S", "bash"),
    ("unzip -o $B/x.zip -d $S", "bash"),
    ("Expand-Archive $B/x.zip -DestinationPath $S -Force", "powershell"),
    ("Copy-Item $B/bk/* $S -Force", "powershell"),
    ("robocopy $B/bk $S", "powershell"),
], ids=lambda x: x if len(x) < 12 else x[:42])
def test_a_bulk_save_change_needs_the_typed_approval(box, cmd, shell):
    v = box(cmd, shell)
    assert v.decision == "deny" and codes.classify(v).code == "shell.S-SAVE-APPROVAL", v


def test_extracting_from_inside_the_save_folder_counts_too(box):
    v = box("cd $S && tar -xf $B/x.tar")
    assert v.decision == "deny" and codes.classify(v).code == "shell.S-SAVE-APPROVAL", v


# ---- piped input and member calls reach what the earlier stage names
@pytest.mark.parametrize("cmd", [
    "Get-ChildItem $S | Clear-Content",
    "Get-ChildItem $S | Set-Content -Value x",
    "Get-ChildItem $S | Add-Content -Value x",
    "Get-ChildItem $S | Rename-Item -NewName x",
    "Get-ChildItem $S | % Delete",
    "Get-ChildItem $S | ForEach-Object Delete",
    "Get-ChildItem $S | ForEach-Object -MemberName MoveTo x",
    "Get-ChildItem $B/Game/Data -Recurse | % Delete",
    "Get-ChildItem $B/Game/Data | Rename-Item -NewName x",
    "(Get-Item $S/a$E).MoveTo('x')",
    "(Get-Item $B/Game/Data).MoveTo('x')",
], ids=lambda x: x[:42])
def test_piped_and_member_changes_are_refused(box, cmd):
    assert box(cmd, "powershell").decision == "deny"


# ---- literal interpreter moves and copies
@pytest.mark.parametrize("code", [
    "import shutil; shutil.move('$B/Game/Data', '$B/x')",
    "import os; os.rename('$B/Game/Data', '$B/x')",
    "import pathlib; pathlib.Path('$B/Game/Data').rename('$B/x')",
], ids=lambda x: x[:42])
def test_literal_python_moves_of_data_are_refused(box, code):
    assert box('python -c "{}"'.format(code)).decision == "deny"


@pytest.mark.parametrize("cmd", [
    "python -c \"import os; os.replace('$B/x$E', '$B/My Games/Skyrim VR/Saves/a$E')\"",
    "python -c \"import shutil; shutil.copy('$B/x$E', '$B/My Games/Skyrim VR/Saves/a$E')\"",
    "python -c \"import shutil; shutil.move('$B/My Games/Skyrim VR/Saves/a$E', '$B/x$E')\"",
    "node -e \"require('fs').copyFileSync('$B/x$E', '$B/My Games/Skyrim VR/Saves/a$E')\"",
    "node -e \"require('fs').renameSync('$B/My Games/Skyrim VR/Saves/a$E', '$B/x$E')\"",
], ids=lambda x: x[:42])
def test_literal_interpreter_changes_of_one_save_are_backed_up(box, cmd):
    v = box(cmd)
    assert v.decision == "allow" and _pairs(v) == 1, v


# ---- other save-changing programs
@pytest.mark.parametrize("cmd", ["shred $S/a$E", "install -m644 $B/x $S/a$E", "ln -sf $B/x $S/a$E"],
                         ids=lambda x: x[:20])
def test_other_writers_of_one_save_are_backed_up(box, cmd):
    v = box(cmd)
    assert v.decision == "allow" and _pairs(v) == 1, v


# ---- same-command functions (bash)
G = "$B/Game"


@pytest.mark.parametrize("cmd", [
    "f() { cd " + G + "; }; f; f() { cd /tmp; }; rm -rf Data",
    "f() ( rm -rf Data ); cd " + G + "; f",
    "g() { shift; cd \"$1\"; }; g x " + G + "; rm -rf Data",
    "g() { set -- " + G + "; cd \"$1\"; }; g; rm -rf Data",
    "g() { shift 2; cd \"$1\"; }; g x y " + G + "; rm -rf Data",
    "f() { cd " + G + "; }; \"f\"; rm -rf Data",
], ids=["redefined-after-call", "paren-body", "shift", "set-args", "shift-count", "quoted-call"])
def test_function_forms_cannot_hide_a_delete_in_the_install(box, cmd):
    assert box(cmd, cwd=str(Path(tempfile.gettempdir()).as_posix())).decision == "deny"


# ---- controls: none of these changes a save or the install's folders
@pytest.mark.parametrize("cmd,shell,want", [
    ("Move-Item -Path $S/a$E,$S/b$E -Destination $B/bk", "powershell", 2),
    ("Set-Content $S/a$E,$S/b$E x", "powershell", 2),
    ("rm -f $S/{a,b}$E", "bash", 2),
    ("Set-Content -Path $B/other/x.txt -Value a,b,c", "powershell", 0),
    ("cp $B/other/notes.txt $S/", "bash", 0),
    ("cp $B/bk/*.txt $S/", "bash", 0),
    ("tar -xf $B/x.tar -C $B/other", "bash", 0),
    ("cp $B/bk/* $B/other/", "bash", 0),
    ("rm -f $B/other/{x,y}.txt", "bash", 0),
    ("echo {a,b}", "bash", 0),
    ("echo '{a,b,c}$E'", "bash", 0),
    ("Get-ChildItem $S | Select-Object Name", "powershell", 0),
    ("Get-ChildItem $B/other | Clear-Content", "powershell", 0),
    ("Get-ChildItem $B/other | % Delete", "powershell", 0),
    ("Get-ChildItem $S | ForEach-Object Name", "powershell", 0),
    ("f() { cd /tmp; }; f; ls", "bash", 0),
    ("f() ( ls ); f", "bash", 0),
    ("g() { cd \"$1\"; ls; }; g /tmp", "bash", 0),
    ("w() { local f=\"$1\"; shift; printf '%s' \"$1\" > \"$B/other/$f\"; }; w a.txt hi", "bash", 0),
], ids=lambda x: x[:42] if isinstance(x, str) else str(x))
def test_ordinary_commands_stay_allowed(box, cmd, shell, want):
    v = box(cmd, shell)
    assert v.decision == "allow" and _pairs(v) == want, v


def test_a_helper_that_shifts_still_parses():
    """Priced 2026-11-02: refusing every shift made 39 real helper functions unparsable."""
    from skyguard import shellparse
    assert shellparse.parse('w() { local f="$1"; shift; printf x > "$f"; }; w a b').ok


def test_shift_moves_the_arguments_for_the_text_after_it_only():
    from skyguard.shellparse import _call_body
    assert _call_body('echo "$1"; shift; echo "$1"', ("a", "b"), (False, False)) == 'echo "a"; shift; echo "b"'
    assert _call_body('shift 2; echo "$1"', ("a", "b", "c"), (False,) * 3) == 'shift 2; echo "c"'
    from skyguard.shellparse import ParseError
    for body in ('[ -n x ] && shift; echo "$1"', 'set -- z; echo "$1"', 'shift $2; echo "$1"', '( shift ); echo "$1"',
                 'true | shift; echo "$1"'):
        with pytest.raises(ParseError):
            _call_body(body, ("a", "b"), (False,) * 2)


def test_a_quoted_brace_stays_one_word():
    from skyguard import shellparse
    assert shellparse.parse("rm '{a,b}.txt'").execs[0].argv == ("{a,b}.txt",)
    assert shellparse.parse("rm {a,b}.txt").execs[0].argv == ("a.txt", "b.txt")
    assert shellparse.parse("rm \"${x}\"/{a,b}").execs[0].argv[-1].endswith("/b")


def test_saves_under_a_moved_documents_folder_are_protected_with_mo2_profile_inis(tmp_path):
    inst, profile, docs = tmp_path / "Game", tmp_path / "MO2/profiles/P", tmp_path / "D/Dokumente/My Stuff/Skyrim VR"
    for d in (inst, profile, docs / "Saves"):
        d.mkdir(parents=True)
    (inst / "skyrim-paths.env").write_text(
        "SKYRIM_GAME_ROOT='{}'\nSKYRIM_CONFIG_DIR='{}'\nSKYRIM_DOCUMENTS_DIR='{}'\n".format(
            inst.as_posix(), profile.as_posix(), docs.as_posix()), encoding="utf-8")
    env = paths.read_install(inst.as_posix(), {})
    roots = paths.roots(inst.as_posix(), inst.as_posix(), env)
    from skyguard import writes
    assert writes.is_save((docs / "Saves" / ("a" + EXT)).as_posix().lower(), roots)


def test_setup_records_the_documents_folder():
    text = (REPO / "setup.sh").read_text(encoding="utf-8")
    assert "printf 'SKYRIM_DOCUMENTS_DIR=%s" in text
    assert 'DOCUMENTS_GAME_DIR="$CONFIG_DIR"' in text


def test_a_folder_below_saves_counts_and_a_parent_called_saves_does_not(tmp_path):
    from skyguard import writes
    base = tmp_path / "Saves" / "x"
    inst, cfg = base / "Game", base / "My Games" / "Skyrim VR"
    (cfg / "Saves" / "Sub").mkdir(parents=True)
    inst.mkdir()
    (inst / "skyrim-paths.env").write_text(
        "SKYRIM_GAME_ROOT='{}'\nSKYRIM_CONFIG_DIR='{}'\n".format(inst.as_posix(), cfg.as_posix()), encoding="utf-8")
    roots = paths.roots(inst.as_posix(), inst.as_posix(), paths.read_install(inst.as_posix(), {}))
    assert writes.is_save_folder((cfg / "Saves").as_posix(), roots)
    assert writes.is_save_folder((cfg / "Saves" / "Sub").as_posix(), roots)
    assert not writes.is_save_folder((cfg / "Saves.old").as_posix(), roots)
    assert not writes.is_save_folder(cfg.as_posix(), roots)


# ---- review round 2: spellings the first fixes lost or missed
@pytest.mark.parametrize("cmd", [
    "f() { [ -n \"$2\" ] && shift; rm -rf \"$1\"; }; f Data",
    "f() { if [ -n \"$2\" ]; then shift; fi; rm -rf \"$1\"; }; f Data",
    "f() { set -- \"$@\"; rm -rf \"$1\"; }; f Data",
    "f() { echo shift; rm -rf \"$1\"; }; f Data",
    "f() { echo shift; rm -rf \"$@\"; }; f Data",
    "f() { shift $2; rm -rf \"$1\"; }; f x Data",
    "f() { ( shift ); rm -rf \"$1\"; }; f Data",
    "f() { true | shift; rm -rf \"$1\"; }; f Data",
], ids=["and-shift", "if-shift", "set-args", "echo-shift", "echo-shift-all", "shift-var", "subshell", "pipe"])
def test_round_two_function_forms_stay_refused_in_the_install(box, cmd):
    assert box(cmd).decision == "deny"           # the fixture's default cwd is the install


def test_a_brace_list_past_the_cap_keeps_its_last_member(box):
    cmd = "rm -rf {" + ",".join("a%02d" % i for i in range(65)) + ",$B/Game/Data}"
    assert box(cmd, cwd=Path(tempfile.gettempdir()).as_posix()).decision == "deny"


@pytest.mark.parametrize("cmd", [
    "$d='$B/other/x.txt'; Set-Content $d,$B/Game/Data/Skyrim.esm x",
    "$d='$B/other/x.txt'; Move-Item $d,$B/Game/Data/Skyrim.esm $B/bk",
], ids=["set-content", "move-item"])
def test_a_comma_list_keeps_its_group_through_a_variable(box, cmd):
    assert box(cmd, "powershell").decision == "deny"


def test_three_saves_through_a_variable_are_counted(box):
    v = box("$d='$B/My Games/Skyrim VR/Saves/a$E'; Move-Item $d,$S/b$E,$S/c$E $B/bk", "powershell")
    assert v.decision == "deny" and codes.classify(v).code == "shell.S-SAVE-APPROVAL", v


@pytest.mark.parametrize("cmd,shell", [
    ("xcopy $B/bk $S /E", "powershell"),
    ("xcopy $B/bk/* $S", "bash"),
    ("tar -f $B/x.tar -C $S -x", "bash"),
    ("tar -C $S --verbose --no-same-owner --extract -f $B/x", "bash"),
    ("rsync -a $B/bk/ $S/ --exclude x", "bash"),
], ids=lambda x: x[:30])
def test_round_two_bulk_spellings_need_the_typed_approval(box, cmd, shell):
    v = box(cmd, shell)
    assert v.decision == "deny" and codes.classify(v).code == "shell.S-SAVE-APPROVAL", v


def test_a_linear_shift_is_still_read_precisely():
    from skyguard.shellparse import _call_body
    assert _call_body('echo shift; echo "$1"', ("a",), (False,)) == 'echo shift; echo "a"'
