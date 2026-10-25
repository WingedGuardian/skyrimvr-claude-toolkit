"""tools/vr-headless.py against a fake Steam and a fake game folder -- never the real ones.

The promise is that the user's VR setup is never left broken: both files are backed up and verified before the
first change, a failure partway puts them back, `off` restores byte-exact and verifies, and `off` is safe to repeat.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

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
    (vr / "drivers" / "null" / "bin" / "win64" / "driver_null.dll.sig").write_bytes(b"valve signature of stock")
    (vr / "drivers" / "null" / "driver.vrdrivermanifest").write_bytes(b'{"name": "null"}\n')
    (vr / "drivers" / "null" / "resources" / "settings").mkdir(parents=True)
    (vr / "drivers" / "null" / "resources" / "settings" / "default.vrsettings").write_bytes(b'{"driver_null": {}}\n')
    (vr / "bin" / "win64" / "vrstartup.exe").write_bytes(b"fixture runtime launcher")
    vdf = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % (
        str(steam).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\"))
    (steam / "steamapps" / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")
    (steam / "config" / "steamvr.vrsettings").write_text(json.dumps(SETTINGS, indent=3) + "\n", encoding="utf-8")
    game = tmp_path / "game"
    game.mkdir()
    (game / "SkyrimVR.exe").write_bytes(b"")
    (game / "openvr_api.dll").write_bytes(OC_BYTES)
    (game / "sksevr_loader.exe").write_bytes(b"")
    (steam / "steam.exe").write_bytes(b"")
    plugins = game / "Data" / "SKSE" / "Plugins"
    plugins.mkdir(parents=True)
    (plugins / "OpenCompositeInput.dll").write_bytes(b"oc input plugin")
    (plugins / "Unrelated.dll").write_bytes(b"someone else's plugin")
    local = tmp_path / "LocalAppData"
    (local / "SkyrimVR Devkit" / "Actions").mkdir(parents=True)
    (local / "SkyrimVR Devkit" / "Actions" / "controls.txt").write_bytes(b"turn_speed 216\r\nmouse_enabled 1\r\n")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("STEAM_ROOT", str(steam))
    monkeypatch.setenv("SKYRIM_GAME_ROOT", str(game))
    monkeypatch.setenv("VR_HEADLESS_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("DEVBENCH_PORT", raising=False)
    mod = load()
    # Nothing here may reach the real machine: no process list, desktop, DevBench call or process handle.
    monkeypatch.setattr(mod, "steamvr_running", lambda: False)
    monkeypatch.setattr(mod, "read_processes", lambda: [])
    monkeypatch.setattr(mod, "make_desktop", lambda *a, **k: pytest.fail("a real desktop was requested"))
    monkeypatch.setattr(mod, "devbench_health", lambda port: None)
    monkeypatch.setattr(mod, "devbench_quit", lambda port: pytest.fail("a real quit was requested"))
    monkeypatch.setattr(mod, "process_alive", lambda pid, start: False)
    monkeypatch.setattr(mod, "terminate_verified", lambda pid, start: pytest.fail("a real terminate was requested"))
    monkeypatch.setattr(mod, "parent_pid", lambda pid: None)
    package = tmp_path / "emu-package" / "null"
    monkeypatch.setattr(mod, "EMU_PACKAGE", package)
    fetch = SimpleNamespace(DLL="bin/win64/driver_null.dll", DRIVER_DLL_SHA256=hashlib.sha256(EMU_DLL).hexdigest(),
                            SWAPPED=("bin/win64/driver_null.dll", "driver.vrdrivermanifest",
                                     "resources/settings/default.vrsettings"))
    monkeypatch.setattr(mod, "load_fetch", lambda: fetch)
    null = vr / "drivers" / "null"
    return {"mod": mod, "steam": steam, "game": game, "vr": vr, "tmp": tmp_path, "package": package,
            "settings": steam / "config" / "steamvr.vrsettings", "dll": game / "openvr_api.dll",
            "null": null / "bin" / "win64" / "driver_null.dll", "sig": null / "bin" / "win64" / "driver_null.dll.sig",
            "manifest": null / "driver.vrdrivermanifest", "defaults": null / "resources" / "settings" / "default.vrsettings",
            "controls": local / "SkyrimVR Devkit" / "Actions" / "controls.txt",
            "oc_plugin": plugins / "OpenCompositeInput.dll", "other_plugin": plugins / "Unrelated.dll"}


EMU_DLL = b"MZ official devkit 2.0.1 driver"
WATCHED = ("settings", "dll", "null", "sig", "manifest", "defaults", "controls", "oc_plugin", "other_plugin")


def snapshot(t):
    return {k: t[k].read_bytes() if t[k].exists() else None for k in WATCHED}


def fetch_package(t, dll=EMU_DLL):
    files = {"bin/win64/driver_null.dll": dll, "driver.vrdrivermanifest": b'{"name": "null", "devkit": 1}\n',
             "resources/settings/default.vrsettings": b'{"driver_null": {"serialNumber": "devkit"}}\n'}
    for rel, body in files.items():
        (t["package"] / rel).parent.mkdir(parents=True, exist_ok=True)
        (t["package"] / rel).write_bytes(body)
    return files


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
    assert rc == 2 and "every file was restored and verified" in err, err
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


def test_emu_needs_a_fetch_swaps_the_driver_and_is_restored_by_off(tree, capsys):
    mod = tree["mod"]
    before = snapshot(tree)
    mod.main(["on"])
    assert mod.main(["emu", "on"]) == 2 and "not fetched" in capsys.readouterr().err
    files = fetch_package(tree)
    assert mod.main(["emu", "on"]) == 0
    assert tree["null"].read_bytes() == EMU_DLL
    assert tree["manifest"].read_bytes() == files["driver.vrdrivermanifest"]
    assert tree["defaults"].read_bytes() == files["resources/settings/default.vrsettings"]
    assert not tree["sig"].exists(), "Valve's signature of the stock DLL must be out of play"
    assert list((tree["tmp"] / "state").glob("*/emu-*/set-aside/*-driver_null.dll.sig")), "set aside, never deleted"
    assert tree["controls"].read_bytes() == b"turn_speed 216\r\nmouse_enabled 0\r\n"
    assert mod.main(["off"]) == 0
    assert snapshot(tree) == before


def test_emu_refuses_a_package_that_is_not_the_pinned_build(tree, capsys):
    """Twin vr-headless-emu-pin-unchecked: a fetched DLL that does not hash to the pin is never swapped in."""
    mod = tree["mod"]
    before = snapshot(tree)
    mod.main(["on"])
    fetch_package(tree, dll=b"MZ something else")
    assert mod.main(["emu", "on"]) == 2 and "not the pinned" in capsys.readouterr().err
    assert tree["null"].read_bytes() == b"stock null driver"
    assert mod.main(["off"]) == 0 and snapshot(tree) == before


def test_emu_refuses_while_steam_or_steamvr_runs(tree, monkeypatch, capsys):
    """Twin vr-headless-emu-ignores-steam: Steam and SteamVR hold the driver files; swapping under them fails."""
    mod = tree["mod"]
    mod.main(["on"])
    fetch_package(tree)
    monkeypatch.setattr(mod, "read_processes", lambda: [{"Id": 1, "ProcessName": "steam"}])
    assert mod.main(["emu", "on"]) == 2 and "close Steam and SteamVR first" in capsys.readouterr().err
    assert tree["null"].read_bytes() == b"stock null driver" and not mod.load_state().get("emu")


def test_failed_emulator_swap_and_restore_retain_recovery_for_retry(tree, monkeypatch, capsys):
    mod = tree["mod"]
    before = snapshot(tree)
    assert mod.main(["on"]) == 0
    fetch_package(tree)
    real = mod.replace_file
    def broken_swap_and_restore(src, dst):
        if src == tree["package"] / "bin" / "win64" / "driver_null.dll":
            dst.write_bytes(b"failed swap")
        elif dst == tree["null"]:
            raise PermissionError("temporarily held")
        else:
            real(src, dst)
    monkeypatch.setattr(mod, "replace_file", broken_swap_and_restore)
    assert mod.main(["emu", "on"]) == 3
    assert "recovery is incomplete" in capsys.readouterr().err
    state = mod.load_state()
    assert state["emu"][0]["path"] == str(tree["null"])
    assert mod.main(["emu", "on"]) == 2
    assert "recovery" in capsys.readouterr().err
    assert mod.main(["off"]) == 3 and mod.load_state()["emu"]
    monkeypatch.setattr(mod, "replace_file", real)
    assert mod.main(["off"]) == 0
    assert snapshot(tree) == before and mod.load_state() is None


def test_bad_usage_is_exit_2(tree):
    for args in (["sideways"], ["on", "--bogus"], ["emu"], ["emu", "maybe"], ["run"], ["run", "--desktop"],
                 ["run", "--desktop", "elsewhere"], ["launch", "--desktop", "current", "--park"],
                 ["on", "--park", "--park"]):
        assert tree["mod"].main(args) == 2, args


def test_no_driver_binary_or_upstream_source_is_tracked():
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "tools/vr-emu-driver"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("COULD NOT CHECK: not a git work tree")
    tracked = sorted(r.stdout.split())
    allowed = {"tools/vr-emu-driver/README.md", "tools/vr-emu-driver/fetch.py", "tools/vr-emu-driver/UPSTREAM-LICENSE.txt"}
    assert set(tracked) <= allowed and "tools/vr-emu-driver/fetch.py" in tracked, tracked


def test_the_shell_wrapper_runs_the_tool():
    r = subprocess.run([BASH, str(REPO / "tools" / "vr-headless.sh"), "sideways"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 2 and "usage" in r.stderr, r.stdout + r.stderr


# =========================================================================== the managed lifecycle
# A fake machine: processes, the private desktop, DevBench, process handles and the clock are all simulated.
# Nothing below starts Steam, SteamVR, the game, a desktop or a real process query.

class Clock:
    def __init__(self, world):
        self.now, self.world = 0.0, world

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.world.tick()

    def __getattr__(self, name):          # time.time() etc. for anything else
        import time
        return getattr(time, name)


def proc(pid, name, path="C:/fake/{}.exe", start="2026-10-25T10:00:00.0000000Z"):
    return {"Id": pid, "ProcessName": name, "Path": path.format(name), "StartTime": start}


class World:
    """The simulated machine. `events` are (time, callable) applied as the clock passes them."""

    def __init__(self, t, steam_open=True, isolated=True, room=None):
        self.t, self.isolated, self.room = t, isolated, room
        self.procs = [proc(10, "steam"), proc(13, "steamwebhelper")] if steam_open else []
        self.health = None
        self.events = []
        self.reads = 0
        self.drop_window = None           # (from, to): snapshots then omit the still-running game
        self.failed_reads = set()         # read numbers that fail outright
        self.quits, self.terminated, self.started = [], [], []
        self.frame_advances = True
        self.lifecycle = "dataLoaded"
        self.desktop_closed = 0
        self.private_windows = True
        self.clock = Clock(self)

    def tick(self):
        for when, fn in list(self.events):
            if self.clock.now >= when:
                self.events.remove((when, fn))
                fn()
        if self.health and self.frame_advances:
            self.health["frame"] += 90

    def game(self):
        return next((p for p in self.procs if p["ProcessName"] == "SkyrimVR"), None)

    def read(self):
        self.reads += 1
        if self.reads in self.failed_reads:
            raise self.t["mod"].ProcessReadError("fixture: enumeration failed")
        dropping = self.drop_window and self.drop_window[0] <= self.clock.now <= self.drop_window[1]
        return [dict(p) for p in self.procs if not (dropping and p["ProcessName"] == "SkyrimVR")]

    def game_exits(self):
        self.procs = [p for p in self.procs if p["ProcessName"] not in ("SkyrimVR", "sksevr_loader")]
        self.health = None

    # --- the fakes the tool calls
    def devbench_health(self, port):
        if not self.health:
            return None
        return dict(self.health, lastLifecycle=self.lifecycle)

    def devbench_quit(self, port):
        self.quits.append(self.clock.now)
        self.events.append((self.clock.now + 3, self.game_exits))
        return {"ok": True}

    def process_alive(self, pid, start):
        return any(p["Id"] == pid and p["StartTime"] == start for p in self.procs)

    def terminate_verified(self, pid, start):
        assert self.process_alive(pid, start), "terminated a process that is not the recorded generation"
        self.terminated.append(pid)
        self.procs = [p for p in self.procs if p["Id"] != pid]

    def make_desktop(self, kind, name=None):
        world = self

        class Desktop:
            def __init__(self):
                self.name = "Default" if kind == "current" else "SkyrimVRHeadless_fixture"

            def start(self, command, cwd):
                exe = Path(command[0]).name
                world.started.append(exe)
                if exe in ("vrstartup.exe", "steam.exe"):
                    world.procs.append(proc(11, "vrserver", str(world.t["vr"] / "bin" / "win64" / "vrserver.exe")))
                    if world.room:
                        world.procs.append(world.room)
                    return 11, {"pid": 11}
                world.procs.append(proc(12, "SkyrimVR", str(world.t["game"] / "SkyrimVR.exe")))
                world.health = {"ok": True, "pid": 12, "frame": 0}
                return 12, {"pid": 12}

            def boundary(self, pids):
                visible = [] if world.isolated else [p for p in pids if p == 11]
                private = list(pids) if world.private_windows else [p for p in pids if p != 14]
                return {"isolated": not visible, "private_window_pids": private, "shared_desktop_window_pids": visible}

            def close(self):
                world.desktop_closed += 1
        return Desktop()


@pytest.fixture
def world(tree, monkeypatch):
    w = World(tree)
    mod = tree["mod"]
    monkeypatch.setattr(mod, "IS_WINDOWS", True)
    monkeypatch.setattr(mod, "time", w.clock)
    for name in ("devbench_health", "devbench_quit", "process_alive", "terminate_verified", "make_desktop"):
        monkeypatch.setattr(mod, name, getattr(w, name))
    monkeypatch.setattr(mod, "read_processes", w.read)
    return w


GAME = proc(12, "SkyrimVR", "C:/game/SkyrimVR.exe")


def watch_reads(world, mod, present, alive=None):
    """Script the process list read by read (True = the game is listed). `alive` stubs both second opinions;
    None keeps the real ones (DevBench and the process handle, both faked by the world)."""
    script = iter(present)

    def read():
        world.reads += 1
        try:
            listed = next(script)
        except StopIteration:
            raise AssertionError("the watcher kept reading past its script")
        if listed == "fail":
            raise mod.ProcessReadError("fixture")
        return [dict(GAME)] if listed else []
    mod.read_processes = read
    if alive is not None:
        mod.still_alive = lambda game, port: alive


def test_one_missed_process_read_is_never_an_exit(tree, world):
    """The recorded defect: one incomplete snapshot ended `run` and quit a still-loading game. Twin
    vr-headless-exit-on-one-miss."""
    mod = tree["mod"]
    watch_reads(world, mod, [False, True, True, False, True] + [True] * 10, alive=False)
    assert mod.wait_until_exited([GAME], 8921, deadline=8) is False


def test_seeing_the_game_resets_the_miss_count(tree, world):
    """Twin vr-headless-exit-no-reset-on-sight: alternating snapshots are a running game, not an exit."""
    mod = tree["mod"]
    watch_reads(world, mod, [False, True] * 10, alive=False)
    assert mod.wait_until_exited([GAME], 8921, deadline=15) is False


def test_three_quick_misses_still_need_the_time_window(tree, world):
    """Twin vr-headless-exit-no-window: three misses one second apart are not five seconds of absence."""
    mod = tree["mod"]
    watch_reads(world, mod, [False] * 6, alive=False)
    assert mod.wait_until_exited([GAME], 8921) is True
    assert world.reads == 6 and world.clock.now >= mod.EXIT_WINDOW


def test_a_long_window_with_too_few_misses_is_not_an_exit(tree, world):
    """Twin vr-headless-exit-no-miss-count: two misses ten seconds apart are two reads, not three."""
    mod = tree["mod"]
    watch_reads(world, mod, [False, False, False], alive=False)
    watch = mod.ExitWatch([GAME], 8921)
    assert watch.gone() is False
    world.clock.now = 10
    assert watch.gone() is False
    assert watch.gone() is True


def test_devbench_naming_the_pid_overrules_the_process_list(tree, world, monkeypatch):
    """Twin vr-headless-exit-ignores-devbench."""
    mod = tree["mod"]
    watch_reads(world, mod, [False] * 40)
    world.health = {"ok": True, "pid": 12, "frame": 5}
    monkeypatch.setattr(mod, "process_alive", lambda pid, start: False)
    assert mod.wait_until_exited([GAME], 8921, deadline=20) is False


def test_the_live_process_handle_overrules_the_process_list(tree, world, monkeypatch):
    """Twin vr-headless-exit-ignores-handle: DevBench silent (loading), but the PID's generation still runs."""
    mod = tree["mod"]
    watch_reads(world, mod, [False] * 40)
    world.health = None
    monkeypatch.setattr(mod, "process_alive", lambda pid, start: (pid, start) == (12, GAME["StartTime"]))
    assert mod.wait_until_exited([GAME], 8921, deadline=20) is False


