"""The maintainers' local pre-push gate selects the tests a push can turn red.

scripts/prepush-gate.py never ships (test_release_build / test_release_payload pin that); these tests pin what it
RUNS, because a gate that skips the test CI will fail on is the month of CI rounds it exists to end.
"""
import importlib.util
import os
import subprocess
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
    assert len(chunks) == 5, [len(c) for c in chunks]
    assert sorted(f for c in chunks for f in c) == files           # each file once, none dropped
    assert max(len(c) for c in chunks) - min(len(c) for c in chunks) <= 1   # balanced
    assert len(g.rest_chunks(files[:7])) == 2


def test_chunks_are_balanced_by_file_size_not_position(tmp_path, monkeypatch):
    g = _gate()
    monkeypatch.setattr(g, "REPO", tmp_path)
    for name, size in (("big", 900), ("a", 100), ("b", 100), ("c", 100), ("d", 100), ("e", 100), ("f", 100), ("g", 100)):
        (tmp_path / name).write_bytes(b"x" * size)
    chunks = g.rest_chunks(["big", "a", "b", "c", "d", "e", "f", "g"], per_chunk=4, max_chunks=2)
    assert sorted(len(c) for c in chunks) == [1, 7] and ["big"] in chunks


def test_the_real_batch_that_turned_ci_red_is_now_selected():
    """Against the real tests/ tree: the three files CI 41254615914 failed in are picked for the files that changed."""
    g = _gate()
    picked = set(g.tests_for([".github/workflows/full-qualification.yml", ".github/workflows/ci.yml",
                              "scripts/build_release_zip.py"]))
    assert {"tests/test_dependency_locks.py", "tests/test_pr_ci_policy.py", "tests/test_verify_release.py"} <= picked, picked
    assert "tests/test_mutations.py" not in picked


def test_a_changed_guard_file_selects_its_twins():
    """CI run 41560815549: three surviving twins of a file the gate had otherwise passed."""
    gate = _gate()
    assert gate.twin_selection(["tools/skyguard/writes.py"]) == ["tools/skyguard/writes.py"]
    assert gate.twin_selection(["tests/test_mutations.py"]) == []                    # the table alone names nothing yet


def test_lines_added_to_the_twin_table_select_the_files_they_name():
    gate = _gate()
    added = """+    ("tools/skyguard/saves.py", 'a', 'b', "tests/test_x.py::test_y", "id"),\n+    ("x", _WR, 'a', 'b'),\n"""
    assert gate.twin_selection(["tests/test_mutations.py"], added, {"_WR": "tools/skyguard/writes.py", "_NO": "tools/no.py"}) == [
        "tests/test_x.py", "tools/skyguard/saves.py", "tools/skyguard/writes.py"]


def test_the_twin_table_aliases_are_read_from_the_table():
    assert _gate().twin_aliases()["_WR"] == "tools/skyguard/writes.py"


def _collected(**env):
    node = "tests/test_mutations.py::test_guard_actually_bites"
    full = {k: v for k, v in os.environ.items() if not k.startswith("SKYGUARD_MUTATION")}
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", node], cwd=REPO,
                       env={**full, **env}, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [l for l in r.stdout.splitlines() if l.startswith(node)]


def test_the_twin_filter_keeps_only_the_named_files():
    from_table = _collected(SKYGUARD_MUTATION_FILES="tools/automod-cli.sh")
    everything = _collected()
    assert 0 < len(from_table) < len(everything) / 10
    limited = _collected(SKYGUARD_MUTATION_FILES="tools/automod-cli.sh", SKYGUARD_MUTATION_LIMIT="5")
    assert 0 < len(limited) <= 5 and set(limited) < set(from_table)
    shards = [_collected(SKYGUARD_MUTATION_FILES="tools/automod-cli.sh", SKYGUARD_MUTATION_SHARD=s) for s in ("1/2", "2/2")]
    assert sorted(shards[0] + shards[1]) == sorted(from_table) and shards[0] and shards[1]
