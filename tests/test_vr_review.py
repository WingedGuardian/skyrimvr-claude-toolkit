"""Task 7 P3/A1: retained synthetic probes, with no native calls."""
import pytest

from test_vr_headless import tree, world, proc, launch_session, load, VALVE_BYTES
from types import SimpleNamespace


def test_foreign_process_seen_before_start_is_never_owned(tree, world, monkeypatch):
    mod = tree["mod"]
    assert mod.cmd_on() == 0
    foreign = proc(27, "vrmonitor", str(tree["vr"] / "bin" / "vrmonitor.exe"))
    read = world.read

    def race():
        found = read()
        if world.reads > 1:
            return found + [foreign]
        return found

    monkeypatch.setattr(mod, "read_processes", race)
    world.isolated = False
    with pytest.raises(mod.Refused):
        mod.cmd_launch("background")
    assert mod.load_state()["session"]["processes"] == []
    assert "boundary" not in mod.load_state()["session"], "an unproved candidate's PID leaked into state"
    assert world.started == []
    mod.stop_owned(mod.load_state())
    assert world.terminated == []


@pytest.mark.parametrize("refusal", ["ancestry", "boundary", "census"])
def test_managed_run_keeps_files_when_ownership_refusal_is_followed_by_a_missed_read(tree, world, monkeypatch, refusal):
    mod = tree["mod"]
    foreign = proc(27, "vrmonitor", str(tree["vr"] / "bin" / "vrmonitor.exe"))
    world.isolated = refusal != "boundary"
    read = world.read
    def race():
        found = read()
        if refusal in ("ancestry", "census"):
            return found + ([foreign] if world.reads == 2 else [])
        return [p for p in found if p["ProcessName"] not in mod.VR_NAMES] if world.reads > 3 else found
    monkeypatch.setattr(mod, "read_processes", race)
    if refusal == "census":
        def unknown():
            raise mod.ProcessReadError("synthetic unreadable ancestry")
        monkeypatch.setattr(mod, "read_process_tree", unknown)
    with pytest.raises((mod.Refused, mod.ProcessReadError)):
        mod.cmd_run("background")
    assert mod.load_state() is not None, "a failed ownership proof restored files after a missed process read"
    assert tree["dll"].read_bytes() == VALVE_BYTES
    assert world.terminated == [] and world.quits == []
    assert world.started == (["vrstartup.exe"] if refusal == "boundary" else [])


def test_off_also_refuses_an_observed_foreign_vr_process(tree, world):
    mod = tree["mod"]
    assert mod.cmd_on() == 0
    world.procs.append(proc(27, "vrmonitor"))
    with pytest.raises(mod.Refused):
        mod.cmd_off()
    assert mod.load_state() is not None and tree["dll"].read_bytes() == VALVE_BYTES


def test_legacy_session_without_launch_proof_is_never_stopped(tree, world):
    mod = tree["mod"]
    launch_session(tree, world)
    state = mod.load_state()
    state["session"].pop("ownership_version")
    with pytest.raises(mod.Refused, match="legacy session"):
        mod.stop_owned(state)
    assert world.quits == [] and world.terminated == []


@pytest.mark.parametrize("signal", ["list", "health", "handle", "unknown"])
@pytest.mark.parametrize("command", ["off", "emu-off"])
def test_missing_list_entry_cannot_restore_a_live_game(tree, world, monkeypatch, signal, command):
    mod = tree["mod"]
    assert mod.cmd_on() == 0
    game = proc(55, "SkyrimVR", str(tree["game"] / "SkyrimVR.exe"))
    state = mod.load_state()
    state["session"] = {"processes": [game], "port": 8921}
    # Give emu-off a real synthetic entry; it must not restore this either.
    state["emu"] = list(state["files"])
    mod.save_state(state)
    monkeypatch.setattr(mod, "read_processes", lambda: [game] if signal == "list" else [])
    world.health = {"pid": 55, "ok": True} if signal == "health" else None

    def alive(pid, start):
        if signal == "unknown":
            raise mod.ProcessReadError("synthetic inaccessible handle")
        return signal == "handle"

    monkeypatch.setattr(mod, "process_alive", alive)
    restored = []
    monkeypatch.setattr(mod, "restore_entries", lambda entries: restored.append(entries) or [])
    assert mod.main(["off"] if command == "off" else ["emu", "off"]) == 2
    assert restored == []
    assert mod.load_state() is not None
    assert tree["dll"].read_bytes() == VALVE_BYTES