def test_an_unreadable_process_list_is_not_a_miss(tree, world):
    """Twin vr-headless-exit-unknown-is-absent: a failed enumeration is unknown, never 'gone'."""
    mod = tree["mod"]
    watch_reads(world, mod, ["fail"] * 20, alive=False)
    assert mod.wait_until_exited([GAME], 8921, deadline=15) is False


def test_a_reappearing_game_restarts_the_count(tree, world):
    mod = tree["mod"]
    watch_reads(world, mod, [False, False, False, False, False, True] + [False] * 6, alive=False)
    assert mod.wait_until_exited([GAME], 8921) is True
    assert world.reads == 12


def launch_session(tree, world, desktop="background"):
    mod = tree["mod"]
    assert mod.main(["on", "--park"]) == 0
    return mod.cmd_launch(desktop)


def test_background_launch_uses_the_open_steam_client_and_owns_only_vr_processes(tree, world):
    result = launch_session(tree, world)
    st = tree["mod"].load_state()
    assert result["started"] and st["session"]["desktop_mode"] == "background"
    assert world.started == ["vrstartup.exe", "sksevr_loader.exe"]
    assert sorted(p["ProcessName"] for p in st["session"]["processes"]) == ["SkyrimVR", "vrserver"]
    assert len(st["session"]["preexisting_steam"]) == 2 and world.desktop_closed == 1


