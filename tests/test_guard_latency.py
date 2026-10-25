"""scripts/guard-latency.py: a hook that crashes, is missing or answers garbage is a FAILED run, never a fast PASS.

v4.0.0 review L4b M2: the wrapper's result was discarded, so an install whose hooks all died (or that had no hooks)
printed `WORST p95 = 27 ms ... PASS` and exited 0.
"""
from __future__ import annotations

import json
import os
import pytest

import subprocess
import sys

from conftest import REPO

SCRIPT = REPO / "scripts" / "guard-latency.py"
HOOKS = ("protect-bash", "protect-files", "protect-mcp", "backup-before-edit", "snapshot-before-tool",
         "session-kb-guard")


def _install(root, body):
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for h in HOOKS:
        (hooks / (h + ".sh")).write_text("#!/usr/bin/env bash\ncat >/dev/null\n" + body + "\n", encoding="utf-8",
                                          newline="\n")
    return root


def _run(root):
    return subprocess.run([sys.executable, "-B", str(SCRIPT), str(root), "--runs", "2"], capture_output=True,
                          text=True, timeout=300)


def test_hooks_that_crash_fail_the_gate(tmp_path):
    r = _run(_install(tmp_path / "broken", "echo 'GUARD INERT: crashed' >&2; exit 3"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAILED RUN" in r.stdout and ": FAIL" in r.stdout, r.stdout


def test_every_timed_hook_runs_with_the_session_sweep_off(tmp_path):
    """review2 B-tools L1: a configured copy holds its own housekeep.py, so a timed session hook would launch a real
    sweep of the real temp dir. The parent here has the switch REMOVED, as a maintainer's shell would."""
    log = tmp_path / "sweep.log"
    root = _install(tmp_path / "ok", 'printf "%s\\n" "${SKYGUARD_HOUSEKEEP_SWEEP:-unset}" >> "' + log.as_posix() + '"')
    env = {k: v for k, v in os.environ.items() if k != "SKYGUARD_HOUSEKEEP_SWEEP"}
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), str(root), "--runs", "2"], capture_output=True,
                       text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = log.read_text(encoding="utf-8").split()
    assert len(seen) == 2 * len(HOOKS) and set(seen) == {"off"}, seen


def test_an_install_with_no_hooks_fails_the_gate(tmp_path):
    (tmp_path / "empty").mkdir()
    r = _run(tmp_path / "empty")
    assert r.returncode == 1 and "MISSING" in r.stdout, r.stdout + r.stderr


def test_a_tool_hook_answering_garbage_fails_the_gate(tmp_path):
    r = _run(_install(tmp_path / "garbage", "echo 'not a verdict'"))
    assert r.returncode == 1 and "not JSON" in r.stdout, r.stdout + r.stderr


def test_hooks_that_answer_pass_the_gate(tmp_path):
    """The twin: an install whose hooks answer (allow = silence) passes, so the gate is not simply always red."""
    r = _run(_install(tmp_path / "ok", "exit 0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failed run(s): PASS" in r.stdout


@pytest.mark.parametrize("answer", [1, [], {"allow": True},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "bogus"}},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}],
    ids=["scalar", "array", "wrong-object", "invalid-decision", "missing-reason"])
def test_invalid_pretooluse_schema_fails_the_gate(tmp_path, answer):
    body = "printf '%s\\n' '" + json.dumps(answer) + "'"
    r = _run(_install(tmp_path / "invalid", body))
    assert r.returncode == 1 and "FAILED RUN" in r.stdout and ": FAIL" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("answer", [{},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                            "permissionDecisionReason": "fixture refusal"}},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "fixture context"}}],
    ids=["empty-object", "deny", "context"])
def test_valid_pretooluse_schema_passes_the_gate(tmp_path, answer):
    body = "printf '%s\\n' '" + json.dumps(answer) + "'"
    r = _run(_install(tmp_path / "valid", body))
    assert r.returncode == 0 and "0 failed run(s): PASS" in r.stdout, r.stdout + r.stderr


def test_session_start_plain_text_banner_is_accepted(tmp_path):
    install = _install(tmp_path / "banner", "exit 0")
    (install / ".claude/hooks/session-kb-guard.sh").write_text("#!/usr/bin/env bash\ncat >/dev/null\necho banner\n",
                                                             encoding="utf-8", newline="\n")
    r = _run(install)
    assert r.returncode == 0, r.stdout + r.stderr
