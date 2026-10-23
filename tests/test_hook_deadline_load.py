"""OPT-IN load test: do the bash hook entries still answer before the agent's own timeout under CPU load?

The defect this guards (investigation 2026-10-05): coreutils `timeout` cannot stop a NATIVE python.exe
once the CPU is oversubscribed on Windows, so a hook could outlive its 60 s definition timeout -- and
then Claude Code discards the deny and Codex runs the command (fail-open). The entries now kill a child
by its Windows PID and keep an outer deadline below the definition timeout.

This file burns CPU on purpose, so it runs only with SKYGUARD_LOAD_TEST=1:

    SKYGUARD_LOAD_TEST=1 python -m pytest tests/test_hook_deadline_load.py -v -s

  SKYGUARD_LOAD_CORES    logical CPUs the cell is pinned to (default 4): the hooks under test and the
                         burners share ONLY these, so the rest of the machine stays free
  SKYGUARD_LOAD_FACTOR   burners per pinned CPU (default 2 -- the level that reproduced the defect)
  SKYGUARD_LOAD_N        runs per scenario (default 3)
  SKYGUARD_LOAD_REPO     a checkout to take the entries FROM (default: this repo) -- point it at an
                         older tree to measure the "before" numbers
  SKYGUARD_LOAD_REPORT   a JSON-lines file every measurement is appended to
  SKYGUARD_LOAD_MAX_S    hard time box per burner in seconds (default 300): a burner stops itself then
  SKYGUARD_LOAD_PRIORITY "idle" (default) or "normal" -- see the resource budget below

Resource budget (unified user rule 2026-10-19: self-started work shares at most 40% of CPU; machine
load stays under the 80% admission line, counting what already runs; load generators never one worker
per core, idle priority, time-boxed, tree-killed):
  * before any burner starts, machine CPU use is MEASURED over 3 s; the cell runs only if
    busy + pinned cores <= 80% of the logical CPUs AND pinned cores <= 40% of them -- otherwise the
    test SKIPS and prints the numbers (it never "just runs");
  * the burners are pinned to the cell's cores, so however many there are they can take at most CORES
    CPUs of the machine;
  * burners run at IDLE priority by default. Honest trade-off: an idle burner yields to the hook under
    test, so the default no longer reproduces the starvation the defect needs -- it proves the deadline
    machinery works under background load, not under oversubscription. SKYGUARD_LOAD_PRIORITY=normal
    reproduces the defect and must only be used with the user's explicit OK for that run;
  * every burner exits by itself after SKYGUARD_LOAD_MAX_S and is stopped by its own pid (whole tree on
    Windows) right after its scenario -- never by image name.
Pass criterion, per the investigation: the WORST run of every scenario ends below the definition timeout
minus 10 s -- a p95 is the wrong statistic for a fail-open bound.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import BASH, REPO, BS, python_free_path

pytestmark = pytest.mark.skipif(os.environ.get("SKYGUARD_LOAD_TEST") != "1",
                                reason="opt-in CPU load test: set SKYGUARD_LOAD_TEST=1")

SRC = Path(os.environ.get("SKYGUARD_LOAD_REPO") or REPO)
REAL_PY = sys.executable.replace(BS, "/")
ON_WINDOWS = sys.platform == "win32"
FACTOR = float(os.environ.get("SKYGUARD_LOAD_FACTOR", "2"))
CORES = int(os.environ.get("SKYGUARD_LOAD_CORES", "4"))
N = int(os.environ.get("SKYGUARD_LOAD_N", "3"))
REPORT = os.environ.get("SKYGUARD_LOAD_REPORT", "")
MISSING = "C:/definitely/not/here/python.exe"
DEF_TIMEOUT = 60          # the frozen PreToolUse definition timeout, both agents
CAP = 240                 # a run still going after this is recorded as >= CAP and stopped
MAX_S = int(os.environ.get("SKYGUARD_LOAD_MAX_S", "300"))
PRIORITY = os.environ.get("SKYGUARD_LOAD_PRIORITY", "idle")
MACHINE_CAP = 0.80        # admission line: machine load under 80%, counting what already runs
CPU_HEAVY_CAP = 0.40      # user rule 2026-10-19: self-started work shares at most 40% of CPU
IDLE_PRIORITY_CLASS = 0x00000040


def _busy_cpus(seconds: float = 3.0) -> float:
    """Logical CPUs' worth of CPU in use machine-wide, averaged over `seconds` (not one instant)."""
    total = os.cpu_count() or 1
    if ON_WINDOWS:
        import ctypes
        from ctypes import wintypes

        def times():
            idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                raise OSError("GetSystemTimes")
            v = [(f.dwHighDateTime << 32) | f.dwLowDateTime for f in (idle, kern, user)]
            return v[0], v[1] + v[2]                       # kernel time includes idle time
        i0, t0 = times()
        time.sleep(seconds)
        i1, t1 = times()
        span = (t1 - t0) or 1
        return total * (1 - (i1 - i0) / span)
    with open("/proc/stat") as f:
        a = [int(x) for x in f.readline().split()[1:]]
    time.sleep(seconds)
    with open("/proc/stat") as f:
        b = [int(x) for x in f.readline().split()[1:]]
    d = [y - x for x, y in zip(a, b)]
    span = sum(d) or 1
    return total * (1 - (d[3] + (d[4] if len(d) > 4 else 0)) / span)


def _budget_check(k: int) -> float:
    """Skip (with the numbers) unless the cell fits the user's resource budget. Returns the busy CPUs measured."""
    total = os.cpu_count() or 1
    busy = _busy_cpus()
    if k > CPU_HEAVY_CAP * total:
        pytest.skip(f"resource budget: {k} pinned cores > {CPU_HEAVY_CAP:.0%} of {total} CPUs")
    if busy + k > MACHINE_CAP * total:
        pytest.skip(f"resource budget: {busy:.1f} CPUs busy now + {k} pinned > {MACHINE_CAP:.0%} of {total}; "
                    "wait for the machine to quieten or lower SKYGUARD_LOAD_CORES")
    return busy


def _burner_cmd() -> list:
    # Self-terminating: the burner stops itself after MAX_S even if this process dies without cleanup.
    return [sys.executable, "-c", f"import time\ne=time.monotonic()+{MAX_S}\nwhile time.monotonic()<e: pass"]


def _start_burner():
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if PRIORITY == "idle":
        if ON_WINDOWS:
            kw["creationflags"] = IDLE_PRIORITY_CLASS
        else:
            kw["preexec_fn"] = lambda: os.nice(19)
    return subprocess.Popen(_burner_cmd(), **kw)