@pytest.mark.parametrize("bad", ["none", "unrelated", "older", "cycle", "missing-parent", "parent-reused", "anchor-pid", "anchor-path",
                                 "snapshot-path", "snapshot-time", "path", "time", "desktop"])
def test_ownership_needs_each_independent_proof(tree, world, bad):
    mod = tree["mod"]
    root = proc(90, "vrstartup", "C:/fixture/vrstartup.exe")
    child = proc(11, "vrserver", "C:/fixture/vrserver.exe", "2026-10-25T10:00:01Z")
    census = [dict(root, ParentId=1), dict(child, ParentId=90)]
    session = {"processes": [], "desktop_mode": "background", "runtime_launch": {
        "pid": 90, "executable": root["Path"], "start_time": root["StartTime"]}}
    if bad == "unrelated":
        session.pop("runtime_launch")
    elif bad == "anchor-pid":
        session["runtime_launch"]["pid"] = 999
    elif bad == "anchor-path":
        session["runtime_launch"]["executable"] = "C:/different/vrstartup.exe"
    elif bad == "older":
        child["StartTime"] = census[1]["StartTime"] = "2026-10-25T09:59:59Z"
    elif bad == "cycle":
        census[0]["ParentId"], census[1]["ParentId"] = 11, 90
        session.pop("runtime_launch")
        census[1]["StartTime"] = child["StartTime"] = root["StartTime"]
    elif bad == "missing-parent":
        census[1]["ParentId"] = 999
    elif bad == "parent-reused":
        census[0]["StartTime"] = "2026-10-25T10:00:00.500000Z"
    elif bad.startswith("snapshot-"):
        census[1]["Path" if bad == "snapshot-path" else "StartTime"] = (
            "C:/different/vrserver.exe" if bad == "snapshot-path" else "2026-10-25T10:00:02Z")
    elif bad in ("path", "time"):
        key = "Path" if bad == "path" else "StartTime"
        census[1][key] = child[key] = None
    desk = SimpleNamespace(process_on_desktop=lambda pid: bad != "desktop")
    assert mod.proven_process(child, session, census, desk) is (bad == "none")
    if bad == "desktop":
        session["desktop_mode"] = "current"
        assert mod.proven_process(child, session, census, desk)


def test_foreign_process_on_private_desktop_still_needs_launch_ancestry(tree, world, monkeypatch):
    mod = tree["mod"]
    assert mod.cmd_on() == 0
    foreign = proc(27, "vrmonitor", str(tree["vr"] / "bin" / "vrmonitor.exe"))
    read = world.read
    monkeypatch.setattr(mod, "read_processes", lambda: read() + ([foreign] if world.reads > 1 else []))
    monkeypatch.setattr(mod, "read_process_tree", lambda: [dict(foreign, ParentId=999)])
    with pytest.raises(mod.Refused, match="unproved"):
        mod.cmd_launch("background")
    assert mod.load_state()["session"]["processes"] == []
    assert world.started == [] and world.terminated == []


