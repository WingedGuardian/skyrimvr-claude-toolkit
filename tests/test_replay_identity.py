"""Resume qualification uses real hooks in an owned tiny repository, never user history or installs."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO


@pytest.fixture
def replay_fixture(tmp_path):
    root = tmp_path / "source"
    scripts = root / "scripts/guard-corpus"
    scripts.mkdir(parents=True)
    for name in ("record.py", "replay.py"):
        shutil.copy2(REPO / "scripts/guard-corpus" / name, scripts / name)
    (root / "tests/guard_corpus").mkdir(parents=True)
    (root / "tests/guard_corpus/game-root.txt").write_text("C:/Synthetic/ReplayFixture\n", encoding="utf-8")
    (root / "tools/skyguard").mkdir(parents=True)
    (root / "tools/modding_harness").mkdir()
    runtime = root / "tools/modding_harness/__init__.py"
    runtime.write_text('DECISION = "allow"\n', encoding="utf-8")
    contacts = tmp_path / "contacts.txt"
    helper = root / "tools/skyguard/owned.py"
    helper.write_text('from pathlib import Path\nimport sys,json\n'
        'sys.path.insert(0,str(Path(__file__).resolve().parent.parent))\n'
        'from modding_harness import DECISION\n'
        'with Path(' + repr(str(contacts)) + ').open("a") as f: f.write("contact\\n")\n'
        'print(json.dumps({"hookSpecificOutput":{"permissionDecision":DECISION,"permissionDecisionReason":"owned"}}))\n',
        encoding="utf-8")
    old, new = root / "old", root / "new"
    old.mkdir(); new.mkdir()
    allowed = json.dumps({"hookSpecificOutput": {"permissionDecision": "allow", "permissionDecisionReason": "owned"}})
    (old / "protect-bash.sh").write_text("#!/bin/bash\nprintf '%s\\n' '" + allowed + "'\n", encoding="utf-8", newline="\n")
    (new / "protect-bash.sh").write_text(
        '#!/bin/bash\n"{{PYTHON_PATH}}" -B "$CLAUDE_PROJECT_DIR/tools/skyguard/owned.py"\n', encoding="utf-8", newline="\n")
    history = tmp_path / "history"
    history.mkdir()
    (history / "calls.jsonl").write_text("".join(json.dumps({"message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "owned input " + str(index)}}]}}) + "\n"
        for index in range(2)), encoding="utf-8")
    def run(out=None, limit=None):
        command = [sys.executable, "-B", str(scripts / "replay.py"), "--old", str(old), "--new", str(new),
                   "--history", str(history), "--out-dir", str(out or tmp_path / "output"), "--jobs", "1"]
        if limit is not None:
            command += ["--limit", str(limit)]
        return subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=40)
    return {"root": root, "scripts": scripts, "old": old, "new": new, "runtime": runtime, "helper": helper,
            "contacts": contacts, "out": tmp_path / "output", "run": run}


def test_unchanged_resume_skips_only_completed_bound_rows_and_can_extend_limit(replay_fixture):
    f = replay_fixture
    first = f["run"](limit=1)
    assert first.returncode == 0, first.stderr
    original = f["contacts"].read_bytes()
    second = f["run"](limit=1)
    assert second.returncode == 0 and "0 to run" in second.stdout, second.stderr
    assert f["contacts"].read_bytes() == original
    third = f["run"](limit=2)
    assert third.returncode == 0 and "1 to run" in third.stdout, third.stderr
    expected_line = b"contact\r\n" if sys.platform == "win32" else b"contact\n"
    assert f["contacts"].read_bytes() == original + expected_line


@pytest.mark.parametrize("changed", ["old", "new", "runtime", "helper", "record", "driver", "items", "game-root"])
def test_changed_inputs_refuse_cached_rows_without_overwriting_evidence(replay_fixture, changed):
    f = replay_fixture
    first = f["run"]()
    assert first.returncode == 0, first.stderr
    before = (f["out"] / "results.jsonl").read_bytes()
    contacts = f["contacts"].read_bytes()
    paths = {"old": f["old"] / "protect-bash.sh", "new": f["new"] / "protect-bash.sh", "runtime": f["runtime"],
             "helper": f["helper"], "record": f["scripts"] / "record.py", "driver": f["scripts"] / "replay.py",
             "game-root": f["root"] / "tests/guard_corpus/game-root.txt", "items": f["out"] / "items.json"}
    path = paths[changed]
    if changed == "items":
        snap = json.loads(path.read_text(encoding="utf-8"))
        snap["items"][0]["payload"]["tool_input"]["command"] = "changed owned input"
        path.write_text(json.dumps(snap), encoding="utf-8")
    elif changed == "game-root":
        path.write_text("C:/Synthetic/ChangedFixture\n", encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n# changed instrument\n")
    result = f["run"]()
    assert result.returncode == 2 and "fresh --out-dir" in result.stderr, result.stdout + result.stderr
    assert (f["out"] / "results.jsonl").read_bytes() == before
    assert f["contacts"].read_bytes() == contacts


@pytest.mark.parametrize("problem", ["missing", "invalid", "boolean-version", "missing-items", "unbound-row"])
def test_unqualified_cache_is_preserved_and_refused(replay_fixture, problem):
    f = replay_fixture
    assert f["run"]().returncode == 0
    identity = f["out"] / "replay.identity.json"
    if problem == "missing":
        identity.unlink()
    elif problem == "invalid":
        identity.write_text("invalid", encoding="utf-8")
    elif problem == "boolean-version":
        record = json.loads(identity.read_text(encoding="utf-8")); record["version"] = True
        identity.write_text(json.dumps(record), encoding="utf-8")
    elif problem == "missing-items":
        (f["out"] / "items.json").unlink()
    else:
        path = f["out"] / "results.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0].pop("epoch")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    before = (f["out"] / "results.jsonl").read_bytes()
    result = f["run"]()
    assert result.returncode == 2 and "REFUSING" in result.stderr, result.stdout + result.stderr
    assert (f["out"] / "results.jsonl").read_bytes() == before


def test_identical_engine_errors_are_nonanswers_and_are_retried(replay_fixture):
    f = replay_fixture
    for folder in (f["old"], f["new"]):
        (folder / "protect-bash.sh").write_text("#!/bin/bash\nexit 7\n", encoding="utf-8", newline="\n")
    first, second = f["run"](), f["run"]()
    assert first.returncode == second.returncode == 2
    assert "2 to run" in second.stdout
    assert json.loads((f["out"] / "summary.json").read_text(encoding="utf-8"))["errors"] == 2


def test_environment_binding_keeps_secret_values_out_of_the_identity(replay_fixture, monkeypatch):
    f = replay_fixture
    secret = "FAKE-private-replay-token-abcdef12345"
    monkeypatch.setenv("REPLAY_FAKE_TOKEN", secret)
    assert f["run"]().returncode == 0
    assert secret not in (f["out"] / "replay.identity.json").read_text(encoding="utf-8")
    monkeypatch.setenv("REPLAY_FAKE_TOKEN", secret + "changed")
    assert f["run"]().returncode == 2


@pytest.mark.parametrize("change", ["add", "remove"])
def test_empty_runtime_directory_changes_require_new_evidence(replay_fixture, change):
    f = replay_fixture
    path = f["root"] / "tools/skyguard/decision-flag"
    helper = f["helper"]
    helper.write_text(helper.read_text(encoding="utf-8").replace('from modding_harness import DECISION\n',
        'from modding_harness import DECISION\n'
        'if (Path(__file__).parent / "decision-flag").is_dir(): DECISION = "deny"\n'), encoding="utf-8")
    if change == "remove":
        path.mkdir()
    first = f["run"]()
    assert first.returncode == (1 if change == "remove" else 0), first.stderr
    before = (f["out"] / "results.jsonl").read_bytes()
    if change == "add":
        path.mkdir()
    else:
        path.rmdir()
    resumed = f["run"]()
    assert resumed.returncode == 2 and "fresh --out-dir" in resumed.stderr, resumed.stdout + resumed.stderr
    assert (f["out"] / "results.jsonl").read_bytes() == before
    fresh = f["run"](out=f["out"].parent / "fresh-evidence")
    assert fresh.returncode == (1 if change == "add" else 0), fresh.stdout + fresh.stderr


def _directory_alias(link, target):
    # Both endpoints belong to this test's private fixture. Junctions avoid requesting symlink privileges.
    if os.name == "nt":
        powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:REPLAY_LINK "
            "-Target $env:REPLAY_TARGET | Out-Null"],
            env=dict(os.environ, REPLAY_LINK=str(link), REPLAY_TARGET=str(target)), capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    else:
        link.symlink_to(target, target_is_directory=True)


def test_linked_runtime_directory_contents_are_bound_and_cycles_refuse(replay_fixture):
    f = replay_fixture
    target = f["out"].parent / "owned-linked-target"
    target.mkdir()
    source = target / "data.txt"
    source.write_text("before", encoding="utf-8")
    alias = f["root"] / "tools/skyguard/linked-data"
    _directory_alias(alias, target)
    assert f["run"]().returncode == 0
    source.write_text("changed", encoding="utf-8")
    assert f["run"]().returncode == 2
    # A second alias creates a true ancestor cycle. Refuse it before copytree can recurse.
    _directory_alias(target / "cycle", f["root"] / "tools/skyguard")
    fresh = f["out"].parent / "cycle-output"
    result = f["run"](out=fresh)
    assert result.returncode == 2 and "bind replay inputs" in result.stderr, result.stdout + result.stderr
    assert not (fresh / "results.jsonl").exists()


def test_input_changes_during_replay_permanently_invalidate_that_cache(replay_fixture):
    f = replay_fixture
    with f["helper"].open("a", encoding="utf-8") as stream:
        stream.write("Path(" + repr(str(f["runtime"])) + ").write_text(" + repr('DECISION = "deny"\n') + ")\n")
    result = f["run"]()
    assert result.returncode == 2 and "changed during replay" in result.stderr, result.stdout + result.stderr
    assert (f["out"] / "replay.invalid").is_file()
    assert not (f["out"] / "summary.json").exists()
    assert f["run"]().returncode == 2
