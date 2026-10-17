"""Deterministic kill-wait control flow, complementing the native deadline tests.

Run the actual wrapper function with a fake clock and process-liveness sequence.
No real kill is issued. This measures the wait and its two bounds, not whether a
native taskkill lands; test_hook_deadline.py retains that integration coverage.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from conftest import BASH, REPO


WRAPPERS = [
    "claude/backup-before-edit.sh",
    "claude/protect-bash.sh",
    "claude/protect-files.sh",
    "claude/protect-mcp.sh",
    "claude/session-kb-guard.sh",
    "claude/snapshot-before-tool.sh",
    "codex/skyguard-entry.sh",
]


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=[p.split("/")[-1][:-3] for p in WRAPPERS])
@pytest.mark.parametrize("step,deadline,death_check,expected_checks,expected_clock", [
    pytest.param(100, 100000, 4, 4, 600, id="time-left"),
    pytest.param(100, 400, 20, 1, 400, id="outer-deadline"),
    pytest.param(1000, 100000, 20, 10, 13000, id="post-kill-cap"),
])
def test_bounded_waits_for_kill_within_both_deadlines(
        wrapper, step, deadline, death_check, expected_checks, expected_clock):
    source = (REPO / "agent/guards/adapters" / wrapper).read_text(encoding="utf-8")
    start, end = "\n_bounded() {\n", "\npy_good() {\n"
    assert source.count(start) == source.count(end) == 1, wrapper
    function = "_bounded() {\n" + source.split(start, 1)[1].split(end, 1)[0] + "\n"
    # The child is a harmless, immediately exiting true command. Liveness is a
    # controlled shell function, so OS scheduling and tasklist start cost cannot
    # turn the omitted-wait mutation into a passing observation.
    script = function + f"""
MS=0; INPUT=''; DL={deadline}; STEP={step}; DEATH_CHECK={death_check}
KILL_REQUESTED=0; WAIT_CHECKS=0; PROBE_PID=''; KILL_PID=''; BAD_PROBE=0
_now() {{ MS=$((MS + STEP)); }}
sleep() {{ :; }}
kill() {{
    [ "$#" -eq 2 ] && [ "$1" = -0 ] || {{ BAD_PROBE=1; return 2; }}
    if [ -z "$PROBE_PID" ]; then PROBE_PID="$2"; fi
    [ "$2" = "$PROBE_PID" ] || {{ BAD_PROBE=1; return 2; }}
    [ "$KILL_REQUESTED" -ne 0 ] || return 0
    WAIT_CHECKS=$((WAIT_CHECKS + 1))
    [ "$WAIT_CHECKS" -lt "$DEATH_CHECK" ]
}}
_kill_tree() {{ KILL_REQUESTED=$((KILL_REQUESTED + 1)); KILL_PID="$1"; }}
_bounded $((2 * STEP)) none true
printf '%s %s %s %s %s %s %s\\n' "$RC" "$KILL_REQUESTED" "$WAIT_CHECKS" "$MS" "$BAD_PROBE" "$PROBE_PID" "$KILL_PID"
"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS") and not k.startswith("BASH_FUNC_")}
    result = subprocess.run([BASH, "--noprofile", "--norc", "-s"], input=script,
                            capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0, (wrapper, result.stdout, result.stderr)
    fields = result.stdout.split()
    assert len(fields) == 7, (wrapper, result.stdout, result.stderr)
    assert fields[:5] == ["124", "1", str(expected_checks), str(expected_clock), "0"], (
        wrapper, result.stdout, result.stderr)
    assert fields[5].isdigit() and int(fields[5]) > 0 and fields[5] == fields[6], fields