@pytest.mark.parametrize("bad", [False, True])
def test_late_helpers_need_the_same_proof_before_termination(tree, world, monkeypatch, bad):
    mod = tree["mod"]
    launch_session(tree, world)
    world.game_exits()
    helper = proc(28, "vrmonitor", str(tree["vr"] / "bin" / "vrmonitor.exe"))
    world.procs.append(helper)
    tree_read = mod.read_process_tree

    def census():
        return [dict(p, ParentId=999 if bad else 11) if p["Id"] == 28 else p for p in tree_read()]

    monkeypatch.setattr(mod, "read_process_tree", census)
    mod.stop_owned(mod.load_state())
    assert (28 in world.terminated) is (not bad)
    assert (28 in [p["Id"] for p in mod.load_state()["session"]["processes"]]) is (not bad)


def test_unreadable_handle_resets_exit_confirmation(tree, world, monkeypatch):
    mod = tree["mod"]
    game = proc(55, "SkyrimVR")
    monkeypatch.setattr(mod, "read_processes", lambda: [])
    def unknown(pid, start):
        raise mod.ProcessReadError("synthetic handle failure")
    monkeypatch.setattr(mod, "process_alive", unknown)
    watch = mod.ExitWatch([game], 8921)
    for _ in range(10):
        assert not watch.gone()
        world.clock.sleep(1)


@pytest.mark.parametrize("start", [None, "2026-10-25T10:00:00Z"])
def test_handle_wrapper_never_turns_unknown_into_absence(tree, monkeypatch, start):
    import vr_background_desktop as desktop
    mod = load()
    def fail(*args):
        raise OSError("synthetic inaccessible handle")
    monkeypatch.setattr(desktop, "process_alive", fail)
    with pytest.raises(mod.ProcessReadError):
        mod.process_alive(55, start)


@pytest.mark.parametrize("case", ["open-denied", "absent", "wait-failed", "running", "exited"])
def test_native_handle_errors_are_not_exits(tree, monkeypatch, case):
    import ctypes
    import ctypes.wintypes as w
    import vr_background_desktop as desktop
    closed = []
    kernel = SimpleNamespace(OpenProcess=lambda *a: 0 if case in ("open-denied", "absent") else 77,
                             GetProcessTimes=lambda *a: 1, CloseHandle=closed.append,
                             WaitForSingleObject=lambda *a: {"running": 258, "exited": 0}.get(case, 0xffffffff))
    monkeypatch.setattr(desktop, "_api", lambda: (None, kernel, w))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87 if case == "absent" else 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda *a: OSError("synthetic native failure"), raising=False)
    if case in ("open-denied", "wait-failed"):
        with pytest.raises(OSError):
            desktop.process_generation(55)
    elif case == "absent":
        assert desktop.process_generation(55) is None
    else:
        assert desktop.process_generation(55)[1] is (case == "running")
    assert closed == ([] if case in ("open-denied", "absent") else [77])


@pytest.mark.parametrize("case", ["private", "mixed", "empty", "unknown"])
def test_windowless_desktop_proof_requires_readable_threads(tree, monkeypatch, case):
    import vr_background_desktop as desktop
    def threads(pid):
        assert pid == 55
        if case == "unknown":
            raise OSError("synthetic thread read error")
        return [] if case == "empty" else [1, 2]
    monkeypatch.setattr(desktop, "process_thread_ids", threads)
    monkeypatch.setattr(desktop, "thread_desktop", lambda tid: "Default" if case == "mixed" and tid == 2 else "private")
    desk = desktop.BackgroundDesktop.__new__(desktop.BackgroundDesktop)
    desk.name = "private"
    assert desk.process_on_desktop(55) is (case == "private")


