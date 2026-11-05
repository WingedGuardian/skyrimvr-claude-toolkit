"""What the independent review of commit 35f8cd0 found, each reproduced before it was fixed. Guard commands are only judged here,
never run; the saves are empty files in a test folder."""
import importlib.util
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, shellparse, writes  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    t = tmp_path_factory.mktemp("f35")
    install, config = (t / "Game").as_posix(), (t / "Docs/My Games/Skyrim VR").as_posix()
    os.makedirs(install + "/Data")
    os.makedirs(config + "/Saves")
    for name in ("main.ess", "main.skse"):
        open(config + "/Saves/" + name, "wb").close()
    env = {"SKYRIM_GAME_ROOT": install, "SKYRIM_CONFIG_DIR": config, "SKYRIM_DOCUMENTS_DIR": config}
    return install, config, paths.roots(install, "", env)


def judge(world, command, cwd=None):
    install, config, roots = world
    a = audit.Audit("")
    a.raw = lambda line: None
    command = command.replace("<I>", install).replace("<C>", config)
    v = shell_rules.decide(command, roots, a, cwd=install if cwd is None else cwd, shell="bash", env=os.environ)
    return v.decision, {rid for rid, _ in a.fired}


# ---- M1: rsync -R writes below the destination, so a named file is not known to land where it looks

@pytest.mark.parametrize("command", [
    "rsync -R Saves/main.ess '<C>/'", "rsync -aR Saves/main.skse '<C>/'", "rsync --relative Saves/main.ess '<C>/'",
])
def test_a_relative_rsync_into_the_settings_folder_needs_the_saves_approval(world, command):
    assert judge(world, command, os.path.dirname(world[0]))[1] == {"S-SAVE-APPROVAL"}


def test_a_plain_rsync_of_a_named_settings_file_stays_allowed(world):
    assert judge(world, "rsync a.ini '<C>/'", os.path.dirname(world[0]))[0] == "allow"


# ---- M2: `)` counts as command position only in a case arm

@pytest.mark.parametrize("command", [
    'f() { # (1) shift\n rm -rf "$1"; }; f Data /tmp/x',
    'f() { echo $(date) shift; rm -rf "$1"; }; f Data /tmp/x',
    'f() { echo "(1) shift ; ok"; rm -rf "$1"; }; f Data /tmp/x',
])
def test_a_shift_after_a_closing_bracket_that_is_no_case_arm_moves_nothing(world, command):
    assert judge(world, command)[0] == "deny"


def test_a_real_shift_still_moves_the_arguments(world):
    assert judge(world, 'f() { shift; rm -rf "$1"; }; f Data /tmp/x', os.path.dirname(world[0]))[0] == "allow"


# ---- M3 and L4: wrapper options

@pytest.mark.parametrize("command", [
    "runuser -l -c 'rm -rf Data' root", "runuser --login -c 'rm -rf Data' root", "runuser -lc 'rm -rf Data' root",
    "runuser --command='rm -rf Data' root", "runuser -l root -c 'rm -rf Data'",
    "systemd-run --on-active 5 rm -rf Data", "systemd-run --on-calendar daily rm -rf Data",
    "systemd-run --service-type oneshot rm -rf Data", "systemd-run --timer-property X rm -rf Data",
    "systemd-run --path-property X rm -rf Data",
])
def test_the_wrapped_command_is_read_past_its_options(world, command):
    assert "S-DEL-INSTALL" in judge(world, command)[1]


# ---- the same pipe into python with an interpreter option

def test_python_with_an_option_value_still_reads_its_program_from_a_pipe(world):
    code = "import shutil; shutil.rmtree('Data')"
    plain = judge(world, 'echo "{}" | python'.format(code))
    assert plain[0] == "deny" and judge(world, 'echo "{}" | python -X utf8'.format(code)) == plain


def test_a_script_named_after_the_option_value_still_takes_the_pipe_as_data(world):
    assert judge(world, 'echo hello | python -X utf8 tool.py')[0] == "allow"


# ---- M4 and M5: a 3.x path

def _migrate():
    spec = importlib.util.spec_from_file_location("migrate_3x_f35", REPO / "tools" / "migrate-3x.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("verdict, carried, recorded, want", [
    ("edited", {"CONFIG_DIR": "a"}, {"CONFIG_DIR": "a"}, False),   # a note was added; the record says setup wrote this path
    ("edited", {"CONFIG_DIR": "b"}, {"CONFIG_DIR": "a"}, True),    # the record says another path: the user changed it
    ("edited", {"CONFIG_DIR": "b"}, {}, True),                     # no record: it may be a correction, so it is kept
    ("shipped", {"CONFIG_DIR": "b"}, {}, False),
    ("edited", {"CONFIG_DIR": "a", "MO2_MODS": "m"}, {"CONFIG_DIR": "a", "GAME_ROOT": "g"}, True),   # no record of MO2's path
])
def test_a_path_counts_as_set_by_hand_only_when_the_record_does_not_say_setup_wrote_it(verdict, carried, recorded, want):
    assert _migrate().path_set_by_hand(verdict, carried, recorded) is want


# ---- M6, L1, L2: mod-install --update (see also tests/test_mod_install.py)

