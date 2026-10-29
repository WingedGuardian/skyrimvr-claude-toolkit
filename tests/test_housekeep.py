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
    age(tracked, 10)              # `git add` READ it; reads now count as activity, which would protect it for that reason
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

def test_build_intermediates_are_planned_and_products_are_kept(tree):
    # A .lib is a build PRODUCT another build links (2026-10-28: a recycled CommonLibSSE.lib broke a live build).
    obj = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.obj", b"obj")
    lib = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.lib", b"lib")
    dll = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.dll", b"dll")
    pdb = write(tree / "_CLAUDE_OUTPUT" / "t" / "build-sdk" / "Release" / "x.pdb", b"pdb")
    loose = write(tree / "_CLAUDE_OUTPUT" / "t" / "notes" / "x.obj", b"obj outside a build dir")
    doc = hk.plan(cfg_for(tree))
    assert planned(doc, "build") == {key(obj)}
    assert not {key(lib), key(dll), key(pdb), key(loose)} & planned(doc)


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
    fake = bin_for(tree)               # the bad .gz goes to a bin -- never the user's real one, and not Windows-only
    monkeypatch.setattr(hk, "recycle", fake)
    with pytest.raises(OSError, match="round trip"):
        hk.compress(action)
    assert f.exists() and not (f.parent / "SkyrimNet.log.gz").exists()
    assert [os.path.basename(p) for p in fake.got] == ["SkyrimNet.log.gz"]


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


# --------------------------------------------------------------------------- temp sweep (the one permanent delete)

def stale(d, days=3):
    t = time.time() - days * DAY
    for p in sorted(d.rglob("*"), key=lambda p: -len(p.parts)) + [d]:
        os.utime(p, (t, t))
    return d


def tdir(temp, name, days=3):
    d = temp / name
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f.txt").write_text("x", encoding="utf-8")
    return stale(d, days)


def bin_for(tmp_path):
    """A fake Recycle Bin OUTSIDE the swept dir: no test may put anything in the real one."""
    root = tmp_path.parent / (tmp_path.name + "-bin")
    root.mkdir(exist_ok=True)
    return FakeBin(root)


def sweep_cfg():
    cfg = hk.load_config(None)
    cfg["temp_prefixes"] = ["skyrim-harness-", "t11-nohome-"]
    return cfg


def test_the_sweep_deletes_a_stale_toolkit_temp_dir_and_keeps_a_foreign_one(tmp_path):
    ours = tdir(tmp_path, "skyrim-harness-abc123")
    theirs = tdir(tmp_path, "someone-else-abc123")        # twin: same age, a name the toolkit never makes
    res = hk.sweep_temp(sweep_cfg(), temp=tmp_path, recycler=bin_for(tmp_path))
    assert not ours.exists()
    assert theirs.exists()
    assert res["rows"]["recycled"] == 1


def test_the_sweep_keeps_a_dir_whose_child_changed_recently(tmp_path):
    busy = tdir(tmp_path, "t11-nohome-busy01")
    (busy / "new.txt").write_text("y", encoding="utf-8")   # a direct child written now
    os.utime(busy, (time.time() - 3 * DAY,) * 2)            # the dir's own mtime still says old
    idle = tdir(tmp_path, "t11-nohome-idle01")              # twin: nothing recent
    res = hk.sweep_temp(sweep_cfg(), temp=tmp_path, recycler=bin_for(tmp_path))
    assert busy.exists() and not idle.exists()
    assert res["rows"]["kept: changed recently"] == 1


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to rename a dir with an open file inside")
def test_the_sweep_keeps_a_dir_a_process_has_a_file_open_in(tmp_path):
    held = tdir(tmp_path, "skyrim-harness-held01")
    free = tdir(tmp_path, "skyrim-harness-free01")          # twin: nothing open
    with open(held / "sub" / "f.txt", encoding="utf-8"):
        res = hk.sweep_temp(sweep_cfg(), temp=tmp_path, recycler=bin_for(tmp_path))
    assert held.exists() and not free.exists()
    assert res["rows"]["kept: in use"] == 1


def test_the_sweep_keeps_the_newest_pytest_run_and_removes_older_runs_and_garbage(tmp_path):
    base = tmp_path / "pytest-of-someone"
    old = tdir(base, "pytest-9")
    newest = tdir(base, "pytest-10")                         # numerically newest, though just as stale
    garbage = tdir(base, "garbage-0d1e")
    hk.sweep_temp(sweep_cfg(), temp=tmp_path, recycler=bin_for(tmp_path))
    assert newest.exists()
    assert not old.exists() and not garbage.exists()


