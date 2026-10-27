"""Task 7 P5/HK1: synthetic inaccessible trees; no actual recycle or permission changes."""
import os
import time
from pathlib import Path

import pytest
from test_housekeep import hk, cfg_for, write, age, planned, key, DAY


def aged_tree(root):
    for p in [*root.rglob("*"), root]:
        os.utime(p, (time.time() - 60 * DAY,) * 2)


@pytest.mark.parametrize("failure", ["walk", "stat"])
def test_recycle_refuses_an_uninspectable_descendant(tmp_path, monkeypatch, failure):
    target = tmp_path / "scratchbed"
    child = write(target / "subfolder" / ("AUDIT_LOG.txt" if failure == "walk" else "ordinary.txt"), b"synthetic evidence")
    cfg = cfg_for(tmp_path, scratch=[str(target)])
    assert bool(hk.recycle_refusal(cfg, target)) is (failure == "walk")
    if failure == "walk":
        original = os.scandir
        def denied(path):
            if Path(path) == child.parent:
                raise PermissionError("synthetic inaccessible descendant")
            return original(path)
        monkeypatch.setattr(os, "scandir", denied)
    else:
        original = os.stat
        def denied(path, *args, **kwargs):
            if Path(path) == child:
                raise PermissionError("synthetic unreadable metadata")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(os, "stat", denied)
    assert hk.recycle_refusal(cfg, target)
    called = []
    monkeypatch.setattr(hk, "recycle", lambda items: called.extend(items) or {})
    assert hk._run("recycle", [str(target)], cfg) == 1
    assert called == []


@pytest.mark.parametrize("failure", ["root-file", "walk-root"])
def test_strict_recycle_inspection_checks_root_metadata(tmp_path, monkeypatch, failure):
    target = write(tmp_path / "scratchbed/ordinary.txt", b"synthetic bytes")
    if failure == "walk-root":
        target = target.parent
    cfg = cfg_for(tmp_path, scratch=[str(target)])
    assert hk.recycle_refusal(cfg, target) == ""
    inspected = hk._real(target)
    original, path_stat, info, calls = os.stat, Path.stat, inspected.stat(), []
    def denied(path, *args, **kwargs):
        if Path(path) == inspected:
            calls.append(path)
            raise FileNotFoundError("synthetic metadata read race")
        return original(path, *args, **kwargs)
    def initial_stat(path, *args, **kwargs):
        if path == inspected:
            if failure == "root-file":
                raise PermissionError("synthetic root metadata failure")
            return info
        return path_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", initial_stat)
    monkeypatch.setattr(os, "stat", denied)
    assert hk.recycle_refusal(cfg, target), calls


@pytest.mark.parametrize("failure", ["walk", "stat", "empty-child", "root"])
@pytest.mark.parametrize("phase", ["plan", "apply"])
def test_uncertain_or_recent_directory_activity_keeps_derived_trace(tmp_path, monkeypatch, failure, phase):
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/events.xml", b"decoded")
    write(trace.parent / "source.etl", b"synthetic source")
    child = write(trace.parent / "active-child/activity.txt", b"new evidence")
    aged_tree(trace.parent)
    cfg = cfg_for(tmp_path)
    doc = hk.plan(cfg, only={"derived"})
    assert planned(doc) == {key(trace)}
    if failure == "walk":
        os.utime(child, None)
        original = os.scandir
        def denied(path):
            if Path(path) == child.parent:
                raise PermissionError("synthetic unreadable activity")
            return original(path)
        monkeypatch.setattr(os, "scandir", denied)
    elif failure == "stat":
        original = os.stat
        def denied(path, *args, **kwargs):
            if Path(path) == child:
                raise PermissionError("synthetic unreadable activity metadata")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(os, "stat", denied)
    elif failure == "empty-child":
        empty = trace.parent / "recent-empty"
        empty.mkdir()
        os.utime(trace.parent, (time.time() - 60 * DAY,) * 2)
    else:
        os.utime(trace.parent, None)
    if phase == "plan":
        assert planned(hk.plan(cfg, only={"derived"})) == set()
    else:
        called = []
        assert hk.apply(doc, cfg, recycler=lambda items: called.extend(items) or {}) == 1
        assert called == [] and trace.exists()