def test_current_desktop_launch_asks_steam_for_steamvr(tree, world):
    launch_session(tree, world, desktop="current")
    assert world.started == ["steam.exe", "sksevr_loader.exe"]


def test_background_launch_needs_the_users_steam_client_open(tree, world, capsys):
    """Twin vr-headless-background-without-steam: the private desktop never gets a Steam client of its own."""
    world.procs = []
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    assert mod.main(["launch", "--desktop", "background"]) == 2
    assert "open Steam normally" in capsys.readouterr().err and world.started == []


def test_launch_refuses_while_a_game_or_steamvr_runs(tree, world, capsys):
    """Twin vr-headless-launch-over-running-vr."""
    world.procs.append(proc(40, "vrserver"))
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    assert mod.main(["launch", "--desktop", "current"]) == 2
    assert "close the game and SteamVR" in capsys.readouterr().err and world.started == []


def test_a_window_on_the_users_desktop_stops_the_launch_before_the_game(tree, world):
    """Twin vr-headless-escape-ignored."""
    world.isolated = False
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    with pytest.raises(mod.Refused, match="escaped onto your desktop"):
        mod.cmd_launch("background")
    assert "sksevr_loader.exe" not in world.started and world.desktop_closed == 1


def room(tree):
    return proc(14, "steamvr_room_setup", str(tree["vr"] / "tools" / "steamvr_room_setup" / "win64" /
                                             "steamvr_room_setup.exe"))


