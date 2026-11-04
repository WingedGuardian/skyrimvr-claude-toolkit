"""Guard gaps from the Opus review of v4.0.0, each reproduced through shell_rules.decide before it was fixed. Each group pins a
control too: the spelling that was already right, or the ordinary command that must stay allowed. Commands are only judged here,
never run; the saves are empty files in a test folder."""
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, deletes, paths, shell_rules, shellparse, targets, write_rules, writes  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    t = tmp_path_factory.mktemp("opus")
    install, config = (t / "Game").as_posix(), (t / "Docs/My Games/Skyrim VR").as_posix()
    os.makedirs(install + "/Data")
    os.makedirs(config + "/Saves")
    for name in ("main.ess", "main.skse", "b.ess", "b.skse"):
        open(config + "/Saves/" + name, "wb").close()
    env = {"SKYRIM_GAME_ROOT": install, "SKYRIM_CONFIG_DIR": config, "SKYRIM_DOCUMENTS_DIR": config}
    return install, config, paths.roots(install, "", env)


def judge(world, command, shell="bash", cwd=None):
    install, config, roots = world
    a = audit.Audit("")
    a.raw = lambda line: None
    command = command.replace("<I>", install).replace("<C>", config)
    v = shell_rules.decide(command, roots, a, cwd=install if cwd is None else cwd, shell=shell, env=os.environ)
    return v.decision, {rid for rid, _ in a.fired}, bool(getattr(v, "saves", None))


# ---- 1.1: PowerShell gives a positional word to the first position not filled by name
@pytest.mark.parametrize("command", [
    "Copy-Item -Path C:/bk/a.ess '<C>/Saves/b.ess'",
    "Copy-Item -LiteralPath C:/bk/a.ess '<C>/Saves/b.ess'",
    "Rename-Item -Path '<C>/Saves/main.ess' old.ess",
    "Rename-Item -LiteralPath '<C>/Saves/main.ess' old.ess",
])
def test_a_named_path_leaves_the_positional_word_to_the_destination(world, command):
    assert judge(world, command, "powershell", "C:/Temp")[2], command


def test_a_named_archive_path_leaves_the_positional_word_to_the_destination(world):
    assert judge(world, "Expand-Archive -Path x.zip '<C>/Saves'", "powershell", "C:/Temp")[1] == {"S-SAVE-APPROVAL"}


def test_a_positional_rename_of_a_guard_file_is_read(world):
    assert "S-SELF-GUARD" in judge(world, "Rename-Item -Path '<I>/.claude/hooks/x.sh' y.sh", "powershell", "C:/Temp")[1]


@pytest.mark.parametrize("command, destination", [
    ("Copy-Item a b", "b"), ("Copy-Item -Path a b", "b"), ("Copy-Item -LiteralPath a b", "b"), ("Copy-Item a -Destination b", "b"),
    ("Copy-Item -Destination b a", "b"),
])
def test_positional_binding(command, destination):
    e = shellparse.parse(command, "powershell").execs[0]
    assert writes.copy_targets(e) == [(destination, ("a",))]


def test_a_source_bound_by_name_is_not_read_twice():
    e = shellparse.parse("Copy-Item -Path a b", "powershell").execs[0]
    assert shellparse.ps_param(e, "path", 0, slot=("literalpath",)) == ("a",)


# ---- 1.2: `$(pwd)` has one value, the same as `$PWD`
@pytest.mark.parametrize("command, shell", [
    ('rm -rf "$(pwd)/Data"', "bash"), ('rm -rf "$(pwd -P)/Data"', "bash"), ("rm -rf `pwd`/Data", "bash"),
    ('Remove-Item "$(Get-Location)\\Data" -Recurse', "powershell"), ('Remove-Item "$(pwd)\\Data" -Recurse', "powershell"),
])
def test_the_working_folder_by_command_substitution_is_the_working_folder(world, command, shell):
    assert judge(world, command, shell)[1] == {"S-DEL-INSTALL"}


def test_another_substitution_is_still_unknown():
    assert targets.resolve("$(cat f)/a", "C:/x", "bash", {}) is None


