"""The maintainers' local pre-push gate selects the tests a push can turn red.

scripts/prepush-gate.py never ships (test_release_build / test_release_payload pin that); these tests pin what it
RUNS, because a gate that skips the test CI will fail on is the month of CI rounds it exists to end.
"""
import importlib.util
import sys

from conftest import REPO


def _gate():
    spec = importlib.util.spec_from_file_location("prepush_gate", REPO / "scripts" / "prepush-gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_changed_file_selects_every_test_that_names_it(tmp_path):
    """CI run 41254615914: full-qualification.yml changed; test_pr_ci_policy asserts on it by name and shares no stem
    with it, so the stem-only gate never ran it and all 8 legs went red on a deterministic assertion."""
    g = _gate()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_pr_ci_policy.py").write_text('def test_x():\n    _workflow("full-qualification.yml")\n', encoding="utf-8")
    (tests / "test_unrelated.py").write_text("def test_y():\n    pass\n", encoding="utf-8")
    (tests / "test_mutations.py").write_text('MUTANTS = ["full-qualification.yml"]\n', encoding="utf-8")
    g.REPO = tmp_path
    picked = g.referencing_tests([".github/workflows/full-qualification.yml"], test_dir=tests)
    assert picked == ["tests/test_pr_ci_policy.py"], picked        # named: yes; unrelated: no; the mutation gate: never


def test_a_changed_workflow_selects_a_test_that_only_names_the_folder(tmp_path):
    """X4 CI run 41399417477: a test globbed .github/workflows and named no file, so name matching skipped it."""
    g = _gate()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_globber.py").write_text('ROOT = ".github/workflows"\n', encoding="utf-8")
    (tests / "test_unrelated.py").write_text("def test_y():\n    pass\n", encoding="utf-8")
    g.REPO = tmp_path
    assert g.referencing_tests([".github/workflows/ci.yml"], test_dir=tests) == ["tests/test_globber.py"]
    assert g.referencing_tests(["tools/x.py"], test_dir=tests) == []          # only a workflow change adds the folder


def test_an_unreadable_base_is_not_read_as_nothing_changed():
    g = _gate()
    assert g.base_readable("HEAD")
    assert not g.base_readable("no-such-revision-at-all")
    assert not g.base_readable("")


def test_a_changed_test_file_selects_only_itself_by_reference(tmp_path):
    """A changed test's own name is not a reference to chase: every test that mentions test_x.py would otherwise run."""
    g = _gate()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text('# see tests/test_b.py\n', encoding="utf-8")
    (tests / "test_b.py").write_text("def test_b():\n    pass\n", encoding="utf-8")
    g.REPO = tmp_path
    assert g.referencing_tests(["tests/test_b.py"], test_dir=tests) == []


def test_a_large_changed_file_set_is_split_into_parallel_chunks_not_refused():
    """A peer's 177-file branch timed the one changed-file process out at its 900 s cap (2026-11-01): a time-budget
    refusal, not a test failure. More files mean more processes; every file still runs exactly once."""
    g = _gate()
    assert g.rest_chunks([]) == []
    assert g.rest_chunks(["a"]) == [["a"]]
    files = ["t%02d" % i for i in range(30)]
    chunks = g.rest_chunks(files)
    assert len(chunks) == 4, [len(c) for c in chunks]
    assert sorted(f for c in chunks for f in c) == files           # each file once, none dropped
    assert max(len(c) for c in chunks) - min(len(c) for c in chunks) <= 1   # balanced
    assert len(g.rest_chunks(files[:7])) == 2


def test_the_real_batch_that_turned_ci_red_is_now_selected():
    """Against the real tests/ tree: the three files CI 41254615914 failed in are picked for the files that changed."""
    g = _gate()
    picked = set(g.tests_for([".github/workflows/full-qualification.yml", ".github/workflows/ci.yml",
                              "scripts/build_release_zip.py"]))
    assert {"tests/test_dependency_locks.py", "tests/test_pr_ci_policy.py", "tests/test_verify_release.py"} <= picked, picked
    assert "tests/test_mutations.py" not in picked
