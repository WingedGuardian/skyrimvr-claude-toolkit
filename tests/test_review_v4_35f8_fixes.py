"""What the independent review of commit 35f8cd0 found, each reproduced before it was fixed. Guard commands are only judged here,
never run; the saves are empty files in a test folder."""
import importlib.util
import os
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, writes  # noqa: E402


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


# ---- a destination named by keyword is the same destination

def test_a_keyword_destination_is_read_like_a_positional_one():
    keyword = writes.text_write_targets("import os; os.replace(src='a', dst='b')", python_literals=True)
    assert keyword == writes.text_write_targets("import os; os.replace('a', 'b')", python_literals=True) and "b" in keyword


# ---- a twin the piped-listing rule came to shadow: a pipe is now caught earlier, and this clause still decides this shape

def test_an_unresolved_move_source_with_a_protected_root_named_is_refused(world):
    assert judge(world, 'mv "$X" /elsewhere/y; ls "<I>/Data"', "/elsewhere")[1] == {"S-DEL-UNRESOLVED"}

