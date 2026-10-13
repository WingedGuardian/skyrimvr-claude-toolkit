"""tools/bench-vr.py against tests/mock_devbench.py (mode "bench"): no game, no network beyond localhost.

The two rules that make a before/after number mean anything are tested directly: window 1 is discarded, and every
change the run makes for the measurement is put back even when the run fails partway.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO
from test_devbench_cli import Server

TOOL = REPO / "tools" / "bench-vr.py"
_spec = importlib.util.spec_from_file_location("bench_vr", TOOL)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

FAST = ["--windows", "2", "--window", "0.6", "--settle", "0", "--interval", "0.1"]


def run(*args, port=None, log=None, **env):
    e = dict(os.environ)
    e.pop("DEVBENCH_PORT", None)
    if log is not None:
        e["MOCK_LOG"] = str(log)
    e.update({k: str(v) for k, v in env.items()})
    cmd = [sys.executable, "-B", str(TOOL), *map(str, args)]
    if port is not None and args and args[0] == "run":
        cmd += ["--port", str(port)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=e)


def start(tmp_path, **env):
    """A mock in bench mode, with its console log and environment."""
    log = tmp_path / "console.log"
    log.write_text("", encoding="utf-8")
    old = {k: os.environ.get(k) for k in ("MOCK_LOG", "MOCK_FAIL_ON", "MOCK_CELL")}
    os.environ["MOCK_LOG"] = str(log)
    for k, v in env.items():
        os.environ[k] = v
    try:
        s = Server("bench")
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return s, log


def commands(log):
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln]


def result_file(out):
    files = list(Path(out).glob("bench-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_a_run_pins_conditions_measures_and_restores(tmp_path):
    s, log = start(tmp_path)
    try:
        r = run("run", "--label", "before", "--out", tmp_path / "out", *FAST, port=s.port)
    finally:
        s.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    cmds = commands(log)
    assert cmds[:5] == ["show timescale", "show gamehour", "set timescale to 0", "set gamehour to 12",
                        "fw 0010E1EF"], cmds
    assert "coc WhiterunOrigin" in cmds, "the default location is one vanilla coc"
    assert cmds[-3:] == ["tgm", "set gamehour to 9.50", "set timescale to 20.00"], cmds
    res = result_file(tmp_path / "out")
    assert res["conditions"]["window1_discarded"] is True and res["unrestored"] == []
    assert res["results"]["WhiterunOrigin"]["pooled_excluding_window1"]["samples"] > 0


def test_a_failure_partway_still_restores_everything(tmp_path):
    """The console fails at the location's coc: god mode, timescale and game hour must still be put back."""
    s, log = start(tmp_path, MOCK_FAIL_ON="coc WhiterunOrigin")
    try:
        r = run("run", "--label", "x", "--out", tmp_path / "out", *FAST, port=s.port)
    finally:
        s.stop()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Putting back what it changed" in r.stderr
    cmds = commands(log)
    assert cmds[-3:] == ["tgm", "set gamehour to 9.50", "set timescale to 20.00"], cmds
    res = result_file(tmp_path / "out")
    assert "HTTP 500" in res["error"] and res["unrestored"] == []


def test_a_restore_that_fails_is_named_with_its_command(tmp_path):
    s, log = start(tmp_path, MOCK_FAIL_ON="set timescale to 20")
    try:
        r = run("run", "--label", "x", "--out", tmp_path / "out", *FAST, port=s.port)
    finally:
        s.stop()
    assert r.returncode == 1
    assert "NOT RESTORED: timescale: run `set timescale to 20.00` in the console" in r.stdout, r.stdout


def test_the_current_cell_is_never_re_entered(tmp_path):
    s, log = start(tmp_path, MOCK_CELL="WhiterunOrigin")
    try:
        r = run("run", "--label", "x", "--out", tmp_path / "out", *FAST, port=s.port)
    finally:
        s.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "coc WhiterunOrigin" not in commands(log)
    assert "already in WhiterunOrigin" in r.stdout


def test_window_one_is_discarded():
    assert bench.pooled_after_first([[1.0, 2.0], [50.0], [60.0, 70.0]]) == [50.0, 60.0, 70.0]


def test_one_window_is_refused_because_nothing_would_be_left():
    r = run("run", "--label", "x", "--windows", "1", "--port", "1")
    assert r.returncode == 2 and "at least 2" in r.stderr


def test_devbench_not_running_is_refused_cleanly(tmp_path):
    r = run("run", "--label", "x", "--port", "9", "--out", tmp_path / "out")
    assert r.returncode == 2 and "DevBench is not answering" in r.stderr, r.stderr
    assert not (tmp_path / "out").exists()


def test_no_save_loaded_is_refused():
    s = Server("notingame")
    try:
        r = run("run", "--label", "x", port=s.port)
    finally:
        s.stop()
    assert r.returncode == 2 and "no save is loaded" in r.stderr, r.stderr


@pytest.mark.parametrize("line", ["coc Whiterun; killall", "player.additem f 1", "coc ../x", "cow Tamriel 5"])
def test_a_locations_file_can_only_name_places(tmp_path, line):
    f = tmp_path / "locs.txt"
    f.write_text(line + "\n", encoding="utf-8")
    r = run("run", "--label", "x", "--locations", f, "--port", "1")
    assert r.returncode == 2 and "locations" in r.stderr


def test_locations_parse_cells_and_cow():
    assert bench.parse_locations(["# comment", "RiverwoodSleepingGiantInn", "cow Tamriel 5 -5"]) == [
        ("RiverwoodSleepingGiantInn", "coc RiverwoodSleepingGiantInn"), ("Tamriel_5_-5", "cow Tamriel 5 -5")]


def test_the_port_comes_from_runtime_json(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "Data" / "SKSE" / "Plugins" / "devbench").mkdir(parents=True)
    (game / "Data" / "SKSE" / "Plugins" / "devbench" / "runtime.json").write_text('{"port":8933}', encoding="utf-8")
    monkeypatch.setenv("SKYRIM_GAME_ROOT", str(game))
    monkeypatch.delenv("DEVBENCH_PORT", raising=False)
    assert bench.resolve_port(None) == 8933
    (game / "Data" / "SKSE" / "Plugins" / "devbench" / "runtime.json").unlink()
    (game / "SkyrimVR.exe").write_text("", encoding="utf-8")
    assert bench.resolve_port(None) == 8921
    monkeypatch.setenv("DEVBENCH_PORT", "8999")
    assert bench.resolve_port(None) == 8999


def _result(tmp_path, name, label, median, **cond):
    c = {"gamehour": "12", "weather_form": "0010E1EF", "locations": ["A"], "windows": 3, "window_seconds": 30,
         "settle_seconds": 20, "window1_discarded": True}
    c.update(cond)
    p = tmp_path / name
    p.write_text(json.dumps({"label": label, "conditions": c, "results": {"A": {"pooled_excluding_window1": {
        "samples": 50, "median": median, "p5": median - 5}}}}), encoding="utf-8")
    return p


def test_compare_prints_the_delta(tmp_path):
    r = run("compare", _result(tmp_path, "a.json", "before", 70.0), _result(tmp_path, "b.json", "after", 77.0))
    assert r.returncode == 0 and "+7.00, +10.0%" in r.stdout, r.stdout + r.stderr


def test_compare_refuses_runs_under_different_conditions(tmp_path):
    r = run("compare", _result(tmp_path, "a.json", "before", 70.0), _result(tmp_path, "b.json", "after", 77.0, gamehour="18"))
    assert r.returncode == 2 and "gamehour" in r.stderr
