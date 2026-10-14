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
from skyguard import audit as audit_mod, backup, cli, recovery, shell_rules, snapshot  # noqa: E402


@pytest.fixture
def inst(tmp_path):
    i = Install(tmp_path)
    put(i.game / "Data" / "Scripts" / "a.pex", b"script a v1")
    put(i.game / "Data" / "Scripts" / "Source" / "a.psc", b"source a v1")
    write_paths(i, i.cfg)
    return i


def write_paths(i, cfg):
    """The install's declared roots, as setup.sh writes them: --in-place writes only inside these (fix round 1, I3)."""
    put(i.game / ".claude" / "skyrim-paths.env", "SKYRIM_CONFIG_DIR='{}'\nSKYRIM_LOADORDER_DIR='{}'\n".format(
        cfg.as_posix(), i.lo.as_posix()).encode("utf-8"))


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
    inst.change("bash tools/resaver-cli.sh clean Quick.ess --apply", cwd=str(inst.game))
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
    assert (inst.game / ".claude" / "backups" / "restored").is_dir(), text      # review M8: an assertion, not a crash
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
    inst.change("bash tools/resaver-cli.sh clean Quick.ess --apply", cwd=str(inst.game))
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


def _linked_install(inst):
    """a.pex hard-linked from a mod manager's staging folder (Vortex deploys this way), then snapshotted."""
    live = inst.game / "Data" / "Scripts" / "a.pex"
    staging = inst.game.parent / "staging" / "a.pex"
    staging.parent.mkdir(parents=True)
    os.link(str(live), str(staging))
    take_all(inst)
    assert os.stat(str(live)).st_nlink == 2
    return live, staging


def _backups(inst):
    return backup.read_manifest(inst.game / ".claude" / "backups")[0]


def test_in_place_restore_of_an_unchanged_snapshot_leaves_a_hard_link_intact(inst):
    """Final review B-M1 (MEASURED by the reviewer: nothing had changed, yet all 3 files were rewritten, each backed up,
    and the hard-linked .pex went from nlink 2 to 1). A file already identical to the snapshot is not rewritten."""
    live, staging = _linked_install(inst)
    before = os.stat(str(live))
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--in-place")
    assert rc == 0, text
    after = os.stat(str(live))
    assert (after.st_ino, after.st_dev, after.st_nlink) == (before.st_ino, before.st_dev, 2), text
    assert os.path.samefile(str(live), str(staging)), "the mod manager's link still points at the live file"
    assert "unchanged {}".format(live) in text and "Restored 0 file(s)" in text, text
    assert "2 already matched" in text, text
    assert _backups(inst) == {}, "nothing was replaced, so nothing was backed up"


def test_in_place_restore_still_replaces_a_changed_file_beside_an_unchanged_one(inst):
    """The twin: only IDENTICAL bytes are skipped. A changed file is backed up and replaced (which cuts its link, by
    design: write_blobs never writes through a link)."""
    live, _staging = _linked_install(inst)
    src = inst.game / "Data" / "Scripts" / "Source" / "a.psc"
    put(src, b"broken experiment")
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--in-place")
    assert rc == 0, text
    assert src.read_bytes() == b"source a v1" and "restored {}".format(src) in text, text
    assert "unchanged {}".format(live) in text and os.stat(str(live)).st_nlink == 2, text
    assert [e["src"] for e in _backups(inst).values()] == [str(src)], "only the replaced file was backed up"


def _moved_config_dir(inst):
    """A config snapshot, then the user points SKYRIM_CONFIG_DIR somewhere else, and plugins.txt goes bad."""
    take_all(inst)
    new_cfg = inst.game.parent / "docs2" / "Skyrim VR"
    new_cfg.mkdir(parents=True)
    write_paths(inst, new_cfg)
    put(inst.cfg / "Skyrim.ini", b"[General]\nedited since\n")       # in the no-longer-configured folder
    put(inst.lo / "plugins.txt", b"*Broken.esp\n")
    return ids(inst, "config")[0]