def test_oar_user_settings_are_not_treated_as_the_mods_data():
    spec = importlib.util.spec_from_file_location("mod_install_f35", REPO / "tools" / "mod-install.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    base = "meshes/actors/character/animations/OpenAnimationReplacer/Pack/Sub/"
    assert m.MOD_DATA_CONFIG.search(base + "config.json")
    assert not m.MOD_DATA_CONFIG.search(base + "user.json")
    assert not m.MOD_DATA_CONFIG.search("meshes/actors/character/animations/OpenAnimationReplacer/user.json")


# ---- the review of 9190266

@pytest.mark.parametrize("command", ["cp --parents Saves/main.ess '<C>/'", "cp --parents -t '<C>' Saves/main.skse"])
def test_cp_parents_into_the_settings_folder_needs_the_saves_approval(world, command):
    assert judge(world, command, os.path.dirname(world[0]))[1] == {"S-SAVE-APPROVAL"}


@pytest.mark.parametrize("command", ["rsync -R a.ini '<C>/'", "scp -R a.ini '<C>/'", "cp --parents a.ini '<C>/'"])
def test_a_relative_copy_of_a_top_level_file_is_no_bulk_save_change(world, command):
    assert judge(world, command, os.path.dirname(world[0]))[0] == "allow"


@pytest.mark.parametrize("code", [
    "from os import remove; remove('<C>/Saves/main.ess')",
    "from send2trash import send2trash; send2trash('<C>/Saves/main.ess')",
])
def test_a_python_delete_of_a_save_by_a_bare_name_is_read(world, code):
    assert judge(world, 'python -c "{}"'.format(code))[1] & {"S-SAVE-BACKUP", "S-DEL-CONFIG"}


def test_a_python_delete_of_an_ordinary_file_adds_no_save_rule(world):
    assert judge(world, """python -c "from os import remove; remove('x.txt')" """, os.path.dirname(world[0]))[0] == "allow"


def test_an_option_value_stops_at_a_file_name_for_perl(world):
    assert judge(world, """echo "unlink('Data/x.txt')" | perl -W tool.pl""")[0] == "allow"


# ---- a destination named by keyword is the same destination

def test_a_keyword_destination_is_read_like_a_positional_one():
    keyword = writes.text_write_targets("import os; os.replace(src='a', dst='b')", python_literals=True)
    assert keyword == writes.text_write_targets("import os; os.replace('a', 'b')", python_literals=True) and "b" in keyword


# ---- a twin the piped-listing rule came to shadow: a pipe is now caught earlier, and this clause still decides this shape

def test_an_unresolved_move_source_with_a_protected_root_named_is_refused(world):
    assert judge(world, 'mv "$X" /elsewhere/y; ls "<I>/Data"', "/elsewhere")[1] == {"S-DEL-UNRESOLVED"}



# ---- the final review of the release range

@pytest.mark.parametrize("command", [
    "cp --parents --target-directory='<C>' Saves/main.ess", "cp --target-directory='<C>' --parents ./Saves/main.ess",
    "cp --parents -t'<C>' Saves/main.ess", "cp --parent Saves/main.ess '<C>/'", "cp --pa Saves/main.skse '<C>/'",
])
def test_every_spelling_of_a_parents_copy_into_the_settings_folder_needs_the_approval(world, command):
    assert judge(world, command, os.path.dirname(world[0]))[1] == {"S-SAVE-APPROVAL"}


@pytest.mark.parametrize("command", [
    "cp --parents -t '<C>' a.ini", "cp --parents '<C>/a.ini' '<C>/'", "rsync -e /usr/bin/ssh -R a.ini '<C>/'",
    "rsync -t a.ini '<C>/'",
])
def test_a_named_settings_file_is_no_bulk_save_change_however_it_is_copied(world, command):
    assert judge(world, command, os.path.dirname(world[0]))[0] == "allow"


def test_to_rsync_t_keeps_times_and_names_no_destination(world):
    assert [writes.copy_targets(e) for e in shellparse.parse("rsync -t a.ini b/", "bash").execs] == [[("b/", ("a.ini",))]]


@pytest.mark.parametrize("code", [
    "from os import remove; from pathlib import Path; remove(Path('<C>/Saves/main.ess'))",
    "import os as o; from pathlib import Path; o.remove(Path('<C>/Saves/main.ess'))",
    "from os import remove; remove(path='<C>/Saves/main.ess')",
    "import send2trash; send2trash.send2trash('<C>/Saves/main.ess')",
    "from send2trash import send2trash; send2trash(['<C>/Saves/main.ess'])",
    "from send2trash import send2trash; send2trash(paths='<C>/Saves/main.ess')",
])
def test_every_spelling_of_a_python_save_delete_is_read(world, code):
    assert judge(world, 'python -c "{}"'.format(code))[1] & {"S-SAVE-BACKUP", "S-DEL-CONFIG"}


@pytest.mark.parametrize("options", ["-W ignore::foo.Bar", "-W ignore::foo.Bar -", "-X pycache_prefix=.cache", "-X utf8"])
def test_a_python_option_value_with_a_dot_still_reads_its_program_from_a_pipe(world, options):
    assert judge(world, """echo "import os; os.remove('Data/x.esp')" | python {}""".format(options))[0] == "deny"


@pytest.mark.parametrize("argv, want", [
    (["-i"], True), (["-Bi"], True), (["-ic", "x"], True), (["-i", "-c", "x"], True), (["-W", "x", "-i"], True),
    (["-Wignore", "-c", "x"], False), (["-Ximporttime", "-c", "x"], False), (["tool.py", "-i"], False),
    (["-c", "x", "-i"], False), (["-m", "x", "-i"], False), (["-ci"], False), (["-"], False),
])
def test_only_pythons_own_i_counts(argv, want):
    assert writes._python_option_i(argv) is want
