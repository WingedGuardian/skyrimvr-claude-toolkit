"""Release-review P1/P2/P3/P4: synthetic ownership, plus a real disposable Python child."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_vr_headless import tree, world, proc, load


@pytest.mark.skipif(sys.platform != "win32", reason="real GetThreadDesktop")
def test_real_non_gui_child_passes_desktop_check(tree):
    import vr_background_desktop as d
    desk = d.BackgroundDesktop.__new__(d.BackgroundDesktop)
    desk.name = d.input_desktop()
    # Exits by itself; no game, Steam, input or other session's process.
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"]) as child:
        assert desk.process_on_desktop(child.pid)


@pytest.mark.parametrize("other", [False, True])
def test_unreadable_threads_are_neutral_but_different_desktop_refuses(tree, monkeypatch, other):
    import vr_background_desktop as d
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="[1,2]"))
    def desktop(tid):
        if tid == 1:
            raise OSError("no user32 desktop")
        return "other" if other else "private"
    monkeypatch.setattr(d, "thread_desktop", desktop)
    desk = d.BackgroundDesktop.__new__(d.BackgroundDesktop)
    desk.name = "private"
    assert desk.process_on_desktop(55) is (not other)


@pytest.mark.parametrize("in_census", [False, True])
def test_steam_client_is_never_an_ownership_anchor(tree, world, monkeypatch, in_census):
    mod = tree["mod"]
    steam = proc(500, "steam", "C:/Steam/steam.exe")
    user = proc(700, "vrserver", "C:/Steam/steamapps/common/SteamVR/bin/win64/vrserver.exe",
                "2026-10-25T10:00:02Z")
    launch = dict(pid=500, executable=steam["Path"], start_time=steam["StartTime"])
    session = dict(ownership_version=1, desktop_mode="current", desktop="Default", processes=[],
                   observed_runtime=[], port=8921, steam_launch=launch,
                   launch_lifetimes={"steam_launch": dict(launch, exit_time=None,
                                                        observed_until="2026-10-25T10:03:00Z")})
    census = [dict(user, ParentId=500)] + ([dict(steam, ParentId=0)] if in_census else [])
    monkeypatch.setattr(mod, "read_process_tree", lambda: census)
    world.procs = [steam, user]
    assert not mod.proven_process(user, session, census, world.make_desktop("current"))
    assert mod.stop_owned({"session": session})["helpers_stopped"] == []
    assert world.terminated == []


@pytest.mark.parametrize("census", ["reused", "same", "absent", "unknown"])
def test_access_denied_handle_uses_census_generation(tree, monkeypatch, census):
    import vr_background_desktop as d
    mod = load()
    def denied(*a):
        raise PermissionError("OpenProcess denied")
    monkeypatch.setattr(d, "process_alive", denied)
    def read():
        if census == "unknown":
            raise mod.ProcessReadError("census unreadable")
        return [] if census == "absent" else [dict(Id=55, StartTime=(
            "2026-10-26T10:00:00Z" if census == "reused" else "2026-10-25T10:00:00Z"))]
    monkeypatch.setattr(mod, "read_process_tree", read)
    if census == "unknown":
        with pytest.raises(mod.ProcessReadError):
            mod.process_alive(55, "2026-10-25T10:00:00Z")
    else:
        assert mod.process_alive(55, "2026-10-25T10:00:00Z") is (census == "same")


def test_launcher_exiting_between_snapshots_is_not_a_refusal(tree, world, monkeypatch):
    mod = tree["mod"]
    mod.cmd_on()
    read = world.read
    def race():
        found = read()
        # Visible to Get-Process, already gone in CIM and the confirming re-read.
        if world.reads == 3:
            return found + [proc(90, "vrstartup", str(tree["vr"] / "bin/win64/vrstartup.exe"))]
        return found
    world.roots_visible = False
    monkeypatch.setattr(mod, "read_processes", race)
    assert mod.cmd_launch("background")["started"]
    assert not mod.load_state()["session"].get("ownership_refused")


def test_input_desktop_change_sets_refusal_marker(tree, world, monkeypatch):
    mod = tree["mod"]
    make = world.make_desktop
    def changed(*a, **k):
        desk = make(*a, **k)
        def boundary(pids):
            raise RuntimeError("input desktop changed")
        desk.boundary = boundary
        return desk
    monkeypatch.setattr(mod, "make_desktop", changed)
    with pytest.raises((RuntimeError, mod.Refused)):
        mod.cmd_run("background")
    assert mod.load_state()["session"]["ownership_refused"]


def test_running_launcher_ignores_undefined_exit_time(tree, monkeypatch):
    import ctypes.wintypes as w
    import vr_background_desktop as d
    def times(handle, *args):
        args[1]._obj.dwLowDateTime = 12345  # undefined while running
        return 1
    kernel = SimpleNamespace(WaitForSingleObject=lambda *a: 258, GetProcessTimes=times)
    monkeypatch.setattr(d, "_api", lambda: (None, kernel, w))
    desk = d.BackgroundDesktop.__new__(d.BackgroundDesktop)
    record = dict(pid=55, start_time=d._filetime(w.FILETIME()).isoformat())
    desk._launch_handles = {55: (77, record)}
    assert desk.launch_lifetime(55)["exit_time"] is None


def test_status_explains_kept_state_and_observed_runtime(tree, world, capsys):
    mod = tree["mod"]
    mod.cmd_on()
    st = mod.load_state()
    st["session"] = dict(ownership_refused=True, observed_runtime=[proc(700, "vrserver")])
    mod.save_state(st)
    mod.cmd_status()
    out = capsys.readouterr().out
    assert "ownership refused" in out and "observed-only" in out and "700" in out


def test_devbench_skill_describes_kept_state():
    root = Path(__file__).resolve().parents[1]
    for name in ("agent", ".agents", ".claude"):
        text = (root / name / "skills/devbench/SKILL.md").read_text(encoding="utf-8")
        assert "on error too" not in text
        assert "ownership or placement refusal" in text
        assert "observed SteamVR" in text
        assert "not loading, then attempts restoration under the same checks" in text
        assert "restores byte-exact only once no owned or observed runtime remains" in text


def test_launcher_race_accepts_equivalent_native_and_census_timestamps(tree, world, monkeypatch):
    import vr_background_desktop as d
    mod = tree["mod"]
    make, read = world.make_desktop, world.read
    def native_format(*a, **k):
        desk = make(*a, **k)
        start = desk.start
        def launch(*a, **k):
            pid, identity = start(*a, **k)
            identity["start_time"] = d.parse_start(identity["start_time"]).isoformat()
            return pid, identity
        desk.start = launch
        return desk
    def race():
        found = read()
        if world.reads == 3:
            return found + [proc(90, "vrstartup", str(tree["vr"] / "bin/win64/vrstartup.exe"))]
        return found
    world.roots_visible = False
    monkeypatch.setattr(mod, "make_desktop", native_format)
    monkeypatch.setattr(mod, "read_processes", race)
    mod.cmd_on()
    assert mod.cmd_launch("background")["started"]
