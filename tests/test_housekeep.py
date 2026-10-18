"""tools/housekeep.py: plan, then apply exactly the plan -- and never touch what the policy says is Forever.

Every guard here has a twin: the same file, minus the one property the guard keys on, IS planned. Without the twin a
guard that never plans anything at all would pass. The Recycle Bin is faked (a move into tmp_path/bin); the real
PowerShell helper is exercised once by hand, never per test run, so the suite does not fill the user's bin.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time

import pytest

from conftest import REPO

TOOL = REPO / "tools" / "housekeep.py"
_spec = importlib.util.spec_from_file_location("housekeep", TOOL)
hk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hk)

MB = 1024 * 1024
DAY = 86400


def age(path, days):
    t = time.time() - days * DAY
    targets = [path] if path.is_file() else [p for p in path.rglob("*")]
    for p in targets:
        os.utime(p, (t, t))


def write(path, data, days=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    age(path, days)
    return path


def blob(seed, size=2 * MB):
    return (seed.encode() * (size // len(seed) + 1))[:size]


def cfg_for(root, **over):
    base = {"agent_output": ["_CLAUDE_OUTPUT"], "live_roots": ["Data"], "launch_logs": ["launches"],
            "legacy_snapshots": ["snaps"], "evidence_docs": ["CLAUDE.md"], "active_hours": 48}
    base.update(over)
    (root / "hk.json").write_text(json.dumps(base), encoding="utf-8")
    return hk.load_config(root / "hk.json") | {"root": root}


class FakeBin:
    def __init__(self, root):
        self.bin = root / "bin"
        self.bin.mkdir(exist_ok=True)
        self.got = []

    def __call__(self, paths):
        out = {}
        for i, p in enumerate(paths):
            self.got.append(p)
            os.replace(p, self.bin / "{}-{}".format(len(self.got), os.path.basename(p)))
            out[p] = None
        return out


def planned(doc, cls=None):
    return {os.path.normcase(a["path"]) for a in doc["actions"] if cls is None or a["class"] == cls}


def key(p):
    return os.path.normcase(str(p))


@pytest.fixture
def tree(tmp_path):
    write(tmp_path / "Data" / "Skyrim.esm", blob("master"))
    (tmp_path / "CLAUDE.md").write_text("", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- parity with the guard

def test_save_extensions_match_the_guard():
    import ast
    tree = ast.parse((REPO / "tools" / "skyguard" / "writes.py").read_text(encoding="utf-8"))
    values = [ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "SAVE_EXTS" for t in n.targets)]
    assert values == [hk.SAVE_EXTS], "housekeep.SAVE_EXTS drifted from skyguard/writes.py"


def test_a_root_that_sits_under_a_backups_folder_is_not_all_protected(tmp_path):
    root = tmp_path / "old-backups" / "install"
    write(root / "Data" / "Skyrim.esm", blob("master"))
    (root / "CLAUDE.md").write_text("", encoding="utf-8")
    f = write(root / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    assert key(f) in planned(hk.plan(cfg_for(root)), "recreatable"), \
        "the backup-folder rule must judge folders below the root, not the root's own ancestors"


# --------------------------------------------------------------------------- recreatable

def test_a_byte_identical_copy_of_a_live_file_is_recreatable_and_a_one_byte_variant_is_not(tree):
    same = write(tree / "_CLAUDE_OUTPUT" / "t1" / "fixture" / "Skyrim.esm", blob("master"))
    variant = bytearray(blob("master"))
    variant[-1] ^= 1
    off = write(tree / "_CLAUDE_OUTPUT" / "t2" / "fixture" / "Skyrim.esm", bytes(variant))
    doc = hk.plan(cfg_for(tree))
    assert key(same) in planned(doc, "recreatable")
    assert key(off) not in planned(doc, "recreatable")


def test_a_snapshot_of_a_live_database_is_not_recreatable_but_still_deduplicated(tree):
    """A live DB equals its snapshot only until the next write; after that the snapshot is the only record."""
    write(tree / "Data" / "SKSE" / "mem.db", blob("memory"))
    a = write(tree / "_CLAUDE_OUTPUT" / "t1" / "before" / "mem.db", blob("memory"))
    b = write(tree / "_CLAUDE_OUTPUT" / "t2" / "before" / "mem.db", blob("memory"))
    master = write(tree / "_CLAUDE_OUTPUT" / "t3" / "fixture" / "Skyrim.esm", blob("master"))
    doc = hk.plan(cfg_for(tree))
    assert not {key(a), key(b)} & planned(doc, "recreatable")
    assert planned(doc, "link") == {key(b)}, "the snapshots are still stored once"
    assert key(master) in planned(doc, "recreatable"), "twin: a content file is still recreatable"


# --------------------------------------------------------------------------- the Forever class, each with a twin

@pytest.mark.parametrize("rel", ["t/after/Saves/x.ess", "t/after/Saves/x.skse", "t/permanent-backups/Skyrim.esm"])
def test_saves_and_backup_folders_are_never_planned(tree, rel):
    f = write(tree / "_CLAUDE_OUTPUT" / rel, blob("master"))
    twin = write(tree / "_CLAUDE_OUTPUT" / "t" / "plain" / "Skyrim.esm", blob("master"))
    doc = hk.plan(cfg_for(tree))
    assert key(f) not in planned(doc)
    assert key(twin) in planned(doc), "twin: the same bytes outside the protected shape must be planned"


def test_a_file_a_project_doc_links_to_is_never_planned(tree):
    linked = write(tree / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    twin = write(tree / "_CLAUDE_OUTPUT" / "u" / "fixture" / "Skyrim.esm", blob("master"))
    (tree / "CLAUDE.md").write_text("[evidence](_CLAUDE_OUTPUT/t/fixture/Skyrim.esm).", encoding="utf-8")
    doc = hk.plan(cfg_for(tree))
    assert key(linked) not in planned(doc)
    assert key(twin) in planned(doc)


def test_a_git_tracked_file_is_never_planned(tree):
    tracked = write(tree / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    twin = write(tree / "_CLAUDE_OUTPUT" / "u" / "fixture" / "Skyrim.esm", blob("master"))
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run(["git", "-C", str(tree), "add", "-f", "_CLAUDE_OUTPUT/t/fixture/Skyrim.esm"], check=True)
    doc = hk.plan(cfg_for(tree))
    assert key(tracked) not in planned(doc)
    assert key(twin) in planned(doc)


def test_an_active_task_folder_is_skipped_whole(tree):
    busy = write(tree / "_CLAUDE_OUTPUT" / "busy" / "fixture" / "Skyrim.esm", blob("master"))
    write(tree / "_CLAUDE_OUTPUT" / "busy" / "notes.md", b"still working", days=0)
    twin = write(tree / "_CLAUDE_OUTPUT" / "idle" / "fixture" / "Skyrim.esm", blob("master"))
    doc = hk.plan(cfg_for(tree))
    assert key(busy) not in planned(doc)
    assert key(twin) in planned(doc)


def test_nested_task_roots_judge_activity_per_child(tree):
    old = write(tree / "_CLAUDE_OUTPUT" / "guard" / "old" / "Skyrim.esm", blob("master"))
    write(tree / "_CLAUDE_OUTPUT" / "guard" / "new" / "note.md", b"x", days=0)
    flat = hk.plan(cfg_for(tree))
    nested = hk.plan(cfg_for(tree, nested_task_roots=["_CLAUDE_OUTPUT/guard"]))
    assert key(old) not in planned(flat), "one busy sibling makes the whole flat task folder active"
    assert key(old) in planned(nested)


def test_nested_task_roots_recurse(tree):
    deep = write(tree / "_CLAUDE_OUTPUT" / "og" / "guardian" / "task-a" / "Skyrim.esm", blob("master"))
    write(tree / "_CLAUDE_OUTPUT" / "og" / "guardian" / "task-b" / "note.md", b"x", days=0)
    doc = hk.plan(cfg_for(tree, nested_task_roots=["_CLAUDE_OUTPUT/og", "_CLAUDE_OUTPUT/og/guardian"]))
    assert key(deep) in planned(doc), "a task two levels down is judged on its own activity"


def test_a_loose_file_in_a_root_is_never_planned(tree):
    """Shared live state sits loose in a root: a service's append-only events.jsonl. Linking it read-only would
    break its next write, and one appended weekly is idle by the activity gate's measure."""
    loose = write(tree / "_CLAUDE_OUTPUT" / "og" / "events.jsonl", blob("events"))
    write(tree / "_CLAUDE_OUTPUT" / "og" / "t1" / "events.jsonl", blob("events"))
    twin = write(tree / "_CLAUDE_OUTPUT" / "og" / "t2" / "events.jsonl", blob("events"))
    cfg = cfg_for(tree, nested_task_roots=["_CLAUDE_OUTPUT/og"])
    doc = hk.plan(cfg)
    assert key(loose) not in planned(doc)
    assert key(loose) not in {key(a.get("canonical", "")) for a in doc["actions"]}, \
        "a loose file must not be a canonical copy either: apply marks the canonical read-only"
    assert key(twin) in planned(doc, "link"), "twin: the same bytes inside a task folder are a duplicate"
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 0
    assert os.access(loose, os.W_OK), "the live log must still be writable after apply"