def _stop(p) -> None:
    """By this module's own pid only -- the whole tree on Windows; never by image name."""
    try:
        if ON_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
    except OSError:
        pass


def _get_affinity():
    if ON_WINDOWS:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                             ctypes.POINTER(ctypes.c_size_t))
        proc, system = ctypes.c_size_t(), ctypes.c_size_t()
        if not k.GetProcessAffinityMask(k.GetCurrentProcess(), ctypes.byref(proc), ctypes.byref(system)):
            raise OSError(ctypes.get_last_error(), "GetProcessAffinityMask")
        return proc.value
    return set(os.sched_getaffinity(0))


def _set_affinity(value) -> None:
    if ON_WINDOWS:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.c_size_t)
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(), value):
            raise OSError(ctypes.get_last_error(), "SetProcessAffinityMask")
    else:
        os.sched_setaffinity(0, value)


@contextlib.contextmanager
def _load_cell(factor: float, pin: bool = True):
    """One CELL's load, within a CPU cap: this process is pinned to CORES logical CPUs (the last ones), so the
    burners it starts AND every hook/engine it launches inherit that affinity -- `factor` burners per pinned
    core oversubscribe those cores while the machine as a whole loses at most CORES CPUs. factor 0 = idle (no
    burner; still pinned unless pin=False). The burners run only for this cell and are stopped by their own
    handles when it ends, on failure too (finally); the affinity is restored."""
    total = os.cpu_count() or 1
    k = max(1, min(CORES, total))
    busy = _budget_check(k)                                 # measured BEFORE anything starts
    cores = list(range(total - k, total))
    before = _get_affinity()
    if pin:
        _set_affinity(sum(1 << c for c in cores) if ON_WINDOWS else set(cores))
    n = round(factor * k)
    burners = []
    try:
        for _ in range(n):
            burners.append(_start_burner())
        if n:
            time.sleep(2)
        yield {"burners": n, "cores": k if pin else total, "priority": PRIORITY, "factor": factor,
               "busy_cpus_before": round(busy, 2)}
    finally:
        for p in burners:
            _stop(p)
        for p in burners:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        _set_affinity(before)


@pytest.fixture()
def load():
    """The existing scenarios' cell: FACTOR burners per pinned core (see _load_cell)."""
    with _load_cell(max(FACTOR, 1.0 / max(1, CORES))) as cell:
        yield cell


