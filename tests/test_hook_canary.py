"""tools/hook-canary.sh -- the user-facing liveness report -- driven against fixture telemetry.

It is a thin front for `tools/skyguard/health.py report` (the state rules are unit-tested in
tests/test_skyguard_health.py). What THIS file proves is the real script, end to end: it finds an
interpreter, reads the staged records under `.claude/backups/.hook-heartbeat/<agent>/`, and exits per
the table, with every scenario the old heartbeat canary was written for:

  * blind, then recovered       -- a failure clears only on a NEWER COMPLETED evaluation, and says so
  * equal timestamps            -- the same instant proves no ordering
  * a truncated timestamp       -- used to sort LOW and silently clear a failure; now never green
  * stale                       -- an old completion
  * not exercised               -- no records: proves nothing either way

Exit: 1 if any hook is DEGRADED; else 2 if any is UNMEASURABLE or the install cannot be checked; else 0.
The 3.x files (`.hook-heartbeat/<hook>`, `<hook>.blind`) are ignored and the report says so.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import BASH, REPO, install_hooks, offers_python, python_free_path

TOOL = REPO / "tools" / "hook-canary.sh"
HOOK = "protect-bash"


def _install(tmp_path: Path) -> Path:
    root = install_hooks(tmp_path / "install")
    return root


def _hb(root: Path) -> Path:
    d = root / ".claude" / "backups" / ".hook-heartbeat" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _w(root, stage, line, hook=HOOK):
    (_hb(root) / "{}.{}".format(hook, stage)).write_text(line + "\n", encoding="utf-8")


def _complete(root, t, inv="1-1", hook=HOOK):
    _w(root, "invoked", "{} {}".format(t - 0.2, inv), hook)
    _w(root, "received", "{} {} bytes=40".format(t - 0.1, inv), hook)
    _w(root, "evaluated", "{} {} evaluated allow allow".format(t, inv), hook)
    _w(root, "emitted", "{} {} ok".format(t + 0.01, inv), hook)


def _run(root: Path, stale_min=None, args=()):
    env = {
        "CLAUDE_PROJECT_DIR": str(root).replace("\\", "/"),
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        # Without SystemDrive, py.exe / the python3 Store alias create a literal `%SystemDrive%/ProgramData/...`
        # cache folder in the current directory -- the repo, under pytest (found in Plan 4 Task 11).
        "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", ""),
    }
    if stale_min is not None:
        env["HOOK_CANARY_STALE_MIN"] = str(stale_min)
    p = subprocess.run([BASH, str(TOOL), *args], capture_output=True, text=True, env=env, timeout=120)
    line = next((ln for ln in p.stdout.splitlines() if HOOK in ln), "")
    return p.returncode, line, p.stdout


def _status(line):
    # The state is the column right after the hook name: a recovered HEALTHY row names DEGRADED in its detail.
    after = line.split(HOOK, 1)[1].strip() if HOOK in line else ""
    for word in ("NOT EXERCISED", "UNMEASURABLE", "DEGRADED", "STALE", "HEALTHY"):
        if after.startswith(word):
            return word
    return "?"


def test_a_completed_evaluation_reads_healthy_and_exits_0(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 5)
    rc, line, out = _run(root)
    assert _status(line) == "HEALTHY", line
    assert rc == 0, out


def test_nothing_exercised_exits_0_and_does_not_claim_health(tmp_path):
    root = _install(tmp_path)
    rc, line, out = _run(root)
    assert _status(line) == "NOT EXERCISED", out
    assert "UNPROVEN" in out and "OK --" not in out
    assert rc == 0


def test_a_failure_exits_1(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 50)
    _w(root, "failed", "{} 2-2 engine exit 1".format(time.time() - 10))
    rc, line, out = _run(root)
    assert _status(line) == "DEGRADED" and "engine" in line, line
    assert rc == 1, out
    assert "RESULT: DEGRADED" in out


def test_a_receipt_alone_is_not_recovery(tmp_path):
    """The defect class this tool exists for: payload arrival read as proof of life."""
    root = _install(tmp_path)
    _w(root, "failed", "{} 2-2 engine exit 1".format(time.time() - 10))
    _w(root, "invoked", "{} 3-3".format(time.time() - 5))
    _w(root, "received", "{} 3-3 bytes=40".format(time.time() - 5))
    rc, line, _ = _run(root)
    assert _status(line) == "DEGRADED", line
    assert rc == 1


def test_a_newer_completed_evaluation_clears_it_but_says_it_was_degraded(tmp_path):
    root = _install(tmp_path)
    _w(root, "failed", "{} 2-2 engine exit 1".format(time.time() - 100))
    _complete(root, time.time() - 5, inv="3-3")
    rc, line, _ = _run(root)
    assert _status(line) == "HEALTHY" and "recovered" in line and "DEGRADED" in line, line
    assert rc == 0


def test_equal_timestamps_stay_degraded(tmp_path):
    root = _install(tmp_path)
    t = time.time() - 20
    _complete(root, t, inv="3-3")
    _w(root, "failed", "{} 2-2 engine exit 1".format(t))
    rc, line, _ = _run(root)
    assert _status(line) == "DEGRADED", line
    assert rc == 1


def test_a_truncated_stamp_is_unmeasurable_not_green(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 5)
    _w(root, "failed", "20260909_15")
    rc, line, out = _run(root)
    assert _status(line) == "UNMEASURABLE", line
    assert rc == 2, out


def test_a_stale_completion_reads_stale_and_exits_0(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 3 * 3600)
    rc, line, _ = _run(root)
    assert _status(line) == "STALE", line
    assert rc == 0


def test_the_stale_threshold_is_configurable(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 600)
    assert _status(_run(root, stale_min=999)[1]) == "HEALTHY"
    assert _status(_run(root, stale_min=5)[1]) == "STALE"


def test_a_killed_invocation_exits_1(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 1000)
    _w(root, "invoked", "{} 9-9".format(time.time() - 400))
    rc, line, _ = _run(root)
    assert _status(line) == "DEGRADED" and "never finished" in line, line
    assert rc == 1


def test_degraded_outranks_unmeasurable_in_the_exit_code(tmp_path):
    root = _install(tmp_path)
    _w(root, "failed", "{} 2-2 python cannot run".format(time.time() - 10))
    _w(root, "emitted", "garbage", hook="protect-files")
    rc, _, out = _run(root)
    assert "UNMEASURABLE" in out and rc == 1


def test_the_3x_heartbeat_files_are_ignored_and_named(tmp_path):
    """A stale 3.x BLIND must not pin the canary red, and the report says what it skipped."""
    root = _install(tmp_path)
    d = root / ".claude" / "backups" / ".hook-heartbeat"
    d.mkdir(parents=True, exist_ok=True)
    (d / (HOOK + ".blind")).write_text("20260101_000000 NO PAYLOAD\n", encoding="utf-8")
    (d / HOOK).write_text("20260101_000100 payload_bytes=908\n", encoding="utf-8")
    rc, line, out = _run(root)
    assert _status(line) == "NOT EXERCISED", out
    assert "ignored 2 legacy 3.x heartbeat file" in out
    assert rc == 0


def test_json_output_is_machine_readable(tmp_path):
    import json
    root = _install(tmp_path)
    _complete(root, time.time() - 5)
    rc, _, out = _run(root, args=("--json",))
    data = json.loads(out)
    assert data["exit"] == rc == 0
    assert {r["hook"]: r["state"] for r in data["rows"]}[HOOK] == "HEALTHY"


def test_no_hooks_installed_exits_2(tmp_path):
    root = tmp_path / "empty"
    (root / "tools" / "skyguard").mkdir(parents=True)
    for f in (REPO / "tools" / "skyguard").glob("*.py"):
        (root / "tools" / "skyguard" / f.name).write_bytes(f.read_bytes())
    rc, _, _ = _run(root)
    assert rc == 2


def test_with_no_python_it_says_so_and_exits_2(tmp_path):
    """No Python means every blocking hook is refusing every command; the canary must not claim a state."""
    root = tmp_path / "nopy"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "hooks" / "protect-bash.sh").write_text('PY="{{PYTHON_PATH}}"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # a PATH holding only bash's own directory, minus any python3, python or py. Linux CI run 38277736359: bash's
    # directory there is /usr/bin, which holds python3, so this test SKIPPED on every Linux leg (it said "a Python 3
    # is reachable"); python_free_path keeps /usr/bin's other commands through a python-free mirror instead.
    path = python_free_path(str(Path(BASH).parent))
    assert not any(offers_python(d) for d in path.split(os.pathsep) if d), path
    env = {"CLAUDE_PROJECT_DIR": str(root).replace("\\", "/"), "PATH": path,
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "")}
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120)
    assert "UNMEASURABLE: no Python 3" in p.stdout, p.stdout[-1500:]
    assert p.returncode == 2


def test_the_wrapper_telemetry_round_trips_through_the_canary(tmp_path):
    """The real wrappers, the real engine, the real canary -- no fixture records. This is the check that
    would have caught a wrapper writing a record shape the reader cannot parse."""
    import json
    root = install_hooks(tmp_path / "install")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    for hook in ("protect-bash", "snapshot-before-tool"):
        r = subprocess.run([BASH, str(root / ".claude" / "hooks" / (hook + ".sh"))], input=payload,
                           capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 0, r.stderr
    rc, line, out = _run(root)
    assert rc == 0, out
    assert _status(line) == "HEALTHY", out
    assert "snapshot-before-tool" in out and "HEALTHY" in next(
        ln for ln in out.splitlines() if "snapshot-before-tool" in ln)


def test_a_telemetry_directory_hooks_cannot_write_to_is_unmeasurable(tmp_path):
    """The wrappers swallow a failed write, so without this a frozen directory reads HEALTHY, then STALE."""
    root = _install(tmp_path)
    _complete(root, time.time() - 5)
    (_hb(root) / ".canary-probe").mkdir()      # makes the probe write fail on every platform
    rc, line, out = _run(root)
    assert _status(line) == "UNMEASURABLE" and "not writable" in line, out
    assert rc == 2


def test_the_canary_probe_leaves_nothing_behind(tmp_path):
    root = _install(tmp_path)
    _complete(root, time.time() - 5)
    _run(root)
    assert not (_hb(root) / ".canary-probe").exists()