def test_a_full_in_place_config_restore_skips_a_no_longer_configured_folder_and_restores_the_rest(inst):
    """Final review B-M4: the snapshot's INIs sit in a folder that is no longer configured; the whole restore was
    refused. Now the configured files are restored and the others are named, untouched."""
    sid = _moved_config_dir(inst)
    rc, text = cli_run(inst, "restore", sid, "--in-place")
    assert rc == 0, text
    assert (inst.lo / "plugins.txt").read_bytes() == b"*Skyrim.esm\n*Mine.esp\n", text
    assert (inst.cfg / "Skyrim.ini").read_bytes() == b"[General]\nedited since\n", "outside the roots: never written"
    for n in ("Skyrim.ini", "SkyrimVR.ini", "skyrimprefs.INI"):     # the drive letter's case is the recorder's
        assert "not restored {}: its folder is no longer".format(inst.cfg / n).lower() in text.lower(), text
    assert "3 file(s) were NOT restored" in text, text
    assert "SKIPPED" in inst.log(), "the skip is in the audit log too"


def test_a_per_file_in_place_restore_of_a_no_longer_configured_ini_still_refuses(inst):
    """The twin: asked for BY NAME (--path), the out-of-roots file is still a refusal, not a silent skip."""
    sid = _moved_config_dir(inst)
    rc, text = cli_run(inst, "restore", sid, "--path", "plugins.txt", "--path", "Skyrim.ini", "--in-place")
    assert rc == 2 and "not a load-order file or game INI" in text and "Nothing was restored" in text, text
    assert (inst.cfg / "Skyrim.ini").read_bytes() == b"[General]\nedited since\n"
    assert (inst.lo / "plugins.txt").read_bytes() == b"*Broken.esp\n", "a refusal writes nothing at all"


def _low_disk(monkeypatch, room):
    """The backups drive has SAVE_RESERVE_BYTES + `room` bytes free."""
    real = backup.shutil.disk_usage
    monkeypatch.setattr(backup.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=recovery.SAVE_RESERVE_BYTES + room))


def test_in_place_save_restore_refuses_when_its_backup_would_fill_the_drive(inst, monkeypatch):
    """Final review B-M6: the live save (up to 192 MB) was backed up with no free-space reserve, on a drive the user
    keeps ~88% full. The backup now keeps the recovery store's reserve; when it cannot, nothing is restored."""
    inst.change("bash tools/resaver-cli.sh clean Quick.ess --apply", cwd=str(inst.game))
    put(inst.saves / "Quick.ess", b"what resaver wrote")
    _low_disk(monkeypatch, 10)
    rc, text = cli_run(inst, "restore", ids(inst, "save")[0], "--in-place")
    assert rc == 2 and "could not back up" in text, text
    assert (inst.saves / "Quick.ess").read_bytes() == b"what resaver wrote", "no backup, so no replace"
    assert "would leave under" in inst.log() and "NO BACKUP EXISTS" in inst.log()


def test_the_save_reserve_applies_to_saves_only(inst, monkeypatch):
    """The twin: on the same nearly full drive, a load-order file is still backed up and restored."""
    take_all(inst)
    put(inst.lo / "plugins.txt", b"*Broken.esp\n")
    _low_disk(monkeypatch, 10)
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0], "--path", "plugins.txt", "--in-place")
    assert rc == 0 and (inst.lo / "plugins.txt").read_bytes() == b"*Skyrim.esm\n*Mine.esp\n", text


def test_in_place_save_restore_with_room_backs_the_save_up(inst, monkeypatch):
    inst.change("bash tools/resaver-cli.sh clean Quick.ess --apply", cwd=str(inst.game))
    put(inst.saves / "Quick.ess", b"what resaver wrote")
    _low_disk(monkeypatch, 10 * 1024 * 1024)
    rc, text = cli_run(inst, "restore", ids(inst, "save")[0], "--in-place")
    assert rc == 0 and (inst.saves / "Quick.ess").read_bytes() == b"quick save bytes", text
    assert any(e["src"].endswith("Quick.ess") for e in _backups(inst).values())


