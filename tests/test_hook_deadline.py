"""The bash entries' time limits and the fallback chain's process budget (load-deadline fix, 2026-10-05).

The defect: coreutils `timeout` cannot stop a native python.exe once the CPU is oversubscribed on Windows,
so a hook could outlive its definition timeout -- and then Claude Code discards the deny and Codex runs the
command (fail-open). The entries now:

  1. kill a child (probe, engine) by its WINDOWS pid (`taskkill /F /T`), never by image name;
  2. keep an OUTER deadline (definition timeout - 15 s) and answer on time even while a child is dying;
  3. probe a candidate with ONE process (run.py --selftest refuses old Pythons itself);
  4. remember a verified fallback, and make every fallback engine run prove itself with a nonce;
  5. size each probe from the time left, sub-second where asked;
  6. do not re-probe the configured Python when run.py's own watchdog ended the engine.

Every test drives the REAL wrapper (rendered from the source) with controlled shims. The CPU-load proof is
the opt-in tests/test_hook_deadline_load.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import BASH, REPO, BS
from test_python_fallback import (MISSING, REAL_PY, _clean_path, _esp_write, _failed, _install, _reason,
                                  _shim, _working)

ON_WINDOWS = sys.platform == "win32"
CLAUDE_SRC = REPO / "agent" / "guards" / "adapters" / "claude"
CODEX_SRC = REPO / "agent" / "guards" / "adapters" / "codex"
BLOCKED = "Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."


def _fire(root: Path, shim_dirs: list, payload: dict, extra_env: dict | None = None, timeout: float = 60,
          hook: str = "protect-files.sh", cwd: Path | None = None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), LOCALAPPDATA="", PROGRAMFILES="")
    for k in [k for k in env if k.startswith("SKYGUARD_")]:
        env.pop(k)
    env["SKYGUARD_NO_SYSTEM_PYTHON"] = "1"    # only the shims: not the runner's /usr/bin/python3
    env.update(extra_env or {})
    env["PATH"] = os.pathsep.join([str(d) for d in shim_dirs] + [_clean_path()])
    t = time.time()
    r = subprocess.run([BASH, str(root / ".claude" / "hooks" / hook)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=timeout, cwd=str(cwd) if cwd else None)
    r.took = time.time() - t
    return r


def _alive(pid: int) -> bool:
    if ON_WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return re.search(rf"\b{pid}\b", out) is not None
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(pid: int) -> None:
    """Cleanup of a process THIS test started -- by its pid only."""
    if ON_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _pid_writer(pidfile: Path, seconds: int = 60) -> str:
    """Python source: write our own (Windows) pid, then hang."""
    return ("import os, time\nopen({!r}, 'w').write(str(os.getpid()))\ntime.sleep({})\n"
            .format(str(pidfile), seconds))


def _log_lines(log: Path) -> list:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _cache(root: Path) -> Path:
    return root / ".claude" / "backups" / ".hook-heartbeat" / "claude" / ".python-fallback"


# --------------------------------------------------------------------------------------------------
# the deadline is the DEFINITION's
# --------------------------------------------------------------------------------------------------
def _claude_definition_timeouts() -> dict:
    d = json.loads((REPO / "agent" / "targets" / "claude" / "settings.json").read_text(encoding="utf-8"))
    out = {}
    for groups in d["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                m = re.search(r"\.claude/hooks/([\w-]+\.sh)", h["command"])
                out[m.group(1)] = h["timeout"]
    return out


def test_every_claude_wrapper_carries_its_definition_timeout():
    defs = _claude_definition_timeouts()
    wrappers = sorted(p.name for p in CLAUDE_SRC.glob("*.sh"))
    assert sorted(defs) == wrappers, (sorted(defs), wrappers)
    for name, t in defs.items():
        m = re.findall(r"^HOOK_TIMEOUT_S=(\d+)\b", (CLAUDE_SRC / name).read_text(encoding="utf-8"), re.M)
        assert m == [str(t)], (name, m, t)


def test_the_codex_entry_carries_the_definition_timeouts():
    d = json.loads((REPO / "agent" / "targets" / "codex" / "hooks.json").read_text(encoding="utf-8"))
    want = {"pre-tool": d["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"],
            "session-start": d["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]}
    sh = (CODEX_SRC / "skyguard-entry.sh").read_text(encoding="ascii")
    assert ('HOOK_TIMEOUT_S={}; [ "$ROLE" = "session-start" ] && HOOK_TIMEOUT_S={}'
            .format(want["pre-tool"], want["session-start"])) in sh, want


def test_no_entry_kills_by_image_name_or_uses_coreutils_timeout():
    for f in list(CLAUDE_SRC.glob("*.sh")) + [CODEX_SRC / "skyguard-entry.sh"]:
        t = f.read_text(encoding="utf-8")
        code = "\n".join(line for line in t.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"/IM\b|//IM\b|\bpkill\b|\bkillall\b", code, re.I), f.name
        assert not re.search(r"\btimeout -k\b|command -v timeout", code), f.name


# --------------------------------------------------------------------------------------------------
# 1 + 2: kill by Windows pid; the outer deadline holds even while the child is still dying
# --------------------------------------------------------------------------------------------------
def test_a_hung_engine_on_the_healthy_path_is_killed_by_its_pid(tmp_path):
    """The configured interpreter works, but the engine hangs before run.py's watchdog exists (a hung
    interpreter start looks the same). It used to run unbounded; it must be ended at its limit and the
    PROCESS must be gone -- killed by its Windows pid."""
    root = _install(tmp_path, REAL_PY)
    pidfile = tmp_path / "engine.pid"
    (root / "tools" / "skyguard" / "run.py").write_text(_pid_writer(pidfile), encoding="utf-8")
    try:
        r = _fire(root, [], _esp_write(root), {"SKYGUARD_ENGINE_TIMEOUT": "1"}, timeout=45)
        assert "GUARD INERT" in _reason(r) and "engine failed" in _reason(r), r.stdout
        assert "exit 124" in _failed(root, "protect-files")
        # limit = SKYGUARD_ENGINE_TIMEOUT 1 s + 10 s of room for a slow interpreter start
        assert r.took < 20, f"the engine was not ended at its limit ({r.took:.1f} s)"
        pid = int(pidfile.read_text())
        end = time.time() + 15
        while _alive(pid) and time.time() < end:
            time.sleep(0.3)
        assert not _alive(pid), "the hung engine process outlived the wrapper's kill"
    finally:
        if pidfile.is_file():
            _kill(int(pidfile.read_text()))


def test_with_time_left_the_wrapper_answers_only_once_the_killed_child_is_gone(tmp_path):
    """The kill is not waited for past the deadline -- but while time is left the wrapper does let it
    land, so no hung engine is still running when the agent gets its answer."""
    root = _install(tmp_path, REAL_PY)
    pidfile = tmp_path / "engine.pid"
    (root / "tools" / "skyguard" / "run.py").write_text(_pid_writer(pidfile), encoding="utf-8")
    try:
        r = _fire(root, [], _esp_write(root), {"SKYGUARD_ENGINE_TIMEOUT": "1"}, timeout=45)
        assert "GUARD INERT" in _reason(r), r.stdout
        assert not _alive(int(pidfile.read_text())), "the answer came while the killed engine was still running"
    finally:
        if pidfile.is_file():
            _kill(int(pidfile.read_text()))


def test_a_hung_probe_is_killed_by_its_pid(tmp_path):
    root = _install(tmp_path, MISSING)
    pidfile = tmp_path / "probe.pid"
    src = tmp_path / "hang.py"
    src.write_text(_pid_writer(pidfile), encoding="utf-8")
    _shim(tmp_path / "shims", "python", f'exec "{REAL_PY}" "{src.as_posix()}"\n')
    _working(tmp_path / "shims", "python3")
    try:
        r = _fire(root, [tmp_path / "shims"], _esp_write(root), {"SKYGUARD_PROBE_TIMEOUT": "1"}, timeout=45)
        assert _reason(r).startswith(BLOCKED), r.stdout
        pid = int(pidfile.read_text())
        end = time.time() + 15
        while _alive(pid) and time.time() < end:
            time.sleep(0.3)
        assert not _alive(pid), "the hung probe outlived the wrapper's kill"
    finally:
        if pidfile.is_file():
            _kill(int(pidfile.read_text()))


def _no_op_taskkill(directory: Path, log: Path) -> None:
    """A kill that does not land in time: records the call, kills nothing, takes 20 s."""
    _shim(directory, "taskkill", f'echo "taskkill $*" >> "{log.as_posix()}"\nsleep 20\n')


@pytest.mark.parametrize("entry", ["claude", "codex"])
def test_the_outer_deadline_answers_while_the_child_is_still_dying(tmp_path, entry):
    """Definition timeout 20 s (test knob) -> the deadline is 5 s. The kill is made to fail (a taskkill
    that does nothing for 20 s), so the child keeps running: the answer must still be a deny, well inside
    the definition timeout - 10 s."""
    pidfile = tmp_path / "engine.pid"
    log = tmp_path / "kill.log"
    kdir = tmp_path / "kill"
    _no_op_taskkill(kdir, log)
    env = {"SKYGUARD_HOOK_TIMEOUT_S": "20"}
    try:
        if entry == "claude":
            root = _install(tmp_path, REAL_PY)
            (root / "tools" / "skyguard" / "run.py").write_text(_pid_writer(pidfile), encoding="utf-8")
            r = _fire(root, [kdir], _esp_write(root), env, timeout=40)
            assert "GUARD INERT" in _reason(r) and "engine failed" in _reason(r), r.stdout
            took = r.took
        else:
            import test_codex_entry as ce
            root = ce._install(tmp_path)
            ce._replace_run_py(root, _pid_writer(pidfile))
            e = dict(env, PATH=os.pathsep.join([str(kdir), os.environ.get("PATH", "")]))
            t = time.time()
            r = ce._run("bash", root, "pre-tool", ce._benign(root), e, timeout=40)
            took = time.time() - t
            body = json.loads(r.stdout)["hookSpecificOutput"]
            assert body["permissionDecision"] == "deny" and "did not answer" in body["permissionDecisionReason"], r.stdout
        assert took < 20 - 10, f"the deny came {took:.1f} s after the call, past the deadline"
        if ON_WINDOWS:
            end = time.time() + 10          # the kill is started in the background, after the answer
            while not log.exists() and time.time() < end:
                time.sleep(0.2)
            assert re.search(r"taskkill /+F /+T /+PID \d+", log.read_text(encoding="utf-8")), "no kill by pid"
    finally:
        if pidfile.is_file():
            _kill(int(pidfile.read_text()))


# --------------------------------------------------------------------------------------------------
# 3: one process per candidate
# --------------------------------------------------------------------------------------------------
def test_a_candidate_costs_one_probe_process(tmp_path):
    root = _install(tmp_path, MISSING)
    log = tmp_path / "calls.log"
    _working(tmp_path / "shims", "python", log)
    r = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r).startswith(BLOCKED), r.stdout
    calls = _log_lines(log)
    assert [c for c in calls if "version_info" in c or " -c " in c] == [], calls
    assert len([c for c in calls if "--selftest" in c]) == 1, calls
    assert len(calls) == 2, calls          # the probe and the engine run, nothing else


def test_run_py_selftest_refuses_an_old_python():
    code = ("import runpy, sys; sys.version_info = (3, 7, 9, 'final', 0); "
            "sys.argv = ['run.py', '--selftest', '77']; "
            "runpy.run_path({!r}, run_name='__main__')").format(str(REPO / "tools" / "skyguard" / "run.py"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "skyguard-selftest" not in r.stdout, (r.returncode, r.stdout, r.stderr)


# --------------------------------------------------------------------------------------------------
# 4: the remembered fallback, and the nonce every fallback engine run must echo
# --------------------------------------------------------------------------------------------------
def test_a_verified_fallback_is_remembered_and_not_probed_again(tmp_path):
    root = _install(tmp_path, MISSING)
    log = tmp_path / "calls.log"
    _working(tmp_path / "shims", "python", log)
    r1 = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r1).startswith(BLOCKED), r1.stdout
    assert any("--selftest" in c for c in _log_lines(log))
    log.unlink()
    r2 = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r2).startswith(BLOCKED), r2.stdout
    calls = _log_lines(log)
    assert not any("--selftest" in c for c in calls), calls
    assert len(calls) == 1 and "--nonce" in calls[0], calls
    assert "(verified earlier)" in _failed(root, "protect-files")


def _remembered(root: Path, tmp_path: Path) -> Path:
    """Run once so the fallback is remembered; return the cache file."""
    r = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r).startswith(BLOCKED), r.stdout
    assert _cache(root).is_file()
    return _cache(root)


def _later(p: Path, seconds: float = 30) -> None:
    t = time.time() + seconds
    os.utime(p, (t, t))


@pytest.mark.parametrize("what", ["interpreter", "engine"])
def test_a_remembered_fallback_is_dropped_when_it_or_the_engine_changed(tmp_path, what):
    root = _install(tmp_path, MISSING)
    log = tmp_path / "calls.log"
    _working(tmp_path / "shims", "python", log)
    _remembered(root, tmp_path)
    log.unlink()
    _later(tmp_path / "shims" / "python" if what == "interpreter" else root / "tools" / "skyguard" / "run.py")
    r = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r).startswith(BLOCKED), r.stdout
    assert any("--selftest" in c for c in _log_lines(log)), "a changed " + what + " was not probed again"


def _write_cache(root: Path, *words: str, tag: str = "skyguard-pyfb-v1") -> None:
    p = _cache(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\t".join((tag,) + words) + "\n", encoding="utf-8", newline="\n")


def test_a_cache_naming_a_stub_cannot_substitute_the_interpreter(tmp_path):
    """Writing the cache file is not a way in: the stub answers `{}` (ALLOW) to everything, but every
    fallback engine run must echo the call's nonce first, so it is not used and the chain probes afresh."""
    root = _install(tmp_path, MISSING)
    stub = _shim(tmp_path / "stub", "python", "echo '{}'\nexit 0\n")
    _working(tmp_path / "shims", "python3")
    _write_cache(root, stub.as_posix())
    r = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r).startswith(BLOCKED), r.stdout
    failed = _failed(root, "protect-files")
    assert "used python3" in failed, failed


