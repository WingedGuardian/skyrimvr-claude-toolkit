"""Plan 6 Task 14 (Plan 5 R16): every .psc/.pex under both source layouts and Data/Scripts/** is snapshotted, whatever
its age, into a content-addressed store (`auto_snapshots/blobs/<sha256>`) with one manifest per snapshot."""
import hashlib
import json
import os
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit as audit_mod, snapshot  # noqa: E402

DAY = 86400


def store(root):
    return root / ".claude" / "backups" / "auto_snapshots"


def log_text(root):
    p = root / ".claude" / "backups" / "AUDIT_LOG.txt"
    return p.read_bytes().decode("utf-8") if p.is_file() else ""


def put(root, rel, body, age_days=0):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    if age_days:
        t = time.time() - age_days * DAY
        os.utime(str(p), (t, t))
    return p


def snap(root, command="make", now=None):
    snapshot.run(str(root), command, False, audit_mod.Audit(str(root)), now)


def latest(root):
    """The newest snapshot's full map {rel: sha256}."""
    names = snapshot.manifest_names(store(root))
    assert names, "no snapshot was taken; audit: " + log_text(root)
    return snapshot.resolve(store(root), names[-1])


def stored(root, rel_body):
    rel, body = rel_body
    # Layout-free first: are these bytes anywhere in the snapshot store at all? (This is the assertion that was RED
    # before Task 14, whatever the store looked like.)
    held = [p for p in store(root).rglob("*") if p.is_file() and p.read_bytes() == body] if store(root).is_dir() else []
    assert held, "{} is in no snapshot; audit: {}".format(rel, log_text(root))
    m = latest(root)
    sha = hashlib.sha256(body).hexdigest()
    return m.get(rel) == sha and (store(root) / "blobs" / sha).read_bytes() == body


# ---------------------------------------------------------------- step 1: the three RED cases


def test_a_nested_pex_is_snapshotted(tmp_path):
    put(tmp_path, "Data/Scripts/sub/deep/Nested.pex", b"nested pex")
    snap(tmp_path)
    assert stored(tmp_path, ("Data/Scripts/sub/deep/Nested.pex", b"nested pex"))


def test_a_psc_older_than_seven_days_is_snapshotted(tmp_path):
    put(tmp_path, "Data/Scripts/Source/Old.psc", b"eight days old", age_days=8)
    snap(tmp_path)
    assert stored(tmp_path, ("Data/Scripts/Source/Old.psc", b"eight days old"))


def test_the_sse_source_layout_is_snapshotted(tmp_path):
    put(tmp_path, "Data/Source/Scripts/x.psc", b"sse layout")
    snap(tmp_path)
    assert stored(tmp_path, ("Data/Source/Scripts/x.psc", b"sse layout"))


# ---------------------------------------------------------------- step 2: the store, the index, the manifests


def sha(body):
    return hashlib.sha256(body).hexdigest()


def blobs(root):
    return sorted(p.name for p in (store(root) / "blobs").iterdir() if not p.name.startswith("."))


def manifest(root, name):
    return json.loads((store(root) / "manifests" / name).read_bytes().decode("utf-8"))


def later(n=1):
    """A `now` outside the rate-limit window (each call further out), so the full index comparison runs."""
    return time.time() + n * (snapshot.RATE + 60)


def test_identical_bytes_are_stored_once(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"same")
    put(tmp_path, "Data/Source/Scripts/b.psc", b"same")
    snap(tmp_path)
    assert blobs(tmp_path) == [sha(b"same")]
    m = latest(tmp_path)
    assert m["Data/Scripts/a.pex"] == m["Data/Source/Scripts/b.psc"] == sha(b"same")


def test_only_files_whose_entry_changed_are_read_again(tmp_path, monkeypatch):
    for i in range(5):
        put(tmp_path, "Data/Scripts/s{}.pex".format(i), b"body %d" % i)
    snap(tmp_path)
    reads = []
    real = snapshot.backup._copy_hashed
    monkeypatch.setattr(snapshot.backup, "_copy_hashed", lambda s, p: reads.append(os.path.basename(s)) or real(s, p))
    put(tmp_path, "Data/Scripts/s3.pex", b"body 3, changed")
    snap(tmp_path, now=later())
    assert reads == ["s3.pex"]
    assert latest(tmp_path)["Data/Scripts/s3.pex"] == sha(b"body 3, changed")


