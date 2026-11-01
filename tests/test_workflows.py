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
    if name == "ci.yml":
        from ci_workflow import load_ci
        data = load_ci(REPO)
        text += "\n" + (WF / "full-qualification.yml").read_text(encoding="utf-8")
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
        if ref.startswith("./"):
            assert ref == "./.github/workflows/full-qualification.yml" and (WF / "full-qualification.yml").is_file()
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{name}: {ref} is not pinned by a full commit sha"
        assert re.search(r"#\s*v\d", rest), f"{name}: {ref} has no `# vX.Y.Z` comment naming the release"


@pytest.mark.parametrize("name", _workflows())
def test_every_job_has_a_timeout(name):
    _, data = _load(name)
    for jid, job in data["jobs"].items():
        if "uses" in job:
            assert jid == "qualification" and job["uses"] == "./.github/workflows/full-qualification.yml"
            _, called = _load("full-qualification.yml")
            assert all(isinstance(j.get("timeout-minutes"), int) and 0 < j["timeout-minutes"] <= 300 for j in called["jobs"].values())
            continue
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
                assert (s.get("with") or {}).get("persist-credentials") is False, \
                    f"{name}/{jid}: checkout keeps the token in .git/config"


def test_the_load_test_never_runs_in_ci_and_the_deadline_tests_run_on_both_legs():
    text, data = _load("ci.yml")
    assert "SKYGUARD_LOAD_TEST" not in text and "test_hook_deadline_load" not in text.replace(
        "(test_hook_deadline_load.py) is NOT run in CI", "")
    beh = data["jobs"]["behavior"]
    timing = [s for s in _steps(beh) if "tests/test_hook_deadline.py" in s.get("run", "")]
    # Both legs: no condition beyond the setup gate every check carries (test_one_red_check_never_skips_the_later_ones).
    assert timing and timing[0].get("if", SETUP_GATE) == SETUP_GATE and isinstance(timing[0].get("timeout-minutes"), int), timing


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
    suite = _index(steps, lambda s: "run_harness_tests.py $SUITE -v" in s.get("run", ""))
    log = _index(steps, lambda s: "check_ci_leg.py log" in s.get("run", ""))
    assert None not in (collect, suite, log) and collect < suite < log, (collect, suite, log)
    assert steps[collect].get("if") == SETUP_GATE and steps[log].get("if") == "always()"
    for i in (collect, log):
        assert '--leg "$RUNNER_OS"' in steps[i]["run"]
    # The two parts are the whole suite: one runs exactly the mutation gate (as shards, see
    # test_the_mutation_gate_is_sharded_and_summed), the other everything but it, and the mutation part is checked
    # against its own numbers.
    m = data["jobs"]["behavior"]["strategy"]["matrix"]
    assert m["part"] == ["suite"] and {e["part"] for e in m["include"]} == {"mutations"}
    assert steps[suite]["env"]["SUITE"] == ("${{ matrix.part == 'mutations' && 'tests/test_mutations.py' "
                                            "|| 'tests/ --ignore=tests/test_mutations.py' }}")
    assert steps[suite]["env"]["SKYGUARD_MUTATION_SHARD"] == (
        "${{ matrix.part == 'mutations' && format('{0}/{1}', matrix.shard, matrix.shards) || '' }}")
    assert steps[log]["env"]["PART"] == "${{ matrix.part == 'mutations' && '--part mutations' || '' }}"
    assert "$PART" in steps[log]["run"]
    assert "set -o pipefail" in steps[suite]["run"], "a `| tee` without pipefail turns a red suite green"
    # Review M7: the job default is what makes every step bash -eo pipefail on BOTH legs; on Windows the
    # runner's default shell is PowerShell, where `tee`, `set -o` and "$RUNNER_OS" mean something else.
    assert data["jobs"]["behavior"].get("defaults", {}).get("run", {}).get("shell") == "bash"


SETUP_GATE = "${{ !cancelled() && steps.setup.outcome == 'success' }}"
# Jobs whose later steps CONSUME an earlier step's output (the restored tools, the built zip), so a red step there
# must skip the rest, and jobs with one check. Every other job ends its setup with the marker below.
SEQUENTIAL_JOBS = {"setup-smoke", "dotnet", "release-build", "resaver-plus", "mutations-total",
                   "policy", "qualification", "required", "full-required"}


def test_one_red_check_never_skips_the_later_ones():
    """Final sweep 2026-10-25 (X4 a0e4fd1). A step `if:` without a status function means `success() && ...`, so the
    first red check skipped every check after it: one failing gate hid the suite, its ceilings and the Node tests.
    Each job closes its setup with a `Setup complete` step (id: setup); every later step is gated on that marker
    (`!cancelled() && steps.setup.outcome == 'success'`, plus its own condition) or on always(). A failed step still
    fails the job."""
    _, data = _load("ci.yml")
    jobs = data["jobs"]
    assert set(jobs) - SEQUENTIAL_JOBS, "no job left to check: this test would be vacuous"
    for name, job in jobs.items():
        if name in SEQUENTIAL_JOBS:
            continue
        steps = _steps(job)
        marker = _index(steps, lambda s: s.get("id") == "setup")
        assert marker is not None, f"{name}: no `Setup complete` step (id: setup) closes the setup block"
        later = steps[marker + 1:]
        assert later, f"{name}: nothing after the setup marker"
        for s in later:
            cond = str(s.get("if", ""))
            assert cond == "always()" or cond.startswith(SETUP_GATE[:-3]), (
                f"{name}: step {s.get('name')!r} runs only if EVERY earlier check passed (if: {cond or 'none'})")


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
    # Review I1: the identity comes from the LEAK_TOKENS secret, and an absent one fails the step closed.
    env = steps[verify].get("env", {})
    assert env.get("LEAK_TOKENS") == "${{ secrets.LEAK_TOKENS }}" and str(env.get("VERIFY_REQUIRE_LEAK_TOKENS")) == "1", env
    co = steps[0]
    assert co["with"]["fetch-depth"] == 0 and co["with"]["ref"] == "${{ github.event.inputs.tag || github.ref }}"