# ---- 1.3: a tree restored into the settings folder replaces same-named saves
@pytest.mark.parametrize("command, shell", [
    ('cp -r bk/Saves "<C>/"', "bash"), ('rsync -a bk/ "<C>/"', "bash"), ('tar -xf p.tar -C "<C>"', "bash"),
    ('robocopy bk "<C>" /E', "bash"), ('Expand-Archive x.zip -DestinationPath "<C>"', "powershell"),
])
def test_a_tree_into_the_settings_folder_needs_the_saves_approval(world, command, shell):
    assert judge(world, command, shell, "C:/Temp")[1] == {"S-SAVE-APPROVAL"}


@pytest.mark.parametrize("command, shell", [
    ('cp -r bk/Saves "<C>/"', "bash"), ('tar -xf p.tar -C "<C>"', "bash"), ('rsync -a bk/ "<C>/"', "bash"),
    ('Expand-Archive x.zip -DestinationPath "<C>"', "powershell"),
    ("python -c \"import shutil; shutil.copytree('bk', r'<C>', dirs_exist_ok=True)\"", "bash"),
])
def test_a_tree_into_a_settings_folder_with_no_saves_needs_no_approval(tmp_path, command, shell):
    """Nothing is at risk, and no typed line could approve a wildcard that matches nothing."""
    install, config = (tmp_path / "Game").as_posix(), (tmp_path / "Docs/My Games/Skyrim VR").as_posix()
    os.makedirs(install + "/Data")
    os.makedirs(config + "/Saves")
    roots = paths.roots(install, "", {"SKYRIM_GAME_ROOT": install, "SKYRIM_CONFIG_DIR": config})
    assert judge((install, config, roots), command, shell, "C:/Temp")[0] == "allow"


@pytest.mark.parametrize("command", ['cp Skyrim.ini "<C>/"', 'cp a.ini "<C>/SkyrimPrefs.ini"'])
def test_a_named_settings_file_is_not_a_bulk_save_change(world, command):
    assert not judge(world, command, "bash", "C:/Temp")[2]


# ---- 1.4: a listing of Data piped into a delete
@pytest.mark.parametrize("command, shell", [
    ("ls Data/* | xargs rm -rf", "bash"), ("find Data -type f | xargs rm", "bash"),
    ("Get-ChildItem Data -Recurse | Remove-Item -Recurse -Force", "powershell"),
    ("Get-ChildItem Data | % { Remove-Item $_ }", "powershell"),
])
def test_a_listing_of_data_piped_into_a_delete_is_refused(world, command, shell):
    assert judge(world, command, shell)[0] == "deny"


def test_a_local_listing_piped_into_a_delete_stays_the_priced_limit(world):
    assert judge(world, "ls *.bak | xargs rm")[0] == "allow"


# ---- 1.5: aliases, and a delete Python launches
@pytest.mark.parametrize("code", [
    "import shutil as s; s.rmtree('Data')",
    "from shutil import rmtree as r; r('Data')",
    "from pathlib import Path as P; P('Data/x.dll').unlink()",
    "import pathlib as pl; pl.Path('Data/x.dll').unlink()",
    "import subprocess; subprocess.run('rm -rf Data', shell=True)",
    "import subprocess as sp; sp.run(['rm', '-rf', 'Data'])",
    "import os; os.system('rm -rf Data')",
    "from shutil import rmtree as r; r('<I>/Data')",
])
def test_an_aliased_or_launched_python_delete_is_read(world, code):
    assert judge(world, 'python -c "' + code + '"')[1] == {"S-DEL-INSTALL"}


@pytest.mark.parametrize("code", [
    "import subprocess; subprocess.run('git status', shell=True)",
    "import subprocess; subprocess.run(['echo', 'rm', 'Data'])",
    "import subprocess; subprocess.run('rm -rf Data; echo', shell=True)",
    "from foo import remove as r; r('Data')",
])
def test_other_python_calls_are_not_the_literal_delete_reader(code):
    assert deletes.interpreter_delete_targets(code, True) == []


# ---- 1.6: a move that stays where it was is no delete
@pytest.mark.parametrize("code", [
    "python -c \"import os; os.replace('Data/x.tmp','Data/x.ini')\"",
    "python -c \"import shutil; shutil.move('Data/a.txt','Data/b.txt')\"",
    "python -c \"from pathlib import Path; Path('Data/a').rename('Data/b')\"",
    "node -e \"require('fs').renameSync('Data/a','Data/b')\"",
])
def test_a_move_inside_the_install_is_no_delete(world, code):
    assert judge(world, code)[0] == "allow"


