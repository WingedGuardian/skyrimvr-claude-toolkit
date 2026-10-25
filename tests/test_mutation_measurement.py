"""Mutation verdicts require a passing pristine case in the same fixture topology."""
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest
import test_mutations as instrument


def _report(path, outcome, name="test_value"):
    suite = ET.Element("testsuite", tests="0" if outcome == "empty" else "1",
                       failures=str(int(outcome == "failed")),
                       errors=str(int(outcome == "error")),
                       skipped=str(int(outcome == "skipped")))
    if outcome != "empty":
        case = ET.SubElement(suite, "testcase", file="tests/test_owned.py",
                             classname="test_owned", name=name)
        tag = {"failed": "failure", "error": "error", "skipped": "skipped"}.get(outcome)
        if tag:
            ET.SubElement(case, tag)
    ET.ElementTree(suite).write(path, encoding="utf-8")


@pytest.mark.parametrize("case,expected", [
    ("kill", ""),
    ("baseline-red", "PRISTINE ANCHOR"),
    ("baseline-collection", "PRISTINE ANCHOR"),
    ("baseline-empty", "no executed"),
    ("baseline-skipped", "pristine anchor did not execute"),
    ("survivor", "expected"),
    ("mutant-error", "test execution error"),
    ("mutant-collection", "expected"),
    ("different-case", "actually passed"),
])
def test_pristine_pass_precedes_each_measured_kill(tmp_path, monkeypatch, case, expected):
    repo = tmp_path / "pristine"
    repo.mkdir()
    original = b"VALUE = 1\n"
    (repo / "value.py").write_bytes(original)
    monkeypatch.setattr(instrument, "REPO", repo)
    row = tmp_path / "row"
    calls = []
    def run(args, **kwargs):
        scratch = Path(args[args.index("--basetemp") + 1])
        report = Path(args[args.index("--junitxml") + 1])
        assert scratch.parent.is_dir()
        assert scratch.parent.parent.resolve() == row.parent.resolve()
        assert args[:4] == [instrument.sys.executable, "-B", "-m", "pytest"]
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        value = (Path(kwargs["cwd"]) / "value.py").read_bytes()
        calls.append((scratch, value))
        code, outcome, name = (0, "passed", "test_value") if len(calls) == 1 else (1, "failed", "test_value")
        if len(calls) == 1:
            if case == "baseline-red": code, outcome = 1, "failed"
            if case == "baseline-collection": code, outcome = 2, "error"
            if case == "baseline-empty": outcome = "empty"
            if case == "baseline-skipped": outcome = "skipped"
        else:
            if case == "survivor": code, outcome = 0, "passed"
            if case == "mutant-error": outcome = "error"
            if case == "mutant-collection": code, outcome = 2, "error"
            if case == "different-case": name = "test_unmeasured"
        _report(report, outcome, name)
        return subprocess.CompletedProcess(args, code, "owned fixture", "")
    monkeypatch.setattr(instrument.subprocess, "run", run)
    def measure():
        instrument.test_guard_actually_bites(row, "value.py", "VALUE = 1", "VALUE = 2",
                                             "tests/test_owned.py::test_value")
    if expected:
        with pytest.raises(AssertionError, match=expected):
            measure()
    else:
        measure()
    assert calls[0][1] == original
    if len(calls) == 2:
        assert calls[0][0] == calls[1][0], "both legs must use exactly the same path"
        assert calls[1][1] == b"VALUE = 2\n"
    assert not calls[0][0].parent.exists(), "owned scratch must be cleaned on failure too"
    assert (repo / "value.py").read_bytes() == original
    if expected:    # a failing row keeps its copy for debugging, with the mutant reapplied
        assert (row / "mutated/value.py").read_bytes() == b"VALUE = 2\n"
    else:           # a killed row's whole-toolkit copy is removed (one local run once held ~25 GiB of them)
        assert not (row / "mutated").exists()


