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


def test_a_tree_without_the_twin_selector_leaves_twins_to_ci(monkeypatch, capsys):
    """A listing over the cap means the selector was ignored: running it would be four copies of the whole table."""
    g = _gate()
    node = "tests/test_mutations.py::test_guard_actually_bites"

    def listing(count):
        out = "\n".join("{}[t{}]".format(node, i) for i in range(count))
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
    monkeypatch.setattr(g, "twin_selection", lambda *a: ["tools/x.py"])
    monkeypatch.setattr(g, "twin_aliases", lambda: {})
    monkeypatch.setattr(g.subprocess, "run", listing(201))
    assert g.twin_steps("py", "", ["tools/x.py"]) == []
    assert "left to CI" in capsys.readouterr().out
    monkeypatch.setattr(g.subprocess, "run", listing(200))           # the cap itself still runs, shared out
    assert len(g.twin_steps("py", "", ["tools/x.py"])) == 4


class _Pool:
    """A fake clock and fake processes: real ones take over a second to start here, so real-time order was a flake. A step's command
    is [name, ticks]; it finishes after that many ticks of the clock, and None never does."""

    def __init__(self, g, tmp_path, monkeypatch):
        from types import SimpleNamespace
        self.now, self.running, self.peak, self.ended = 0, 0, 0, []
        pool = self

        class Proc:
            def __init__(self, cmd, **_kwargs):
                self.name, ticks = cmd
                self.until = None if ticks is None else pool.now + ticks
                pool.running += 1
                pool.peak = max(pool.peak, pool.running)
                self.done = False

            def _finish(self):
                if not self.done:
                    self.done = True
                    pool.running -= 1
                    pool.ended.append(self.name)

            def poll(self):
                if self.until is not None and pool.now >= self.until:
                    self._finish()
                    return 0
                return None

            def kill(self):
                self._finish()

            def wait(self):
                return -9

        monkeypatch.setattr(g, "REPO", tmp_path)
        monkeypatch.setattr(g.subprocess, "Popen", Proc)
        monkeypatch.setattr(g, "time", SimpleNamespace(time=lambda: pool.now, sleep=lambda _s: setattr(pool, "now", pool.now + 1)))
        self.gate = g.Gate(sys.executable)


def _pool(tmp_path, monkeypatch):
    return _Pool(_gate(), tmp_path, monkeypatch)


def test_the_pool_runs_every_step_and_never_more_than_its_workers(tmp_path, monkeypatch):
    pool = _pool(tmp_path, monkeypatch)
    pool.gate.parallel([("s{}".format(i), ["s{}".format(i), 2], 60) for i in range(5)], workers=2)
    assert sorted(pool.ended) == ["s0", "s1", "s2", "s3", "s4"] and pool.peak == 2 and not pool.gate.failed


def test_no_worker_limit_runs_everything_at_once(tmp_path, monkeypatch):
    pool = _pool(tmp_path, monkeypatch)
    pool.gate.parallel([("s{}".format(i), ["s{}".format(i), 2], 60) for i in range(3)])
    assert pool.peak == 3


def test_a_slow_step_does_not_hold_up_the_others(tmp_path, monkeypatch):
    """What fixed chunks got wrong: the short steps finish on the free worker while one long step runs."""
    pool = _pool(tmp_path, monkeypatch)
    pool.gate.parallel([("slow", ["slow", 50], 60)] + [("q{}".format(i), ["q{}".format(i), 1], 60) for i in range(3)], workers=2)
    assert pool.ended == ["q0", "q1", "q2", "slow"] and pool.now < 55


def test_a_step_times_out_from_when_it_starts_not_from_when_the_pool_did(tmp_path, monkeypatch):
    pool = _pool(tmp_path, monkeypatch)
    pool.gate.parallel([("first", ["first", 10], 60), ("second", ["second", 2], 5)], workers=1)
    assert not pool.gate.failed and pool.ended == ["first", "second"]


def test_a_hung_step_fails_by_name_and_the_pool_goes_on(tmp_path, monkeypatch):
    pool = _pool(tmp_path, monkeypatch)
    pool.gate.parallel([("hung", ["hung", None], 3), ("after", ["after", 1], 60)], workers=1)
    assert pool.gate.failed == ["hung"] and pool.ended == ["hung", "after"]


def test_the_install_path_scan_runs_on_every_push():
    """It scans every test file, and a new test file names no path that maps to it: 14 CI jobs went red on one fixture path."""
    assert "tests/test_no_real_install_path.py" in _gate().SENTINELS


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