def forge_recovery(inst, victim, sid):
    """A whole one-file recovery snapshot whose line names `victim` -- what a tampered store can hold."""
    inst.run("make")
    put(victim, b"keep me")
    line = dict(inst.lines()[0], src=str(victim), snap=sid, lines=1, state=[str(victim)])
    with open(str(inst.store / recovery.MANIFEST), "ab") as fh:
        fh.write((json.dumps(line) + "\n").encode("ascii"))
    assert list(recovery.resolve(inst.store, sid)) == [str(victim)], "the forged snapshot itself is whole"


@pytest.mark.parametrize("where", ["skse-ini", "outside-ini", "other-file-in-config-dir"])
def test_in_place_refuses_an_ini_outside_the_configured_config_dir(inst, where):
    """Fix round 1, I3 (MEASURED by the reviewer: an SKSE plugin INI was overwritten, rc 0)."""
    victim = {"skse-ini": inst.game / "Data" / "SKSE" / "Plugins" / "SkyrimUpscaler.ini",
              "outside-ini": inst.game.parent / "elsewhere" / "Skyrim.ini",
              "other-file-in-config-dir": inst.cfg / "Other.ini"}[where]
    forge_recovery(inst, victim, "20991231_000000-config")
    rc, text = cli_run(inst, "restore", "20991231_000000-config", "--in-place")
    assert rc == 2 and "not a load-order file or game INI" in text and victim.read_bytes() == b"keep me"


@pytest.mark.parametrize("name", ["outside", "not-a-save"])
def test_in_place_refuses_a_save_outside_the_saves_folder(inst, name):
    victim = inst.game.parent / "victim.ess" if name == "outside" else inst.saves / "notes.txt"
    forge_recovery(inst, victim, "20991231_000000-save")
    rc, text = cli_run(inst, "restore", "20991231_000000-save", "--in-place")
    assert rc == 2 and "not a save directly in the configured Saves folder" in text
    assert victim.read_bytes() == b"keep me"


@pytest.mark.parametrize("key", [".claude/hooks/protect-bash.sh", "Data/x.esp", "Data/Scripts/x.ini", "Data/x.pex"])
def test_in_place_refuses_a_script_snapshot_key_outside_the_script_files(inst, key):
    """MEASURED by the reviewer: a script manifest key `.claude/hooks/protect-bash.sh` overwrote the hook, rc 0."""
    take_all(inst)
    store = snapshot.store_dir(str(inst.game))
    name = snapshot.manifest_names(store)[-1]
    victim = inst.game.joinpath(*key.split("/"))
    put(victim, b"keep me")
    m = json.loads((store / "manifests" / name).read_bytes().decode("utf-8"))
    m["set"] = {key: sha(b"script a v1")}
    (store / "manifests" / name).write_bytes(json.dumps(m).encode("ascii"))
    rc, text = cli_run(inst, "restore", name[:-len("__snapshot.json")], "--in-place")
    assert rc == 2 and victim.read_bytes() == b"keep me", text


def test_in_place_never_writes_inside_the_guards_own_folders_even_when_configured_there(inst):
    """Clause (c) holds even when (a) and (b) pass: a config dir declared under .claude/ (an edited paths file)."""
    odd = inst.game / ".claude" / "cfg"
    write_paths(inst, odd)
    victim = odd / "Skyrim.ini"
    forge_recovery(inst, victim, "20991231_000000-config")
    rc, text = cli_run(inst, "restore", "20991231_000000-config", "--in-place")
    assert rc == 2 and "inside the guard's own files" in text and victim.read_bytes() == b"keep me"


def test_in_place_needs_readable_roots(inst):
    (inst.game / ".claude" / "skyrim-paths.env").unlink()
    inst.run("make")
    rc, text = cli_run(inst, "restore", ids(inst, "config")[0], "--path", "plugins.txt", "--in-place")
    assert rc == 2 and "not a load-order file" in text


def test_an_unreadable_stored_copy_is_a_refusal_not_a_traceback(inst, monkeypatch):
    """Review M8: check_blobs reading a blob it cannot open raised out of cmd_restore."""
    take_all(inst)

    def denied(path):
        raise PermissionError(13, "Access is denied", str(path))
    monkeypatch.setattr(snapshot.backup, "_sha256", denied)
    rc, text = cli_run(inst, "restore", ids(inst, "scripts")[0], "--to", str(inst.game.parent / "out"))
    assert rc == 2 and "cannot be read" in text