@pytest.mark.parametrize("case", ["ok", "time-error", "placement-error"])
def test_start_records_creation_time_from_the_opened_handle(tree, monkeypatch, case):
    import ctypes
    import ctypes.wintypes as w
    import vr_background_desktop as desktop
    closed, reads = [], []
    def create(*args):
        pi = args[-1]._obj
        pi.dwProcessId, pi.dwThreadId, pi.hProcess, pi.hThread = 55, 56, 77, 78
        return 1
    def times(handle, *args):
        reads.append(handle)
        return case != "time-error"
    def check(ok):
        if not ok:
            raise OSError("synthetic creation-time read error")
        return ok
    kernel = SimpleNamespace(CreateProcessW=create, GetProcessTimes=times, CloseHandle=closed.append)
    monkeypatch.setattr(desktop, "_api", lambda: (None, kernel, w))
    monkeypatch.setattr(desktop, "_check", check)
    desk = desktop.BackgroundDesktop.__new__(desktop.BackgroundDesktop)
    calls = []
    def verify():
        calls.append(True)
        if case == "placement-error" and len(calls) == 2:
            raise RuntimeError("synthetic placement error after creation")
    desk.name, desk.verify = "private", verify
    desk.handle, desk._launch_handles = None, {}
    if case in ("time-error", "placement-error"):
        with pytest.raises(desktop.LaunchIdentityError):
            desk.start([tree["game"] / "sksevr_loader.exe"], tree["game"])
        if case == "placement-error":
            desk.close()
    else:
        pid, record = desk.start([tree["game"] / "sksevr_loader.exe"], tree["game"])
        assert pid == record["pid"] == 55
        assert desktop.parse_start(record["start_time"]) == desktop._filetime(w.FILETIME())
        assert closed == [78], "the launched process handle must remain open"
        desk.close()
    assert reads == [77] and closed == [78, 77]


@pytest.mark.parametrize("bad", [False, True])
def test_ancestry_census_read_errors_are_unknown(tree, monkeypatch, bad):
    mod = load()
    def run(*args, **kwargs):
        assert kwargs["check"] and kwargs["timeout"] == 8
        if bad:
            raise OSError("synthetic ancestry read error")
        return SimpleNamespace(stdout='{"Id":55,"ParentId":90}')
    monkeypatch.setattr(mod.subprocess, "run", run)
    if bad:
        with pytest.raises(mod.ProcessReadError):
            mod.read_process_tree()
    else:
        assert mod.read_process_tree() == [{"Id": 55, "ParentId": 90}]


def test_exited_launchers_can_prove_their_children_without_census_roots(tree, world):
    world.roots_visible = False
    result = launch_session(tree, world)
    assert result["started"]
    owned = tree["mod"].load_state()["session"]["processes"]
    assert {p["Id"] for p in owned} == {11, 12}


def test_steam_handoff_can_start_without_owning_the_users_runtime(tree, world):
    world.roots_visible = False
    world.steam_handoff = True
    result = launch_session(tree, world, desktop="current")
    assert result["started"]
    session = tree["mod"].load_state()["session"]
    assert [p["Id"] for p in session["processes"]] == [12]
    assert [p["Id"] for p in session["observed_runtime"]] == [11]
    world.game_exits()
    with pytest.raises(tree["mod"].Refused):
        tree["mod"].cmd_stop()
    assert world.terminated == []
    assert tree["mod"].load_state() is not None


@pytest.mark.parametrize("bad", ["none", "no-proof", "no-launch", "parent", "proof-pid", "path", "start", "before", "after", "unknown-upper", "observed-upper"])
def test_retained_launcher_lifetime_is_bound_to_identity_and_birth(tree, world, bad):
    mod = tree["mod"]
    child = dict(proc(11, "vrserver", "C:/fixture/vrserver.exe", "2026-10-25T10:00:01Z"), ParentId=90)
    launch = dict(pid=90, executable="C:/fixture/vrstartup.exe", start_time="2026-10-25T10:00:00Z")
    proof = dict(launch, exit_time="2026-10-25T10:00:02Z", observed_until="2026-10-25T10:00:03Z")
    session = dict(processes=[], desktop_mode="background", runtime_launch=launch,
                   launch_lifetimes={"runtime_launch": proof})
    if bad == "no-proof":
        session["launch_lifetimes"] = {}
    elif bad == "no-launch":
        session.pop("runtime_launch")
    elif bad == "parent":
        child["ParentId"] = 999
    elif bad == "proof-pid":
        proof["pid"] = 999
    elif bad == "path":
        proof["executable"] = "C:/other/vrstartup.exe"
    elif bad == "start":
        proof["start_time"] = "2026-10-25T09:00:00Z"
    elif bad == "before":
        child["StartTime"] = "2026-10-25T09:59:59Z"
    elif bad == "after":
        child["StartTime"] = "2026-10-25T10:00:03Z"
    elif bad == "unknown-upper":
        proof["exit_time"] = None
        proof["observed_until"] = None
    elif bad == "observed-upper":
        proof["exit_time"] = None
    desk = SimpleNamespace(process_on_desktop=lambda pid: True)
    assert mod.proven_process(child, session, [child], desk) is (bad in ("none", "observed-upper"))


