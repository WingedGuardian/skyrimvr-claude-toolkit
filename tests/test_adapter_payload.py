"""The adapter kit ships, and the release gates would notice if it did not.

`release.yml` zips the tree by rule, so the files ship today without a list. A rule can change; a file
that is expected but missing does not error on a user's machine, it just leaves an agent with no
ADAPTING.md. So each path is asserted on the payload (tools/check-adapter-payload.py), by both gates.
`tests/` is not shipped, so the toy-agent test is a CI gate, not a bundle member.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

CHECKER = REPO / "tools" / "check-adapter-payload.py"

NEW_PATHS = [
    "ADAPTING.md",
    "tools/adapting/adapter_template.py",
    "tools/adapting/capability-report.template.json",
    "tools/adapter_conformance.py",
    "tools/inventory-runtimes.py",
    "tools/skyguard/neutral.py",
    ".claude/skills/toolkit-setup/SKILL.md",
    ".agents/skills/toolkit-setup/SKILL.md",
]


def _run(root):
    return subprocess.run([sys.executable, "-B", str(CHECKER), str(root)],
                          capture_output=True, text=True, timeout=120)


@pytest.fixture()
def payload(tmp_path):
    root = tmp_path / "payload"
    for rel in NEW_PATHS:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dest)
    return root


def test_the_repo_itself_passes():
    r = _run(REPO)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_whole_payload_passes(payload):
    r = _run(payload)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ADAPTER PAYLOAD: OK" in r.stdout


@pytest.mark.parametrize("rel", NEW_PATHS)
def test_each_path_is_checked_and_missing_it_goes_red(payload, rel):
    """One assertion per new path, and each can fail: delete it and ITS line goes red."""
    (payload / rel).unlink()
    r = _run(payload)
    assert r.returncode == 1, r.stdout + r.stderr
    line = [l for l in r.stdout.splitlines() if rel in l]
    assert line and "!!" in line[0], f"{rel}: no red line naming it:\n{r.stdout}"


def test_an_empty_adapter_file_is_not_present(payload):
    """A zero-byte ADAPTING.md is a file that exists and says nothing."""
    (payload / "ADAPTING.md").write_text("", encoding="utf-8")
    assert _run(payload).returncode == 1


def test_both_release_gates_run_the_adapter_check():
    """The construct, not a name in a comment: an executable line in each gate runs the checker."""
    for rel in (".github/workflows/release.yml", "tools/verify-release.sh"):
        text = (REPO / rel).read_text(encoding="utf-8")
        runs = [l for l in text.splitlines()
                if not l.strip().startswith("#") and "python" in l and "check-adapter-payload.py" in l]
        assert runs, f"{rel} never runs tools/check-adapter-payload.py"


def test_the_release_zip_rule_does_not_exclude_the_new_paths():
    """release.yml builds the zip by exclusion; none of its --exclude names may swallow a new path."""
    import re
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    excluded = set(re.findall(r"--exclude '([^']+)'", text))
    assert excluded, "no --exclude rules found: the zip rule changed, re-read it"
    for rel in NEW_PATHS:
        for part in rel.split("/"):
            assert part not in excluded, f"{rel} is excluded from the zip by '--exclude {part}'"
