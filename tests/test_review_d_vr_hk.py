"""Round D fixes: native disposable children and synthetic trees only; never launch a game."""
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_vr_headless import tree, world, load, snapshot
from test_housekeep import hk, cfg_for, write, planned, key
from test_housekeep_derived import stage_tree


def test_all_unreadable_threads_need_explicit_ancestry(tree, monkeypatch):
    import vr_background_desktop as d
    monkeypatch.setattr(d, "process_thread_ids", lambda pid: [1])
    def unreadable(tid):
        raise OSError("no user32 desktop")
    monkeypatch.setattr(d, "thread_desktop", unreadable)
    desk = d.BackgroundDesktop.__new__(d.BackgroundDesktop)
    desk.name = "bogus"
    assert not desk.process_on_desktop(55)
    assert desk.process_on_desktop(55, allow_ancestry_only=True)
    monkeypatch.setattr(d, "process_thread_ids", lambda pid: [])
    assert not desk.process_on_desktop(55, allow_ancestry_only=True)
    monkeypatch.setattr(d, "process_thread_ids", lambda pid: [1])
    monkeypatch.setattr(d, "thread_desktop", lambda tid: "wrong")
    assert not desk.process_on_desktop(55, allow_ancestry_only=True)


def test_proven_helper_explicitly_allows_ancestry_only(tree):
    mod = tree["mod"]
    node = dict(Id=55, ParentId=90, Path="C:/synthetic/vrserver.exe", StartTime="2026-10-25T10:00:01Z")
    root = dict(Id=90, ParentId=0, Path="C:/synthetic/vrstartup.exe", StartTime="2026-10-25T10:00:00Z")
    session = dict(processes=[], desktop_mode="background", runtime_launch=dict(
        pid=90, executable=root["Path"], start_time=root["StartTime"]))
    calls = []
    def placed(pid, *, allow_ancestry_only=False):
        calls.append((pid, allow_ancestry_only))
        return allow_ancestry_only
    desk = SimpleNamespace(process_on_desktop=placed)
    assert mod.proven_process(node, session, [root, node], desk)
    assert calls == [(55, True)]
    calls.clear()
    assert not mod.proven_process(node, session, [node], desk)
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows process and desktop APIs")
def test_user32_child_checks_actual_desktop(tree):
    import vr_background_desktop as d
    desk = d.BackgroundDesktop.__new__(d.BackgroundDesktop)
    desk.name = d.thread_desktop(ctypes.windll.kernel32.GetCurrentThreadId())
    code = ("import ctypes,time; ctypes.windll.user32.GetDesktopWindow(); "
            "print('ready',flush=True); time.sleep(3)")
    with subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True) as child:
        assert child.stdout.readline().strip() == "ready"
        assert desk.process_on_desktop(child.pid)
        desk.name = "NonexistentRoundDDesktop"
        assert not desk.process_on_desktop(child.pid)
        assert not desk.process_on_desktop(child.pid, allow_ancestry_only=True)


def test_current_handoff_timeout_names_manual_close(tree, world, capsys):
    mod = tree["mod"]
    before = snapshot(tree)
    world.steam_handoff = True
    make = world.make_desktop
    def no_health(*a, **k):
        desk = make(*a, **k)
        start = desk.start
        def launch(*a, **k):
            result = start(*a, **k)
            world.health = None
            return result
        desk.start = launch
        return desk
    mod.make_desktop = no_health
    world.events.append((250, world.game_exits))
    assert mod.main(["run", "--desktop", "current"]) == 2
    st = mod.load_state()
    assert st["mode"] == "on"
    assert [p["Id"] for p in st["session"]["observed_runtime"]] == [11]
    assert 11 not in world.terminated
    err = capsys.readouterr().err
    assert "Close observed-only SteamVR processes yourself: vrserver (PID 11)" in err
    assert "then run `off`" in err
    world.procs = [p for p in world.procs if p["Id"] != 11]
    assert mod.cmd_off() == 0
    assert snapshot(tree) == before and mod.load_state() is None


@pytest.mark.skipif(sys.platform != "win32", reason="real Toolhelp32 and limited process handle")
def test_denied_handle_never_needs_powershell(tree, monkeypatch):
    import vr_background_desktop as d
    mod = load()
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("powershell.exe", 8)
    def denied(*a):
        raise PermissionError("SYNCHRONIZE denied")
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"]) as child:
        created, running = d.process_generation(child.pid)
        assert running
        monkeypatch.setattr(mod.subprocess, "run", slow)
        monkeypatch.setattr(d, "process_alive", denied)
        assert mod.process_alive(child.pid, created.isoformat())
        assert not mod.process_alive(child.pid, "2000-01-01T00:00:00Z")