def test_room_setup_with_an_unproved_window_never_launches_the_game(tree, world):
    """Twin vr-headless-room-setup-unproved: no private-window proof within 25 s = no game, nothing terminated."""
    world.room, world.private_windows = room(tree), False
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    with pytest.raises(mod.Refused, match="not proved within 25 s"):
        mod.cmd_launch("background")
    assert "sksevr_loader.exe" not in world.started and world.terminated == []


def test_room_setup_at_the_wrong_path_is_refused(tree, world):
    world.room = proc(14, "steamvr_room_setup", "C:/elsewhere/steamvr_room_setup.exe")
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    with pytest.raises(mod.Refused, match="unproved"):
        mod.cmd_launch("background")
    assert world.terminated == [] and "sksevr_loader.exe" not in world.started


def test_proved_room_setup_is_stopped_then_the_game_launches(tree, world):
    world.room = room(tree)
    launch_session(tree, world)
    assert world.terminated == [14] and world.started[-1] == "sksevr_loader.exe"


def test_stop_quits_a_ready_game_stops_owned_helpers_leaves_steam_and_restores(tree, world):
    before = snapshot(tree)
    launch_session(tree, world)
    mod = tree["mod"]
    assert mod.main(["stop"]) == 0
    assert len(world.quits) == 1 and world.terminated == [11]
    assert sorted(p["ProcessName"] for p in world.procs) == ["steam", "steamwebhelper"]
    assert snapshot(tree) == before and mod.load_state() is None


