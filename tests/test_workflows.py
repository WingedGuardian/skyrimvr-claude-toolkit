"""Workflow safety and CI shape (Plan 8 T10.4, T10.6) -- asserted on the parsed YAML, step by step.

A workflow is code that runs with the repo's token. These are the properties a reviewer would otherwise have to
re-check by eye on every edit: actions pinned by commit sha, a timeout on every job, user input never spliced
into a script, least privilege, the release gates in the right ORDER (a build placed after a step that dirties
the tree, or a check placed after the publish, looks correct and protects nothing).
"""
from __future__ import annotations

import re

import pytest

from conftest import REPO

yaml = pytest.importorskip("yaml")
WF = REPO / ".github" / "workflows"
TESTED_PYTHONS = ["3.10", "3.11", "3.12", "3.13"]     # user ruling 2026-10-05: the tested range


def _load(name):
    text = (WF / name).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    # PyYAML reads the bare key `on` as the boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return text, data


def _steps(job):
    return job.get("steps", [])


def _index(steps, pred):
    return next((i for i, s in enumerate(steps) if pred(s)), None)


def _workflows():
    return sorted(p.name for p in WF.glob("*.yml"))


@pytest.mark.parametrize("name", _workflows())
def test_every_action_is_pinned_by_commit_sha(name):
    text, _ = _load(name)
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", text, re.M)
    assert uses, f"{name}: no `uses:` at all -- this check would be vacuous"
    for ref, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"{name}: {ref} is not pinned by a full commit sha"
        assert re.search(r"#\s*v\d", rest), f"{name}: {ref} has no `# vX.Y.Z` comment naming the release"


