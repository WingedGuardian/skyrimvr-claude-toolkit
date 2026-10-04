"""Plan 5 Task 12: a command the guard cannot judge fails closed (S-INERT-NOCMD).

Whitespace-only and non-string commands used to be ALLOWED (the audit, 2026-10-02): `command` stringified a number
or an array and the rules then read it as typed text. Both now deny. The opaque-command advisory (S-OPAQUE) was
priced and DROPPED (R15); the test at the bottom pins that it is gone.
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, rules, shell_rules  # noqa: E402

RUN = str(REPO / "tools" / "skyguard" / "run.py")


def _run(payload, tmp_path):
    r = subprocess.run(
        [sys.executable, RUN, "claude", "protect-bash", "--root", "C:/GOG Games/Fake",
         "--root-win", "C:" + chr(92) + "GOG Games" + chr(92) + "Fake", "--project", str(tmp_path)],
        input=json.dumps(payload).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


@pytest.mark.parametrize("cmd", ["   \n\t", "     ", "\n", "\t\t"])
def test_run_denies_a_whitespace_only_command(cmd, tmp_path):
    out = _run({"tool_name": "Bash", "tool_input": {"command": cmd}}, tmp_path)
    assert _decision(out) == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == rules.RULES["S-INERT-NOCMD"]["template"]


@pytest.mark.parametrize("cmd", [["rm", "-rf"], 42, {"a": 1}, True, 1.5])
def test_run_denies_a_non_string_command(cmd, tmp_path):
    assert _decision(_run({"tool_name": "Bash", "tool_input": {"command": cmd}}, tmp_path)) == "deny"


def test_run_still_allows_an_ordinary_command(tmp_path):
    assert _decision(_run({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}, tmp_path)) == "allow"


@pytest.mark.parametrize("cmd", ["   \n\t", " ", "\n\n"])
def test_decide_denies_a_whitespace_only_command(cmd, tmp_path):
    a = audit.Audit(str(tmp_path))
    roots = paths.Roots("C:/GOG Games/Fake", "C:/GOG Games/Fake")
    v = shell_rules.decide(cmd, roots, a)
    assert v.decision == "deny" and v.reason == rules.RULES["S-INERT-NOCMD"]["template"]
    assert ("S-INERT-NOCMD", "deny") in a.fired


def test_s_opaque_is_dropped():
    """R15: priced at 41 hits in 65,694 calls (0.062%) but only 1 of 41 (2%) wrote a protected file -- under the 50%
    bar, so the rule, its module and its rows were deleted. Re-adding it must be a decision, not a drift."""
    assert "S-OPAQUE" not in rules.RULES
    assert not (REPO / "tools" / "skyguard" / "opaque.py").exists()