def test_stop_never_quits_a_game_whose_frame_counter_is_frozen(tree, world, capsys):
    """Twin vr-headless-quit-while-frozen: a frozen counter is loading, paused or a console (DevBench semantics)."""
    launch_session(tree, world)
    world.frame_advances = False
    mod = tree["mod"]
    assert mod.main(["stop"]) == 2
    assert "not ready for an orderly exit" in capsys.readouterr().err
    assert world.quits == [] and mod.load_state()["session"], "the session must be kept, nothing restored"
    assert tree["dll"].read_bytes() == VALVE_BYTES


def test_stop_never_quits_a_game_that_is_loading_a_save(tree, world, capsys):
    """Twin vr-headless-quit-while-loading: frames may tick, but preLoadGame is a load in progress."""
    launch_session(tree, world)
    world.lifecycle = "preLoadGame"
    mod = tree["mod"]
    assert mod.main(["stop"]) == 2 and world.quits == []


def test_stop_needs_devbench_to_name_the_owned_pid(tree, world):
    """Twin vr-headless-quit-any-pid: DevBench answering for some other process is not this game."""
    launch_session(tree, world)
    world.health["pid"] = 999
    mod = tree["mod"]
    assert mod.main(["stop"]) == 2 and world.quits == []


def test_stop_refuses_an_unowned_game_and_sends_nothing(tree, world, capsys):
    """Twin vr-headless-stop-unowned-game."""
    launch_session(tree, world)
    world.procs = [p for p in world.procs if p["ProcessName"] != "SkyrimVR"] + [
        proc(12, "SkyrimVR", "C:/other/SkyrimVR.exe", start="2026-10-25T11:00:00.0000000Z")]
    mod = tree["mod"]
    assert mod.main(["stop"]) == 2
    assert "did not start" in capsys.readouterr().err and world.quits == [] and world.terminated == []