@pytest.mark.parametrize("code", [
    "python -c \"import os; os.replace('Data/x.tmp','C:/tmp/x.ini')\"",
    "python -c \"import shutil; shutil.move('Data/a.txt','C:/tmp/b.txt')\"",
    "python -c \"from pathlib import Path; Path('Data/a').rename('C:/tmp/b')\"",
    "node -e \"require('fs').renameSync('Data/a','C:/tmp/b')\"",
])
def test_a_move_out_of_the_install_still_is(world, code):
    assert judge(world, code)[1] == {"S-DEL-INSTALL"}


# ---- 1.7: a copy of a plugin by literal name is judged as cp, not as a raw write
@pytest.mark.parametrize("code, rules", [
    ("import shutil; shutil.copy2('Data/My.esp','C:/bk/My.esp')", set()),
    ("import shutil; shutil.copy('build/My.esp','Data/My.esp')", {"S-COPY-PLUGIN"}),
])
def test_a_plugin_copy_is_advice_not_a_refusal(world, code, rules):
    assert judge(world, 'python -c "' + code + '"')[:2] == ("allow", rules)


def test_a_raw_plugin_write_is_still_refused(world):
    assert judge(world, "python -c \"open('Data/My.esp','wb').write(b'x')\"")[1] == {"S-WRITE-PLUGIN"}


# ---- 1.8: data on stdin to a named script is not code
@pytest.mark.parametrize("command", ["python tools/parse.py <<'EOF'\nsome: yaml, not python\nEOF", 'python x.py <<< "a b"'])
def test_a_script_reading_its_input_runs(world, command):
    assert judge(world, command, cwd="C:/Temp")[0] == "allow"


@pytest.mark.parametrize("command", ["python - <<'EOF'\nnot python at all\nEOF", "python -i x.py <<'EOF'\nnot python at all\nEOF"])
def test_a_body_python_runs_as_code_must_still_parse(world, command):
    with pytest.raises(ValueError):
        judge(world, command, cwd="C:/Temp")


# ---- 1.9: a save path the guard cannot resolve is refused, as saves.py says; a run-time value says nothing
def test_an_unresolvable_literal_save_path_is_refused(world):
    command = 'cp x.ess "C:/Users/Foo [x]/Documents/My Games/Skyrim VR/Saves/a.ess"'
    assert judge(world, command, cwd="C:/Temp")[1] == {"S-SAVE-UNKNOWN"}


def test_a_run_time_folder_is_a_known_limit(world):
    assert judge(world, 'cp bk.ess "$SAVES/main.ess"', cwd="C:/Temp")[0] == "allow"
    assert judge(world, 'cp bk.ess "$X/My Games/Skyrim VR/Saves/main.ess"', cwd="C:/Temp")[0] == "allow"


def test_the_write_tool_refuses_a_short_name_save_path(world):
    a = audit.Audit("")
    a.raw = lambda line: None
    v = write_rules.decide("C:/Users/MOONA~1/Documents/My Games/Skyrim VR/Saves/a.ess", a, roots=world[2], cwd="C:/Temp", env=os.environ)
    assert v.decision == "deny"


# ---- 1.10: Python copies into the Saves folder
@pytest.mark.parametrize("code, rule", [
    ("import shutil; shutil.copy('C:/bk/main.ess', r'<C>/Saves/')", "S-SAVE-BACKUP"),
    ("import shutil; shutil.move('C:/bk/main.ess', r'<C>/Saves/')", "S-SAVE-BACKUP"),
    ("import shutil; shutil.copytree('bk', r'<C>/Saves', dirs_exist_ok=True)", "S-SAVE-APPROVAL"),
    ("import shutil; shutil.copytree('bk', r'<C>', dirs_exist_ok=True)", "S-SAVE-APPROVAL"),
])
def test_a_python_copy_into_saves_gets_the_save_policy(world, code, rule):
    assert rule in judge(world, 'python -c "' + code + '"', cwd="C:/Temp")[1]


# ---- 1.11: programs that run or delete
@pytest.mark.parametrize("command", [
    "gsudo rm -rf Data", "pkexec rm -rf Data", "runuser -u x -- rm -rf Data", "systemd-run rm -rf Data", "chrt -f 1 rm -rf Data",
    "prlimit rm -rf Data", "toybox rm -rf Data", "trash Data", "trash-put Data", "gio trash Data",
    'forfiles /p Data /s /c "cmd /c del @path"',
])
def test_a_wrapped_or_other_delete_is_read(world, command):
    assert judge(world, command)[1] == {"S-DEL-INSTALL"}