@pytest.mark.parametrize("bad", ["none", "background", "not-absent", "no-before-proof", "no-request", "game", "other-name", "outside", "prefix-collision", "filename", "before", "late", "census-pid", "census-path", "census-time", "desktop", "unreadable"])
def test_handoff_readiness_checks_every_boundary_without_granting_ownership(tree, bad):
    mod = tree["mod"]
    child = proc(11, "vrserver", "C:/fixture/SteamVR/bin/vrserver.exe", "2026-10-25T10:00:01Z")
    session = dict(desktop_mode="current", preexisting_vr=[], steamvr_root="C:/fixture/SteamVR",
                   steam_launch=dict(pid=90, start_time="2026-10-25T10:00:00Z"), processes=[],
                   runtime_observed_at="2026-10-25T10:01:30Z")
    if bad == "background":
        session["desktop_mode"] = "background"
    elif bad == "not-absent":
        session["preexisting_vr"] = [child]
    elif bad == "no-before-proof":
        session.pop("preexisting_vr")
    elif bad == "no-request":
        session.pop("steam_launch")
    elif bad in ("game", "other-name"):
        child["ProcessName"] = "SkyrimVR" if bad == "game" else "unrelated"
        child["Path"] = "C:/fixture/SteamVR/bin/" + child["ProcessName"] + ".exe"
    elif bad in ("outside", "prefix-collision", "filename"):
        child["Path"] = {"outside": "C:/elsewhere/vrserver.exe",
                         "prefix-collision": "C:/fixture/SteamVR-other/vrserver.exe",
                         "filename": "C:/fixture/SteamVR/bin/other.exe"}[bad]
    elif bad == "before":
        child["StartTime"] = "2026-10-25T09:59:59Z"
    elif bad == "late":
        child["StartTime"] = "2026-10-25T10:01:31Z"
    census = [dict(child)]
    if bad == "census-pid":
        census[0]["Id"] = 12
    elif bad == "census-path":
        census[0]["Path"] = "C:/other/vrserver.exe"
    elif bad == "census-time":
        census[0]["StartTime"] = "2026-10-25T10:00:02Z"
    def placed(pid):
        if bad == "unreadable":
            raise OSError("synthetic inaccessible thread")
        return bad != "desktop"
    assert mod.steam_handoff_runtime(child, session, census, SimpleNamespace(process_on_desktop=placed)) is (bad == "none")
    assert session["processes"] == []


def test_observed_runtime_stays_non_terminable_even_if_late_proof_changes(tree, world, monkeypatch):
    world.roots_visible, world.steam_handoff = False, True
    launch_session(tree, world, desktop="current")
    world.game_exits()
    monkeypatch.setattr(tree["mod"], "proven_process", lambda *args: True)
    tree["mod"].stop_owned(tree["mod"].load_state())
    assert world.terminated == []
    assert 11 not in [p["Id"] for p in tree["mod"].load_state()["session"]["processes"]]


def test_observed_runtime_handle_blocks_restore_after_a_missed_census(tree, world, monkeypatch):
    mod = tree["mod"]
    world.roots_visible, world.steam_handoff = False, True
    launch_session(tree, world, desktop="current")
    world.game_exits()
    monkeypatch.setattr(mod, "read_processes", lambda: [])
    with pytest.raises(mod.Refused):
        mod.cmd_off()
    assert mod.load_state() is not None and tree["dll"].read_bytes() == VALVE_BYTES