def test_stop_waits_for_a_game_that_exits_by_itself(tree, world):
    launch_session(tree, world)
    world.frame_advances = False
    world.events.append((world.clock.now + 10, world.game_exits))
    mod = tree["mod"]
    assert mod.main(["stop"]) == 0 and world.quits == [] and mod.load_state() is None


def test_off_refuses_while_a_recorded_process_still_runs(tree, world, capsys):
    """Twin vr-headless-off-under-running-game."""
    launch_session(tree, world)
    mod = tree["mod"]
    assert mod.main(["off"]) == 2 and "Use `stop`" in capsys.readouterr().err
    assert tree["dll"].read_bytes() == VALVE_BYTES


def test_run_survives_a_dropped_snapshot_and_restores_only_after_the_real_exit(tree, world):
    """The recorded defect, end to end: a snapshot without the game while it runs must not end the session."""
    before = snapshot(tree)
    mod = tree["mod"]
    world.drop_window = (100, 104)                     # five reads in a row miss the still-running game
    world.events.append((240, world.game_exits))       # the user's real exit, much later
    assert mod.main(["run", "--desktop", "background", "--park"]) == 0
    assert world.quits == [], "the game exited by itself; nothing may have been sent to it"
    assert world.clock.now >= 240 + mod.EXIT_WINDOW
    assert snapshot(tree) == before and mod.load_state() is None


