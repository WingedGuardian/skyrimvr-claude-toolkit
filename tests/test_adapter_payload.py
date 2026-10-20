"""The adapter kit ships, and the release gates would notice if it did not.

The release zip is built by rule (scripts/build-release-zip.sh), so the files ship without a list. A rule can
change; a file that is expected but missing does not error on a user's machine, it just leaves an agent with no
ADAPTING.md. So each path is asserted on the payload by the adapter section of tools/check-release-payload.py
(Plan 8 T10 folded the separate check-adapter-payload.py into it), which both release gates run.
`tests/` is not shipped, so the toy-agent test is a CI gate, not a bundle member.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

CHECKER = REPO / "tools" / "check-release-payload.py"

CITE = re.compile(r"\[(src|repo|spec|measured): ([^\]]+)\]")
SPEC = "docs/evidence/2026-09-30-universal-agent-support-design.md"


def adapting_references():
    """Every repo file ADAPTING.md tells the agent to use (`tools/...` in backticks) or cites as evidence."""
    text = (REPO / "ADAPTING.md").read_text(encoding="utf-8")
    out = {p for p in re.findall(r"`((?:tools|tests|agent|docs)/[A-Za-z0-9_./-]+\.[a-z]+)`", text) if "<" not in p}
    out.update(re.findall(r"\]\(((?:tools|tests|agent|docs)/[A-Za-z0-9_./-]+\.[a-z]+)\)", text))
    for kind, target in CITE.findall(text):
        out.add(SPEC if kind == "spec" else re.split(r"::|#", target)[0].strip())
    return sorted(out)


NEW_PATHS = sorted(set([
    "ADAPTING.md",
    "tools/adapting/adapter_template.py",
    "tools/adapting/capability-report.template.json",
    "tools/adapter_conformance.py",
    "tools/inventory-runtimes.py",
    "tools/skyguard/neutral.py",
    ".claude/skills/toolkit-setup/SKILL.md",
    ".agents/skills/toolkit-setup/SKILL.md",
    "tools/adapting/examples/json_deny.slots.py",
    "tools/adapting/examples/exit2.slots.py",
] + adapting_references()))


def test_every_file_adapting_md_names_or_cites_is_checked():
    """Final review M2: ADAPTING.md sends the agent to the worked examples and cites the spec and measurements,
    but the payload check did not list them: a rule change could drop them and leave dead references."""
    spec = importlib.util.spec_from_file_location("check_release_payload", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    refs = adapting_references()
    assert "tools/adapting/examples/json_deny.slots.py" in refs and SPEC in refs, refs   # the scan sees them
    missing = sorted(set(refs) - set(mod.ADAPTER_REQUIRED))
    assert not missing, missing


def _run(root):
    return subprocess.run([sys.executable, "-B", str(CHECKER), str(root), "--section", "adapter"],
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
    assert "RELEASE PAYLOAD: OK" in r.stdout


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
    """The construct, not a name in a comment: verify-release.sh runs the one checker (adapter section included),
    and release.yml runs verify-release.sh --local on the zip it publishes."""
    verify = (REPO / "tools" / "verify-release.sh").read_text(encoding="utf-8")
    runs = [l for l in verify.splitlines()
            if not l.strip().startswith("#") and ("python" in l.lower() or l.lstrip().startswith('"$PY"'))
            and "check-release-payload.py" in l]
    assert runs, "tools/verify-release.sh never runs tools/check-release-payload.py"
    workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    runs = [l for l in workflow.splitlines()
            if not l.strip().startswith("#") and "tools/verify-release.sh" in l and "--local" in l]
    assert runs, "release.yml never runs tools/verify-release.sh --local"


LOST_CONTINUATION = re.compile(r"\S {6,}(\|\||&&)")


def test_the_release_gates_parse_and_lost_no_line_continuation():
    """Final review M1: a heredoc edit ate the backslash of a `\\` continuation in release.yml, leaving the command
    on one physical line with a run of spaces where the line break was. It still ran, by luck of the spacing."""
    yaml = pytest.importorskip("yaml")
    for wf in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        yaml.safe_load(wf.read_text(encoding="utf-8"))
    for rel in [p.relative_to(REPO).as_posix() for p in sorted((REPO / ".github" / "workflows").glob("*.yml"))] + [
            "tools/verify-release.sh"]:
        bad = [l.strip()[:90] for l in (REPO / rel).read_text(encoding="utf-8").splitlines()
               if LOST_CONTINUATION.search(l)]
        assert not bad, (rel, bad)
    assert LOST_CONTINUATION.search('python3 x.py "/tmp/$NAME"             || { exit 1; }')
    assert not LOST_CONTINUATION.search('          || { echo "x"; exit 1; }')


def test_the_release_zip_rule_does_not_exclude_the_new_paths():
    """The builder ships `git archive` minus EXCLUDE; no excluded path prefix may swallow a new path."""
    spec = importlib.util.spec_from_file_location("build_release_zip", REPO / "scripts" / "build_release_zip.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    assert b.EXCLUDE, "no exclude rules found: the zip rule changed, re-read it"
    for rel in NEW_PATHS:
        assert not b.is_excluded(rel), f"{rel} is excluded from the zip by EXCLUDE"