@pytest.mark.parametrize("cmd", ["bash tools/skyguard.sh snapshot restore 20261005_120000-config --in-place",
                                 "python tools/skyguard snapshot restore --in-place 20261005_120000"])
def test_the_guard_asks_before_an_in_place_restore(inst, cmd):
    v = shell_rules.decide(cmd, inst.roots, inst.audit, env={})
    assert v.decision == "ask" and "S-RESTORE-INPLACE" in [r for r, _d in inst.audit.fired]


@pytest.mark.parametrize("cmd,shell", [
    ("bash tools/skyguard.sh snapshot restore 20261005_120000-config --in", "bash"),
    ("bash tools/skyguard.sh snapshot restore 20261005_120000-config --in-p", "bash"),
    ("bash tools/skyguard.sh snapshot --in-place restore 20261005_120000-config", "bash"),
    ("bash tools/skyguard.sh snapshot restore 20261005_120000-config --in-place=1", "bash"),
    ("tools\\skyguard.cmd snapshot restore 20261005_120000-config --in-place", "powershell"),
    ("py -3 tools/skyguard snapshot restore 20261005_120000-config --in-place", "bash"),
    ("python -m skyguard snapshot --project x restore 20261005_120000-config --in-place", "bash"),
], ids=["abbrev-in", "abbrev-in-p", "option-first", "equals", "cmd-launcher", "py-3", "python-m"])
def test_the_guard_asks_for_every_spelling_of_an_in_place_restore(inst, cmd, shell):
    """Fix round 2, N1 (MEASURED by the re-reviewer: `--in`, `--in-p` and `snapshot --in-place restore` restored in place
    while the rule said allow). N2: the .cmd and python launcher forms."""
    v = shell_rules.decide(cmd, inst.roots, inst.audit, shell=shell, env={})
    assert v.decision == "ask", v


@pytest.mark.parametrize("flag", ["--in", "--in-p", "--in-plac"])
def test_the_cli_refuses_an_abbreviated_in_place(inst, flag):
    """Only the full spelling runs (allow_abbrev=False), so the guard's rule and the CLI read the same flag."""
    take_all(inst)
    put(inst.lo / "plugins.txt", b"*Broken.esp\n")
    rc, _text = cli_run(inst, "restore", ids(inst, "config")[0], flag)
    assert rc == 2 and (inst.lo / "plugins.txt").read_bytes() == b"*Broken.esp\n"


@pytest.mark.parametrize("full,short", [
    (["guards", "status", "--project", "{d}"], ["guards", "status", "--proj", "{d}"]),
    (["lock", "status", "--json", "--root", "{d}"], ["lock", "status", "--js", "--root", "{d}"]),
], ids=["guards", "lock-json"])
def test_no_skyguard_subcommand_takes_an_abbreviated_option(tmp_path, full, short):
    """Fix round 2, N1: the full spelling runs (its own output), the abbreviation is a usage error with none of it."""
    d = str(tmp_path)
    ok, bad = io.StringIO(), io.StringIO()
    cli.main([a.format(d=d) for a in full], out=ok)
    rc = cli.main([a.format(d=d) for a in short], out=bad)
    assert ok.getvalue().strip(), "the control must produce output"
    assert rc == 2 and ok.getvalue().strip()[:12] not in bad.getvalue()


def test_a_recovery_id_with_a_trailing_newline_is_not_an_id():
    """Fix round 2, N3: `$` also matches before a final newline; `\\Z` does not."""
    assert recovery._ID.match("20261005_120000-config") and not recovery._ID.match("20261005_120000-config\n")


def test_the_guard_lets_a_restore_to_a_new_folder_through(inst):
    v = shell_rules.decide("bash tools/skyguard.sh snapshot restore 20261005_120000-config --to out",
                                    inst.roots, inst.audit, env={})
    assert v.decision == "allow"


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