@pytest.mark.parametrize("case", ["live", "exited", "absent", "snapshot-error", "null-snapshot-error", "enumeration-error",
                                  "open-error", "race-exit", "time-error", "exit-error"])
def test_native_limited_identity_checks_each_read(tree, monkeypatch, case):
    import ctypes.wintypes as w
    import vr_background_desktop as d
    closed, opened, flags = [], [], []
    error = [0]
    def failure(code):
        error[0] = code
        return 0
    def snapshot(flag, pid):
        flags.append((flag, pid))
        return 0 if case == "null-snapshot-error" else w.HANDLE(-1).value if case == "snapshot-error" else 77
    def first(handle, pointer):
        pointer._obj.th32ProcessID = 55 if case not in ("absent", "enumeration-error") else 99
        return 1
    def next_process(*a):
        return failure(5 if case == "enumeration-error" else 18)
    def open_process(access, inherit, pid):
        opened.append((access, inherit, pid))
        return failure(5 if case == "open-error" else 87) if case in ("open-error", "race-exit") else 88
    def times(*a):
        a[1]._obj.dwLowDateTime = 12345  # creation differs from the other three FILETIMEs
        return failure(5) if case == "time-error" else 1
    def exit_code(handle, pointer):
        pointer._obj.value = 0 if case == "exited" else 259
        return failure(5) if case == "exit-error" else 1
    kernel = SimpleNamespace(CreateToolhelp32Snapshot=snapshot, Process32FirstW=first,
                             Process32NextW=next_process, OpenProcess=open_process,
                             GetProcessTimes=times, GetExitCodeProcess=exit_code, CloseHandle=closed.append)
    monkeypatch.setattr(d, "_api", lambda: (None, kernel, w))
    monkeypatch.setattr(d.ctypes, "get_last_error", lambda: error[0], raising=False)
    monkeypatch.setattr(d.ctypes, "WinError", lambda code: OSError("native read failed"), raising=False)
    if case.endswith("error"):
        with pytest.raises(OSError):
            d.process_generation_limited(55)
    else:
        result = d.process_generation_limited(55)
        assert result is None if case in ("absent", "race-exit") else result == (
            d._filetime(w.FILETIME(dwLowDateTime=12345)), case == "live")
    assert flags == [(2, 0)]
    assert closed == ([] if case.endswith("snapshot-error") else [77, 88] if case in
                      ("live", "exited", "time-error", "exit-error") else [77])
    if opened:
        assert opened == [(d.PROCESS_QUERY_LIMITED_INFORMATION, False, 55)]


@pytest.mark.parametrize("writer", ["text", "bytes", "copy"])
@pytest.mark.parametrize("error", [5, 32, 87])
@pytest.mark.parametrize("persistent", [False, True])
def test_atomic_replace_retries_and_cleans_own_temp(tmp_path, monkeypatch, writer, error, persistent):
    mod = load()
    dst, src = tmp_path / "state.json", tmp_path / "source"
    dst.write_bytes(b"before")
    src.write_bytes(b"after")
    unrelated = tmp_path / ".vrheadless_unrelated"
    unrelated.write_bytes(b"keep")
    replace = os.replace
    calls, sleeps = [], []
    def flaky(a, b):
        calls.append((a, b))
        if persistent or len(calls) < 3:
            exc = PermissionError("busy")
            exc.winerror = error
            raise exc
        replace(a, b)
    monkeypatch.setattr(mod.os, "replace", flaky)
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)
    def run():
        if writer == "copy":
            mod.replace_file(src, dst)
        elif writer == "text":
            mod.write_text_atomic(dst, "after")
        else:
            mod.write_bytes_atomic(dst, b"after")
    if persistent or error == 87:
        with pytest.raises(PermissionError):
            run()
        assert dst.read_bytes() == b"before"
    else:
        run()
        assert dst.read_bytes() == b"after"
    assert len(calls) == (10 if persistent and error in (5, 32) else 1 if error == 87 else 3)
    assert sleeps == [0.05] * (len(calls) - 1)
    assert list(tmp_path.glob(".vrheadless_*")) == [unrelated]