def test_pristine_timeout_cleans_scratch_and_reapplies_only_private_mutant(tmp_path, monkeypatch):
    repo = tmp_path / "pristine"
    repo.mkdir()
    (repo / "value.py").write_bytes(b"VALUE = 1\n")
    monkeypatch.setattr(instrument, "REPO", repo)
    observed = []
    def timeout(args, **kwargs):
        observed.append(Path(args[args.index("--basetemp") + 1]).parent)
        raise subprocess.TimeoutExpired(args, 600)
    monkeypatch.setattr(instrument.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        instrument.test_guard_actually_bites(tmp_path / "row", "value.py", "VALUE = 1",
                                             "VALUE = 2", "tests/test_owned.py::test_value")
    assert len(observed) == 1 and not observed[0].exists()
    assert (repo / "value.py").read_bytes() == b"VALUE = 1\n"
    assert (tmp_path / "row/mutated/value.py").read_bytes() == b"VALUE = 2\n"


def test_actual_children_distinguish_semantic_mutation_and_noop(tmp_path, monkeypatch):
    repo = tmp_path / "pristine"
    (repo / "tests").mkdir(parents=True)
    (repo / "value.py").write_bytes(b"VALUE = 1\n")
    (repo / "tests/test_owned.py").write_text(
        "from value import VALUE\ndef test_value():\n    assert VALUE == 1\n",
        encoding="utf-8")
    monkeypatch.setattr(instrument, "REPO", repo)
    instrument.test_guard_actually_bites(tmp_path / "semantic", "value.py", "VALUE = 1",
                                         "VALUE = 2", "tests/test_owned.py::test_value")
    with pytest.raises(AssertionError, match="expected.*FAIL"):
        instrument.test_guard_actually_bites(tmp_path / "noop", "value.py", "VALUE = 1",
                                             "VALUE = 1 # noop", "tests/test_owned.py::test_value")
    assert (repo / "value.py").read_bytes() == b"VALUE = 1\n"


@pytest.mark.parametrize("leg", ["baseline", "mutant"])
def test_existing_platform_skip_is_handled_before_report_qualification(tmp_path, monkeypatch, leg):
    repo = tmp_path / "pristine"
    repo.mkdir()
    (repo / "value.py").write_bytes(b"VALUE = 1\n")
    monkeypatch.setattr(instrument, "REPO", repo)
    skip_reason = instrument._anchor_skipped_off_windows
    monkeypatch.setattr(instrument, "_anchor_skipped_off_windows",
                        lambda code, out, platform="linux": skip_reason(code, out, platform))
    calls = []
    def run(args, **kwargs):
        calls.append(Path(args[args.index("--basetemp") + 1]).parent)
        if leg == "baseline" or len(calls) == 2:
            # Collection skips need not carry an ordinary testcase identity.
            return subprocess.CompletedProcess(args, 0,
                "s\nSKIPPED [1] tests/test_owned.py:1: platform fixture skipped\n1 skipped in 0.1s\n", "")
        _report(Path(args[args.index("--junitxml") + 1]), "passed")
        return subprocess.CompletedProcess(args, 0, "1 passed in 0.1s\n", "")
    monkeypatch.setattr(instrument.subprocess, "run", run)
    # A pristine skip excuses the twin off Windows. A mutant-only skip after a PASSED pristine leg is the revert
    # switching the check off: red on every platform (review L7 LOW).
    expected = (pytest.raises(pytest.skip.Exception, match="skipped") if leg == "baseline"
                else pytest.raises(AssertionError, match="SURVIVOR: .* only SKIPPED with this fix reverted"))
    with expected:
        instrument.test_guard_actually_bites(tmp_path / "row", "value.py", "VALUE = 1",
                                             "VALUE = 2", "tests/test_owned.py::test_value")
    assert len(calls) == (1 if leg == "baseline" else 2)
    assert all(not path.exists() for path in calls)


def test_node_backed_anchor_keeps_locked_dependencies_and_required_flag_for_both_legs(tmp_path, monkeypatch):
    repo = tmp_path / "pristine"
    modules = repo / "node_modules"
    modules.mkdir(parents=True)
    marker = modules / "owned-dependency.txt"
    marker.write_bytes(b"unchanged locked dependency fixture")
    (repo / "value.py").write_bytes(b"VALUE = 1\n")
    monkeypatch.setattr(instrument, "REPO", repo)
    monkeypatch.setenv("TOOLKIT_REQUIRE_NODE_DEPS", "1")
    monkeypatch.setenv("NODE_PATH", str(tmp_path / "unrelated-dependencies"))
    calls = []

    def run(args, **kwargs):
        work = Path(kwargs["cwd"])
        assert not (work / "node_modules").exists(), "mutation copies still exclude dependencies"
        assert kwargs["env"]["NODE_PATH"] == str(modules)
        assert kwargs["env"]["TOOLKIT_REQUIRE_NODE_DEPS"] == "1"
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        value = (work / "value.py").read_bytes()
        calls.append(value)
        _report(Path(args[args.index("--junitxml") + 1]), "passed" if len(calls) == 1 else "failed")
        return subprocess.CompletedProcess(args, 0 if len(calls) == 1 else 1, "owned fixture", "")

    monkeypatch.setattr(instrument.subprocess, "run", run)
    instrument.test_guard_actually_bites(tmp_path / "row", "value.py", "VALUE = 1", "VALUE = 2",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests")
    assert calls == [b"VALUE = 1\n", b"VALUE = 2\n"]
    assert marker.read_bytes() == b"unchanged locked dependency fixture"


@pytest.mark.parametrize("test_id", [
    "tests/test_owned.py::test_value",
    "tests/test_repo_invariants.py::test_setup_prompt_is_identical_everywhere",
    "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests_extra",
])
def test_other_anchors_keep_their_existing_dependency_environment(tmp_path, monkeypatch, test_id):
    monkeypatch.setattr(instrument, "REPO", tmp_path / "pristine")
    monkeypatch.setenv("NODE_PATH", "owned-existing-node-path")
    monkeypatch.setenv("TOOLKIT_REQUIRE_NODE_DEPS", "1")
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "owned fixture", "")

    monkeypatch.setattr(instrument.subprocess, "run", run)
    instrument._run_mutation_anchor(tmp_path / "work", test_id, tmp_path / "scratch", tmp_path / "report.xml")
    assert len(calls) == 1
    assert calls[0]["NODE_PATH"] == "owned-existing-node-path"
    assert calls[0]["TOOLKIT_REQUIRE_NODE_DEPS"] == "1"