def _record(row: dict) -> None:
    print(json.dumps(row))
    if REPORT:
        with open(REPORT, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def _clean_path() -> str:
    return python_free_path()    # no python on it, and cat/mkdir still there (Linux: /usr/bin)


def _shim(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    (directory / name).chmod(0o755)


def _claude_install(tmp: Path, py: str) -> Path:
    root = tmp / "install"
    (root / ".claude" / "hooks").mkdir(parents=True)
    text = (SRC / "agent" / "guards" / "adapters" / "claude" / "protect-files.sh").read_text(encoding="utf-8")
    (root / ".claude" / "hooks" / "protect-files.sh").write_text(text.replace("{{PYTHON_PATH}}", py),
                                                                  encoding="utf-8", newline="\n")
    shutil.copytree(SRC / "tools" / "skyguard", root / "tools" / "skyguard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if (SRC / "tools" / "modding_harness").is_dir():    # the engine imports it; the pre-fix tree has none
        shutil.copytree(SRC / "tools" / "modding_harness", root / "tools" / "modding_harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _codex_install(tmp: Path, py: str) -> Path:
    root = tmp / "Program Files (x86)" / "Skyrim VR"
    (root / ".codex" / "hooks").mkdir(parents=True)
    for name in ("skyguard-entry.ps1", "skyguard-entry.sh"):
        text = (SRC / "agent" / "guards" / "adapters" / "codex" / name).read_text(encoding="ascii")
        text = text.replace("{{PYTHON_PATH}}", py).replace("{{BASH_PATH}}", BASH.replace(BS, "/"))
        (root / ".codex" / "hooks" / name).write_text(text, encoding="ascii", newline="\n")
    shutil.copytree(SRC / "tools" / "skyguard", root / "tools" / "skyguard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if (SRC / "tools" / "modding_harness").is_dir():    # the engine imports it; the pre-fix tree has none
        shutil.copytree(SRC / "tools" / "modding_harness", root / "tools" / "modding_harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(SRC / "tools" / "kb-guard.sh", root / "tools" / "kb-guard.sh")
    return root


def _hang_run_py(root: Path) -> None:
    (root / "tools" / "skyguard" / "run.py").write_text("import time\ntime.sleep(150)\n", encoding="utf-8")


def _regex_run_py(root: Path) -> None:
    """The real engine, stuck in a catastrophic regex: only run.py's own watchdog ends it (20 s default)."""
    p = root / "tools" / "skyguard" / "run.py"
    src = p.read_text(encoding="utf-8")
    needle = "    raw = sys.stdin.buffer.read()"
    p.write_text(src.replace(needle, "    import re; re.match(r'(a+)+$', 'a' * 40 + 'b')\n" + needle, 1),
                 encoding="utf-8", newline="\n")


def _payload_claude(root: Path) -> bytes:
    return json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{root.as_posix()}/Data/X.esp",
                                                            "content": "x"}}).encode()


def _payload_codex(root: Path) -> bytes:
    d = json.loads((REPO / "tests" / "fixtures" / "payloads" / "codex" / "0.160.0" /
                    "codex-PreToolUse-Bash-1.json").read_text(encoding="utf-8"))
    d["cwd"] = root.as_posix()
    d["tool_input"]["command"] = "Get-Date"
    return json.dumps(d).encode()


def _timed(cmd: list, data: bytes, env: dict) -> tuple:
    t = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        out, _ = p.communicate(data, timeout=CAP)
        return time.time() - t, out
    except subprocess.TimeoutExpired:
        # the hook itself (and its tree) -- by PID, never by image name
        if ON_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        try:
            p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        return float(CAP), b""


def _env(extra: dict | None = None, path: str | None = None) -> dict:
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("SKYGUARD_"):
            e.pop(k)
    e.update(LOCALAPPDATA="", PROGRAMFILES="", SKYGUARD_NO_SYSTEM_PYTHON="1")
    if path is not None:
        e["PATH"] = path
    e.update(extra or {})
    return e


def _claude_scenario(tmp: Path, name: str):
    shims = tmp / "shims"
    if name == "hot-hang":                     # configured PY fine, engine hangs before any watchdog
        root = _claude_install(tmp, REAL_PY)
        _hang_run_py(root)
        return root, _env(), [BASH, str(root / ".claude" / "hooks" / "protect-files.sh")]
    if name == "hot-watchdog":                 # configured PY fine, the engine's own watchdog fires (20 s)
        root = _claude_install(tmp, REAL_PY)
        _regex_run_py(root)
        return root, _env(), [BASH, str(root / ".claude" / "hooks" / "protect-files.sh")]
    if name == "fallback-hang-engine":         # configured PY missing; the fallback's engine run hangs (native)
        root = _claude_install(tmp, MISSING)
        _shim(shims, "python", f'case "$*" in *--selftest*|*version_info*) exec "{REAL_PY}" "$@" ;; esac\n'
                               f'exec "{REAL_PY}" -c "import time; time.sleep(150)"\n')
        return root, _env(path=os.pathsep.join([str(shims), _clean_path()])), \
            [BASH, str(root / ".claude" / "hooks" / "protect-files.sh")]
    if name == "fallback-hang-probe":          # configured PY missing; the first candidate hangs (native)
        root = _claude_install(tmp, MISSING)
        _shim(shims, "python", f'exec "{REAL_PY}" -c "import time; time.sleep(150)"\n')
        _shim(shims, "python3", f'exec "{REAL_PY}" "$@"\n')
        return root, _env(path=os.pathsep.join([str(shims), _clean_path()])), \
            [BASH, str(root / ".claude" / "hooks" / "protect-files.sh")]
    raise AssertionError(name)


CLAUDE = ["hot-hang", "hot-watchdog", "fallback-hang-engine", "fallback-hang-probe"]
CODEX = ["bash-hang", "bash-fallback-hang-engine"] + (["pwsh-hang"] if ON_WINDOWS and shutil.which("pwsh") else [])


@pytest.mark.parametrize("scenario", CLAUDE)
def test_claude_entry_answers_before_its_definition_timeout(tmp_path, load, scenario):
    worst = 0.0
    for i in range(N):
        tmp = tmp_path / f"r{i}"
        root, env, cmd = _claude_scenario(tmp, scenario)
        took, out = _timed(cmd, _payload_claude(root), env)
        deny = b'"permissionDecision":"deny"' in out.replace(b" ", b"")
        _record({"entry": "claude-bash", "scenario": scenario, "run": i, "seconds": round(took, 1),
                 "deny": deny, "burners": load["burners"], "cores": load["cores"], "src": str(SRC)})
        worst = max(worst, took)
        assert deny, out
    assert worst < DEF_TIMEOUT - 10, f"{scenario}: worst {worst:.1f} s against {DEF_TIMEOUT} - 10 s"


@pytest.mark.parametrize("scenario", CODEX)
def test_codex_entry_answers_before_its_definition_timeout(tmp_path, load, scenario):
    worst = 0.0
    for i in range(N):
        tmp = tmp_path / f"r{i}"
        tmp.mkdir()
        if scenario == "bash-fallback-hang-engine":
            root = _codex_install(tmp, MISSING)
            shims = tmp / "shims"
            _shim(shims, "python", f'case "$*" in *--selftest*|*version_info*) exec "{REAL_PY}" "$@" ;; esac\n'
                                   f'exec "{REAL_PY}" -c "import time; time.sleep(150)"\n')
            env = _env(path=os.pathsep.join([str(shims), _clean_path()]))
        else:
            root = _codex_install(tmp, REAL_PY)
            _hang_run_py(root)
            env = _env()
        hooks = root / ".codex" / "hooks"
        if scenario.startswith("pwsh"):
            cmd = [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(hooks / "skyguard-entry.ps1"), "pre-tool"]
        else:
            cmd = [BASH, str(hooks / "skyguard-entry.sh"), "pre-tool"]
        took, out = _timed(cmd, _payload_codex(root), env)
        deny = b'"permissionDecision":"deny"' in out.replace(b" ", b"")
        _record({"entry": "codex-" + scenario.split("-")[0], "scenario": scenario, "run": i,
                 "seconds": round(took, 1), "deny": deny, "burners": load["burners"], "cores": load["cores"], "src": str(SRC)})
        worst = max(worst, took)
        assert deny, out
    assert worst < DEF_TIMEOUT - 10, f"{scenario}: worst {worst:.1f} s against {DEF_TIMEOUT} - 10 s"


EXISTING = [
    "tests/test_python_fallback.py::test_a_catastrophic_regex_inside_the_engine_is_killed_at_the_budget",
    "tests/test_python_fallback.py::test_a_hanging_fallback_engine_is_cut_off_by_timeout",
    "tests/test_python_fallback.py::test_a_hanging_probe_is_cut_off_and_the_chain_moves_on",
    "tests/test_python_fallback.py::test_known_locations_are_tried_newest_version_first",
    "tests/test_codex_entry.py::test_a_hung_engine_is_a_deny",
    "tests/test_codex_entry.py::test_a_hung_engine_is_killed_with_its_children",
]


def test_the_existing_hung_engine_tests_pass_under_load(load):
    """The suite's own hung-engine / hanging-probe tests, run from SRC while the burners run."""
    r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rfE",
                        "--durations=0", "-W", "ignore", *EXISTING],
                       cwd=str(SRC), capture_output=True, text=True, timeout=3600,
                       env={k: v for k, v in os.environ.items() if k != "SKYGUARD_LOAD_TEST"})
    tail = "\n".join(r.stdout.splitlines()[-40:])
    _record({"entry": "pytest", "scenario": "existing hung-engine tests", "rc": r.returncode,
             "summary": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "", "burners": load["burners"], "cores": load["cores"],
             "src": str(SRC)})
    print(tail)
    assert r.returncode == 0, tail


# =====================================================================================================================
# Plan 8 T14b: the latency and deadline MATRIX (entries x paths x load), the idle A/B against v3.9.1, the kill-latency
# probe, and a report mode. Each part has its own switch on top of SKYGUARD_LOAD_TEST=1, because the matrix takes hours:
#
#   SKYGUARD_LOAD_MATRIX=1            the matrix cells (test_matrix_cell)
#   SKYGUARD_LOAD_LEVELS="0,1,2"      load levels: burners per pinned core (0 = idle, still pinned)
#   SKYGUARD_LOAD_ENTRIES / _PATHS    comma lists selecting cells (default: all)
#   SKYGUARD_LOAD_N_LOW / _N_HIGH     runs per cell below 2x / at 2x and above (default 5 / 10)
#   SKYGUARD_LOAD_STOP_AFTER_FAILS    stop a cell after this many over-deadline runs (0 = never; the RED run uses it:
#                                     a cell is already proven red, and every further run costs the cap)
#   SKYGUARD_LOAD_LABEL               the tree's label in the report rows (e.g. "green", "red-544efd4")
#   SKYGUARD_LOAD_PROBE=1             the kill-latency probe at SKYGUARD_LOAD_FACTOR
#   SKYGUARD_LOAD_AB=1                the idle hot-path A/B; SKYGUARD_LOAD_AB_BASE = a v3.9.1 checkout,
#                                     SKYGUARD_LOAD_AB_N runs per entry (default 40), SKYGUARD_LOAD_AB_BAR_MS (1000)
#
#   python tests/test_hook_deadline_load.py report <rows.jsonl> [--out file.md]   renders the matrix from the rows
#
# Every hook runs in a SCRATCH install built from SKYGUARD_LOAD_REPO; the failure paths are mutations of that copy's
# run.py, never switches in the product. A run is timed from spawn to the entry's exit AND to its stdout closing (a
# grandchild holding the pipe delays the agent too); the later of the two is the number judged.
# =====================================================================================================================

LABEL = os.environ.get("SKYGUARD_LOAD_LABEL", "")
BASE_PY = (getattr(sys, "_base_executable", "") or sys.executable).replace(BS, "/")
N_LOW = int(os.environ.get("SKYGUARD_LOAD_N_LOW", "5"))
N_HIGH = int(os.environ.get("SKYGUARD_LOAD_N_HIGH", "10"))
STOP_AFTER = int(os.environ.get("SKYGUARD_LOAD_STOP_AFTER_FAILS", "0"))
MARGIN_S = 10             # pass: worst < definition timeout - 10 s
OUTER_S = 15              # the entries' own outer deadline is definition timeout - 15 s: 45-50 s is a margin WARNING

CLAUDE_KIND = {"protect-bash": "gate", "protect-files": "gate", "protect-mcp": "gate",
               "backup-before-edit": "side", "snapshot-before-tool": "side", "session-kb-guard": "session"}
ENTRIES = [f"claude:{h}" for h in CLAUDE_KIND] + ["codex:bash", "codex:powershell", "codex:pwsh"]
PATHS = ["healthy", "fallback", "engine-fail", "hang-pre-watchdog", "hang-post-watchdog"]
FAILURE_PATHS = {"engine-fail", "hang-pre-watchdog", "hang-post-watchdog"}
ANCHOR = "    ap = argparse.ArgumentParser()"     # in main(), AFTER the watchdog is armed (both trees)


def _csv_env(name: str, default: list) -> list:
    v = os.environ.get(name, "")
    return [x.strip() for x in v.split(",") if x.strip()] if v else list(default)


def _kind(entry: str) -> str:
    return CLAUDE_KIND[entry.split(":", 1)[1]] if entry.startswith("claude:") else "gate"


def _def_timeout(entry: str) -> int:
    return 120 if entry == "claude:session-kb-guard" else DEF_TIMEOUT


def _expected(entry: str, path: str) -> str:
    kind = _kind(entry)
    failing = path in FAILURE_PATHS
    if kind == "gate":
        return "inert" if failing else "deny"
    if kind == "side":
        return "failed-recorded" if failing else "done"
    return "not-run" if failing else "done"


def _sys_times():
    """(idle, total) CPU time machine-wide, for the load beside each run. None off Windows."""
    if not ON_WINDOWS:
        try:
            with open("/proc/stat") as f:
                a = [int(x) for x in f.readline().split()[1:]]
            return a[3] + (a[4] if len(a) > 4 else 0), sum(a)
        except OSError:
            return None
    import ctypes
    from ctypes import wintypes
    idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None
    v = [(f.dwHighDateTime << 32) | f.dwLowDateTime for f in (idle, kern, user)]
    return v[0], v[1] + v[2]


def _busy_pct(a, b):
    if not a or not b or b[1] == a[1]:
        return None
    return round(100.0 * (1 - (b[0] - a[0]) / (b[1] - a[1])), 1)


def _kill_pid_tree(pid: int) -> None:
    """By the pid this module started -- never by image name."""
    if ON_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _run_timed(cmd: list, data: bytes, env: dict, cap: float) -> dict:
    """Spawn, feed the payload, time the exit and the stdout EOF separately; stop the tree at `cap`."""
    s0 = _sys_times()
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    chunks: list = []
    eof_at: list = []

    def reader():
        try:
            chunks.append(p.stdout.read())
        finally:
            eof_at.append(time.perf_counter() - t0)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        p.stdin.write(data)
        p.stdin.close()
    except OSError:
        pass
    capped = False
    try:
        p.wait(timeout=cap)
        t_exit = time.perf_counter() - t0
    except subprocess.TimeoutExpired:
        capped = True
        _kill_pid_tree(p.pid)
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        t_exit = float(cap)
    th.join(timeout=max(0.1, cap - (time.perf_counter() - t0)))
    t_eof = eof_at[0] if eof_at else float(cap)
    if not eof_at:
        capped = True
    s1 = _sys_times()
    return {"t_exit": round(t_exit, 3), "t_eof": round(min(t_eof, cap), 3), "seconds": round(max(t_exit, min(t_eof, cap)), 3),
            "capped": capped, "out": b"".join(chunks), "rc": p.returncode, "machine_busy_pct": _busy_pct(s0, s1)}


def _mutate_run_py(root: Path, path: str) -> None:
    p = root / "tools" / "skyguard" / "run.py"
    if path == "hang-pre-watchdog":
        # the interpreter never reaches main(): a hang at start-up/import, before any watchdog is armed (native)
        p.write_text("import time\ntime.sleep(150)\n", encoding="utf-8", newline="\n")
        return
    if path not in ("engine-fail", "hang-post-watchdog"):
        return
    src = p.read_text(encoding="utf-8")
    assert src.count(ANCHOR) == 1, f"run.py anchor not found once in {p}"
    ins = "    return 3\n" if path == "engine-fail" else "    import time; time.sleep(150)\n"
    p.write_text(src.replace(ANCHOR, ins + ANCHOR, 1), encoding="utf-8", newline="\n")


def _matrix_install(tmp: Path, entry: str, path: str) -> Path:
    """A scratch install of SRC: the entry, the engine, the KB files, the install declared as the game root."""
    root = tmp / "Skyrim VR"
    py = MISSING if path == "fallback" else BASE_PY
    agent, name = entry.split(":", 1)
    if agent == "claude":
        (root / ".claude" / "hooks").mkdir(parents=True)
        text = (SRC / "agent" / "guards" / "adapters" / "claude" / f"{name}.sh").read_text(encoding="utf-8")
        (root / ".claude" / "hooks" / f"{name}.sh").write_text(text.replace("{{PYTHON_PATH}}", py),
                                                              encoding="utf-8", newline="\n")
    else:
        (root / ".codex" / "hooks").mkdir(parents=True)
        (root / ".claude").mkdir(parents=True)
        for f in ("skyguard-entry.ps1", "skyguard-entry.sh"):
            text = (SRC / "agent" / "guards" / "adapters" / "codex" / f).read_text(encoding="ascii")
            text = text.replace("{{PYTHON_PATH}}", py).replace("{{BASH_PATH}}", BASH.replace(BS, "/"))
            (root / ".codex" / "hooks" / f).write_text(text, encoding="ascii", newline="\n")
    shutil.copytree(SRC / "tools" / "skyguard", root / "tools" / "skyguard", ignore=shutil.ignore_patterns("__pycache__"))
    if (SRC / "tools" / "modding_harness").is_dir():    # the engine imports it; the pre-fix tree has none
        shutil.copytree(SRC / "tools" / "modding_harness", root / "tools" / "modding_harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(SRC / "tools" / "kb-guard.sh", root / "tools" / "kb-guard.sh")
    for f in ("KNOWLEDGEBASE.md", "CLAUDE.md"):
        if (SRC / f).is_file():
            shutil.copy2(SRC / f, root / f)
    env_text = f'SKYRIM_GAME_ROOT="{root.as_posix()}"\n'
    for d in (root, root / ".claude"):          # the 4.0 location and the legacy one (the pre-fix tree reads only it)
        (d / "skyrim-paths.env").write_text(env_text, encoding="utf-8", newline="\n")
    (root / "Data").mkdir()
    (root / "Data" / "a.ini").write_text("[x]\n", encoding="utf-8")
    _mutate_run_py(root, path)
    return root


def _payload(entry: str, root: Path) -> bytes:
    r = root.as_posix()
    delete = f'rm "{r}/Data/Textures/x.dds"'
    if entry.startswith("codex:"):
        d = json.loads((REPO / "tests" / "fixtures" / "payloads" / "codex" / "0.160.0" /
                        "codex-PreToolUse-Bash-1.json").read_text(encoding="utf-8"))
        d["cwd"] = r
        d["tool_input"]["command"] = delete
        return json.dumps(d).encode()
    name = entry.split(":", 1)[1]
    p = {"protect-bash": {"tool_name": "Bash", "tool_input": {"command": delete}},
         "protect-files": {"tool_name": "Write", "tool_input": {"file_path": f"{r}/Data/X.esp", "content": "x"}},
         "protect-mcp": {"tool_name": "mcp__devbench-vr__console", "tool_input": {"action": "exec", "command": "smp reset"}},
         "backup-before-edit": {"tool_name": "Edit", "tool_input": {"file_path": f"{r}/Data/a.ini"}},
         "snapshot-before-tool": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
         "session-kb-guard": {"hook_event_name": "SessionStart", "source": "startup"}}[name]
    p.setdefault("session_id", "t14b")
    return json.dumps(p).encode()


def _entry_cmd(entry: str, root: Path) -> list:
    agent, name = entry.split(":", 1)
    if agent == "claude":
        return [BASH, str(root / ".claude" / "hooks" / f"{name}.sh")]
    entry_file = root / ".codex" / "hooks"
    if name == "bash":
        return [BASH, str(entry_file / "skyguard-entry.sh"), "pre-tool"]
    exe = shutil.which("pwsh" if name == "pwsh" else "powershell")
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
            str(entry_file / "skyguard-entry.ps1"), "pre-tool"]


def _entry_env(root: Path) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("SKYGUARD_")}
    e["CLAUDE_PROJECT_DIR"] = str(root)
    return e


