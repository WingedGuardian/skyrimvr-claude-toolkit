"""Held inventory is explicit; independent tasks remain eligible for inspection."""
import os
import subprocess

import pytest

from test_housekeep import hk, cfg_for, write


def config(root):
    return cfg_for(root, archive_enabled=False, live_roots=[])


@pytest.mark.parametrize("protection", ["tracked", "linked"])
def test_external_repository_output_keeps_its_own_protected_evidence(tmp_path, protection):
    root = tmp_path / "toolkit"
    root.mkdir()
    external = tmp_path / "external"
    first = write(external / "reports/task-a/input file.bin", b"identical")
    second = write(external / "reports/task-b/input file.bin", b"identical")
    subprocess.run(["git", "init", "-q", str(external)], check=True, capture_output=True)
    doc = external / "CLAUDE.md"
    doc.write_text("[evidence](reports/task-a/input file.bin)")
    if protection == "tracked":
        subprocess.run(["git", "-C", str(external), "add", "--", "reports/task-a/input file.bin"],
                       check=True, capture_output=True)
    cfg = config(root)
    cfg.update(agent_output=[str(external / "reports")], evidence_docs=[str(doc)] if protection == "linked" else [],
               min_bytes=1, active_hours=0)
    guard = hk.Guard(cfg)
    assert guard.why_protected(first) == ("git-tracked" if protection == "tracked" else "linked evidence")
    result = hk.plan(cfg, guard)
    assert not result["actions"]
    assert first.read_bytes() == second.read_bytes() == b"identical"


def test_failed_task_is_held_whole_while_independent_task_is_measured(tmp_path, monkeypatch):
    write(tmp_path / "_CLAUDE_OUTPUT/held/unique.bin", b"unique")
    write(tmp_path / "_CLAUDE_OUTPUT/healthy/keep.bin", b"kept")
    original = hk.newest_activity
    def inspect(path):
        if path.name == "held":
            raise OSError("unreadable cleanup target")
        return original(path)
    monkeypatch.setattr(hk, "newest_activity", inspect)
    doc = hk.plan(config(tmp_path))
    assert doc["coverage"]["state"] == "accounted_with_holds"
    assert doc["coverage"]["targets_inspected"] == 1
    assert doc["coverage"]["targets_held"] == 1
    assert doc["coverage"]["held"][0]["path"] == str(tmp_path / "_CLAUDE_OUTPUT/held")
    assert doc["coverage"]["held"][0]["bytes"] is None
    assert sum(doc["rows"].values()) == 4
    assert doc["actions"] == []


def test_missing_output_root_is_named_instead_of_reported_as_scanned(tmp_path):
    doc = hk.plan(config(tmp_path))
    assert doc["coverage"]["targets_inspected"] == 0
    assert doc["coverage"]["targets_held"] == 1
    assert doc["coverage"]["held"][0]["path"] == str(tmp_path / "_CLAUDE_OUTPUT")
    assert doc["rows"] == {} and doc["actions"] == []


def test_class_limited_coverage_discloses_unrequested_output(tmp_path, capsys):
    doc = hk.plan(config(tmp_path), only={"snapshot"})
    assert doc["coverage"]["classes_requested"] == ["snapshot"]
    assert not doc["coverage"]["agent_output_requested"]
    hk.print_coverage(doc)
    assert "class-limited plan" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="real Windows junction contract")
def test_junction_holds_entire_task_without_visiting_target(tmp_path):
    write(tmp_path / "_CLAUDE_OUTPUT/held/unique.bin", b"unique")
    outside = write(tmp_path / "outside/keep.bin", b"external")
    write(tmp_path / "_CLAUDE_OUTPUT/healthy/keep.bin", b"kept")
    junction = tmp_path / "_CLAUDE_OUTPUT/held/node_modules"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside.parent)],
                   check=True, capture_output=True)
    doc = hk.plan(config(tmp_path))
    assert doc["coverage"]["targets_inspected"] == 1
    assert doc["coverage"]["targets_held"] == 1
    assert str(junction) in doc["coverage"]["held"][0]["reason"]
    assert sum(doc["rows"].values()) == 4
    assert doc["actions"] == []
    assert outside.read_bytes() == b"external" and junction.is_dir()


def test_daily_review_names_holds_and_preserves_complete_growth_baseline(tmp_path):
    daily = hk._daily_module()
    cfg = config(tmp_path)
    baseline = daily.f(cfg, "SIZES")
    baseline.parent.mkdir(parents=True)
    baseline.write_text('{"total": 999, "date": "previous complete scan"}')
    doc = {"actions": [], "agent_total": 4, "coverage": {"held": [
        {"path": "held-task", "reason": "unreadable", "bytes": None}]}}
    daily.write_review(cfg, doc, "fixture-code-hash")
    daily.write_alerts(doc, cfg)
    assert '"total": 999' in baseline.read_text()
    assert "held-task (unreadable)" in daily.f(cfg, "REVIEW").read_text()
    assert "sizes are unknown and excluded" in daily.f(cfg, "ALERTS").read_text()
