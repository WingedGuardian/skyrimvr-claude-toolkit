"""tools/mod-install.py: one manual mod install, recorded and reversible; a dry run unless --apply.

Driven through the fake 7-Zip of tests/test_archive_triage.py (tests/fake_7z.py), against a made-up game folder.
Every decision the tool makes has a test here and a mutation twin in tests/test_mutations.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

TOOL = REPO / "tools" / "mod-install.py"
FAKE = REPO / "tests" / "fake_7z.py"


@pytest.fixture
def sevenz(tmp_path):
    d = tmp_path / "fake7z"
    d.mkdir()
    if os.name == "nt":
        p = d / "7z.cmd"
        p.write_text('@"{}" "{}" %*\r\n'.format(sys.executable, FAKE), encoding="utf-8")
    else:
        p = d / "7z"
        p.write_text("#!/bin/sh\nexec '{}' '{}' \"$@\"\n".format(sys.executable, FAKE), encoding="utf-8")
        p.chmod(0o755)
    return p


@pytest.fixture
def game(tmp_path):
    g = tmp_path / "game"
    (g / "Data" / "SKSE" / "Plugins").mkdir(parents=True)
    (g / "SkyrimSE.exe").write_text("", encoding="utf-8")
    (g / "Data" / "ExampleMod.esp").write_bytes(b"old plugin")
    (g / "Data" / "SKSE" / "Plugins" / "ExampleFramework.ini").write_bytes(b"mine=1\n")
    return g


def archive(tmp_path, entries, name="ExampleMod.7z"):
    p = tmp_path / name
    p.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return p


ENTRIES = [
    {"path": "Data/ExampleMod.esp", "data": "new plugin"},
    {"path": "Data/SKSE/Plugins/ExampleFramework.ini", "data": "theirs=1\n"},
    {"path": "Data/SKSE/Plugins/ExampleFramework.dll", "data": "MZ dll"},
    {"path": "Data/textures/exm/a.dds", "data": "tex"},
    {"path": "d3d11.dll", "data": "wrapper"},
]


def env_for(tmp_path, sevenz, game, **extra):
    e = dict(os.environ)
    for k in ("SEVENZIP", "SKYRIM_GAME_ROOT", "SKYRIM_MO2_MODS", "SKYRIM_MANUAL_INSTALLS"):
        e.pop(k, None)
    e.update({"SEVENZIP": str(sevenz), "SKYRIM_GAME_ROOT": str(game),
              "SKYRIM_MANUAL_INSTALLS": str(tmp_path / "records")})
    e.update({k: str(v) for k, v in extra.items()})
    return e


def run(*args, env):
    r = subprocess.run([sys.executable, "-B", str(TOOL), *map(str, args)], capture_output=True, timeout=120, env=env)
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode("utf-8", "replace"),
                                       r.stderr.decode("utf-8", "replace"))


def line(out, needle):
    return next((ln for ln in out.splitlines() if needle in ln), "")


def test_dry_run_writes_nothing_and_shows_every_destination(tmp_path, sevenz, game):
    before = {p: p.read_bytes() for p in game.rglob("*") if p.is_file()}
    r = run(archive(tmp_path, ENTRIES), env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DRY RUN -- nothing written" in r.stdout
    assert {p: p.read_bytes() for p in game.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "records").exists()
    assert "accounting OK" in r.stdout, "the archive-triage report comes first"
    assert str(game / "Data" / "SKSE" / "Plugins" / "ExampleFramework.dll") in line(r.stdout, "NEW            ")
    d3d = line(r.stdout, str(game / "d3d11.dll"))
    assert "outside Data/ -- check this is intended" in d3d, r.stdout
    assert "OVERWRITE       " + str(game / "Data" / "ExampleMod.esp") in r.stdout
    assert "plugin: copied, NOT activated" in line(r.stdout, "OVERWRITE       ")


def test_apply_installs_backs_up_and_writes_a_manifest(tmp_path, sevenz, game):
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"new plugin"
    assert (game / "Data" / "SKSE" / "Plugins" / "ExampleFramework.dll").read_bytes() == b"MZ dll"
    assert (game / "d3d11.dll").read_bytes() == b"wrapper", "outside Data/ is flagged, never refused"
    backups = list((tmp_path / "records" / "backups").rglob("ExampleMod.esp"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"old plugin"
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "# STATUS: complete, 5 file(s), 0 verify mismatch(es)" in manifest
    want = hashlib.sha256(b"new plugin").hexdigest()
    assert "{}  {}".format(want, game / "Data" / "ExampleMod.esp") in manifest
    assert "PLANNED" not in manifest


@pytest.mark.parametrize("kind", ["mapped", "case", "config-new"])
@pytest.mark.parametrize("apply_", [False, True])
def test_effective_destination_collisions_refuse_before_writing(tmp_path, sevenz, game, kind, apply_):
    if kind == "config-new":
        entries = [{"path": "Data/SKSE/Plugins/ExampleFramework.ini", "data": "config"},
                   {"path": "Data/SKSE/Plugins/ExampleFramework.ini.new", "data": "other"}]
    else:
        entries = [{"path": "ExampleMod.esp", "data": "first"},
                   {"path": "Data/" + ("examplemod.ESP" if kind == "case" else "ExampleMod.esp"), "data": "second"}]
    before = {p: p.read_bytes() for p in game.rglob("*") if p.is_file()}
    r = run(archive(tmp_path, entries), *(["--apply"] if apply_ else []), env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 2 and "destination collision" in r.stderr, r.stdout + r.stderr
    assert {p: p.read_bytes() for p in game.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "records").exists()


def test_same_second_installs_keep_distinct_backups_and_operation_manifests(tmp_path, sevenz, game, monkeypatch):
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    class FixedDateTime:
        @staticmethod
        def now():
            return type("Clock", (), {"strftime": lambda self, fmt: "20261015-120000"})()
    monkeypatch.setattr(mod.datetime, "datetime", FixedDateTime)
    arc = archive(tmp_path, [{"path": "Data/ExampleMod.esp", "data": "first"}])
    assert mod.main([str(arc), "--apply"]) == 0
    first_manifests = {p: p.read_bytes() for p in (tmp_path / "records/backups").rglob("manifest.files.txt")}
    archive(tmp_path, [{"path": "Data/ExampleMod.esp", "data": "second"}])
    assert mod.main([str(arc), "--apply"]) == 0
    backups = list((tmp_path / "records/backups").rglob("ExampleMod.esp"))
    assert sorted(p.read_bytes() for p in backups) == [b"first", b"old plugin"]
    assert len(first_manifests) == 1
    assert all(p.read_bytes() == data for p, data in first_manifests.items())
    assert len(list((tmp_path / "records/backups").rglob("manifest.files.txt"))) == 2


def test_failed_backup_verification_leaves_installed_original_untouched(tmp_path, sevenz, game, monkeypatch):
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    def corrupt_copy(source, destination):
        destination.write(b"incomplete backup")
    monkeypatch.setattr(mod.shutil, "copyfileobj", corrupt_copy)
    with pytest.raises(OSError, match="backup verification failed"):
        mod.main([str(archive(tmp_path, [{"path": "Data/ExampleMod.esp", "data": "new"}])), "--apply"])
    assert (game / "Data/ExampleMod.esp").read_bytes() == b"old plugin"
    manifests = list((tmp_path / "records/backups").rglob("manifest.files.txt"))
    assert len(manifests) == 1 and "# STATUS: in progress" in manifests[0].read_text()


def test_target_ancestor_collision_is_found_with_an_intervening_sibling(tmp_path, sevenz, game, monkeypatch):
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    entries = [("Scripts/a", "Scripts/a", "data"), ("Scripts/a-b", "Scripts/a-b", "data"),
               ("Data/Scripts/a/b", "Scripts/a/b", "data")]
    plan = mod.plan_files(entries, game / "Data", game, False, False, False)
    assert "destination collision" in mod.destination_problem(plan)
    assert not (game / "Data/Scripts").exists()


def test_an_existing_config_gets_a_new_file_beside_it(tmp_path, sevenz, game):
    ini = game / "Data" / "SKSE" / "Plugins" / "ExampleFramework.ini"
    run(archive(tmp_path, ENTRIES), "--apply", env=env_for(tmp_path, sevenz, game))
    assert ini.read_bytes() == b"mine=1\n", "the user's config was replaced"
    assert ini.with_name("ExampleFramework.ini.new").read_bytes() == b"theirs=1\n"


def test_overwrite_config_replaces_it_and_backs_it_up(tmp_path, sevenz, game):
    ini = game / "Data" / "SKSE" / "Plugins" / "ExampleFramework.ini"
    run(archive(tmp_path, ENTRIES), "--apply", "--overwrite-config", env=env_for(tmp_path, sevenz, game))
    assert ini.read_bytes() == b"theirs=1\n"
    assert [p.read_bytes() for p in (tmp_path / "records" / "backups").rglob("ExampleFramework.ini")] == [b"mine=1\n"]


def test_a_hardlinked_file_is_replaced_not_written_through(tmp_path, sevenz, game):
    staging = tmp_path / "manager-staging"
    staging.mkdir()
    linked = staging / "ExampleMod.esp"
    os.replace(game / "Data" / "ExampleMod.esp", linked)
    os.link(linked, game / "Data" / "ExampleMod.esp")
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"new plugin"
    assert linked.read_bytes() == b"old plugin", "the write went THROUGH the hardlink into the manager's copy"


def test_plugins_are_never_activated(tmp_path, sevenz, game):
    local = tmp_path / "local" / "Skyrim Special Edition"
    local.mkdir(parents=True)
    (local / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env_for(tmp_path, sevenz, game, LOCALAPPDATA=tmp_path / "local"))
    assert (local / "plugins.txt").read_text(encoding="utf-8") == "*Skyrim.esm\n"
    assert "Plugins were NOT activated" in r.stdout


def _in_process(monkeypatch, tmp_path, sevenz, game):
    for k, v in env_for(tmp_path, sevenz, game).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SKYRIM_MO2_MODS", raising=False)
    spec = importlib.util.spec_from_file_location("mod_install", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_written_file_that_does_not_rehash_is_exit_1(tmp_path, sevenz, game, monkeypatch, capsys):
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    real = mod.write_replacing

    def corrupting(src, target):
        real(src, target)
        if target.name == "ExampleMod.esp":
            target.write_bytes(b"corrupted on the way")

    monkeypatch.setattr(mod, "write_replacing", corrupting)
    rc = mod.main([str(archive(tmp_path, ENTRIES)), "--apply"])
    captured = capsys.readouterr()
    assert rc == 1, captured.out + captured.err
    assert "VERIFY MISMATCH" in captured.err and "ExampleMod.esp" in captured.err
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "1 verify mismatch(es)" in manifest


def test_a_file_7zip_did_not_stage_stops_before_any_write(tmp_path, sevenz, game):
    env = env_for(tmp_path, sevenz, game, FAKE_7Z_DROP="Data/textures/exm/a.dds")
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env)
    assert r.returncode == 2 and "was not staged" in r.stderr, r.stdout + r.stderr
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"old plugin", "a file was written before the check"
    assert not (game / "d3d11.dll").exists()


@pytest.mark.parametrize("name", ["a/b", "a\\b", "x:y", "what?", "", "..", "trailing.", "CON", "nul.txt", "tab\there"])
def test_a_name_that_cannot_be_a_file_name_is_refused(tmp_path, sevenz, game, name):
    r = run(archive(tmp_path, ENTRIES), "--name", name, "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 2 and "--name" in r.stderr, r.stdout + r.stderr
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"old plugin"


def test_an_option_archive_needs_one_group(tmp_path, sevenz, game):
    entries = [{"path": "Option A/Data/ExampleMod.esp", "data": "a"}, {"path": "Option B/Data/ExampleMod.esp", "data": "b"}]
    arc = archive(tmp_path, entries)
    r = run(arc, "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 2 and "pick ONE group" in r.stderr and "Option A" in r.stderr, r.stderr
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"old plugin"
    r = run(arc, "--group", "Option B", "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 0 and (game / "Data" / "ExampleMod.esp").read_bytes() == b"b", r.stdout + r.stderr


def test_mo2_installs_a_new_mod_folder_and_holds_back_game_root_files(tmp_path, sevenz, game):
    mods = tmp_path / "MO2" / "mods"
    mods.mkdir(parents=True)
    env = env_for(tmp_path, sevenz, game, SKYRIM_MO2_MODS=mods)
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (mods / "ExampleMod" / "ExampleMod.esp").read_bytes() == b"new plugin"
    assert (mods / "ExampleMod" / "SKSE" / "Plugins" / "ExampleFramework.dll").is_file()
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"old plugin", "MO2 mode wrote into the real Data/"
    assert not (game / "d3d11.dll").exists()
    assert str(game / "d3d11.dll") in line(r.stdout, "SKIP-GAME-ROOT  "), r.stdout


def test_mo2_game_root_files_need_the_explicit_flag(tmp_path, sevenz, game):
    mods = tmp_path / "MO2" / "mods"
    mods.mkdir(parents=True)
    env = env_for(tmp_path, sevenz, game, SKYRIM_MO2_MODS=mods)
    r = run(archive(tmp_path, ENTRIES), "--apply", "--game-root-files", env=env)
    assert r.returncode == 0 and (game / "d3d11.dll").read_bytes() == b"wrapper", r.stdout + r.stderr


def test_mo2_refuses_an_existing_mod_folder(tmp_path, sevenz, game):
    mods = tmp_path / "MO2" / "mods"
    (mods / "ExampleMod").mkdir(parents=True)
    (mods / "ExampleMod" / "keep.txt").write_text("x", encoding="utf-8")
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env_for(tmp_path, sevenz, game, SKYRIM_MO2_MODS=mods))
    assert r.returncode == 2 and "already exists" in r.stderr and "--update" in r.stderr, r.stderr
    assert sorted(p.name for p in (mods / "ExampleMod").iterdir()) == ["keep.txt"]


def _old_mod(tmp_path):
    mods = tmp_path / "MO2" / "mods"
    old = mods / "ExampleMod"
    (old / "SKSE" / "Plugins").mkdir(parents=True)
    (old / "ExampleMod.esp").write_bytes(b"old plugin")
    (old / "Stale.dll").write_bytes(b"a file the new version dropped")
    (old / "meta.ini").write_bytes(b"[General]\nmodid=101\n")
    (old / "SKSE" / "Plugins" / "ExampleFramework.ini").write_bytes(b"mine=1\n")
    return mods, old


def test_mo2_update_replaces_the_folder_after_keeping_the_old_one_whole(tmp_path, sevenz, game):
    mods, old = _old_mod(tmp_path)
    before = {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    r = run(archive(tmp_path, ENTRIES), "--update", "--apply", env=env_for(tmp_path, sevenz, game, SKYRIM_MO2_MODS=mods))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (old / "ExampleMod.esp").read_bytes() == b"new plugin"
    assert not (old / "Stale.dll").exists(), "an update replaces the folder; it does not merge into it"
    assert (old / "meta.ini").read_bytes() == b"[General]\nmodid=101\n", "MO2's record of the mod was lost"
    ini = old / "SKSE" / "Plugins" / "ExampleFramework.ini"
    assert ini.read_bytes() == b"mine=1\n" and ini.with_name("ExampleFramework.ini.new").read_bytes() == b"theirs=1\n"
    kept = list((tmp_path / "records" / "backups").glob("ExampleMod-*/mod-folder/ExampleMod"))
    assert len(kept) == 1
    assert {p.relative_to(kept[0]).as_posix(): p.read_bytes() for p in kept[0].rglob("*") if p.is_file()} == before
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "# UPDATE: the previous mod folder {} is kept whole at {}".format(old, kept[0]) in manifest
    assert "# UPDATE: 4 file(s) were in the previous mod folder" in manifest
    assert "# STATUS: complete" in manifest


def test_mo2_update_dry_run_changes_nothing(tmp_path, sevenz, game):
    mods, old = _old_mod(tmp_path)
    r = run(archive(tmp_path, ENTRIES), "--update", env=env_for(tmp_path, sevenz, game, SKYRIM_MO2_MODS=mods))
    assert r.returncode == 0 and "UPDATING the existing mod" in r.stdout, r.stdout + r.stderr
    assert (old / "Stale.dll").exists() and (old / "ExampleMod.esp").read_bytes() == b"old plugin"
    assert not (tmp_path / "records").exists()


def test_update_without_mo2_is_refused(tmp_path, sevenz, game):
    r = run(archive(tmp_path, ENTRIES), "--update", "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 2 and "SKYRIM_MO2_MODS" in r.stderr, r.stdout + r.stderr
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"old plugin"


def test_vortex_is_warned_about_not_refused(tmp_path, sevenz, game):
    (game / "Data" / "vortex.deployment.json").write_text("{}", encoding="utf-8")
    r = run(archive(tmp_path, ENTRIES), "--apply", env=env_for(tmp_path, sevenz, game))
    assert r.returncode == 0, r.stdout + r.stderr
    assert line(r.stdout, "WARNING: Vortex manages this game"), r.stdout
    assert (game / "Data" / "ExampleMod.esp").read_bytes() == b"new plugin"


def test_no_vortex_no_warning(tmp_path, sevenz, game):
    r = run(archive(tmp_path, ENTRIES), env=env_for(tmp_path, sevenz, game))
    assert "Vortex" not in r.stdout


@pytest.mark.parametrize("args", [[], ["--bogus", "x.7z"], ["x.7z", "--name"], ["a.7z", "b.7z"]])
def test_bad_usage_is_exit_2(tmp_path, sevenz, game, args):
    assert run(*args, env=env_for(tmp_path, sevenz, game)).returncode == 2


# --------------------------------------------------------------------------- --update across drives

def _cross_drive(monkeypatch, mod):
    """os.rename fails as it does between volumes for any move out of the mods folder, so keep_previous takes the
    copy-verify branch; a rename inside the mods folder (moving the live folder aside) still works."""
    import errno
    real = mod.os.rename

    def cross_rename(src, dst):
        if mod.Path(src).parent != mod.Path(dst).parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
        return real(src, dst)

    monkeypatch.setattr(mod.os, "rename", cross_rename)


def _bin(monkeypatch, mod, why=None):
    """The Recycle Bin, recorded: never the real one from a test."""
    sent = []

    def fake_recycle(paths):
        sent.extend(str(p) for p in paths)
        return {str(p): why for p in paths}

    monkeypatch.setattr(mod, "recycle", fake_recycle)
    return sent


def test_update_across_drives_copies_verifies_and_only_then_recycles(tmp_path, sevenz, game, monkeypatch, capsys):
    mods, old = _old_mod(tmp_path)
    before = {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))
    _cross_drive(monkeypatch, mod)
    sent = _bin(monkeypatch, mod)
    rc = mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    assert rc == 0, out.out + out.err
    kept = list((tmp_path / "records" / "backups").glob("ExampleMod-*/mod-folder/ExampleMod"))
    assert len(kept) == 1
    assert {p.relative_to(kept[0]).as_posix(): p.read_bytes() for p in kept[0].rglob("*") if p.is_file()} == before
    aside = [p for p in mods.iterdir() if p.name.startswith("ExampleMod.modinst-old-")]
    assert len(aside) == 1 and sent == [str(aside[0])], "the copy branch did not hand the aside folder to the bin"
    assert _tree(aside[0]) == before, "the aside folder must be the whole old folder, moved in one rename"
    assert (old / "ExampleMod.esp").read_bytes() == b"new plugin" and not (old / "Stale.dll").exists()
    assert "sent to the Recycle Bin" in out.out


def test_an_aside_folder_the_bin_refuses_is_left_and_named(tmp_path, sevenz, game, monkeypatch, capsys):
    mods, old = _old_mod(tmp_path)
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))
    _cross_drive(monkeypatch, mod)
    _bin(monkeypatch, mod, why="no Recycle Bin on this platform: left in place")
    rc = mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    assert rc == 0, out.out + out.err
    aside = [p for p in mods.iterdir() if p.name.startswith("ExampleMod.modinst-old-")]
    assert len(aside) == 1 and (aside[0] / "Stale.dll").is_file()
    assert "was NOT sent to the Recycle Bin" in out.out and str(aside[0]) in out.out, out.out


def test_a_keep_that_fails_after_moving_the_folder_does_not_claim_nothing_changed(tmp_path, sevenz, game, monkeypatch,
                                                                                capsys):
    mods, old = _old_mod(tmp_path)
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))

    def moved_then_failed(live, kept, before):
        kept.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(live), str(kept))
        raise OSError("the kept folder does not match what was there before")

    monkeypatch.setattr(mod, "keep_previous", moved_then_failed)
    with pytest.raises(OSError, match="does not match"):
        mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    assert "Nothing was installed" not in out.err and "was not changed" not in out.err, out.err
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "aborted before any write" not in manifest


def test_a_copy_that_does_not_match_leaves_the_original_and_installs_nothing(tmp_path, sevenz, game, monkeypatch,
                                                                            capsys):
    mods, old = _old_mod(tmp_path)
    before = {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))
    _cross_drive(monkeypatch, mod)
    real_copytree = mod.shutil.copytree

    def corrupting_copytree(src, dst, *a, **k):
        real_copytree(src, dst, *a, **k)
        (mod.Path(dst) / "ExampleMod.esp").write_bytes(b"bit rot on the way")

    monkeypatch.setattr(mod.shutil, "copytree", corrupting_copytree)
    rc = mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, out.out + out.err
    assert "does not match the original" in out.err and "Nothing was installed" in out.err
    assert {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob("*") if p.is_file()} == before
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "# STATUS: aborted before any write" in manifest


# --------------------------------------------------------------------------- --update: the live folder is never gutted

def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _locked_rmtree(mod, monkeypatch, mods):
    """rmtree as Windows does it with one file held open under mods: it deletes what it can, then raises on the open
    file. The tool's own staging folder is removed for real."""
    real = mod.shutil.rmtree

    def partial_rmtree(path, *a, **k):
        if mods not in mod.Path(path).parents:
            return real(path, *a, **k)
        files = sorted(p for p in mod.Path(path).rglob("*") if p.is_file())
        for p in files[:-1]:
            p.unlink()
        raise PermissionError(32, "The process cannot access the file because it is being used by another process",
                              str(files[-1]))
    monkeypatch.setattr(mod.shutil, "rmtree", partial_rmtree)


