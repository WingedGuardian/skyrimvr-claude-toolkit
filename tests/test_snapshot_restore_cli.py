"""Plan 6 Task 15 (controller ruling): `skyguard snapshot list | restore` for the script snapshots AND the recovery
snapshots. A restore writes to a new folder by default, never over the live files unless --in-place (which backs up what
it replaces first); one damaged blob refuses the whole restore, names it, and the rest restores file by file."""
import io
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import BASH, REPO
from test_recovery_snapshots import Install, put, sha

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit as audit_mod, backup, cli, recovery, snapshot  # noqa: E402


@pytest.fixture
def inst(tmp_path):
    i = Install(tmp_path)
    put(i.game / "Data" / "Scripts" / "a.pex", b"script a v1")
    put(i.game / "Data" / "Scripts" / "Source" / "a.psc", b"source a v1")
    return i


def cli_run(inst, *args):
    out = io.StringIO()
    rc = cli.main(["snapshot", *args, "--project", str(inst.game)], out=out)
    return rc, out.getvalue()


def take_all(inst, command="make"):
    """One script snapshot and one config snapshot, the way the hooks take them."""
    snapshot.run(str(inst.game), command, False, inst.audit)
    inst.run(command)


def ids(inst, kind):
    if kind == "scripts":
        return [n[:-len("__snapshot.json")] for n in snapshot.manifest_names(snapshot.store_dir(str(inst.game)))]
    return [g["id"] for g in recovery.snapshots(inst.store) if g["kind"] == kind]


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- list


def test_list_shows_script_config_and_save_snapshots(inst):
    take_all(inst)
    inst.run("bash tools/resaver-cli.sh clean Quick.ess --apply")
    rc, text = cli_run(inst, "list")
    assert rc == 0
    for sid in ids(inst, "scripts") + ids(inst, "config") + ids(inst, "save"):
        assert sid in text
    assert "Quick.ess + Quick.skse" in text and "plugins.txt" in text and "before: make" in text


def test_list_by_kind(inst):
    take_all(inst)
    rc, text = cli_run(inst, "list", "--kind", "config")
    assert rc == 0 and ids(inst, "config")[0] in text and "Scripts (" not in text and "Saves (" not in text


# ---------------------------------------------------------------- restore: where it writes


def test_a_config_restore_goes_to_a_new_folder_byte_identical_and_the_live_files_are_untouched(inst):
    take_all(inst)
    put(inst.lo / "plugins.txt", b"*Broken.esp\n")         # the bad command
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0])
    assert rc == 0, text
    (folder,) = (inst.game / ".claude" / "backups" / "restored").iterdir()
    got = {k.rsplit("/", 1)[-1]: v for k, v in tree(folder).items()}
    assert got["plugins.txt"] == b"*Skyrim.esm\n*Mine.esp\n"
    assert got["Skyrim.ini"] == b"[General]\n" and len(got) == 5
    assert (inst.lo / "plugins.txt").read_bytes() == b"*Broken.esp\n", "a default restore never writes the live file"


def test_a_config_restore_is_the_state_at_that_snapshot_not_later(inst):
    inst.run("first")
    put(inst.lo / "plugins.txt", b"*Second.esp\n")
    inst.run("second")
    out = inst.game.parent / "out"
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0], "--to", str(out))
    assert rc == 0, text
    files = {k.rsplit("/", 1)[-1]: v for k, v in tree(out).items()}
    assert files["plugins.txt"] == b"*Skyrim.esm\n*Mine.esp\n" and len(files) == 5


def test_a_save_restore_gives_back_the_save_and_its_cosave(inst):
    inst.run("bash tools/resaver-cli.sh clean Quick.ess --apply")
    put(inst.saves / "Quick.ess", b"what resaver wrote")
    out = inst.game.parent / "out"
    rc, text = cli_run(inst, "restore", ids(inst, "save")[0], "--to", str(out))
    assert rc == 0, text
    files = {k.rsplit("/", 1)[-1]: v for k, v in tree(out).items()}
    assert files == {"Quick.ess": b"quick save bytes", "Quick.skse": b"quick co-save"}


def test_a_script_restore_keeps_the_install_layout(inst):
    take_all(inst)
    out = inst.game.parent / "out"
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--to", str(out))
    assert rc == 0, text
    assert tree(out) == {"Data/Scripts/a.pex": b"script a v1", "Data/Scripts/Source/a.psc": b"source a v1"}


def test_restore_refuses_to_overwrite_a_file_in_the_destination_and_writes_nothing(inst):
    take_all(inst)
    out = inst.game.parent / "out"
    put(out / "Data" / "Scripts" / "a.pex", b"already here")
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--to", str(out))
    assert rc == 2 and "already exist" in text
    assert tree(out) == {"Data/Scripts/a.pex": b"already here"}


def test_in_place_backs_up_what_it_replaces_first(inst):
    take_all(inst)
    put(inst.lo / "plugins.txt", b"*Broken.esp\n")
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0], "--path", "plugins.txt", "--in-place")
    assert rc == 0, text
    assert (inst.lo / "plugins.txt").read_bytes() == b"*Skyrim.esm\n*Mine.esp\n"
    entries, _bad = backup.read_manifest(inst.game / ".claude" / "backups")
    (e,) = [e for e in entries.values() if e["src"].endswith("plugins.txt")]
    assert e["sha256"] == sha(b"*Broken.esp\n"), "the replaced content is in a Task 13 backup"
    assert (inst.game / ".claude" / "backups" / e["dest"]).read_bytes() == b"*Broken.esp\n"


def test_in_place_restores_a_script_into_the_install(inst):
    take_all(inst)
    put(inst.game / "Data" / "Scripts" / "a.pex", b"broken experiment")
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--path", "Data/Scripts/a.pex", "--in-place")
    assert rc == 0, text
    assert (inst.game / "Data" / "Scripts" / "a.pex").read_bytes() == b"script a v1"