@pytest.mark.parametrize("command", ['forfiles /p Data /c "cmd /c echo @path"', "gio info Data"])
def test_their_read_only_forms_pass(world, command):
    assert judge(world, command)[0] == "allow"


# ---- 1.12, 1.14, 1.16 to 1.20, 1.22
def test_git_bash_xcopy_switches_are_switches(world):
    assert judge(world, "xcopy bk '<C>/Saves' //E //Y", cwd="C:/Temp")[1] == {"S-SAVE-APPROVAL"}


def test_rsync_of_one_named_save_is_one_save(world):
    install, config, roots = world
    command = "rsync a.ess '{}/Saves/b.ess'".format(config)
    found = writes.scan(command, shellparse.parse(command, "bash"), roots, cwd="C:/Temp", env=os.environ).save_targets
    assert [t.replace("\\", "/").lower() for t, _op in found] == [(config + "/Saves/b.ess").lower()]
    assert judge(world, "rsync a.ess '<C>/Saves/b.ess'", cwd="C:/Temp")[1] == {"S-SAVE-BACKUP"}
    assert judge(world, "rsync bk/x.ess '<C>/Saves/'", cwd="C:/Temp")[0] == "allow"
    assert judge(world, "rsync -a bk/ '<C>/Saves/'", cwd="C:/Temp")[1] == {"S-SAVE-APPROVAL"}


@pytest.mark.parametrize("command", ['f() { if true; then shift; fi; rm -rf "$1"; }; f /tmp/x Data',
                                     'f() { builtin shift; rm -rf "$1"; }; f /tmp/x Data'])
def test_a_shift_after_a_keyword_moves_the_arguments(world, command):
    assert judge(world, command)[0] == "deny"


def test_the_native_adapter_is_read_past_a_valued_python_option(world):
    assert judge(world, "python -X utf8 tools/resaver_native.py clean a.ess b.ess --apply")[1] == {"S-SAVE-BACKUP"}


def test_file_replace_takes_its_source_away(world):
    assert judge(world, "[IO.File]::Replace('<I>/Data/x.txt','C:/tmp/y',$null)", "powershell", "C:/Temp")[1] == {"S-DEL-INSTALL"}
    assert judge(world, "[IO.File]::Replace('<I>/Data/a','<I>/Data/b',$null)", "powershell", "C:/Temp")[0] == "allow"


def test_an_apostrophe_in_a_double_quoted_default_parses(world):
    assert shellparse.parse('echo "${n:-don\'t}"', "bash").ok
    assert judge(world, 'echo "${name:-don\'t}"; rm -f /tmp/x')[0] == "allow"


def test_a_documents_folder_inside_a_proton_prefix_is_protected(world):
    command = "rm -rf /home/u/.local/share/Steam/steamapps/compatdata/1/pfx/drive_c/users/s/Documents"
    assert judge(world, command, cwd="/tmp")[1] == {"S-DEL-CONFIG"}


def test_a_folder_merely_named_documents_stays_allowed(world):
    assert judge(world, "rm -rf /home/u/build/documents", cwd="/tmp")[0] == "allow"


def test_python_prose_beside_a_root_is_no_delete(world):
    body = "python - <<'EOF'\nopen(r'<I>/Data/x.txt').read()\nprint('file delete failed')\nEOF"
    assert judge(world, body, cwd="C:/Temp")[0] == "allow"


def test_other_interpreters_still_read_their_delete_calls(world):
    # A built name: the literal reader cannot read it, so only the delete word beside the root refuses it.
    assert judge(world, "Rscript -e \"file.remove(paste0('<I>/Data', '/x.txt'))\"", cwd="C:/Temp")[0] == "deny"


# ---- 1.13: the whitelist reads the path after `..`
def test_a_whitelisted_folder_climbed_out_of_is_not_whitelisted(world):
    install, config, roots = world
    a = audit.Audit("")
    a.raw = lambda line: None
    path = install + "/.claude/backups/../../../Docs/My Games/Skyrim VR/Saves/main.ess"
    assert write_rules.decide(path, a, roots=roots, cwd=install, env=os.environ).saves
