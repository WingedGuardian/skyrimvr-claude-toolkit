"""Offline failure injection: never starts Codex or reads its trust configuration."""
import importlib.util
import json
import subprocess
import sys

import pytest

from conftest import REPO


@pytest.fixture
def e2e(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("e2e_codex_verdict", REPO / "scripts/e2e-codex.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "install"
    (root / "Data/SKSE/Plugins").mkdir(parents=True)
    (root / "Data/SKSE/Plugins/probe.ini").write_text("value=1", encoding="utf-8")
    (root / "Data/Existing.esp").write_bytes(b"fixture")
    (root / ".codex").mkdir()
    (root / ".codex/hooks.json").write_text('{"timeout": 60}', encoding="utf-8")

    def probe(copy, pid, prompt, **kwargs):
        return {"id": pid, "pass": True, "checks": {"initial check": True},
                "output_tail": "hook: unexpected; registry deletes are refused"}

    monkeypatch.setattr(module, "probe", probe)
    monkeypatch.setattr(module, "canary", lambda copy: subprocess.CompletedProcess([], 0, "", ""))
    return module, root


@pytest.mark.parametrize("phase,pid", [("phase_a", "A8-nogit"), ("phase_b", "B2"),
                                       ("phase_c", "C2-modified")])
def test_checks_added_after_probe_determine_the_phase_verdict(e2e, phase, pid):
    module, root = e2e
    row = next(row for row in getattr(module, phase)(root) if row["id"] == pid)
    assert any(not value for value in row["checks"].values()), "failure injection did not reach the late checks"
    assert row["pass"] is False, row


def test_main_uses_complete_checks_for_output_saved_results_and_exit(e2e, monkeypatch, capsys):
    module, root = e2e
    monkeypatch.setattr(sys, "argv", ["e2e-codex.py", "--phase", "B", str(root)])
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "B2 FAIL" in output and "B2 PASS" not in output
    saved = json.loads((root.parent / "e2e-results.json").read_text())
    assert next(row for rows in saved.values() for row in rows if row["id"] == "B2")["pass"] is False


def test_make_refuses_failed_setup_before_git_initialization(e2e, tmp_path, monkeypatch):
    module, _ = e2e
    calls = []
    monkeypatch.setattr(module.shutil, "copytree", lambda src, dest, **kw: dest.mkdir())
    monkeypatch.setattr(module, "bash", lambda: "fake-bash-never-launched")

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "injected setup failure", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    dest = tmp_path / "make"
    dest.mkdir()
    monkeypatch.setattr(sys, "argv", ["e2e-codex.py", "--make", str(dest), "--git"])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 1
    assert len(calls) == 1, calls
    assert calls[0] == ["fake-bash-never-launched", "setup.sh", "--agent", "codex"]
    assert "injected setup failure" in (dest / "setup.log").read_text()