def test_release_masks_every_identity_spelling_before_anything_prints():
    """Re-review N1: GitHub masks a secret's literal value, not the spellings derived from it. The mask step is
    the first step that runs a script, and it reads the same secret."""
    _, steps = _release_steps()
    first = _index(steps, lambda s: "run" in s)
    assert "check-release-payload.py --mask-tokens" in steps[first]["run"], steps[first].get("name")
    assert steps[first].get("env", {}).get("LEAK_TOKENS") == "${{ secrets.LEAK_TOKENS }}"


def test_the_ci_release_build_builds_before_anything_writes():
    _, data = _load("ci.yml")
    steps = _steps(data["jobs"]["release-build"])
    first_run = _index(steps, lambda s: "run" in s)
    assert "scripts/build-release-zip.sh" in steps[first_run]["run"]
    assert any("verify-release.sh --local" in s.get("run", "") and "--scrubbed" in s.get("run", "") for s in steps)
    assert set(data["jobs"]["release-build"]["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}


def test_resaver_plus_uses_the_qualified_vs2022_runner():
    _, data = _load("ci.yml")
    assert data["jobs"]["resaver-plus"]["runs-on"] == "windows-2022"


def test_mutations_run_once_per_os_on_the_oldest_tested_python():
    """User ruling 2026-10-28: the mutation part runs on one Python per OS; the suite part keeps every tested Python."""
    m = _load("full-qualification.yml")[1]["jobs"]["behavior"]["strategy"]["matrix"]
    assert m["python"] == TESTED_PYTHONS and m["part"] == ["suite"]
    assert {(e["os"], e["python"]) for e in m["include"]} == {(os, TESTED_PYTHONS[0]) for os in m["os"]}
    assert m["os"][0] == "windows-latest"


def test_the_mutation_gate_is_sharded_and_summed():
    """User ruling 2026-10-31: the run is as long as its slowest job. One Windows mutation job was 258 of 270 minutes
    (CI run 40953576542), so the gate runs as shards and a totals job checks the shards' logs as ONE gate."""
    _, data = _load("full-qualification.yml")
    beh = data["jobs"]["behavior"]
    m = beh["strategy"]["matrix"]
    shards = {}
    for e in m["include"]:
        assert set(e) == {"os", "python", "part", "shard", "shards"}, e
        shards.setdefault(e["os"], set()).add((e["shard"], e["shards"]))
    for os_, got in shards.items():
        n = {s for _, s in got}
        assert len(n) == 1, f"{os_}: shard counts disagree: {got}"
        n = n.pop()
        assert got == {(i, n) for i in range(1, n + 1)}, f"{os_}: shards {got} are not 1..{n}: a twin would never run"
    # every leg runs at once: the cap covers the suite legs plus every shard
    assert beh["strategy"]["max-parallel"] >= len(m["os"]) * len(m["python"]) + len(m["include"])
    # the totals job: after every behavior job, even a red one, one per OS, the right shard count, the shards' logs
    tot = data["jobs"]["mutations-total"]
    # no `if:`: the core contract forbids a conditional full job (a red shard already fails the run)
    assert tot["needs"] == ["behavior"] and "if" not in tot
    totals = {e["os"]: e for e in tot["strategy"]["matrix"]["include"]}
    assert set(totals) == set(shards)
    for os_, e in totals.items():
        assert e["shards"] == len(shards[os_]), (os_, e)
        assert e["leg"] == ("Windows" if os_.startswith("windows") else "Linux")
    steps = _steps(tot)
    dl = [s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact@")]
    assert dl and dl[0]["with"]["pattern"] == "behavior-${{ matrix.os }}-" + TESTED_PYTHONS[0] + "-mutations-*"
    check = [s for s in steps if "check_ci_leg.py log" in s.get("run", "")]
    assert check and '"${logs[@]}" --leg "$LEG" --part mutations' in check[0]["run"]
    assert '-ne "$SHARDS"' in check[0]["run"], "a missing shard log must be red, not a smaller sum"
    # the totals job is part of the gate: the full-required verifier needs it and lists it
    fr = data["jobs"]["full-required"]
    assert "mutations-total" in fr["needs"]
    verify = [s for s in _steps(fr) if (s.get("with") or {}).get("mode") == "verify"]
    assert verify and '"mutations-total"' in verify[0]["with"]["required-jobs"]
    # the shard artifact name is what the totals job downloads
    up = [s for s in _steps(beh) if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert up[0]["with"]["name"].startswith("behavior-${{ matrix.os }}-${{ matrix.python }}-${{ matrix.part }}")
    assert "format('-{0}of{1}', matrix.shard, matrix.shards)" in up[0]["with"]["name"]
