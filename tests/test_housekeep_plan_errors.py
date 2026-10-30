"""Plan failures keep candidates and do not stop unrelated synthetic work."""
import pytest

from test_housekeep import cfg_for, hk, key, planned, write
from test_housekeep_derived import stage_tree, historical_staging


def test_events_at_a_task_root_cannot_borrow_an_etl_from_the_shared_root(tmp_path):
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/events.xml", b"decoded")
    source = write(tmp_path / "_CLAUDE_OUTPUT/unrelated.etl", b"original measurement")
    doc = hk.plan(cfg_for(tmp_path), only={"derived"})
    assert not planned(doc)
    assert sum(doc["rows"].values()) == trace.stat().st_size + source.stat().st_size


def test_apply_refuses_an_old_derived_plan_that_borrows_a_shared_root_etl(tmp_path):
    import time
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/events.xml", b"decoded")
    source = write(tmp_path / "_CLAUDE_OUTPUT/unrelated.etl", b"measurement")
    cfg = cfg_for(tmp_path)
    action = dict(type="synthetic", path=str(trace), task=str(trace.parent), source=str(source),
                  hash=hk.blake(trace), bytes=trace.stat().st_size)
    action["class"] = "derived"
    assert hk.check(action, cfg, hk.Guard(cfg), time.time())


def test_unreadable_trace_provenance_keeps_it_and_plans_other_tasks(tmp_path, monkeypatch):
    blocked = write(tmp_path / "_CLAUDE_OUTPUT/blocked/events.xml", b"blocked")
    write(tmp_path / "_CLAUDE_OUTPUT/blocked/source.etl", b"source")
    good = write(tmp_path / "_CLAUDE_OUTPUT/good/events.xml", b"decoded")
    write(tmp_path / "_CLAUDE_OUTPUT/good/source.etl", b"source")
    original = hk.derived_source

    def unreadable(path, *args):
        if path == blocked:
            raise PermissionError("synthetic provenance read failure")
        return original(path, *args)

    monkeypatch.setattr(hk, "derived_source", unreadable)
    doc = hk.plan(cfg_for(tmp_path), only={"derived"})
    assert planned(doc) == {key(good)}
    assert any("unavailable" in row for row in doc["rows"])
    assert blocked.exists()
    assert sum(doc["rows"].values()) == sum(p.stat().st_size for p in (tmp_path / "_CLAUDE_OUTPUT").rglob("*")
                                            if p.is_file())


@pytest.mark.parametrize("special", [False, True])
def test_unreadable_staging_size_keeps_it_and_continues(tmp_path, monkeypatch, special):
    old, new, cfg = stage_tree(tmp_path)
    blocked = old
    if special:
        blocked = write(tmp_path / "staging/_superseded/notes.txt", b"not a list").parent
    original = hk.dir_size

    def unreadable(path):
        if path == blocked:
            raise PermissionError("synthetic staging size read failure")
        return original(path)

    monkeypatch.setattr(hk, "dir_size", unreadable)
    doc = hk.plan(cfg, only={"superseded"})
    assert key(blocked) not in planned(doc)
    assert str(blocked) in doc["unreadable_paths"]
    assert any("unknown bytes" in row for row in doc["rows"])