def test_run_restores_when_the_launch_fails(tree, world, capsys):
    """Twin vr-headless-run-no-restore: restoration is part of failure too, not a caller step."""
    before = snapshot(tree)
    world.procs = []                                    # background, but Steam is not open
    mod = tree["mod"]
    assert mod.main(["run", "--desktop", "background", "--park"]) == 2
    assert "open Steam normally" in capsys.readouterr().err
    assert snapshot(tree) == before and mod.load_state() is None


def test_run_restores_through_stop_when_startup_times_out(tree, world):
    before = snapshot(tree)
    mod = tree["mod"]
    real_start = world.make_desktop

    def no_devbench(kind, name=None):
        d = real_start(kind, name)
        start = d.start

        def start_without_health(command, cwd):
            r = start(command, cwd)
            world.health = None                         # the game never answers DevBench
            return r
        d.start = start_without_health
        return d
    mod.make_desktop = no_devbench
    world.events.append((250, world.game_exits))       # the stuck game is closed by hand, during stop's wait
    assert mod.main(["run", "--desktop", "current"]) == 2
    assert world.quits == [], "DevBench never named the game: no quit may be sent"
    assert snapshot(tree) == before and mod.load_state() is None


def test_run_adopts_a_session_prepared_with_the_emulator(tree, world):
    """Background needs Steam open, swapping the driver needs it closed: prepare first, then run adopts it."""
    before = snapshot(tree)
    mod = tree["mod"]
    fetch_package(tree)
    procs, world.procs = world.procs, []
    assert mod.main(["on"]) == 0 and mod.main(["emu", "on"]) == 0
    world.procs = procs
    world.events.append((60, world.game_exits))
    assert mod.main(["run", "--desktop", "background", "--emu"]) == 0
    assert snapshot(tree) == before and mod.load_state() is None


def test_run_refuses_to_clean_up_another_runs_session(tree, world, capsys):
    launch_session(tree, world)
    mod = tree["mod"]
    st = mod.load_state()
    st["session"]["creator_pid"] = -1
    mod.save_state(st)
    assert mod.main(["run", "--desktop", "current"]) == 2
    assert world.quits == [] and mod.load_state()["session"]


def test_on_refuses_settings_that_already_force_the_null_driver(tree, capsys):
    """Twin vr-headless-on-over-headless: `off` would restore headless, and call it restored."""
    s = dict(SETTINGS, steamvr={"forcedDriver": "null"})
    tree["settings"].write_text(json.dumps(s), encoding="utf-8")
    before = snapshot(tree)
    assert tree["mod"].main(["on"]) == 2 and "already forces" in capsys.readouterr().err
    assert snapshot(tree) == before and tree["mod"].load_state() is None