@pytest.mark.parametrize("name", _workflows())
def test_every_job_has_a_timeout(name):
    _, data = _load(name)
    for jid, job in data["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int) and 0 < job["timeout-minutes"] <= 300, \
            f"{name}/{jid}: no timeout-minutes (a hang would stall for six hours instead of going red)"


@pytest.mark.parametrize("name", _workflows())
def test_no_user_input_is_spliced_into_a_script(name):
    """`${{ github.event.inputs.tag }}` inside `run:` is pasted into the script before bash parses it: a crafted
    tag name becomes code. Values reach scripts through `env:` instead."""
    _, data = _load(name)
    for jid, job in data["jobs"].items():
        for s in _steps(job):
            run = s.get("run", "")
            assert not re.search(r"\$\{\{\s*(github\.event\.|inputs\.|github\.head_ref)", run), \
                f"{name}/{jid}/{s.get('name')}: user-controlled ${{{{ }}}} inside run:"


@pytest.mark.parametrize("name", _workflows())
def test_least_privilege_and_no_dangerous_triggers(name):
    _, data = _load(name)
    assert data.get("permissions") == {"contents": "read"}, f"{name}: top-level permissions must be contents: read"
    assert "pull_request_target" not in (data["on"] or {}), f"{name}: pull_request_target runs fork code with secrets"
    for jid, job in data["jobs"].items():
        for s in _steps(job):
            if str(s.get("uses", "")).startswith("actions/checkout@"):
                assert (s.get("with") or {}).get("persist-credentials") is False or name == "ci.yml", \
                    f"{name}/{jid}: checkout keeps the token in .git/config"


def test_the_load_test_never_runs_in_ci_and_the_deadline_tests_run_on_both_legs():
    text, data = _load("ci.yml")
    assert "SKYGUARD_LOAD_TEST" not in text and "test_hook_deadline_load" not in text.replace(
        "(test_hook_deadline_load.py) is NOT run in CI", "")
    beh = data["jobs"]["behavior"]
    timing = [s for s in _steps(beh) if "tests/test_hook_deadline.py" in s.get("run", "")]
    assert timing and "if" not in timing[0] and isinstance(timing[0].get("timeout-minutes"), int), timing


def test_the_behavior_matrix_is_both_oses_times_the_tested_pythons():
    _, data = _load("ci.yml")
    beh = data["jobs"]["behavior"]
    m = beh["strategy"]["matrix"]
    assert set(m["os"]) == {"ubuntu-latest", "windows-latest"}
    assert m["python"] == TESTED_PYTHONS
    assert beh["strategy"]["fail-fast"] is False
    sp = [s for s in _steps(beh) if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert sp and sp[0]["with"]["python-version"] == "${{ matrix.python }}"


def test_node_comes_from_node_version_and_dotnet_is_channel_10():
    for name in _workflows():
        _, data = _load(name)
        for jid, job in data["jobs"].items():
            for s in _steps(job):
                u = str(s.get("uses", ""))
                if u.startswith("actions/setup-node@"):
                    assert s["with"] == {"node-version-file": ".node-version"}, (name, jid)
                if u.startswith("actions/setup-dotnet@"):
                    assert s["with"]["dotnet-version"] == "10.0.x", (name, jid)
    _, data = _load("ci.yml")
    assert any(str(s.get("uses", "")).startswith("actions/setup-dotnet@") for s in _steps(data["jobs"]["dotnet"]))


def test_the_tripwires_bracket_the_suite_on_every_leg():
    _, data = _load("ci.yml")
    steps = _steps(data["jobs"]["behavior"])
    collect = _index(steps, lambda s: "check_ci_leg.py collect" in s.get("run", ""))
    suite = _index(steps, lambda s: "run_harness_tests.py tests/ -v" in s.get("run", ""))
    log = _index(steps, lambda s: "check_ci_leg.py log" in s.get("run", ""))
    assert None not in (collect, suite, log) and collect < suite < log, (collect, suite, log)
    assert "if" not in steps[collect] and steps[log].get("if") == "always()"
    for i in (collect, log):
        assert '--leg "$RUNNER_OS"' in steps[i]["run"]
    assert "set -o pipefail" in steps[suite]["run"], "a `| tee` without pipefail turns a red suite green"


def test_every_job_that_runs_the_freeze_gate_has_full_history():
    for name in _workflows():
        _, data = _load(name)
        for jid, job in data["jobs"].items():
            runs = " ".join(s.get("run", "") for s in _steps(job))
            if "tests/" in runs and ("run_harness_tests.py tests/ " in runs or "test_codex_definitions" in runs):
                co = [s for s in _steps(job) if str(s.get("uses", "")).startswith("actions/checkout@")]
                assert co and (co[0].get("with") or {}).get("fetch-depth") == 0, f"{name}/{jid}: no fetch-depth: 0"


def test_no_workflow_ever_builds_from_a_dirty_tree():
    """The construct (a run: script passing it), not the word: the comments explaining the rule name it."""
    for name in _workflows():
        _, data = _load(name)
        for jid, job in data["jobs"].items():
            for s in _steps(job):
                assert "--allow-dirty" not in s.get("run", ""), (name, jid, s.get("name"))


def _release_steps():
    _, data = _load("release.yml")
    return data, _steps(data["jobs"]["release"])


def test_release_runs_only_on_a_tag_or_a_hand_named_tag():
    data, _ = _release_steps()
    assert set(data["on"]) == {"push", "workflow_dispatch"}
    assert data["on"]["push"] == {"tags": ["v*"]}
    assert data["jobs"]["release"]["permissions"] == {"contents": "write"}


def test_release_gates_are_in_order():
    """Validate the tag -> safety (on master, CHANGELOG) -> build FIRST (before anything writes into the
    checkout) -> freeze gate -> verify the zip -> publish. A gate after the publish protects nothing."""
    _, steps = _release_steps()
    find = lambda needle: _index(steps, lambda s: needle in s.get("run", ""))  # noqa: E731
    tag = find("^v[0-9]+")
    master = find("merge-base --is-ancestor")
    changelog = find("CHANGELOG.md has no section")
    build = find("scripts/build-release-zip.sh")
    pip = find("pip install")
    freeze = find("test_codex_definitions.py")
    verify = find("tools/verify-release.sh --local")
    publish = find("gh release create")
    order = [tag, master, changelog, build, freeze, verify, publish]
    assert None not in order, order
    assert tag < master <= changelog < build < freeze < verify < publish, order
    assert build < pip, "the build must run before pip writes anything"
    v = steps[verify]["run"]
    for flag in ("--scrubbed", '--expect-commit "$SHA"', '--tag "$TAG"'):
        assert flag in v, flag
    assert "origin/master" in steps[master]["run"]
    co = steps[0]
    assert co["with"]["fetch-depth"] == 0 and co["with"]["ref"] == "${{ github.event.inputs.tag || github.ref }}"


def test_the_ci_release_build_builds_before_anything_writes():
    _, data = _load("ci.yml")
    steps = _steps(data["jobs"]["release-build"])
    first_run = _index(steps, lambda s: "run" in s)
    assert "scripts/build-release-zip.sh" in steps[first_run]["run"]
    assert any("verify-release.sh --local" in s.get("run", "") and "--scrubbed" in s.get("run", "") for s in steps)
    assert set(data["jobs"]["release-build"]["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