def test_a_removed_script_is_gone_from_the_next_snapshot(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    b = put(tmp_path, "Data/Scripts/b.pex", b"b")
    snap(tmp_path)
    os.unlink(str(b))                                 # the test's own tmp_path file
    snap(tmp_path, now=later())
    first, second = snapshot.manifest_names(store(tmp_path))
    assert "Data/Scripts/b.pex" in snapshot.resolve(store(tmp_path), first)
    assert sorted(snapshot.resolve(store(tmp_path), second)) == ["Data/Scripts/a.pex"]


def test_a_small_change_is_a_delta_on_a_full_base(tmp_path):
    for n in "abc":
        put(tmp_path, "Data/Scripts/{}.pex".format(n), n.encode())
    snap(tmp_path)
    put(tmp_path, "Data/Scripts/a.pex", b"a, changed")
    snap(tmp_path, now=later())
    first, second = snapshot.manifest_names(store(tmp_path))
    m1, m2 = manifest(tmp_path, first), manifest(tmp_path, second)
    assert m1["base"] is None and len(m1["set"]) == 3
    assert m2["base"] == first and m2["set"] == {"Data/Scripts/a.pex": sha(b"a, changed")} and m2["gone"] == []
    assert snapshot.resolve(store(tmp_path), second) == {"Data/Scripts/a.pex": sha(b"a, changed"),
                                                         "Data/Scripts/b.pex": sha(b"b"), "Data/Scripts/c.pex": sha(b"c")}


def test_a_large_change_is_written_as_a_new_full_map(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "REBASE_MIN", 1)
    for n in "abcd":
        put(tmp_path, "Data/Scripts/{}.pex".format(n), n.encode())
    snap(tmp_path)
    put(tmp_path, "Data/Scripts/a.pex", b"a2")
    put(tmp_path, "Data/Scripts/b.pex", b"b2")        # 2 changed > max(1, 4 // 20): rebase
    snap(tmp_path, now=later())
    m2 = manifest(tmp_path, snapshot.manifest_names(store(tmp_path))[-1])
    assert m2["base"] is None and len(m2["set"]) == 4


def test_a_delta_at_the_threshold_stays_a_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "REBASE_MIN", 2)
    for n in "abcd":
        put(tmp_path, "Data/Scripts/{}.pex".format(n), n.encode())
    snap(tmp_path)
    put(tmp_path, "Data/Scripts/a.pex", b"a2")
    put(tmp_path, "Data/Scripts/b.pex", b"b2")        # 2 changed == max(2, 0): still a delta
    snap(tmp_path, now=later())
    first, second = snapshot.manifest_names(store(tmp_path))
    assert manifest(tmp_path, second)["base"] == first


def test_a_file_that_changes_size_while_it_is_copied_is_not_stored(tmp_path, monkeypatch):
    put(tmp_path, "Data/Scripts/a.pex", b"growing")
    real = snapshot.backup._copy_hashed

    def torn(src, part):
        want, copied, digest = real(src, part)
        return want + 1, copied, digest              # the file grew between open and end of copy
    monkeypatch.setattr(snapshot.backup, "_copy_hashed", torn)
    snap(tmp_path)
    assert blobs(tmp_path) == []
    assert not [p for p in (store(tmp_path) / "blobs").iterdir()], "a part file was left behind"
    assert "FAILED: 1 script file(s) could not be read" in log_text(tmp_path)