# --------------------------------------------------------------------------- build output

def test_build_intermediates_are_planned_and_dll_pdb_are_kept(tree):
    obj = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.lib", b"lib")
    dll = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.dll", b"dll")
    pdb = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.pdb", b"pdb")
    loose = write(tree / "_CLAUDE_OUTPUT" / "t" / "notes" / "x.lib", b"lib outside a build dir")
    doc = hk.plan(cfg_for(tree))
    assert planned(doc, "build") == {key(obj)}
    assert not {key(dll), key(pdb), key(loose)} & planned(doc)


# --------------------------------------------------------------------------- links

def test_duplicates_become_one_read_only_copy_with_every_path_intact(tree):
    a = write(tree / "_CLAUDE_OUTPUT" / "a" / "SkyrimNet.log", blob("log"))
    b = write(tree / "_CLAUDE_OUTPUT" / "b" / "SkyrimNet.log", blob("log"))
    c = write(tree / "_CLAUDE_OUTPUT" / "c" / "SkyrimNet.log", blob("log"))
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    assert planned(doc, "link") == {key(b), key(c)}
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 0
    assert a.read_bytes() == b.read_bytes() == c.read_bytes() == blob("log")
    assert os.stat(a).st_ino == os.stat(b).st_ino == os.stat(c).st_ino
    assert not os.access(b, os.W_OK)
    again = hk.plan(cfg)
    assert not planned(again, "link"), "linked files must not be planned a second time"


