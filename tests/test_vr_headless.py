"""tools/vr-headless.py against a fake Steam and a fake game folder -- never the real ones.

The promise is that the user's VR setup is never left broken: both files are backed up and verified before the
first change, a failure partway puts them back, `off` restores byte-exact and verifies, and `off` is safe to repeat.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess

import pytest

from conftest import BASH, REPO

TOOL = REPO / "tools" / "vr-headless.py"
OC_BYTES = b"MZ...OpenComposite runtime..." + b"x" * 64
VALVE_BYTES = b"MZ...Valve Corporation openvr..." + b"y" * 32
SETTINGS = {"steamvr": {"enableHomeApp": False}, "driver_oculus": {"something": 1}}


def load():
    spec = importlib.util.spec_from_file_location("vr_headless", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A Steam folder whose SteamVR lives in a SECOND library (found through libraryfolders.vdf), and a VR game."""
    steam = tmp_path / "Steam"
    (steam / "config").mkdir(parents=True)
    (steam / "steamapps").mkdir()
    lib2 = tmp_path / "Library Two"
    vr = lib2 / "steamapps" / "common" / "SteamVR"
    (vr / "bin" / "win64").mkdir(parents=True)
    (vr / "drivers" / "null" / "bin" / "win64").mkdir(parents=True)
    (vr / "bin" / "win64" / "openvr_api.dll").write_bytes(VALVE_BYTES)
    (vr / "drivers" / "null" / "bin" / "win64" / "driver_null.dll").write_bytes(b"stock null driver")
    vdf = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % (
        str(steam).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\"))
    (steam / "steamapps" / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")
    (steam / "config" / "steamvr.vrsettings").write_text(json.dumps(SETTINGS, indent=3) + "\n", encoding="utf-8")
    game = tmp_path / "game"
    game.mkdir()
    (game / "SkyrimVR.exe").write_bytes(b"")
    (game / "openvr_api.dll").write_bytes(OC_BYTES)
    monkeypatch.setenv("STEAM_ROOT", str(steam))
    monkeypatch.setenv("SKYRIM_GAME_ROOT", str(game))
    monkeypatch.setenv("VR_HEADLESS_DIR", str(tmp_path / "state"))
    mod = load()
    monkeypatch.setattr(mod, "steamvr_running", lambda: False)
    monkeypatch.setattr(mod, "EMU_BUILD", tmp_path / "emu-build" / "driver_null.dll")
    return {"mod": mod, "steam": steam, "game": game, "vr": vr, "tmp": tmp_path,
            "settings": steam / "config" / "steamvr.vrsettings", "dll": game / "openvr_api.dll",
            "null": vr / "drivers" / "null" / "bin" / "win64" / "driver_null.dll"}


def snapshot(t):
    return {k: t[k].read_bytes() for k in ("settings", "dll", "null")}


def test_on_swaps_opencomposite_and_enables_the_null_driver_then_off_restores_byte_exact(tree):
    before = snapshot(tree)
    assert tree["mod"].main(["on"]) == 0
    assert tree["dll"].read_bytes() == VALVE_BYTES
    s = json.loads(tree["settings"].read_text(encoding="utf-8"))
    assert s["driver_null"]["enable"] is True and s["steamvr"]["forcedDriver"] == "null"
    assert s["steamvr"]["activateMultipleDrivers"] is False and s["driver_oculus"]["enable"] is False
    assert s["power"]["pauseCompositorOnStandby"] is False
    assert tree["mod"].main(["off"]) == 0
    assert snapshot(tree) == before
    assert not (tree["tmp"] / "state" / "state.json").exists()


def test_off_keeps_a_file_that_changed_after_on_before_restoring_over_it(tree, capsys):
    """Review L5 F1: the user updated OpenComposite and edited SteamVR settings while headless; `off` used to put the
    old files back and destroy the new ones with no copy. Twin: an untouched file is restored with no extra copy."""
    before = snapshot(tree)
    assert tree["mod"].main(["on"]) == 0
    tree["settings"].write_bytes(b'{"steamvr": {"x": 1}, "user_change_during_session": true}\n')
    tree["dll"].write_bytes(b"MZ OpenComposite NEWER build installed by user")
    assert tree["mod"].main(["off"]) == 0
    assert snapshot(tree) == before
    kept = {p.name: p.read_bytes() for p in (tree["tmp"] / "state").glob("*/changed-after-on-*/*")}
    assert kept == {"openvr_api.dll": b"MZ OpenComposite NEWER build installed by user",
                    "steamvr.vrsettings": b'{"steamvr": {"x": 1}, "user_change_during_session": true}\n'}
    assert "changed after `on`" in capsys.readouterr().out
    assert tree["mod"].main(["on"]) == 0                     # twin: nothing changed this time, nothing extra kept
    assert tree["mod"].main(["off"]) == 0
    assert len(list((tree["tmp"] / "state").glob("*/changed-after-on-*/*"))) == 2


def test_off_closes_the_state_file_by_renaming_it_never_deleting(tree):
    """Review L5 F4: state.json was written by an earlier run (`on`), so it is not same-run scratch."""
    assert tree["mod"].main(["on"]) == 0
    record = (tree["tmp"] / "state" / "state.json").read_text(encoding="utf-8")
    assert tree["mod"].main(["off"]) == 0
    closed = list((tree["tmp"] / "state").glob("state-closed-*.json"))
    assert len(closed) == 1 and json.loads(closed[0].read_text(encoding="utf-8"))["since"] == json.loads(record)["since"]
    assert tree["mod"].main(["off"]) == 0                     # a closed record is never read as an open one


def test_off_is_safe_to_repeat_and_with_nothing_recorded(tree, capsys):
    assert tree["mod"].main(["off"]) == 0
    assert "nothing to restore" in capsys.readouterr().out
    tree["mod"].main(["on"])
    assert tree["mod"].main(["off"]) == 0
    assert tree["mod"].main(["off"]) == 0


def test_a_valve_dll_is_left_alone(tree):
    tree["dll"].write_bytes(b"MZ an old Valve openvr_api build")
    assert tree["mod"].main(["on"]) == 0
    assert tree["dll"].read_bytes() == b"MZ an old Valve openvr_api build"


def test_backups_are_taken_and_recorded_before_the_first_change(tree, monkeypatch):
    mod = tree["mod"]
    seen = {}
    real = mod.write_text_atomic

    def spy(dst, text):
        if dst == tree["settings"]:
            st = json.loads((tree["tmp"] / "state" / "state.json").read_text(encoding="utf-8"))
            seen["files"] = [(e["path"], open(e["backup"], "rb").read()) for e in st["files"]]
        return real(dst, text)

    monkeypatch.setattr(mod, "write_text_atomic", spy)
    assert mod.main(["on"]) == 0
    assert dict(seen["files"])[str(tree["dll"])] == OC_BYTES


def test_a_failure_midway_restores_both_files(tree, monkeypatch, capsys):
    """The dll swap fails after the settings were already rewritten: both must come back, verified."""
    mod = tree["mod"]
    before = snapshot(tree)

    real = mod.replace_file

    def failing(src, dst):
        if src == tree["vr"] / "bin" / "win64" / "openvr_api.dll":
            raise OSError("the file is in use")
        return real(src, dst)

    monkeypatch.setattr(mod, "replace_file", failing)
    rc = mod.main(["on"])
    err = capsys.readouterr().err
    assert rc == 2 and "both files were restored and verified" in err, err
    assert snapshot(tree) == before
    assert not (tree["tmp"] / "state" / "state.json").exists()


def test_off_refuses_a_backup_that_does_not_verify_and_keeps_the_state(tree, capsys):
    mod = tree["mod"]
    mod.main(["on"])
    st = json.loads((tree["tmp"] / "state" / "state.json").read_text(encoding="utf-8"))
    dll_backup = next(e["backup"] for e in st["files"] if e["path"] == str(tree["dll"]))
    open(dll_backup, "wb").write(b"tampered")
    assert mod.main(["off"]) == 3
    assert "does not match the recorded hash" in capsys.readouterr().err
    assert (tree["tmp"] / "state" / "state.json").exists(), "the record of what to restore was thrown away"
    assert tree["dll"].read_bytes() == VALVE_BYTES, "a bad backup must not be copied over the live file"


def test_off_after_a_crash_between_backup_and_change_restores(tree):
    """A state file left at "backed-up" (the tool died after the first change): `off` restores from the backups."""
    mod = tree["mod"]
    before = snapshot(tree)
    mod.main(["on"])
    st = json.loads((tree["tmp"] / "state" / "state.json").read_text(encoding="utf-8"))
    st["mode"] = "backed-up"
    (tree["tmp"] / "state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    assert mod.main(["on"]) == 2, "an unfinished run must be cleared with off first"
    assert mod.main(["off"]) == 0
    assert snapshot(tree) == before


def test_se_and_ae_are_refused(tree, capsys):
    (tree["game"] / "SkyrimVR.exe").unlink()
    (tree["game"] / "SkyrimSE.exe").write_bytes(b"")
    before = snapshot(tree)
    assert tree["mod"].main(["on"]) == 2
    assert "Skyrim VR only" in capsys.readouterr().err
    assert snapshot(tree) == before


def test_steamvr_running_is_refused_unless_forced(tree, monkeypatch):
    monkeypatch.setattr(tree["mod"], "steamvr_running", lambda: True)
    assert tree["mod"].main(["on"]) == 2
    assert tree["dll"].read_bytes() == OC_BYTES
    assert tree["mod"].main(["on", "--force"]) == 0


def test_steam_is_found_through_the_registry(tree, monkeypatch):
    monkeypatch.delenv("STEAM_ROOT")
    monkeypatch.setattr(tree["mod"], "registry_steam_paths", lambda: [str(tree["tmp"] / "nowhere"), str(tree["steam"])])
    assert tree["mod"].steam_root() == tree["steam"]
    assert tree["mod"].steamvr_folder(tree["steam"]) == tree["vr"]


def test_emu_needs_a_build_and_is_restored_by_off(tree, capsys):
    mod = tree["mod"]
    mod.main(["on"])
    assert mod.main(["emu", "on"]) == 2 and "not built" in capsys.readouterr().err
    mod.EMU_BUILD.parent.mkdir(parents=True)
    mod.EMU_BUILD.write_bytes(b"emulator build")
    assert mod.main(["emu", "on"]) == 0 and tree["null"].read_bytes() == b"emulator build"
    assert mod.main(["off"]) == 0
    assert tree["null"].read_bytes() == b"stock null driver"


def test_failed_emulator_swap_and_restore_retain_recovery_for_retry(tree, monkeypatch, capsys):
    mod = tree["mod"]
    before = snapshot(tree)
    assert mod.main(["on"]) == 0
    mod.EMU_BUILD.parent.mkdir(parents=True)
    mod.EMU_BUILD.write_bytes(b"emulator build")
    real = mod.replace_file
    def broken_swap_and_restore(src, dst):
        if src == mod.EMU_BUILD:
            dst.write_bytes(b"failed swap")
        elif dst == tree["null"]:
            raise PermissionError("temporarily held")
        else:
            real(src, dst)
    monkeypatch.setattr(mod, "replace_file", broken_swap_and_restore)
    assert mod.main(["emu", "on"]) == 3
    assert "recovery is incomplete" in capsys.readouterr().err
    state = mod.load_state()
    assert state["emu"]["path"] == str(tree["null"])
    assert mod.main(["emu", "on"]) == 2
    assert "recovery" in capsys.readouterr().err
    assert mod.main(["off"]) == 3 and mod.load_state()["emu"]
    monkeypatch.setattr(mod, "replace_file", real)
    assert mod.main(["off"]) == 0
    assert snapshot(tree) == before and mod.load_state() is None


def test_bad_usage_is_exit_2(tree):
    for args in (["sideways"], ["on", "--bogus"], ["emu"], ["emu", "maybe"]):
        assert tree["mod"].main(args) == 2, args


def test_the_build_script_refuses_unpinned_source_and_a_moved_patch_target():
    spec = importlib.util.spec_from_file_location("emu_build", REPO / "tools" / "vr-emu-driver" / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    with pytest.raises(b.BuildError, match="not the pinned"):
        b.verify_head("0" * 40, b.DRIVER_COMMIT, "x")
    b.verify_head(b.DRIVER_COMMIT.upper(), b.DRIVER_COMMIT, "x")
    good = "x\n" + b.PATCH["find"] + "\ny"
    assert b.PATCH["replace"] in b.apply_patch(good)
    assert b.PATCH["replace"].replace("\n", "\r\n") in b.apply_patch(good.replace("\n", "\r\n"))
    with pytest.raises(b.BuildError):
        b.apply_patch("nothing to patch")


def test_no_driver_binary_or_upstream_source_is_tracked():
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "tools/vr-emu-driver"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("COULD NOT CHECK: not a git work tree")
    tracked = sorted(r.stdout.split())
    allowed = {"tools/vr-emu-driver/README.md", "tools/vr-emu-driver/build.py", "tools/vr-emu-driver/UPSTREAM-LICENSE.txt"}
    assert set(tracked) <= allowed and "tools/vr-emu-driver/build.py" in tracked, tracked


def test_the_shell_wrapper_runs_the_tool():
    r = subprocess.run([BASH, str(REPO / "tools" / "vr-headless.sh"), "sideways"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 2 and "usage" in r.stderr, r.stdout + r.stderr