def test_a_folder_that_cannot_be_moved_is_left_whole_and_the_update_refused(tmp_path, sevenz, game, monkeypatch,
                                                                           capsys):
    mods, old = _old_mod(tmp_path)
    before = _tree(old)
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))

    def no_rename(src, dst):
        raise PermissionError(5, "Access is denied", str(src))

    monkeypatch.setattr(mod.os, "rename", no_rename)
    _locked_rmtree(mod, monkeypatch, mods)
    rc = mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, out.out + out.err
    assert _tree(old) == before, "the live mod folder was changed by a refused update"
    assert "was not changed" in out.err, out.err
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "# STATUS: aborted before any write" in manifest


@pytest.mark.skipif(os.name != "nt", reason="an open file blocks a folder rename on Windows only")
def test_an_open_file_is_named_and_the_live_folder_is_untouched(tmp_path, sevenz, game, monkeypatch, capsys):
    mods, old = _old_mod(tmp_path)
    before = _tree(old)
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))
    with open(old / "Stale.dll", "rb"):
        rc = mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, out.out + out.err
    assert _tree(old) == before
    assert "Stale.dll" in out.err and "in use" in out.err, out.err


def test_a_failure_after_the_old_folder_moved_names_the_backup(tmp_path, sevenz, game, monkeypatch, capsys):
    mods, old = _old_mod(tmp_path)
    before = _tree(old)
    mod = _in_process(monkeypatch, tmp_path, sevenz, game)
    monkeypatch.setenv("SKYRIM_MO2_MODS", str(mods))

    def failing_write(src, target):
        raise OSError(28, "No space left on device", str(target))

    monkeypatch.setattr(mod, "write_replacing", failing_write)
    with pytest.raises(OSError, match="No space left"):
        mod.main([str(archive(tmp_path, ENTRIES)), "--update", "--apply"])
    out = capsys.readouterr()
    kept = list((tmp_path / "records" / "backups").glob("ExampleMod-*/mod-folder/ExampleMod"))
    assert len(kept) == 1 and _tree(kept[0]) == before
    assert "Nothing was installed" not in out.err
    assert str(kept[0]) in out.err and "partial" in out.err, out.err
    manifest = (tmp_path / "records" / "ExampleMod.files.txt").read_text(encoding="utf-8")
    assert "aborted before any write" not in manifest and "# STATUS: FAILED after" in manifest, manifest
