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

The burners are started by this module for one scenario at a time and stopped by their own process
handles right after it (never by image name).
Pass criterion, per the investigation: the WORST run of every scenario ends below the definition timeout
minus 10 s -- a p95 is the wrong statistic for a fail-open bound.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import BASH, REPO, BS

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


@pytest.fixture()
def load():
    """Per CELL, and within a CPU cap: this process is pinned to CORES logical CPUs (the last ones), so the
    burners it starts AND every hook/engine it launches inherit that affinity -- FACTOR burners per pinned
    core oversubscribe those cores while the machine as a whole loses at most CORES CPUs. The burners run
    only for one scenario and are stopped by their own handles right after it; the affinity is restored."""
    total = os.cpu_count() or 1
    k = max(1, min(CORES, total))
    cores = list(range(total - k, total))
    before = _get_affinity()
    _set_affinity(sum(1 << c for c in cores) if ON_WINDOWS else set(cores))
    n = max(1, int(round(FACTOR * k)))
    burners = []
    try:
        burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(n)]
        time.sleep(2)
        yield {"burners": n, "cores": k}
    finally:
        for p in burners:      # by this module's own handles only
            try:
                p.kill()
            except OSError:
                pass
        for p in burners:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        _set_affinity(before)


def _record(row: dict) -> None:
    print(json.dumps(row))
    if REPORT:
        with open(REPORT, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def _clean_path() -> str:
    keep = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and not any((Path(d) / n).exists() or (Path(d) / (n + ".exe")).exists()
                         for n in ("python", "python3", "py")):
            keep.append(d)
    return os.pathsep.join(keep)


def _shim(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")


def _claude_install(tmp: Path, py: str) -> Path:
    root = tmp / "install"
    (root / ".claude" / "hooks").mkdir(parents=True)
    text = (SRC / "agent" / "guards" / "adapters" / "claude" / "protect-files.sh").read_text(encoding="utf-8")
    (root / ".claude" / "hooks" / "protect-files.sh").write_text(text.replace("{{PYTHON_PATH}}", py),
                                                                  encoding="utf-8", newline="\n")
    shutil.copytree(SRC / "tools" / "skyguard", root / "tools" / "skyguard",
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
    e.update(LOCALAPPDATA="", PROGRAMFILES="")
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
