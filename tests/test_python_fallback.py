"""The Python interpreter fallback chain in the five Claude hook wrappers (Plan 4, Task 5).

A moved or upgraded Python used to make every blocking hook refuse everything until setup.sh was
re-run: honest, but a full outage. The chain keeps the hooks deciding and REPORTS the degradation.
It runs only once the configured interpreter has failed, so a healthy call costs nothing extra.

Every test goes through the real wrapper, rendered from the SOURCE (`agent/guards/adapters/claude/`)
by the generator, with controlled shims on PATH. The machine's own Pythons are kept out of the way
(a filtered PATH, no LOCALAPPDATA / PROGRAMFILES), otherwise "nothing runnable" could never occur here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASH, REPO, BS

sys.path.insert(0, str(REPO / "scripts"))
import gen_agent_trees  # noqa: E402

REAL_PY = sys.executable.replace(BS, "/")
MISSING = "C:/definitely/not/here/python.exe"
CHAIN_BEGIN, CHAIN_END = "# PY-CHAIN-BEGIN", "# PY-CHAIN-END"


def _install(tmp_path: Path, py: str) -> Path:
    root = tmp_path / "install"
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for rel, data in gen_agent_trees.render("claude", REPO / "agent").items():
        if rel.startswith(".claude/hooks/") and rel.endswith(".sh"):
            text = data.decode("utf-8").replace("{{PYTHON_PATH}}", py)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
    shutil.copytree(REPO / "tools" / "skyguard", root / "tools" / "skyguard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _clean_path() -> str:
    """The inherited PATH minus every directory that offers a python, so only shims can supply one."""
    keep = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and not any((Path(d) / n).exists() or (Path(d) / (n + ".exe")).exists()
                         for n in ("python", "python3", "py")):
            keep.append(d)
    return os.pathsep.join(keep)


def _shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    return p


def _working(directory: Path, name: str, log: Path | None = None) -> None:
    rec = f'echo "{name} $*" >> "{log.as_posix()}"\n' if log else ""
    _shim(directory, name, rec + f'exec "{REAL_PY}" "$@"\n')


def _fire(root: Path, shim_dirs: list[Path], hook: str, payload: dict,
          extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), LOCALAPPDATA="", PROGRAMFILES="")
    env.update(extra_env or {})
    env["PATH"] = os.pathsep.join([str(d) for d in shim_dirs] + [_clean_path()])
    return subprocess.run([BASH, str(root / ".claude" / "hooks" / hook)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


def _esp_write(root: Path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": f"{root.as_posix()}/Data/X.esp", "content": "x"}}


def _rm_install(root: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": f'rm -rf "{root.as_posix()}"'}}


def _reason(r: subprocess.CompletedProcess) -> str:
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def _failed(root: Path, hook: str) -> str:
    p = root / ".claude" / "backups" / ".hook-heartbeat" / "claude" / f"{hook}.failed"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def test_a_missing_configured_python_falls_back_and_still_decides(tmp_path):
    root = _install(tmp_path, MISSING)
    _working(tmp_path / "shims", "python")
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    reason = _reason(r)
    assert "GUARD INERT" not in reason, reason
    assert reason.startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), reason
    failed = _failed(root, "protect-files")
    assert " python " in failed and "configured " + MISSING + " unusable" in failed, failed
    assert "re-run setup.sh" in failed


def test_a_python2_on_path_is_skipped(tmp_path):
    root = _install(tmp_path, MISSING)
    _shim(tmp_path / "shims", "python", "exit 1\n")          # fails the >= 3.8 probe, like a Python 2
    _working(tmp_path / "shims", "python3")
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
    assert "used python3" in _failed(root, "protect-files")


def test_an_old_python3_is_skipped_by_the_capability_probe_not_just_by_existing(tmp_path):
    root = _install(tmp_path, MISSING)
    # answers the selftest the way run.py does on Python 3.7 (it refuses: exit 1, no nonce), and would
    # otherwise run anything else. The probe is ONE process now: there is no separate version check.
    _shim(tmp_path / "shims", "python", f'case "$*" in *--selftest*) exit 1 ;; esac\nexec "{REAL_PY}" "$@"\n')
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert "GUARD INERT" in _reason(r), r.stdout


def test_the_store_alias_is_used_only_last(tmp_path):
    root = _install(tmp_path, MISSING)
    alias_log = tmp_path / "alias.log"
    _working(tmp_path / "WindowsApps", "python", alias_log)   # first on PATH, and it works
    _working(tmp_path / "regular", "python3")
    r = _fire(root, [tmp_path / "WindowsApps", tmp_path / "regular"], "protect-bash.sh", _rm_install(root))
    assert _reason(r).startswith("Blocked: "), r.stdout
    assert not alias_log.exists(), "the Store alias ran although a regular python3 was available:\n" + (
        alias_log.read_text(encoding="utf-8") if alias_log.exists() else "")
    assert "used python3" in _failed(root, "protect-bash")


def test_the_store_alias_is_still_used_when_it_is_all_there_is(tmp_path):
    root = _install(tmp_path, MISSING)
    _working(tmp_path / "WindowsApps", "python")
    r = _fire(root, [tmp_path / "WindowsApps"], "protect-files.sh", _esp_write(root))
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout


def test_nothing_runnable_still_refuses(tmp_path):
    root = _install(tmp_path, MISSING)
    r = _fire(root, [], "protect-files.sh", _esp_write(root))
    reason = _reason(r)
    assert "GUARD INERT" in reason and "cannot run Python" in reason, reason
    assert "cannot run Python" in _failed(root, "protect-files")


def test_a_healthy_configured_python_never_touches_the_chain(tmp_path):
    root = _install(tmp_path, REAL_PY)
    log = tmp_path / "probe.log"
    _working(tmp_path / "shims", "python", log)
    _working(tmp_path / "shims", "python3", log)
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
    assert not log.exists(), "a PATH candidate was probed although the configured interpreter works"
    assert " python " not in _failed(root, "protect-files")


def test_a_present_but_broken_configured_python_falls_back_after_the_engine_fails(tmp_path):
    broken = _shim(tmp_path / "broken", "python", "exit 3\n")        # exists, runs, always fails
    root = _install(tmp_path, broken.as_posix())
    _working(tmp_path / "shims", "python3")
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
    assert "used python3" in _failed(root, "protect-files")


def test_a_broken_python_with_no_fallback_is_an_engine_failure_deny(tmp_path):
    broken = _shim(tmp_path / "broken", "python", "exit 3\n")
    root = _install(tmp_path, broken.as_posix())
    r = _fire(root, [], "protect-files.sh", _esp_write(root))
    assert "GUARD INERT" in _reason(r) and "engine failed" in _reason(r), r.stdout


@pytest.mark.parametrize("hook", ["backup-before-edit.sh", "snapshot-before-tool.sh", "session-kb-guard.sh"])
def test_the_non_blocking_hooks_fall_back_too(tmp_path, hook):
    root = _install(tmp_path, MISSING)
    _working(tmp_path / "shims", "python")
    payload = ({"hook_event_name": "SessionStart", "source": "startup"} if hook.startswith("session")
               else {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    r = _fire(root, [tmp_path / "shims"], hook, payload)
    assert r.returncode == 0
    # the engine RAN (the session guard may still report its own missing tools/kb-guard.sh here)
    assert "Python is not runnable" not in r.stdout and "engine failed" not in r.stdout, r.stdout
    assert "configured " + MISSING + " unusable" in _failed(root, hook[:-3])


def test_every_wrapper_lists_the_same_chain():
    chains = {}
    for f in sorted((REPO / "agent" / "guards" / "adapters" / "claude").glob("*.sh")):
        t = f.read_text(encoding="utf-8")
        assert t.count(CHAIN_BEGIN) == 1 and t.count(CHAIN_END) == 1, f.name
        chains[f.name] = t[t.index(CHAIN_BEGIN):t.index(CHAIN_END)]
    assert len(chains) == 6, sorted(chains)
    assert len(set(chains.values())) == 1, "the wrappers disagree about the fallback chain: " + ", ".join(
        n for n, c in chains.items() if c != next(iter(chains.values())))


# --------------------------------------------------------------------------
# fix round 1: the probe proves the ENGINE runs; nothing may hang; known locations by version
# --------------------------------------------------------------------------

def _run_py(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([REAL_PY, str(REPO / "tools" / "skyguard" / "run.py"), *args],
                          capture_output=True, text=True, timeout=60)


def test_run_py_selftest_echoes_the_nonce_after_importing_the_engine():
    r = _run_py("--selftest", "4242")
    assert r.returncode == 0 and r.stdout.strip() == "skyguard-selftest:4242", (r.stdout, r.stderr)


def test_a_fake_python_that_exits_zero_and_prints_an_empty_object_is_rejected(tmp_path):
    """`C:/Python3*` is user-creatable. A stub that exits 0 and prints `{}` passes a version probe and
    then reads as ALLOW for every command: the probe has to prove the ENGINE runs."""
    root = _install(tmp_path, MISSING)
    _shim(tmp_path / "shims", "python", "echo '{}'\nexit 0\n")
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert "GUARD INERT" in _reason(r), r.stdout


def test_a_candidate_that_echoes_a_stale_nonce_is_rejected_and_the_next_is_used(tmp_path):
    root = _install(tmp_path, MISSING)
    _shim(tmp_path / "shims", "python",
          f'case "$*" in *--selftest*) echo "skyguard-selftest:1234"; exit 0 ;; esac\nexec "{REAL_PY}" "$@"\n')
    _working(tmp_path / "shims", "python3")
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
    assert "used python3" in _failed(root, "protect-files")


def test_a_hanging_probe_is_cut_off_and_the_chain_moves_on(tmp_path):
    import time
    root = _install(tmp_path, MISSING)
    _shim(tmp_path / "shims", "python", "sleep 60\n")
    _working(tmp_path / "shims", "python3")
    t = time.time()
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root))
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
    # the probe budget (15 s default) and then the next candidate, all inside the definition timeout
    # (60 s) - 10 s; the deadline itself is tests/test_hook_deadline.py's
    assert time.time() - t < 50, "a hanging probe held the hook past its budget"


REAPER_S = 4    # under the wrapper's own kill (budget 2 s + 3 s), so a disarmed watchdog shows as the reaper's exit 9


def _reaper_armed(marker: Path) -> bool:
    import time
    end = time.time() + 5
    while time.time() < end:
        if marker.is_file() and marker.read_text(encoding="utf-8").startswith("armed "):
            return True
        time.sleep(0.1)
    return False


def test_a_catastrophic_regex_inside_the_engine_is_killed_at_the_budget(tmp_path):
    """The normal path has no `timeout` fork: run.py's own watchdog must end a call stuck in C code that
    holds the GIL (a catastrophic regex), and the wrapper must turn that into the ordinary deny. The agent
    DISCARDS a deny that arrives after its own 30 s timeout (measured), so ending first is the whole point."""
    import time
    root = _install(tmp_path, REAL_PY)
    run_py = root / "tools" / "skyguard" / "run.py"
    src = run_py.read_text(encoding="utf-8")
    needle = "    raw = sys.stdin.buffer.read()"
    assert needle in src
    # Deferred row 26: the planted spin arms tests/spin_reaper.py first, so it ends within REAPER_S whatever happens
    # to the watchdog (its mutant used to leave a python.exe spinning, orphaned, after the test timed out).
    marker = tmp_path / "reaper.txt"
    reaper = ("    import re; sys.path.insert(0, {!r}); import spin_reaper; spin_reaper.arm({!r}, {!r})\n").format(
        (REPO / "tests").as_posix(), REAPER_S, marker.as_posix())
    run_py.write_text(src.replace(needle, reaper + "    re.match(r'(a+)+$', 'a' * 30 + 'b')\n" + needle, 1),
                      encoding="utf-8", newline="\n")
    t = time.time()
    r = _fire(root, [], "protect-files.sh", _esp_write(root), {"SKYGUARD_ENGINE_TIMEOUT": "2"})
    assert _reaper_armed(marker), "the spin was planted without its reaper"
    assert "GUARD INERT" in _reason(r) and "engine failed" in _reason(r), r.stdout
    assert r.stdout.strip().startswith("{") and "Traceback" not in r.stdout and "Thread" not in r.stdout
    assert time.time() - t < 12, "the watchdog did not end the call at its budget"
    # the WATCHDOG ended it (exit 1), not the wrapper's own kill 3 s later (exit 124)
    assert "exit 1" in _failed(root, "protect-files") and "exit 124" not in _failed(root, "protect-files"), \
        _failed(root, "protect-files")


def test_a_hanging_fallback_engine_is_cut_off_by_timeout(tmp_path):
    """On the fallback path the wrapper bounds the engine (SKYGUARD_ENGINE_TIMEOUT + 3 s), killing it by
    its Windows pid -- not with coreutils `timeout`, which cannot stop a native python.exe under load."""
    import time
    root = _install(tmp_path, MISSING)
    _shim(tmp_path / "shims", "python",
          f'case "$*" in *--selftest*|*version_info*) exec "{REAL_PY}" "$@" ;; esac\nexec sleep 40\n')
    t = time.time()
    r = _fire(root, [tmp_path / "shims"], "protect-files.sh", _esp_write(root), {"SKYGUARD_ENGINE_TIMEOUT": "2"})
    assert "GUARD INERT" in _reason(r) and "engine failed" in _reason(r), r.stdout
    assert "exit 124" in _failed(root, "protect-files"), _failed(root, "protect-files")
    assert time.time() - t < 25, "the fallback engine call was not cut off at its budget"


def test_known_locations_are_tried_newest_version_first(tmp_path):
    lad = tmp_path / "lad"
    log = tmp_path / "used.log"
    for v in ("Python39", "Python312"):
        _working(lad / "Programs" / "Python" / v, "python.exe", log)
    root = _install(tmp_path, MISSING)
    r = _fire(root, [], "protect-files.sh", _esp_write(root), {"LOCALAPPDATA": str(lad)})
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
    assert "Python312/python.exe" in _failed(root, "protect-files"), _failed(root, "protect-files")


def test_unset_location_variables_do_not_break_the_chain(tmp_path):
    root = _install(tmp_path, MISSING)
    _working(tmp_path / "shims", "python")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    env.pop("LOCALAPPDATA", None)
    env.pop("PROGRAMFILES", None)
    env["PATH"] = os.pathsep.join([str(tmp_path / "shims"), _clean_path()])
    r = subprocess.run([BASH, str(root / ".claude" / "hooks" / "protect-files.sh")],
                       input=json.dumps(_esp_write(root)), capture_output=True, text=True, env=env, timeout=60)
    assert _reason(r).startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), r.stdout