def test_in_place_never_writes_a_name_a_recovery_snapshot_does_not_take(inst):
    """A tampered manifest line cannot aim an in-place restore at an arbitrary file."""
    inst.run("make")
    victim = inst.game.parent / "victim.dll"
    put(victim, b"keep me")
    line = dict(inst.lines()[0], src=str(victim), snap="20991231_000000-config")
    with open(str(inst.store / recovery.MANIFEST), "ab") as fh:
        fh.write((json.dumps(line) + "\n").encode("ascii"))
    rc, text = cli_run(inst, "restore", "20991231_000000-config", "--path", "victim.dll", "--in-place")
    assert rc == 2 and victim.read_bytes() == b"keep me"


# ---------------------------------------------------------------- a damaged blob (task-14-rereview N4)


def test_one_damaged_blob_refuses_the_whole_restore_and_names_it(inst):
    take_all(inst)
    (snapshot.store_dir(str(inst.game)) / "blobs" / sha(b"script a v1")).write_bytes(b"torn bytes!")
    out = inst.game.parent / "out"
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--to", str(out))
    assert rc == 2 and "Data/Scripts/a.pex" in text and "damaged" in text
    assert not out.exists(), "nothing is written"
    assert '--path "Data/Scripts/Source/a.psc"' in text, "it says how to restore the others"


def test_per_file_restore_of_the_good_files_works_beside_a_damaged_one(inst):
    take_all(inst)
    (snapshot.store_dir(str(inst.game)) / "blobs" / sha(b"script a v1")).write_bytes(b"torn bytes!")
    out = inst.game.parent / "out"
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--path", "Data/Scripts/Source/a.psc", "--to", str(out))
    assert rc == 0, text
    assert tree(out) == {"Data/Scripts/Source/a.psc": b"source a v1"}


def test_per_file_restore_of_the_damaged_file_is_refused(inst):
    take_all(inst)
    (snapshot.store_dir(str(inst.game)) / "blobs" / sha(b"script a v1")).unlink()
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--path", "a.pex", "--to",
                       str(inst.game.parent / "out"))
    assert rc == 2 and "missing" in text


def test_a_damaged_recovery_blob_is_caught_too(inst):
    inst.run("make")
    (inst.store / "blobs" / sha(b"*Skyrim.esm\n*Mine.esp\n")).write_bytes(b"x" * 22)
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0], "--to", str(inst.game.parent / "out"))
    assert rc == 2 and "plugins.txt" in text and "damaged" in text


def test_materialise_restores_only_the_files_asked_for(inst):
    take_all(inst)
    (snapshot.store_dir(str(inst.game)) / "blobs" / sha(b"script a v1")).write_bytes(b"torn bytes!")
    name = snapshot.manifest_names(snapshot.store_dir(str(inst.game)))[-1]
    out = inst.game.parent / "out"
    assert snapshot.materialise(str(inst.game), name, str(out), only={"Data/Scripts/Source/a.psc"}) == 1
    with pytest.raises(snapshot.Damaged) as e:
        snapshot.materialise(str(inst.game), name, str(inst.game.parent / "out2"))
    assert e.value.bad[0][0] == "Data/Scripts/a.pex"


# ---------------------------------------------------------------- refusals and usage


@pytest.mark.parametrize("args", [["restore", "20000101_000000"], ["restore", "20000101_000000-config"],
                                  ["restore"], ["frobnicate"], ["list", "x"]],
                         ids=["unknown-scripts-id", "unknown-recovery-id", "no-id", "bad-verb", "list-extra"])
def test_unknown_ids_and_bad_usage_are_refused(inst, args):
    take_all(inst)
    rc, _text = cli_run(inst, *args)
    assert rc == 2


def test_a_path_that_matches_nothing_is_refused(inst):
    take_all(inst)
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--path", "nope.pex")
    assert rc == 2 and "matches no file" in text


def test_to_and_in_place_together_is_a_usage_error(inst):
    take_all(inst)
    rc, _text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--to", "x", "--in-place")
    assert rc == 2


def test_the_backup_listing_points_at_the_snapshot_listing(inst, capsys):
    """restore-from-backup.sh is where a user looks first; it names where the snapshots are listed."""
    (inst.game / ".claude" / "backups").mkdir(parents=True, exist_ok=True)
    backup.restore_main([str(inst.game / ".claude" / "backups")])
    assert "tools/skyguard.sh snapshot list" in capsys.readouterr().out


# ---------------------------------------------------------------- the real launchers


def test_the_bash_launcher_lists_and_restores(inst):
    take_all(inst)
    out = inst.game.parent / "out"
    sh = str(REPO / "tools" / "skyguard.sh")
    r = subprocess.run([BASH, sh, "snapshot", "list", "--project", str(inst.game)], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and ids(inst, "config")[0] in r.stdout, r.stdout + r.stderr
    r = subprocess.run([BASH, sh, "snapshot", "restore", ids(inst, "config")[0], "--to", str(out), "--project",
                        str(inst.game)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert {k.rsplit("/", 1)[-1] for k in tree(out)} == {"plugins.txt", "loadorder.txt", "Skyrim.ini",
                                                         "SkyrimVR.ini", "skyrimprefs.INI"}


@pytest.mark.skipif(sys.platform != "win32", reason="the .cmd twin")
def test_the_cmd_launcher_lists(inst):
    take_all(inst)
    r = subprocess.run(["cmd", "/c", str(REPO / "tools" / "skyguard.cmd"), "snapshot", "list", "--project",
                        str(inst.game)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and ids(inst, "scripts")[0] in r.stdout, r.stdout + r.stderr
