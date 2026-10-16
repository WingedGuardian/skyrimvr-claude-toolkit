"""The setup-smoke CI job covers every --agent value on both OSes, and the script it runs really checks.

scripts/setup-smoke.sh is run here for one value end to end (the per-value behaviour itself is asserted in
tests/test_setup_agent.py); the rest is the structure of the job, so a value or an OS cannot drop out unseen.
"""
from __future__ import annotations

import subprocess
import re

import pytest

from conftest import BASH, REPO

VALUES = ["claude", "codex", "all", "both", "auto", "generic", "opencode"]


def _job():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    return wf["jobs"]["setup-smoke"]


def test_the_ci_job_runs_every_agent_value_on_both_oses():
    job = _job()
    m = job["strategy"]["matrix"]
    assert sorted(m["agent"]) == sorted(VALUES)
    assert sorted(m["os"]) == ["ubuntu-latest", "windows-latest"]
    assert job["strategy"]["fail-fast"] is False
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert 'bash scripts/setup-smoke.sh "${{ matrix.agent }}"' in runs


def test_setup_main_values_match_the_smoke_values():
    """Every value setup.sh accepts is smoked, and nothing else."""
    text = (REPO / "setup.sh").read_text(encoding="utf-8")
    accepted = re.search(r'case "\$AGENT_ARG" in\n\s*([a-z|]+)\) ;;\n\s*both\) AGENT_ARG="all" ;;', text)
    assert accepted, "setup's accepted-agent branch could not be inspected"
    assert set(accepted.group(1).split("|")) | {"both"} == set(VALUES)
    smoke = (REPO / "scripts/setup-smoke.sh").read_text(encoding="utf-8")
    smoked = re.search(r'case "\$AGENT" in ([a-z|]+)\) ;;', smoke)
    assert smoked and set(smoked.group(1).split("|")) == set(VALUES)


def _run(tmp_path, value):
    return subprocess.run([BASH, str(REPO / "scripts" / "setup-smoke.sh"), value, str(tmp_path / "w")],
                          capture_output=True, text=True, timeout=600)


@pytest.mark.parametrize("value", ["claude", "generic", "opencode"])
def test_the_smoke_script_passes_on_the_real_setup(tmp_path, value):
    r = _run(tmp_path, value)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "setup-smoke [%s]: 0 failure(s)" % value in r.stdout
    assert "second run changed nothing" in r.stdout and "--agent nonsense wrote nothing" in r.stdout


def test_the_smoke_script_fails_when_setup_ignores_the_agent(tmp_path):
    """Can it go red? A setup.sh that configures everything whatever --agent says must fail the claude smoke."""
    import shutil
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "tests", "node_modules", "__pycache__",
                                                              ".pytest_cache"))
    s = repo / "setup.sh"
    text = s.read_text(encoding="utf-8")
    anchor = 'DO_CLAUDE=""; DO_CODEX=""; DO_PORTABLE=""; DO_OPENCODE=""\n'
    assert text.count(anchor) == 1
    s.write_text(text.replace(anchor, anchor + 'AGENT_SEL=all\n'), encoding="utf-8", newline="\n")
    r = subprocess.run([BASH, str(repo / "scripts" / "setup-smoke.sh"), "claude", str(tmp_path / "w")],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 1, r.stdout[-3000:]
    assert "--agent claude configured 'all'" in r.stdout


def test_the_smoke_script_refuses_a_bad_value(tmp_path):
    r = _run(tmp_path, "gpt")
    assert r.returncode == 2 and "usage" in r.stderr


# Linux bash has no `pwd -W`: emulated in Git Bash by a function that refuses -W, as the review measured it. Both
# setup.sh and the smoke then see a non-drive root, which is the ubuntu CI leg (T7 review I1: three of those legs
# were red and nothing had run them).
LINUXISH = 'pwd(){ if [ "${1:-}" = -W ]; then return 1; fi; builtin pwd "$@"; }; export -f pwd; '


@pytest.mark.parametrize("value", ["all", "codex"])
def test_the_linux_legs_pass(tmp_path, value):
    script = (REPO / "scripts" / "setup-smoke.sh").as_posix()
    r = subprocess.run([BASH, "-c", LINUXISH + 'bash "$0" "$1" "$2"', script, value, (tmp_path / "w").as_posix()],
                       capture_output=True, text=True, errors="replace", timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "setup-smoke [%s]: 0 failure(s)" % value in r.stdout
    if value == "codex":
        assert "--agent codex off Windows exits 3" in r.stdout
    else:
        assert "AGENTS.md filled in" in r.stdout and ".codex/hooks.json left unrendered" in r.stdout