# --------------------------------------------------------------------------- apply re-proves the plan

def test_apply_refuses_a_file_changed_since_the_plan(tree):
    f = write(tree / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    twin = write(tree / "_CLAUDE_OUTPUT" / "u" / "fixture" / "Skyrim.esm", blob("master"))
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    data = bytearray(f.read_bytes())
    data[0] ^= 1
    f.write_bytes(bytes(data))
    age(f, 10)
    fake = FakeBin(tree)
    assert hk.apply(doc, cfg, recycler=fake) == 1
    assert f.exists()
    assert not twin.exists() and key(twin) in {key(p) for p in fake.got}


def test_apply_refuses_when_the_live_file_changed(tree):
    f = write(tree / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    write(tree / "Data" / "Skyrim.esm", blob("updated"))
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 1
    assert f.exists(), "a fixture that no longer matches the live file is the only copy of those bytes"


def test_a_recycle_failure_is_reported_and_the_file_stays(tree):
    f = write(tree / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    cfg = cfg_for(tree)
    assert hk.apply(hk.plan(cfg), cfg, recycler=lambda ps: {p: "bin refused" for p in ps}) == 1
    assert f.exists()


# --------------------------------------------------------------------------- launches

def launch(root, name, days, size=200_000):
    d = root / "launches" / name
    write(d / "SkyrimNet.log", blob(name, size), days=days)
    return d


def test_launch_logs_gzip_after_a_week_recycle_after_ninety_days_and_labelled_folders_stay(tree):
    def stamp(days):
        return time.strftime("launch-%Y%m%d-%H%M%S", time.localtime(time.time() - days * DAY))

    fresh, week, old = launch(tree, stamp(2), 2), launch(tree, stamp(10), 10), launch(tree, stamp(100), 100)
    labelled = launch(tree, "BORKED-ocu-2026-07-02", 400)
    cfg = cfg_for(tree)
    doc = hk.plan(cfg)
    assert planned(doc, "gzip") == {key(week / "SkyrimNet.log")}
    assert planned(doc, "launch") == {key(old)}
    assert not {key(fresh), key(labelled), key(labelled / "SkyrimNet.log")} & planned(doc)
    original = (week / "SkyrimNet.log").read_bytes()
    assert hk.apply(doc, cfg, recycler=FakeBin(tree)) == 0
    import gzip
    assert gzip.decompress((week / "SkyrimNet.log.gz").read_bytes()) == original
    assert not (week / "SkyrimNet.log").exists() and not old.exists() and labelled.exists()


def test_a_gzip_that_does_not_round_trip_keeps_the_original(tree, monkeypatch):
    f = write(tree / "launches" / "x" / "SkyrimNet.log", blob("log", 100_000))
    action = {"class": "gzip", "path": str(f), "bytes": f.stat().st_size, "hash": "0" * 32}
    with pytest.raises(OSError, match="round trip"):
        hk.compress(action)
    assert f.exists() and not (f.parent / "SkyrimNet.log.gz").exists()


# --------------------------------------------------------------------------- legacy snapshots

def test_a_snapshot_repeating_the_previous_one_is_planned_and_distinct_and_newest_ones_stay(tree):
    def snap(name, content):
        write(tree / "snaps" / name / "source" / "a.psc", content)
        return tree / "snaps" / name
    s1, s2, s3, s4, s5 = (snap("20261001_000001", b"v1"), snap("20261001_000002", b"v1"),
                          snap("20261001_000003", b"v2"), snap("20261001_000004", b"v2"),
                          snap("20261001_000005", b"v2"))
    doc = hk.plan(cfg_for(tree))
    assert planned(doc, "snapshot") == {key(s2), key(s4)}, "s1/s3 carry distinct content, s5 is the newest"
    assert not {key(s1), key(s3), key(s5)} & planned(doc)


# --------------------------------------------------------------------------- accounting

def test_report_rows_sum_to_every_byte_scanned(tree):
    write(tree / "_CLAUDE_OUTPUT" / "a" / "Skyrim.esm", blob("master"))
    write(tree / "_CLAUDE_OUTPUT" / "a" / "x.ess", b"save")
    write(tree / "_CLAUDE_OUTPUT" / "b" / "small.txt", b"tiny")
    write(tree / "_CLAUDE_OUTPUT" / "c" / "d1.log", blob("dup"))
    write(tree / "_CLAUDE_OUTPUT" / "d" / "d2.log", blob("dup"))
    write(tree / "_CLAUDE_OUTPUT" / "busy" / "n.md", b"x", days=0)
    launch(tree, "launch-20200101-000000", 2000)
    launch(tree, "GOODREF", 2000)
    write(tree / "snaps" / "20261001_000001" / "a.psc", b"v")
    scanned = sum(p.stat().st_size for d in ("_CLAUDE_OUTPUT", "launches", "snaps")
                  for p in (tree / d).rglob("*") if p.is_file())
    doc = hk.plan(cfg_for(tree))
    assert sum(doc["rows"].values()) == scanned


def test_a_launch_only_plan_never_scans_agent_output_and_still_sums(tree):
    """The launch watcher plans gzip/launch after every game exit; it must not hash every agent-output file."""
    copy = write(tree / "_CLAUDE_OUTPUT" / "t" / "fixture" / "Skyrim.esm", blob("master"))
    old = launch(tree, "launch-20200101-000000", 2000)
    doc = hk.plan(cfg_for(tree), only={"gzip", "launch"})
    assert planned(doc) == {key(old)}
    assert key(copy) not in planned(doc)
    scanned = sum(p.stat().st_size for p in (tree / "launches").rglob("*") if p.is_file())
    assert sum(doc["rows"].values()) == scanned, "only the launch folders were scanned, and every byte is counted"


# --------------------------------------------------------------------------- CLI

def test_cli_refuses_an_unknown_config_key(tmp_path):
    (tmp_path / "c.json").write_text('{"agent_outptu": []}', encoding="utf-8")
    assert hk.main(["--config", str(tmp_path / "c.json"), "report"]) == 2