def test_the_sweep_removes_read_only_files_and_its_rows_sum(tmp_path):
    ro = tdir(tmp_path, "skyrim-harness-ro0001")
    os.chmod(ro / "sub" / "f.txt", 0o444)
    stale(ro)
    tdir(tmp_path, "t11-nohome-young1", days=0)
    res = hk.sweep_temp(sweep_cfg(), temp=tmp_path, recycler=bin_for(tmp_path))
    assert not ro.exists()
    assert sum(res["rows"].values()) == 2 and res["rows"]["recycled"] == 1


def test_the_sweep_never_follows_a_link_out_of_temp(tmp_path):
    temp, outside = tmp_path / "temp", tmp_path / "precious"
    temp.mkdir()
    (outside / "keep.txt").parent.mkdir(parents=True)
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = temp / "skyrim-harness-link01"
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
        else:
            os.symlink(outside, link)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("cannot make a directory link here")
    stale(outside)
    fake = bin_for(tmp_path)
    hk.sweep_temp(sweep_cfg(), temp=temp, recycler=fake)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert fake.got == [] and link.exists(), "a link is never even offered to the bin"


def test_session_writes_a_note_only_when_there_is_something_to_say(tmp_path, monkeypatch):
    cfg = sweep_cfg()
    cfg.update(codex_sessions_warn_gib=10 ** 6, worktrees_warn=10 ** 6, free_floor_gib=0, root=tmp_path,
               sweep_every_hours=0, daily_enabled=False)          # the daily run has its own tests
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(hk, "recycle", bin_for(tmp_path))
    (tmp_path / "temp").mkdir()
    state = tmp_path / "state" / "note.txt"
    assert hk.session(cfg, state) == 0
    assert state.read_text(encoding="utf-8") == ""
    tdir(tmp_path / "temp", "skyrim-harness-sess01")
    assert hk.session(cfg, state) == 0
    assert state.read_text(encoding="utf-8").startswith("DISK: moved 1 leftover temp dir(s) to the Recycle Bin")


def test_session_runs_at_most_once_per_interval(tmp_path, monkeypatch):
    cfg = sweep_cfg()
    cfg.update(codex_sessions_warn_gib=10 ** 6, worktrees_warn=10 ** 6, free_floor_gib=0, root=tmp_path,
               sweep_every_hours=6, daily_enabled=False)          # the daily run has its own tests
    monkeypatch.setattr(hk.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(hk, "recycle", bin_for(tmp_path))
    (tmp_path / "temp").mkdir()
    state = tmp_path / "note.txt"
    state.write_text("", encoding="utf-8")                   # a sweep ran just now
    left = tdir(tmp_path / "temp", "skyrim-harness-thr001")
    hk.session(cfg, state)
    assert left.exists()
    os.utime(state, (time.time() - 7 * 3600,) * 2)           # twin: the last sweep was 7 hours ago
    hk.session(cfg, state)
    assert not left.exists()


def test_a_dir_the_bin_refuses_is_left_in_place_and_named(tmp_path):
    """Never a permanent delete, not even as a fallback: what the Recycle Bin will not take stays put."""
    temp = tmp_path / "temp"
    refused = tdir(temp, "skyrim-harness-big001")
    taken = tdir(temp, "skyrim-harness-ok0001")                # twin: the bin takes this one
    fake = bin_for(tmp_path)

    def picky(paths):
        out = {}
        for p in paths:
            out.update({str(p): "too big for the Recycle Bin"} if "big" in str(p) else fake([p]))
        return out
    res = hk.sweep_temp(sweep_cfg(), temp=temp, recycler=picky)
    assert refused.exists() and (refused / "sub" / "f.txt").read_text(encoding="utf-8") == "x"
    assert not taken.exists()
    assert res["rows"]["failed"] == 1 and res["rows"]["recycled"] == 1
    assert "skyrim-harness-big001" in res["failed"][0] and "too big" in res["failed"][0]


def test_the_tool_never_deletes_permanently():
    """User ruling 2026-10-18: the Recycle Bin, always -- the tool's own files included."""
    src = TOOL.read_text(encoding="utf-8")
    for call in ("os.unlink(", "os.remove(", "shutil.rmtree(", ".unlink(", "os.rmdir(", ".rmdir(",
                 "FileSystem]::Delete"):                    # the VB call: its 'OnlyErrorDialogs' skips the nuke prompt
        assert call not in src, call
    import re
    flags = re.findall(r"fFlags = ([^}]+?) \}", src)         # the expression itself, never a comment about it
    assert len(flags) == 1, flags
    assert {"0x4000", "0x40"} <= set(re.findall(r"0x[0-9A-Fa-f]+", flags[0])), flags  # nuke warning + undo
