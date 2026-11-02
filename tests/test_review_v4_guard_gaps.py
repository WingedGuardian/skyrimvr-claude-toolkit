"""v4.0.0 release review, 2026-11-02: what the guard counted as a save change or a delete. Each row was allowed at
bc246b8 (reproduced through engine.evaluate_shell), and each control pins the ordinary command that must stay allowed.
Commands are only judged here, never run."""
import shutil
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
def box():
    base = Path(tempfile.mkdtemp(prefix="sggap")).resolve()
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
    shutil.rmtree(base, ignore_errors=True)


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
    "f() { cd " + G + "; }; \"f\"; rm -rf Data",
], ids=["redefined-after-call", "paren-body", "shift", "set-args", "quoted-call"])
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
], ids=lambda x: x[:42] if isinstance(x, str) else str(x))
def test_ordinary_commands_stay_allowed(box, cmd, shell, want):
    v = box(cmd, shell)
    assert v.decision == "allow" and _pairs(v) == want, v