def test_a_fresh_fallback_that_answers_without_the_nonce_is_refused(tmp_path):
    """A candidate that passes the selftest but answers the engine run with `{}`: no nonce, no verdict.

    Final review C-M7: this asserted `"did not echo the engine nonce" in failed or "engine" in failed`. The nonce
    record is overwritten by the wrapper's `inert engine` record, so only the second clause could ever hold, and it
    matched any engine failure. What is asserted now: the stub WAS given the engine run with a nonce, it exited 0,
    and the wrapper still recorded `engine exit 1` -- the nonce branch is the only step that turns that 0 into 1."""
    root = _install(tmp_path, MISSING)
    log = tmp_path / "engine-runs.log"
    _shim(tmp_path / "shims", "python", f'case "$*" in *--selftest*) exec "{REAL_PY}" "$@" ;; esac\n'
                                        f'echo "$*" >> "{log.as_posix()}"\necho "{{}}"\nexit 0\n')
    r = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert "GUARD INERT" in _reason(r), r.stdout
    runs = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert "--nonce" in runs, runs
    failed = _failed(root, "protect-files")
    assert failed.rstrip("\n").endswith(" engine exit 1"), failed


@pytest.mark.parametrize("bad", ["tag", "name", "relative", "args"])
def test_a_malformed_cache_is_not_used(tmp_path, bad):
    root = _install(tmp_path, MISSING)
    used = tmp_path / "cached.log"
    cdir = tmp_path / "cached"
    name = "notpython" if bad == "name" else "python"
    _working(cdir, name, used)
    _working(tmp_path / "shims", "python3")
    path = (cdir / name).as_posix()
    if bad == "tag":
        _write_cache(root, path, tag="skyguard-pyfb-v0")
    elif bad == "name":
        _write_cache(root, path)
    elif bad == "relative":
        _write_cache(root, "cached/python")
    else:
        _write_cache(root, path, "-c")
    r = _fire(root, [tmp_path / "shims"], _esp_write(root), cwd=tmp_path)
    assert _reason(r).startswith(BLOCKED), r.stdout
    assert not used.exists(), "a malformed cache entry was run:\n" + used.read_text(encoding="utf-8")
    assert "used python3" in _failed(root, "protect-files")