def test_no_part_file_is_left_in_the_store(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    put(tmp_path, "Data/Scripts/b.pex", b"a")         # the second copy finds the blob already there
    snap(tmp_path)
    assert sorted(p.name for p in (store(tmp_path) / "blobs").iterdir()) == [sha(b"a")]


def test_without_hard_links_the_blob_is_still_stored(tmp_path, monkeypatch):
    put(tmp_path, "Data/Scripts/a.pex", b"no links here")

    def nolink(a, b):
        raise OSError(1, "hard links are not supported on this volume")
    monkeypatch.setattr(snapshot.os, "link", nolink)
    snap(tmp_path)
    monkeypatch.undo()
    assert stored(tmp_path, ("Data/Scripts/a.pex", b"no links here"))
    assert blobs(tmp_path) == [sha(b"no links here")]


def test_materialise_writes_a_snapshot_back_out(tmp_path):
    put(tmp_path, "Data/Scripts/Source/a.psc", b"source a")
    put(tmp_path, "Data/Source/Scripts/b.psc", b"source b")
    snap(tmp_path)
    out = tmp_path / "restored"
    assert snapshot.materialise(str(tmp_path), snapshot.manifest_names(store(tmp_path))[-1], str(out)) == 2
    assert (out / "Data" / "Scripts" / "Source" / "a.psc").read_bytes() == b"source a"
    assert (out / "Data" / "Source" / "Scripts" / "b.psc").read_bytes() == b"source b"


def test_a_failed_manifest_is_written_by_the_next_call(tmp_path, monkeypatch):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    real = snapshot._write_manifest

    def fail(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(snapshot, "_write_manifest", fail)
    snap(tmp_path)
    assert snapshot.manifest_names(store(tmp_path)) == [] and "FAILED" in log_text(tmp_path)
    monkeypatch.setattr(snapshot, "_write_manifest", real)
    snap(tmp_path, now=later())                        # nothing changed since -- but the snapshot is still owed
    assert latest(tmp_path) == {"Data/Scripts/a.pex": sha(b"a")}


def test_the_store_lock_makes_a_concurrent_call_skip(tmp_path, monkeypatch):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    (store(tmp_path) / "blobs").mkdir(parents=True)
    monkeypatch.setattr(snapshot.backup, "LOCK_WAIT", 0.05)
    with snapshot.backup._exclusive(store(tmp_path) / ".lock"):
        snap(tmp_path)
    assert snapshot.manifest_names(store(tmp_path)) == []
    assert "SKIPPED: another snapshot holds the store lock" in log_text(tmp_path)


def test_a_file_changed_after_the_walk_started_is_newer_than_the_last_walk(tmp_path, monkeypatch):
    """The rate-limit file carries the time the walk STARTED: an edit landing while the walk's files are being stored
    is newer than it, so the next call inside the window still looks."""
    f = put(tmp_path, "Data/Scripts/a.pex", b"one")
    real = snapshot._store_blob

    def store_then_edit(blobs_dir, src):
        out = real(blobs_dir, src)
        f.write_bytes(b"two!")
        return out
    monkeypatch.setattr(snapshot, "_store_blob", store_then_edit)
    snap(tmp_path)
    monkeypatch.setattr(snapshot, "_store_blob", real)
    assert latest(tmp_path)["Data/Scripts/a.pex"] == sha(b"one")
    snap(tmp_path)                                     # inside the 60 s window
    assert latest(tmp_path)["Data/Scripts/a.pex"] == sha(b"two!")


# ---------------------------------------------------------------- the time-boxed baseline


class Clock:
    """snapshot.time with a monotonic clock that advances one second per reading."""

    def __init__(self):
        self.t = 0.0
        self.time = time.time
        self.time_ns = time.time_ns

    def monotonic(self):
        self.t += 1.0
        return self.t


def nine(tmp_path):
    for i in range(9):
        put(tmp_path, "Data/Scripts/s{}.pex".format(i), b"body %d" % i)


def test_an_unfinished_baseline_is_partial_and_resumes_on_the_next_call(tmp_path, monkeypatch):
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    au = audit_mod.Audit(str(tmp_path))
    snapshot.take(str(tmp_path), au, "first", box=2.5)        # three files fit in the box
    (n1,) = snapshot.manifest_names(store(tmp_path))
    assert manifest(tmp_path, n1)["partial"] is True and len(snapshot.resolve(store(tmp_path), n1)) == 3
    assert ("AUTO-SNAPSHOT PARTIAL (psc:0 pex:3 changed, 3 files, 6 not yet read -- resumes on the next call) "
            + n1 + " before: first") in log_text(tmp_path)
    assert (store(tmp_path) / ".partial").exists()
    snapshot.take(str(tmp_path), au, "second", box=2.5)       # inside the rate-limit window: resumes anyway
    assert snapshot.manifest_names(store(tmp_path)) == [n1], "a resume that only adds files writes no manifest"
    snapshot.take(str(tmp_path), au, "third", box=2.5)
    names = snapshot.manifest_names(store(tmp_path))
    assert len(names) == 2
    m = manifest(tmp_path, names[-1])
    assert m["partial"] is False and len(snapshot.resolve(store(tmp_path), names[-1])) == 9
    assert not (store(tmp_path) / ".partial").exists()


def test_a_real_change_during_a_baseline_is_snapshotted_at_once(tmp_path, monkeypatch):
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    au = audit_mod.Audit(str(tmp_path))
    snapshot.take(str(tmp_path), au, "first", box=2.5)        # s0..s2 read
    put(tmp_path, "Data/Scripts/s0.pex", b"s0, edited")
    snapshot.take(str(tmp_path), au, "second", box=2.5)
    names = snapshot.manifest_names(store(tmp_path))
    assert len(names) == 2 and snapshot.resolve(store(tmp_path), names[-1])["Data/Scripts/s0.pex"] == sha(b"s0, edited")


def test_a_deletion_during_a_baseline_is_snapshotted_at_once(tmp_path, monkeypatch):
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    au = audit_mod.Audit(str(tmp_path))
    snapshot.take(str(tmp_path), au, "first", box=2.5)        # s0..s2 read
    os.unlink(str(tmp_path / "Data" / "Scripts" / "s1.pex"))  # the test's own tmp_path file
    snapshot.take(str(tmp_path), au, "second", box=1.5)
    names = snapshot.manifest_names(store(tmp_path))
    assert len(names) == 2 and "Data/Scripts/s1.pex" not in snapshot.resolve(store(tmp_path), names[-1])


def test_every_call_makes_progress_however_small_its_box(tmp_path, monkeypatch):
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    snapshot.take(str(tmp_path), audit_mod.Audit(str(tmp_path)), "tiny", box=0)
    (n1,) = snapshot.manifest_names(store(tmp_path))
    assert len(snapshot.resolve(store(tmp_path), n1)) == 1


def test_an_unfinished_baseline_does_not_prune(tmp_path, monkeypatch):
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    calls = []
    monkeypatch.setattr(snapshot, "prune", lambda *a: calls.append(a))
    snapshot.take(str(tmp_path), audit_mod.Audit(str(tmp_path)), "first", box=2.5, prune_due=True)
    assert calls == []


def test_pruning_runs_at_most_once_a_day_per_call(tmp_path, monkeypatch):
    f = put(tmp_path, "Data/Scripts/a.pex", b"a")
    calls = []
    real = snapshot.prune
    monkeypatch.setattr(snapshot, "prune", lambda *a: calls.append(1) or real(*a))
    t = time.time()
    snap(tmp_path, now=t)                              # never pruned before: due
    f.write_bytes(b"a2")
    snap(tmp_path, now=t + 2 * snapshot.RATE)
    assert len(calls) == 1
    f.write_bytes(b"a33")
    snap(tmp_path, now=t + snapshot.DAY + 2 * snapshot.RATE)
    assert len(calls) == 2


# ---------------------------------------------------------------- SessionStart


def test_session_start_takes_no_snapshot_on_compact(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    assert snapshot.session_start(str(tmp_path), "compact", audit_mod.Audit(str(tmp_path))) is None
    assert not store(tmp_path).exists()


def test_session_start_ignores_the_rate_limit(tmp_path):
    f = put(tmp_path, "Data/Scripts/a.pex", b"a")
    snap(tmp_path)
    f.write_bytes(b"a2")
    t = time.time() - 3600
    os.utime(str(f), (t, t))                           # older than the last walk: a rate-limited call would not look
    snapshot.session_start(str(tmp_path), "startup", audit_mod.Audit(str(tmp_path)))
    assert latest(tmp_path)["Data/Scripts/a.pex"] == sha(b"a2")


def _engine(root, argv, payload, env=None):
    import subprocess
    cmd = [sys.executable, "-B", str(REPO / "tools" / "skyguard" / "run.py"), *argv, "--project", str(root),
           "--root", str(root), "--root-win", str(root).replace("\\", "/")]
    e = dict(os.environ)
    e.pop("CLAUDE_ENV_FILE", None)
    e.update(env or {})
    return subprocess.run(cmd, input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=120, env=e)


@pytest.mark.parametrize("agent,role,payload", [
    ("claude", "session-kb-guard", {"session_id": "s-1", "source": "startup", "hook_event_name": "SessionStart"}),
    ("codex", "session-start", {"session_id": "s-1", "source": "startup", "hook_event_name": "SessionStart",
                                "transcript_path": "C:/t/rollout.jsonl", "cwd": "C:/x"}),
], ids=["claude", "codex"])
def test_the_session_start_hook_takes_the_baseline(tmp_path, agent, role, payload):
    """Through the real entry point (run.py), for both agents."""
    put(tmp_path, "Data/Source/Scripts/x.psc", b"at session start")
    r = _engine(tmp_path, [agent, role], payload)
    assert r.returncode == 0, r.stderr
    assert stored(tmp_path, ("Data/Source/Scripts/x.psc", b"at session start"))
    assert "before: session start (startup)" in log_text(tmp_path)


def test_the_session_start_baseline_never_runs_past_the_engine_budget(tmp_path):
    """The engine's watchdog ends the process at its budget, taking the session's banners with it: the baseline's
    box is cut to what the budget leaves, and with nothing left it is skipped and says so."""
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    r = _engine(tmp_path, ["claude", "session-kb-guard"], {"session_id": "s-1", "source": "startup"},
                env={"SKYGUARD_ENGINE_TIMEOUT": "2"})
    assert r.returncode == 0, r.stderr
    assert "SKIPPED: no time left in the session-start budget" in log_text(tmp_path)
    assert not store(tmp_path).exists() or snapshot.manifest_names(store(tmp_path)) == []


# ---------------------------------------------------------------- pruning


def at(days_ago):
    return time.time() - days_ago * DAY


def full_maps(monkeypatch):
    monkeypatch.setattr(snapshot, "REBASE_MIN", -1)    # every snapshot a full map: pruning is then visible per file
    monkeypatch.setattr(snapshot, "PRUNE_EVERY", 10 ** 12)   # and only the test's own prune() call prunes


def test_pruning_drops_old_snapshots_but_never_a_paths_only_latest_content(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    b = put(tmp_path, "Data/Scripts/b.pex", b"b1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    os.unlink(str(b))                                  # b's last content now lives only in the 30-day-old snapshot
    snap(tmp_path, now=at(20))
    m1, m2, m3 = snapshot.manifest_names(store(tmp_path))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert snapshot.manifest_names(store(tmp_path)) == [m2, m3]
    assert blobs(tmp_path) == sorted([sha(b"a22"), sha(b"b1")]), "a1 is pruned; b1 is b's only latest copy"


def test_pruning_keeps_the_newest_snapshot_whatever_its_age(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    os.unlink(str(a))
    snap(tmp_path, now=at(30))                         # the newest snapshot: empty, 30 days old
    names = snapshot.manifest_names(store(tmp_path))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert snapshot.manifest_names(store(tmp_path))[-1] == names[-1]


def test_pruning_keeps_every_snapshot_younger_than_14_days(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(20))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(13))
    a.write_bytes(b"a333")
    snap(tmp_path, now=at(1))
    m1, m2, m3 = snapshot.manifest_names(store(tmp_path))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert snapshot.manifest_names(store(tmp_path)) == [m2, m3]
    assert blobs(tmp_path) == sorted([sha(b"a22"), sha(b"a333")])


def test_pruning_keeps_the_base_of_a_kept_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "PRUNE_EVERY", 10 ** 12)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    put(tmp_path, "Data/Scripts/b.pex", b"b1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(1))                          # a delta on the 40-day-old full map
    base, delta = snapshot.manifest_names(store(tmp_path))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert snapshot.manifest_names(store(tmp_path)) == [base, delta]
    assert snapshot.resolve(store(tmp_path), delta) == {"Data/Scripts/a.pex": sha(b"a22"),
                                                        "Data/Scripts/b.pex": sha(b"b1")}


def test_an_unreadable_snapshot_stops_blob_pruning(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    (store(tmp_path) / "manifests" / "20000101_000000__snapshot.json").write_bytes(b"not json")
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert sha(b"a1") in blobs(tmp_path)
    assert "PRUNE-SKIPPED: 1 unreadable snapshot manifest(s)" in log_text(tmp_path)


def test_pruning_keeps_the_blob_of_live_content_no_snapshot_holds_yet(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")

    def fail(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(snapshot, "_write_manifest", fail)
    snap(tmp_path, now=at(39))                         # a22 is stored and indexed; its manifest failed
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert sha(b"a22") in blobs(tmp_path)


def test_pruning_removes_only_stale_part_files(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path)
    old, new = store(tmp_path) / "blobs" / ".part-99999", store(tmp_path) / "blobs" / ".part-99998"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    t = at(2)
    os.utime(str(old), (t, t))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert not old.exists() and new.exists()