@pytest.mark.parametrize("payload", [b'{"mod_configuration":true}', b'{', b'', b'[]'])
@pytest.mark.parametrize("store", ["manual-installs/backups", ".claude/backups/manual-installs"])
def test_backup_json_payload_does_not_disable_staging(tmp_path, monkeypatch, payload, store):
    monkeypatch.setattr(hk, "TOOLKIT_ROOT", tmp_path)
    monkeypatch.setattr(hk, "staging_activity", lambda path: path.stat().st_mtime)
    old, new, cfg = stage_tree(tmp_path)
    write(tmp_path / store / "op/Data/SKSE/Plugins/settings.json", payload)
    assert planned(hk.plan(cfg, only={"superseded"})) == {key(old)}


@pytest.mark.parametrize("contents", [b'{', b'', b'{"rollback_sources":"bad"}'])
def test_named_backup_manifest_still_refuses_bad_input(tmp_path, monkeypatch, contents):
    monkeypatch.setattr(hk, "TOOLKIT_ROOT", tmp_path)
    path = write(tmp_path / "manual-installs/backups/op/rollback.json", contents)
    with pytest.raises(ValueError):
        hk.manifest_references(cfg_for(tmp_path))


@pytest.mark.parametrize("case", ["valid", "malformed", "wrong-type"])
def test_backup_manifest_recognized_by_shape(tmp_path, monkeypatch, case):
    monkeypatch.setattr(hk, "TOOLKIT_ROOT", tmp_path)
    source = tmp_path / "staging/Mod-1.0/payload"
    contents = (json.dumps({"rollback_sources": [str(source)]}).encode() if case == "valid" else
                b'{"rollback_sources":' if case == "malformed" else b'{"rollback_sources":"bad"}')
    write(tmp_path / "manual-installs/backups/op/other-name.json", contents)
    cfg = cfg_for(tmp_path)
    if case == "valid":
        assert key(hk._real(source)) in hk.manifest_references(cfg)
    else:
        with pytest.raises(ValueError):
            hk.manifest_references(cfg)


@pytest.mark.parametrize("command", ["report", "sweep-temp", "session", "recycle"])
def test_unsafe_staging_allows_other_commands_with_one_warning(tmp_path, monkeypatch, capsys, command):
    config = write(tmp_path / "housekeep.json", json.dumps({
        "root": str(tmp_path), "staging_roots": [str(tmp_path / "MO2/mods")]}).encode())
    calls = []
    if command == "report":
        monkeypatch.setattr(hk, "report", lambda cfg: calls.append(command) or 0)
        args = [command]
    elif command == "sweep-temp":
        monkeypatch.setattr(hk, "sweep_temp", lambda cfg: calls.append(command) or
                            {"rows": {}, "temp": str(tmp_path), "failed": []})
        args = [command]
    elif command == "session":
        monkeypatch.setattr(hk, "session", lambda *a: calls.append(command) or 0)
        args = [command, str(tmp_path / "note")]
    else:
        monkeypatch.setattr(hk, "recycle_refusal", lambda *a: "")
        monkeypatch.setattr(hk, "recycle", lambda paths: calls.append(command) or {p: None for p in paths})
        args = [command, str(tmp_path / "scratch")]
    assert hk.main(["--config", str(config), *args]) == 0
    assert calls == [command]
    out = capsys.readouterr()
    assert (out.out + out.err).count("unsafe staging root") == 1


def test_unsafe_staging_never_scanned_or_applied(tmp_path, monkeypatch):
    cfg = cfg_for(tmp_path, staging_roots=[str(tmp_path / "MO2/mods")])
    unsafe = tmp_path / "MO2/mods"
    old = unsafe / "Mod-1.0"
    old.mkdir(parents=True)
    original = Path.iterdir
    tracked = hk.tracked_files
    def no_unsafe_git(path):
        assert path != unsafe, "unsafe staging root was inspected for tracked files"
        return tracked(path)
    monkeypatch.setattr(hk, "tracked_files", no_unsafe_git)
    def no_unsafe_scan(path):
        assert path != unsafe, "unsafe staging root was scanned"
        return original(path)
    monkeypatch.setattr(Path, "iterdir", no_unsafe_scan)
    assert not planned(hk.plan(cfg, only={"superseded"}))
    action = dict(path=str(old), **{"class": "superseded"}, bytes=0, staging_root=str(unsafe))
    recycled = []
    assert hk.apply({"actions": [action]}, cfg, recycler=lambda p: recycled.extend(p) or {}) == 1
    assert recycled == []