@pytest.mark.parametrize("bad", ["directory", "garbage"])
def test_an_unreadable_cache_is_no_cache(tmp_path, bad):
    root = _install(tmp_path, MISSING)
    _working(tmp_path / "shims", "python")
    p = _cache(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if bad == "directory":
        p.mkdir()
    else:
        p.write_bytes(b"\xff\x00\x01garbage")
    r = _fire(root, [tmp_path / "shims"], _esp_write(root))
    assert _reason(r).startswith(BLOCKED), r.stdout


def test_a_remembered_fallback_that_fails_is_forgotten(tmp_path):
    root = _install(tmp_path, MISSING)
    _working(tmp_path / "shims", "python")
    _remembered(root, tmp_path)
    # the remembered interpreter breaks (exit 3 for everything); a working python3 appears
    (tmp_path / "shims" / "python").write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8", newline="\n")
    t = time.time() - 3600                  # not newer than the cache: only running it shows it is broken
    os.utime(tmp_path / "shims" / "python", (t, t))
    _working(tmp_path / "shims2", "python3")
    r = _fire(root, [tmp_path / "shims", tmp_path / "shims2"], _esp_write(root))
    assert _reason(r).startswith(BLOCKED), r.stdout
    assert "used python3" in _failed(root, "protect-files")
    assert "python3" in _cache(root).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------------------
# 5: probe budgets from the time left; sub-second
# --------------------------------------------------------------------------------------------------
def test_a_probe_gets_only_the_time_left_before_the_deadline(tmp_path):
    """Probe budget asked: 30 s. Definition timeout 30 s (test knob) -> deadline 15 s, no probe after 5 s.
    So the hanging probe gets 5 s, not 30, and nothing is left for another candidate."""
    root = _install(tmp_path, MISSING)
    pidfile = tmp_path / "probe.pid"
    src = tmp_path / "hang.py"
    src.write_text(_pid_writer(pidfile), encoding="utf-8")
    _shim(tmp_path / "shims", "python", f'exec "{REAL_PY}" "{src.as_posix()}"\n')
    try:
        r = _fire(root, [tmp_path / "shims"], _esp_write(root),
                  {"SKYGUARD_PROBE_TIMEOUT": "30", "SKYGUARD_HOOK_TIMEOUT_S": "30"}, timeout=50)
        assert "GUARD INERT" in _reason(r) and "cannot run Python" in _reason(r), r.stdout
        assert r.took < 10, f"the probe was not cut at the time left ({r.took:.1f} s)"
    finally:
        if pidfile.is_file():
            _kill(int(pidfile.read_text()))


def _start_overhead() -> float:
    """Worst of three: bash starting two Pythons, the shim chain's own cost on THIS machine right now."""
    worst = 0.0
    for _ in range(3):
        t = time.perf_counter()
        subprocess.run([BASH, "-c", f'"{REAL_PY}" -c pass; "{REAL_PY}" -c pass'], capture_output=True, check=False)
        worst = max(worst, time.perf_counter() - t)
    return worst


def test_a_sub_second_probe_budget_is_honoured(tmp_path):
    # The gap is 0.5 s plus process starts and the kill landing. A fixed 2 s failed on a slow CI runner
    # (4.01 s, windows-latest 3.13, run 38489393645), so the bound scales with the measured start cost.
    # It is capped at 10 s: a dropped fraction turns 0.5 into the 15 s default, which must stay red.
    overhead = _start_overhead()
    bound = min(0.5 + 3 * overhead + 2.0, 10.0)
    root = _install(tmp_path, MISSING)
    stamps = tmp_path / "stamps.log"
    pidfile = tmp_path / "probe.pid"
    src = tmp_path / "hang.py"
    src.write_text(_pid_writer(pidfile), encoding="utf-8")
    _shim(tmp_path / "shims", "python",
          f'"{REAL_PY}" -c "import time; print(\'python\', time.time())" >> "{stamps.as_posix()}"\n'
          f'exec "{REAL_PY}" "{src.as_posix()}"\n')
    _shim(tmp_path / "shims", "python3",
          f'"{REAL_PY}" -c "import time; print(\'python3\', time.time())" >> "{stamps.as_posix()}"\n'
          f'exec "{REAL_PY}" "$@"\n')
    try:
        r = _fire(root, [tmp_path / "shims"], _esp_write(root), {"SKYGUARD_PROBE_TIMEOUT": "0.5"})
        # only the GAP is asserted: python3's own selftest may or may not fit in 0.5 s on a busy machine
        assert _reason(r), r.stdout
        t = {}
        for line in stamps.read_text(encoding="utf-8").splitlines():
            n, v = line.split()
            t.setdefault(n, float(v))
        gap = t["python3"] - t["python"]
        assert gap < bound, f"a 0.5 s probe held the chain {gap:.2f} s (bound {bound:.2f} s, start cost {overhead:.2f} s)"
    finally:
        if pidfile.is_file():
            _kill(int(pidfile.read_text()))


# --------------------------------------------------------------------------------------------------
# 6: no re-probe after run.py's own watchdog
# --------------------------------------------------------------------------------------------------
def _logged_configured(tmp_path: Path, log: Path) -> Path:
    return _shim(tmp_path / "configured", "python", f'echo "configured $*" >> "{log.as_posix()}"\nexec "{REAL_PY}" "$@"\n')


def test_no_reprobe_after_the_engine_watchdog_fired(tmp_path):
    log = tmp_path / "calls.log"
    py = _logged_configured(tmp_path, log)
    root = _install(tmp_path, py.as_posix())
    run_py = root / "tools" / "skyguard" / "run.py"
    src = run_py.read_text(encoding="utf-8")
    needle = "    raw = sys.stdin.buffer.read()"
    run_py.write_text(src.replace(needle, "    import re; re.match(r'(a+)+$', 'a' * 30 + 'b')\n" + needle, 1),
                      encoding="utf-8", newline="\n")
    r = _fire(root, [], _esp_write(root), {"SKYGUARD_ENGINE_TIMEOUT": "1"})
    assert "GUARD INERT" in _reason(r) and "engine failed" in _reason(r), r.stdout
    assert "Timeout" not in r.stdout
    calls = _log_lines(log)
    assert not any("--selftest" in c for c in calls), "the watchdog's exit sent the configured Python to a re-probe"
    assert len(calls) == 1, calls


def test_an_ordinary_engine_failure_still_reprobes(tmp_path):
    log = tmp_path / "calls.log"
    py = _logged_configured(tmp_path, log)
    root = _install(tmp_path, py.as_posix())
    run_py = root / "tools" / "skyguard" / "run.py"
    src = run_py.read_text(encoding="utf-8")
    needle = "    raw = sys.stdin.buffer.read()"
    run_py.write_text(src.replace(needle, "    raise SystemExit(1)\n" + needle, 1), encoding="utf-8", newline="\n")
    r = _fire(root, [], _esp_write(root))
    assert "GUARD INERT" in _reason(r), r.stdout
    assert any("--selftest" in c for c in _log_lines(log)), _log_lines(log)


def test_run_py_reports_its_watchdog_on_stdout():
    """The wrapper tells "the watchdog ended it" from "the interpreter is broken" by this report."""
    code = ("import runpy, sys, re; sys.argv = ['run.py', 'claude', 'protect-files', '--project', '.']; "
            "import builtins; real = re.match; "
            "re.match = lambda *a, **k: real(r'(a+)+$', 'a' * 30 + 'b'); "
            "runpy.run_path({!r}, run_name='__main__')").format(str(REPO / "tools" / "skyguard" / "run.py"))
    r = subprocess.run([sys.executable, "-c", code], input="{}", capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, SKYGUARD_ENGINE_TIMEOUT="1"))
    assert r.returncode == 1 and r.stdout.startswith("Timeout ("), (r.returncode, r.stdout[:200], r.stderr[:200])