def test_unreadable_launcher_lifetime_keeps_files_and_no_new_ownership(tree, world, monkeypatch):
    mod = tree["mod"]
    make = world.make_desktop
    def failed(kind, name=None):
        desk = make(kind, name)
        def unreadable(pid):
            raise KeyError("synthetic lost handle")
        desk.launch_lifetime = unreadable
        return desk
    monkeypatch.setattr(mod, "make_desktop", failed)
    assert mod.cmd_on() == 0
    with pytest.raises((mod.Refused, mod.ProcessReadError)):
        mod.cmd_launch("background")
    assert mod.load_state()["session"]["ownership_refused"]
    assert mod.load_state()["session"]["processes"] == []
    assert world.terminated == [] and tree["dll"].read_bytes() == VALVE_BYTES


@pytest.mark.parametrize("case", ["running", "exited", "wait-failed", "time-error", "generation", "unknown-exit"])
def test_retained_native_handle_reads_creation_and_exit_without_reopening(tree, monkeypatch, case):
    import ctypes
    import ctypes.wintypes as w
    import vr_background_desktop as desktop
    created = desktop.parse_start("2026-10-25T10:00:00Z")
    ended = desktop.parse_start("2026-10-25T10:00:05Z")
    record = dict(pid=90, executable="C:/fixture/vrstartup.exe", start_time=created.isoformat())
    if case == "generation":
        record["start_time"] = "2026-10-25T09:00:00Z"
    def times(handle, *pointers):
        assert handle == 77
        pointers[0]._obj.dwLowDateTime = 1
        pointers[1]._obj.dwLowDateTime = 2 if case == "exited" else 0
        return case != "time-error"
    status = 0 if case in ("exited", "unknown-exit") else 0xffffffff if case == "wait-failed" else 258
    kernel = SimpleNamespace(GetProcessTimes=times, WaitForSingleObject=lambda *a: status)
    monkeypatch.setattr(desktop, "_api", lambda: (None, kernel, w))
    monkeypatch.setattr(desktop, "_filetime", lambda ft: created if ft.dwLowDateTime == 1 else ended)
    monkeypatch.setattr(ctypes, "WinError", lambda *a: OSError("synthetic native read failure"), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    desk = desktop.BackgroundDesktop.__new__(desktop.BackgroundDesktop)
    desk._launch_handles = {90: (77, record)}
    if case in ("running", "exited"):
        proof = desk.launch_lifetime(90)
        assert proof["start_time"] == record["start_time"]
        assert proof["exit_time"] == (ended.isoformat() if case == "exited" else None)
        assert proof["observed_until"]
    else:
        with pytest.raises(OSError):
            desk.launch_lifetime(90)


@pytest.mark.parametrize("failed", ["vrstartup.exe", "sksevr_loader.exe"])
def test_created_launcher_identity_failure_keeps_state_after_later_missed_reads(tree, world, monkeypatch, failed):
    import vr_background_desktop as desktop
    mod = tree["mod"]
    make, read = world.make_desktop, world.read
    lost = []
    def factory(kind, name=None):
        desk = make(kind, name)
        start = desk.start
        def started_then_failed(command, cwd):
            result = start(command, cwd)
            if command[0].name == failed:
                lost.append(True)
                raise desktop.LaunchIdentityError("synthetic identity failure after CreateProcess")
            return result
        desk.start = started_then_failed
        return desk
    monkeypatch.setattr(mod, "make_desktop", factory)
    monkeypatch.setattr(mod, "read_processes", lambda: [] if lost else read())
    with pytest.raises(mod.Refused):
        mod.cmd_run("background")
    assert mod.load_state()["session"]["ownership_refused"]
    assert tree["dll"].read_bytes() == VALVE_BYTES
    assert world.terminated == [] and world.quits == []