def _outcome(entry: str, out: bytes, root: Path) -> str:
    text = out.decode("utf-8", "replace")
    kind = _kind(entry)
    if kind == "gate" or '"permissionDecision"' in text:
        flat = text.replace(" ", "")
        if '"permissionDecision":"deny"' in flat:
            return "inert" if "GUARD INERT" in text else "deny"
        if kind == "gate":
            return "none" if not text.strip() else "allow"
    name = entry.split(":", 1)[1]
    hb = root / ".claude" / "backups" / ".hook-heartbeat" / "claude"
    if kind == "session" and "NOT RUN" in text:
        return "not-run"
    audit = root / ".claude" / "backups" / "AUDIT_LOG.txt"
    try:
        failed_line = any(name in ln and "FAILED" in ln for ln in audit.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        failed_line = False
    if kind == "side" and failed_line:
        return "failed-recorded"
    if (hb / f"{name}.emitted").is_file():
        return "done"
    return "silent"


def _cells() -> list:
    levels = [float(x) for x in _csv_env("SKYGUARD_LOAD_LEVELS", ["0", "1", "2"])]
    entries = _csv_env("SKYGUARD_LOAD_ENTRIES", ENTRIES)
    paths = _csv_env("SKYGUARD_LOAD_PATHS", PATHS)
    if not (ON_WINDOWS and shutil.which("pwsh")):
        entries = [e for e in entries if e != "codex:pwsh"]
    if not ON_WINDOWS:
        entries = [e for e in entries if e != "codex:powershell"]
    return [(lv, e, p) for lv in levels for e in entries for p in paths]


_META_DONE: list = []


def _version(cmd: list) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else f"rc={r.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"unavailable ({e.__class__.__name__})"


def _meta() -> None:
    """Once per session: the machine and toolchain beside every number."""
    if _META_DONE:
        return
    _META_DONE.append(1)
    cpu = ""
    if ON_WINDOWS:
        cpu = _version(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"])
    src_rev = _version(["git", "-C", str(SRC), "rev-parse", "--short", "HEAD"])
    row = {"kind": "meta", "label": LABEL, "src": str(SRC), "src_rev": src_rev, "cpu": cpu,
           "logical_cpus": os.cpu_count(), "cores_pinned": CORES, "priority": PRIORITY,
           "git": _version(["git", "--version"]), "bash": _version([BASH, "--version"]),
           "bash_ostype": _version([BASH, "-c", "echo $OSTYPE"]),
           "harness_python": sys.version.split()[0], "configured_python": BASE_PY,
           "configured_python_version": _version([BASE_PY, "--version"]),
           "fallback_py_launcher": _version(["py", "-3", "-c", "import sys; print(sys.version.split()[0], sys.executable)"]),
           "pwsh": _version([shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]),
           "powershell": _version(["powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]) if ON_WINDOWS else "",
           "machine_busy_cpus_3s": round(_busy_cpus(), 2), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    _record(row)


@pytest.mark.skipif(os.environ.get("SKYGUARD_LOAD_MATRIX") != "1", reason="the T14b matrix: set SKYGUARD_LOAD_MATRIX=1")
@pytest.mark.parametrize("level,entry,path", _cells(), ids=[f"{lv:g}x-{e}-{p}" for lv, e, p in _cells()])
def test_matrix_cell(tmp_path, level, entry, path):
    """One cell: N runs of one entry on one path at one load. Fails on any run at or past definition timeout - 10 s,
    or on an answer that is not the expected one where the expected one was a refusal (an allow, nothing, late)."""
    _meta()
    n = N_HIGH if level >= 2 else N_LOW
    deadline = _def_timeout(entry)
    cap = deadline + 20                    # past the definition timeout the agent has already discarded the answer
    expected = _expected(entry, path)
    worst, over, problems = 0.0, 0, []
    with _load_cell(level) as cell:
        for i in range(n):
            tmp = tmp_path / f"r{i}"
            root = _matrix_install(tmp, entry, path)
            res = _run_timed(_entry_cmd(entry, root), _payload(entry, root), _entry_env(root), cap)
            got = _outcome(entry, res["out"], root)
            late = res["seconds"] >= deadline - MARGIN_S
            fail_open = _kind(entry) == "gate" and (got in ("allow", "none") or res["seconds"] >= deadline)
            _record({"kind": "run", "label": LABEL, "entry": entry, "path": path, "level": level, "run": i,
                     "t_exit": res["t_exit"], "t_eof": res["t_eof"], "seconds": res["seconds"], "capped": res["capped"],
                     "outcome": got, "expected": expected, "late": late, "fail_open": fail_open, "rc": res["rc"],
                     "machine_busy_pct": res["machine_busy_pct"], "def_timeout": deadline, "cap": cap,
                     "burners": cell["burners"], "cores": cell["cores"], "priority": cell["priority"],
                     "busy_cpus_before_cell": cell["busy_cpus_before"], "src": str(SRC),
                     "out_head": res["out"][:160].decode("utf-8", "replace"), "ts": time.strftime("%H:%M:%S")})
            worst = max(worst, res["seconds"])
            if got != expected:
                problems.append(f"run {i}: {got} (expected {expected}) in {res['seconds']:.1f} s")
            if late:
                over += 1
                if STOP_AFTER and over >= STOP_AFTER:
                    _record({"kind": "stopped", "label": LABEL, "entry": entry, "path": path, "level": level,
                             "after_runs": i + 1, "of": n, "over_deadline_runs": over, "ts": time.strftime("%H:%M:%S")})
                    break
    assert worst < deadline - MARGIN_S and not problems, \
        f"{entry} {path} at {level:g}x: worst {worst:.1f} s against {deadline} - {MARGIN_S} s; {problems}"


# ----------------------------------------------------------------------------------------------- kill-latency probe

def _bash_probe_script(tmp: Path) -> str:
    """The entries' own bound on a native sleeper: `_bounded 3000` from SRC's PY-CHAIN (or, in a tree without
    _bounded -- the pre-fix one -- the bound that tree used: coreutils `timeout -k 2 3`)."""
    text = (SRC / "agent" / "guards" / "adapters" / "claude" / "protect-files.sh").read_text(encoding="utf-8")
    block = text[text.index("# PY-CHAIN-BEGIN"):text.index("# PY-CHAIN-END")]
    sleeper = f'"{BASE_PY}" -c "import time; time.sleep(60)"'
    if "_bounded()" in block:
        call = f"_bounded 3000 none {sleeper}"
        how = "_bounded"
    else:
        block = ""
        call = f"timeout -k 2 3 {sleeper}"
        how = "timeout-k2-3"
    hb = (tmp / "hb").as_posix()
    script = ("T0=\"${EPOCHREALTIME/,/.}\"\nROOT=\"" + tmp.as_posix() + "\"\nROOT_WIN=\"\"\nHB_DIR=\"" + hb + "\"\n"
              "INV=1.1-1\nPY=\"" + BASE_PY + "\"\nINPUT=\"\"\nHOOK_TIMEOUT_S=60\n_fail() { :; }\n"
              + block + "\n"
              "S=\"${EPOCHREALTIME/,/.}\"\n" + call + "\nRCX=$?\nE=\"${EPOCHREALTIME/,/.}\"\n"
              "echo \"PROBE how=" + how + " start=$S end=$E rc=${RC:-$RCX}\"\n")
    (tmp / "hb").mkdir(parents=True, exist_ok=True)
    (tmp / "probe.sh").write_text(script, encoding="utf-8", newline="\n")
    return str(tmp / "probe.sh")


def _ps_probe_script(tmp: Path) -> str:
    text = (SRC / "agent" / "guards" / "adapters" / "codex" / "skyguard-entry.ps1").read_text(encoding="ascii")
    funcs = text[text.index("function Join-Args"):text.index("# PY-CHAIN-BEGIN")]
    script = ("$ErrorActionPreference = 'Stop'\n$script:Utf8 = [System.Text.UTF8Encoding]::new($false, $false)\n"
              "$script:IsWin = [System.IO.Path]::DirectorySeparatorChar -eq [char]92\n" + funcs + "\n"
              "$sw = [System.Diagnostics.Stopwatch]::StartNew()\n"
              "$r = Invoke-Proc @('" + BASE_PY + "') @('-c', 'import time; time.sleep(60)') ([byte[]]@()) 3000\n"
              "$sw.Stop()\n[Console]::Out.WriteLine('PROBE how=Invoke-Proc ms=' + $sw.ElapsedMilliseconds + ' timedout=' + $r.TimedOut)\n")
    (tmp / "probe.ps1").write_text(script, encoding="ascii", newline="\n")
    return str(tmp / "probe.ps1")


PROBE_SHELLS = ["bash"] + (["powershell"] if ON_WINDOWS else []) + (["pwsh"] if ON_WINDOWS and shutil.which("pwsh") else [])


@pytest.mark.skipif(os.environ.get("SKYGUARD_LOAD_PROBE") != "1", reason="kill-latency probe: set SKYGUARD_LOAD_PROBE=1")
@pytest.mark.parametrize("shell", PROBE_SHELLS)
def test_kill_latency_probe(tmp_path, shell):
    """"Deadline 3 s on a native sleeper" through the entries' own bound must end in < deadline + 3 s, at
    SKYGUARD_LOAD_FACTOR (the investigation's single number that would have caught the fail-open)."""
    _meta()
    n = N_HIGH if FACTOR >= 2 else N_LOW
    worst = 0.0
    with _load_cell(FACTOR) as cell:
        for i in range(n):
            tmp = tmp_path / f"p{i}"
            tmp.mkdir()
            if shell == "bash":
                cmd = [BASH, _bash_probe_script(tmp)]
            else:
                cmd = [shutil.which(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                       _ps_probe_script(tmp)]
            res = _run_timed(cmd, b"", _entry_env(tmp), 120)
            text = res["out"].decode("utf-8", "replace")
            inner = None
            for tok in text.split():
                if tok.startswith("ms="):
                    inner = int(tok[3:]) / 1000.0
            if "start=" in text and "end=" in text:
                kv = dict(t.split("=", 1) for t in text.split() if "=" in t)
                try:
                    inner = float(kv["end"]) - float(kv["start"])
                except (KeyError, ValueError):
                    pass
            took = inner if inner is not None else res["seconds"]
            _record({"kind": "probe", "label": LABEL, "shell": shell, "run": i, "bound_s": 3, "inner_s": inner,
                     "wall_s": res["seconds"], "capped": res["capped"], "level": cell["factor"],
                     "burners": cell["burners"], "cores": cell["cores"], "priority": cell["priority"],
                     "machine_busy_pct": res["machine_busy_pct"], "out": text.strip()[:200], "src": str(SRC)})
            worst = max(worst, took if not res["capped"] else res["seconds"])
    assert worst < 3 + 3, f"{shell}: worst {worst:.1f} s for a 3 s bound (limit 6 s)"


# ---------------------------------------------------------------------------------------------- idle A/B vs v3.9.1

AB_BASE = os.environ.get("SKYGUARD_LOAD_AB_BASE", "")
AB_N = int(os.environ.get("SKYGUARD_LOAD_AB_N", "40"))
AB_BAR_MS = float(os.environ.get("SKYGUARD_LOAD_AB_BAR_MS", "1000"))


def _p95(xs: list) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(0.95 * len(s)) - 1))] if s else float("nan")


def _v391_install(base: Path, tmp: Path) -> Path:
    """v3.9.1 as shipped: .claude/hooks/*.sh with jq configured (no engine, no protect-mcp, no Codex)."""
    jq = shutil.which("jq")
    assert jq, "the v3.9.1 hooks need jq"
    root = tmp / "B" / "Skyrim VR"
    (root / ".claude" / "hooks").mkdir(parents=True)
    for f in sorted((base / ".claude" / "hooks").glob("*.sh")):
        (root / ".claude" / "hooks" / f.name).write_text(f.read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq.replace(BS, "/")),
                                                         encoding="utf-8", newline="\n")
    (root / "tools").mkdir()
    for f in ("kb-guard.sh", "hook-canary.sh"):
        if (base / "tools" / f).is_file():
            shutil.copy2(base / "tools" / f, root / "tools" / f)
    for f in ("KNOWLEDGEBASE.md", "CLAUDE.md"):
        if (base / f).is_file():
            shutil.copy2(base / f, root / f)
    (root / ".claude" / "skyrim-paths.env").write_text(f'SKYRIM_GAME_ROOT="{root.as_posix()}"\n', encoding="utf-8", newline="\n")
    (root / "Data").mkdir()
    (root / "Data" / "a.ini").write_text("[x]\n", encoding="utf-8")
    return root


@pytest.mark.skipif(os.environ.get("SKYGUARD_LOAD_AB") != "1", reason="idle A/B: set SKYGUARD_LOAD_AB=1")
def test_idle_hot_path_ab_against_v391(tmp_path):
    """Healthy hot path at idle, unpinned, no burners: p95 <= 1 s for every entry of both agents, interleaved A/B
    against v3.9.1's hooks (A = SRC, B = SKYGUARD_LOAD_AB_BASE). One persistent install per side: the hot path a
    session actually sees after its first call."""
    _meta()
    assert AB_BASE and Path(AB_BASE, ".claude", "hooks").is_dir(), "SKYGUARD_LOAD_AB_BASE must be a v3.9.1 checkout"
    base = Path(AB_BASE)
    with _load_cell(0, pin=False) as cell:
        a_root = _matrix_install(tmp_path / "A", "claude:protect-bash", "healthy")
        for name in CLAUDE_KIND:
            text = (SRC / "agent" / "guards" / "adapters" / "claude" / f"{name}.sh").read_text(encoding="utf-8")
            (a_root / ".claude" / "hooks" / f"{name}.sh").write_text(text.replace("{{PYTHON_PATH}}", BASE_PY),
                                                                    encoding="utf-8", newline="\n")
        cx_root = _matrix_install(tmp_path / "C", "codex:bash", "healthy")
        b_root = _v391_install(base, tmp_path)
        a_entries = [f"claude:{h}" for h in CLAUDE_KIND] + ["codex:bash", "codex:powershell", "codex:pwsh"]
        if not (ON_WINDOWS and shutil.which("pwsh")):
            a_entries.remove("codex:pwsh")
        if not ON_WINDOWS:
            a_entries.remove("codex:powershell")
        times: dict = {}
        for i in range(AB_N):
            for entry in a_entries:
                name = entry.split(":", 1)[1]
                sides = [("A", a_root if entry.startswith("claude:") else cx_root)]
                if entry.startswith("claude:") and (b_root / ".claude" / "hooks" / f"{name}.sh").is_file():
                    sides.append(("B", b_root))
                if i % 2:
                    sides.reverse()
                for side, root in sides:
                    res = _run_timed(_entry_cmd(entry, root), _payload(entry, root), _entry_env(root), 60)
                    got = _outcome(entry, res["out"], root) if side == "A" else ""
                    _record({"kind": "ab", "label": LABEL, "side": side, "entry": entry, "run": i,
                             "ms": round(res["seconds"] * 1000, 1), "outcome": got, "machine_busy_pct": res["machine_busy_pct"],
                             "cores": cell["cores"], "out_head": res["out"][:120].decode("utf-8", "replace")})
                    times.setdefault((side, entry), []).append(res["seconds"] * 1000)
    bad = []
    for (side, entry), xs in sorted(times.items()):
        p95 = _p95(xs)
        _record({"kind": "ab-summary", "label": LABEL, "side": side, "entry": entry, "n": len(xs),
                 "median_ms": round(sorted(xs)[len(xs) // 2], 1), "p95_ms": round(p95, 1), "max_ms": round(max(xs), 1)})
        if side == "A" and p95 > AB_BAR_MS:
            bad.append(f"{entry} p95 {p95:.0f} ms")
    assert not bad, f"idle hot path p95 over {AB_BAR_MS:.0f} ms: {bad}"


# ------------------------------------------------------------------------------------------------------- report mode

def _fmt_cell(rows: list, stopped: dict) -> tuple:
    """(markdown text, status) for one cell's runs."""
    if not rows:
        return "--", "missing"
    deadline = rows[0]["def_timeout"]
    secs = [r["seconds"] for r in rows]
    worst = max(secs)
    p95 = _p95(secs)
    fail_open = sum(1 for r in rows if r["fail_open"])
    wrong = [r for r in rows if r["outcome"] != r["expected"]]
    capped = any(r["capped"] for r in rows)
    if worst >= deadline - MARGIN_S or fail_open or wrong:
        status = "FAIL"
    elif worst >= deadline - OUTER_S:
        status = "WARN"
    else:
        status = "pass"
    busy = [r["machine_busy_pct"] for r in rows if r.get("machine_busy_pct") is not None]
    load = f"{min(busy):.0f}-{max(busy):.0f}%" if busy else "?"
    w = (">=" if capped else "") + f"{worst:.1f}"
    extra = ""
    if fail_open:
        extra += f" **{fail_open} fail-open**"
    if wrong:
        kinds = sorted({r["outcome"] for r in wrong})
        extra += f" {len(wrong)}x {'/'.join(kinds)}"
    if stopped:
        extra += f" (stopped after {stopped['after_runs']}/{stopped['of']})"
    return f"{status} {w} / p95 {p95:.1f} / n{len(rows)} / m{load}{extra}", status


def render(rows: list) -> str:
    out = []
    metas = [r for r in rows if r.get("kind") == "meta"]
    labels = []
    for r in rows:
        if r.get("label", "") not in labels and r.get("kind") in ("run", "probe", "ab", "meta"):
            labels.append(r.get("label", ""))
    for m in metas:
        out.append("- meta `" + (m.get("label") or "-") + "`: " + ", ".join(f"{k}={m[k]}" for k in m if k not in ("kind",)))
    for label in labels:
        runs = [r for r in rows if r.get("kind") == "run" and r.get("label", "") == label]
        if not runs:
            continue
        stops = {(r["entry"], r["path"], r["level"]): r for r in rows if r.get("kind") == "stopped" and r.get("label", "") == label}
        levels = sorted({r["level"] for r in runs})
        out.append(f"\n### Matrix `{label or '-'}`\n")
        out.append("Cell = verdict, worst s / p95 s / runs / machine CPU during the runs (min-max). "
                   "Pass: worst < definition timeout - 10 s and every answer as expected.\n")
        out.append("| entry | path | expected | " + " | ".join(f"{lv:g}x" for lv in levels) + " |")
        out.append("|---|---|---|" + "---|" * len(levels))
        counts: dict = {}
        for entry in ENTRIES:
            for path in PATHS:
                cells = []
                seen = False
                for lv in levels:
                    cr = [r for r in runs if r["entry"] == entry and r["path"] == path and r["level"] == lv]
                    seen = seen or bool(cr)
                    txt, st = _fmt_cell(cr, stops.get((entry, path, lv), {}))
                    counts[st] = counts.get(st, 0) + (1 if cr else 0)
                    cells.append(txt)
                if seen:
                    out.append(f"| {entry} | {path} | {_expected(entry, path)} | " + " | ".join(cells) + " |")
        out.append("\nCells: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if k != "missing"))
        bad = [r for r in runs if r["fail_open"]]
        out.append(f"Runs: {len(runs)}; fail-open runs (allow/nothing/at-or-past the definition timeout on a gate): {len(bad)}")
    probes = [r for r in rows if r.get("kind") == "probe"]
    if probes:
        out.append("\n### Kill-latency probe (3 s bound on a native sleeper; limit 6 s)\n")
        out.append("| tree | shell | load | runs | worst inner s | worst wall s | machine CPU |")
        out.append("|---|---|---|---|---|---|---|")
        keys = []
        for r in probes:
            k = (r.get("label", ""), r["shell"], r["level"])
            if k not in keys:
                keys.append(k)
        for k in keys:
            pr = [r for r in probes if (r.get("label", ""), r["shell"], r["level"]) == k]
            inner = [r["inner_s"] if r["inner_s"] is not None else r["wall_s"] for r in pr]
            busy = [r["machine_busy_pct"] for r in pr if r.get("machine_busy_pct") is not None]
            cap = any(r["capped"] for r in pr)
            out.append(f"| {k[0]} | {k[1]} | {k[2]:g}x | {len(pr)} | {'>=' if cap else ''}{max(inner):.2f} | "
                       f"{max(r['wall_s'] for r in pr):.2f} | {min(busy):.0f}-{max(busy):.0f}% |" if busy else
                       f"| {k[0]} | {k[1]} | {k[2]:g}x | {len(pr)} | {max(inner):.2f} | {max(r['wall_s'] for r in pr):.2f} | ? |")
    summ = [r for r in rows if r.get("kind") == "ab-summary"]
    if summ:
        out.append("\n### Idle hot path A/B (A = this tree, B = v3.9.1; ms)\n")
        out.append("| tree | entry | side | n | median | p95 | max |")
        out.append("|---|---|---|---|---|---|---|")
        for r in summ:
            out.append(f"| {r.get('label', '')} | {r['entry']} | {r['side']} | {r['n']} | {r['median_ms']} | {r['p95_ms']} | {r['max_ms']} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="render the T14b matrix from SKYGUARD_LOAD_REPORT rows")
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("rows", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    all_rows = []
    for f in a.rows:
        all_rows += [json.loads(x) for x in Path(f).read_text(encoding="utf-8").splitlines() if x.strip()]
    text = render(all_rows)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
