"""Setup and tool defects from the Opus review of v4.0.0, each reproduced before it was fixed. Fake installs, fake saves and fake
process lists only."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest
from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def setup_run(game, tmp_path, *args):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    r = conftest.run_setup(game, {"USERPROFILE": str(home), "HOME": str(home)}, shim_path=True, args=args)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ---- 2.1, 2.4: the banner, the agent lines and the exit status agree
@pytest.fixture
def game(tmp_path):
    return conftest.make_game(tmp_path, "game")


def test_a_clean_setup_says_complete_and_exits_0(game, tmp_path):
    rc, text = setup_run(game, tmp_path, "--agent", "all")
    assert rc == 0 and "Setup Complete!" in text and "INCOMPLETE" not in text, text[-800:]


@pytest.mark.parametrize("gone, line", [(".claude/settings.json", "Claude Code: INCOMPLETE"),
                                        (".claude/hooks/protect-bash.sh", "Claude Code: INCOMPLETE"),
                                        (".codex/hooks/skyguard-entry.sh", "Setup INCOMPLETE")])
def test_a_lost_file_is_not_a_complete_setup(game, tmp_path, gone, line):
    (game / gone).unlink()
    rc, text = setup_run(game, tmp_path, "--agent", "all")
    assert rc == 1 and line in text and "Setup Complete!" not in text, text[-800:]


def test_settings_that_are_not_json_are_not_a_complete_setup(game, tmp_path):
    (game / ".claude/settings.json").write_text("{ not json", encoding="utf-8")
    rc, text = setup_run(game, tmp_path, "--agent", "claude")
    assert rc == 1 and "not valid JSON" in text


def test_a_failed_migration_is_not_a_complete_setup(game, tmp_path):
    (game / ".claude/backups").mkdir(exist_ok=True)
    (game / ".claude/backups/migrate-3x").write_text("a file, not a folder", encoding="utf-8")
    snap = game / ".claude/backups/pre-update-1"
    snap.mkdir()
    (snap / "CLAUDE.md").write_text("# CLAUDE.md\n3.x body\n", encoding="utf-8")
    rc, text = setup_run(game, tmp_path, "--agent", "claude")
    assert rc == 1 and "The 3.x migration failed" in text, text[-800:]


# ---- 2.2, 2.3: what a re-run keeps
def _paths(game):
    return (game / "skyrim-paths.env").read_text(encoding="utf-8").splitlines()


def test_a_carried_path_is_used_once(game, tmp_path):
    carried = game / ".claude/backups/migrate-3x"
    carried.mkdir(parents=True, exist_ok=True)
    old = tmp_path / "OldProfile"
    old.mkdir()
    (carried / "carried-paths.env").write_text("SKYRIM_3X_CONFIG_DIR='{}'\n".format(old.as_posix()), encoding="utf-8")
    setup_run(game, tmp_path, "--agent", "claude")
    assert "SKYRIM_CONFIG_DIR='{}'".format(old.as_posix()) in _paths(game)
    assert not (carried / "carried-paths.env").exists() and (carried / "carried-paths.env.used").exists()
    setup_run(game, tmp_path, "--agent", "claude")
    assert "SKYRIM_CONFIG_DIR='{}'".format(old.as_posix()) not in _paths(game)


@pytest.mark.parametrize("form", ["SKYRIM_MO2_MODS={}", "export SKYRIM_MO2_MODS='{}'", "SKYRIM_MO2_MODS='{}'\r", 'SKYRIM_MO2_MODS="{}"'])
def test_a_hand_set_path_survives_a_re_run_in_every_spelling_the_guard_reads(game, tmp_path, form):
    setup_run(game, tmp_path, "--agent", "claude")
    mine = tmp_path / "Mine"
    mine.mkdir()
    rows = [line for line in _paths(game) if not line.startswith("SKYRIM_MO2_MODS=")] + [form.format(mine.as_posix())]
    (game / "skyrim-paths.env").write_bytes(("\n".join(rows) + "\n").encode("utf-8"))
    rc, text = setup_run(game, tmp_path, "--agent", "claude")
    assert "SKYRIM_MO2_MODS='{}'".format(mine.as_posix()) in _paths(game), text[-600:]


def test_a_hand_set_path_that_is_not_a_folder_is_named(game, tmp_path):
    setup_run(game, tmp_path, "--agent", "claude")
    rows = [line for line in _paths(game) if not line.startswith("SKYRIM_MO2_MODS=")] + ["SKYRIM_MO2_MODS='C:/No/Such/Folder'"]
    (game / "skyrim-paths.env").write_bytes(("\n".join(rows) + "\n").encode("utf-8"))
    _, text = setup_run(game, tmp_path, "--agent", "claude")
    assert "not a folder here (C:/No/Such/Folder)" in text


# ---- 2.7
def test_a_stale_or_hand_set_documents_record_does_not_decide_my_games(tmp_path, monkeypatch):
    paths = load("skyrim_paths_opus", "tools/skyrim_paths.py")
    good = tmp_path / "My Games" / "Skyrim VR"
    good.mkdir(parents=True)
    monkeypatch.setenv("SKYRIM_DOCUMENTS_DIR", str(good))
    assert paths.my_games() == good.parent
    monkeypatch.setenv("SKYRIM_DOCUMENTS_DIR", str(tmp_path / "gone" / "My Games" / "Skyrim VR"))
    assert "gone" not in str(paths.my_games())
    monkeypatch.setenv("SKYRIM_DOCUMENTS_DIR", str(tmp_path))
    assert paths.my_games() != tmp_path.parent


# ---- 2.8
def test_the_encoding_mo2_writes_is_read():
    from housecarl_integration.setup import mo2_values
    values = mo2_values("[General]\ngamePath=@ByteArray(C:\\\\Games\\\\Skyrim)\nselected_profile=@ByteArray(Default)\n"
                        "geometry=@ByteArray(\\x1\\x2)\nother=@Variant(\\0)\n")
    assert values["gamepath"] == "C:\\Games\\Skyrim" and values["selected_profile"] == "Default"


@pytest.mark.parametrize("value", ["@Variant(x)", "C:\\x41bc"])
def test_an_escape_in_a_path_key_is_still_refused(value):
    from housecarl_integration import Refusal
    from housecarl_integration.setup import mo2_values
    with pytest.raises(Refusal, match="gamePath|gamepath"):
        mo2_values("[General]\ngamePath={}\n".format(value))


# ---- 2.9
def test_a_later_4x_snapshot_does_not_hide_a_3x_one(tmp_path):
    migrate = load("migrate_opus", "tools/migrate-3x.py")
    a, b = tmp_path / ".claude/backups/pre-update-1", tmp_path / ".claude/backups/pre-update-2"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "CLAUDE.md").write_text("# CLAUDE.md\n3.x\n", encoding="utf-8")
    (b / "CLAUDE.md").write_text("<!-- GENERATED by scripts/gen_agent_trees.py from x -->\n# 4\n", encoding="utf-8")
    os.utime(a, (1000, 1000))
    (tmp_path / "CLAUDE.md").write_text("<!-- GENERATED by scripts/gen_agent_trees.py from x -->\n# 4\n", encoding="utf-8")
    source = migrate.pick_source(tmp_path, None)
    assert source is not None and "pre-update-1" in source.label


# ---- 2.11, 2.13: a process list that failed says nothing
def test_a_failed_tasklist_is_not_no_game_for_the_daily_run(monkeypatch):
    daily = load("housekeep_daily_opus", "tools/housekeep_daily.py")
    monkeypatch.setattr(daily.sys, "platform", "win32")
    monkeypatch.setattr(daily.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert daily.running_games(["SkyrimVR.exe"]) == ["SkyrimVR.exe"]
    monkeypatch.setattr(daily.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout='"other.exe"'))
    assert daily.running_games(["SkyrimVR.exe"]) == []


def test_a_failed_tasklist_or_the_game_holds_vr_preparation(monkeypatch):
    vr = load("vr_headless_opus", "tools/vr-headless.py")
    monkeypatch.setattr(vr.os, "name", "nt", raising=False) if os.name == "nt" else pytest.skip("Windows branch")
    for out, rc, want in (("", 1, True), ("", 0, True), ('"other.exe"', 0, False), ('"SkyrimVR.exe"', 0, True), ('"vrserver.exe"', 0, True)):
        monkeypatch.setattr(vr.subprocess, "run", lambda *a, _o=out, _r=rc, **k: SimpleNamespace(returncode=_r, stdout=_o))
        assert vr.steamvr_running() is want, (out, rc)


# ---- 2.14
def test_a_dry_run_does_not_move_the_growth_baseline(tmp_path, monkeypatch):
    daily = load("housekeep_daily_opus2", "tools/housekeep_daily.py")
    wrote = []
    monkeypatch.setattr(daily, "write_alerts", lambda doc, cfg: wrote.append(1))
    monkeypatch.setattr(daily, "low_priority", lambda: None)
    monkeypatch.setattr(daily, "approved", lambda cfg: True)
    monkeypatch.setattr(daily.housekeep, "plan", lambda cfg: {"actions": [], "rows": [], "coverage": {}})
    monkeypatch.setattr(daily.housekeep, "print_coverage", lambda doc: None)
    monkeypatch.setattr(daily.housekeep, "print_rows", lambda rows, title, actions: None)
    cfg = {"root": str(tmp_path), "daily_minutes": 1, "approved_sha256": "x"}
    daily.run(cfg, dry=True)
    assert not wrote
    daily.run(cfg, dry=False)
    assert wrote


def test_the_daily_announcement_does_not_claim_a_plan_was_approved():
    text = (REPO / "tools/housekeep_daily.py").read_text(encoding="utf-8")
    assert "your approved plan" not in text and "you approved the code, not this list" in text


# ---- 2.15, 5.6
@pytest.mark.parametrize("name", ["Saves/a.ess", "Saves/a.skse"])
def test_restore_from_backup_refuses_half_a_save_pair(tmp_path, name):
    from skyguard import backup
    assert "save pair" in backup._restore_refusal(tmp_path / name)


# ---- 2.16
def test_a_malformed_mo2_path_stops_the_install_rather_than_writing_to_data(tmp_path, monkeypatch):
    install = load("mod_install_opus", "tools/mod-install.py")
    monkeypatch.setattr(install.skyrim_paths, "TOOLKIT_ROOT", tmp_path)
    (tmp_path / "skyrim-paths.env").write_text("SKYRIM_GAME_ROOT='{}'\nSKYRIM_MO2_MODS=relative/mods\n".format(tmp_path.as_posix()), encoding="utf-8")
    monkeypatch.delenv("SKYRIM_MO2_MODS", raising=False)
    with pytest.raises(SystemExit, match="not a usable absolute path"):
        install.mo2_mods()


# ---- 2.18
def test_a_mod_folder_kept_as_a_backup_cannot_turn_staging_cleanup_off(tmp_path):
    kept = tmp_path / "backups" / "x" / "mod-folder"
    kept.mkdir(parents=True)
    (kept / "manifest.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    (kept / "other.json").write_bytes(b"\xff\xfe not utf-8")
    hk = load("housekeep_opus", "tools/housekeep.py")
    assert hk.manifest_references(tmp_path) is not None