def test_park_sets_oc_plugins_aside_and_off_puts_them_back(tree):
    """Twin vr-headless-park-deletes: a parked plugin is moved into the run's folder, never deleted."""
    before = snapshot(tree)
    mod = tree["mod"]
    assert mod.main(["on", "--park"]) == 0
    assert not tree["oc_plugin"].exists() and tree["other_plugin"].exists()
    aside = list((tree["tmp"] / "state").glob("*/set-aside/*-OpenCompositeInput.dll"))
    assert len(aside) == 1 and aside[0].read_bytes() == b"oc input plugin"
    assert mod.main(["off"]) == 0 and snapshot(tree) == before


def test_mouse_look_off_keeps_every_other_line():
    mod = load()
    assert mod.mouse_look_off(b"a 1\r\nmouse_enabled 1\r\nb 2\n") == b"a 1\r\nmouse_enabled 0\r\nb 2\n"
    assert mod.mouse_look_off(b"a 1") == b"a 1\nmouse_enabled 0\n"


def test_a_failed_process_read_is_retried_then_reported_as_unknown(monkeypatch):
    mod = load()
    calls = []
    state = {"ok": False}

    def run(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        if len(calls) == 2 and state["ok"]:
            return subprocess.CompletedProcess(args, 0, '{"Id": 4, "ProcessName": "SkyrimVR"}', "")
        raise subprocess.CalledProcessError(1, "powershell")
    monkeypatch.setattr(mod, "subprocess", SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError,
                                                            TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(mod, "time", SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setattr(mod, "IS_WINDOWS", True)
    with pytest.raises(mod.ProcessReadError):
        mod.read_processes()
    assert calls == [8, 8, 8]
    calls.clear()
    state["ok"] = True
    assert mod.read_processes() == [{"Id": 4, "ProcessName": "SkyrimVR"}]


def test_on_refuses_settings_that_already_enable_the_null_driver(tree, capsys):
    """Twin vr-headless-on-over-enabled-null: the second clause of already_headless."""
    tree["settings"].write_text(json.dumps(dict(SETTINGS, driver_null={"enable": True})), encoding="utf-8")
    assert tree["mod"].main(["on"]) == 2 and "already forces" in capsys.readouterr().err
    assert tree["mod"].load_state() is None


@pytest.mark.parametrize("bad", ["path", "no-path", "no-start"])
def test_room_setup_with_unproved_identity_is_refused(tree, world, bad):
    """Twins vr-headless-room-setup-{path,no-path,no-start}: one per clause of the identity check."""
    r = room(tree)
    if bad == "path":
        r["Path"] = "C:/elsewhere/steamvr_room_setup.exe"
    elif bad == "no-path":
        r["Path"] = None
    else:
        r["StartTime"] = None
    world.room = r
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    with pytest.raises(mod.Refused, match="unproved"):
        mod.cmd_launch("background")
    assert world.terminated == [] and "sksevr_loader.exe" not in world.started


def unreadable(mod):
    def read():
        raise mod.ProcessReadError("fixture: enumeration failed")
    mod.read_processes = read


def test_off_refuses_when_it_cannot_tell_whether_the_session_still_runs(tree, world, capsys):
    """Twin vr-headless-off-unknown-is-idle."""
    launch_session(tree, world)
    mod = tree["mod"]
    unreadable(mod)
    assert mod.main(["off"]) == 2 and "cannot tell" in capsys.readouterr().err
    assert tree["dll"].read_bytes() == VALVE_BYTES


def test_launch_and_emu_refuse_when_the_process_list_is_unreadable(tree, world, capsys):
    """Twins vr-headless-launch-unknown-is-idle and vr-headless-emu-unknown-is-idle."""
    mod = tree["mod"]
    assert mod.main(["on"]) == 0
    fetch_package(tree)
    unreadable(mod)
    assert mod.main(["launch", "--desktop", "current"]) == 2 and world.started == []
    assert not mod.load_state().get("session"), "refused before any desktop or session existed"
    assert mod.main(["emu", "on"]) == 2 and tree["null"].read_bytes() == b"stock null driver"
