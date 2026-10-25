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


@pytest.mark.parametrize("command", [
    "cat notes.txt; printf x > Data/Scripts/a.pex",
    "make\nls y",
    "cat 'unfinished",
    "git status && python change.py", "echo $(python change.py)",
    "Get-Content notes.txt; Set-Content Data/Scripts/a.pex x",
    "Get-Content notes.txt | ForEach-Object { Set-Content Data/Scripts/a.pex x }",
    "find . -delete", "find . -fprint output.txt", "find . -fls Data/Scripts/a.pex", "git diff --output=report.txt",
    "git branch new-branch", "git remote add mine https://example.invalid/repo",
])
def test_read_prefix_does_not_skip_a_mutating_command_snapshot(tmp_path, command):
    put(tmp_path, "Data/Scripts/a.pex", b"before command")
    snap(tmp_path, command)
    assert stored(tmp_path, ("Data/Scripts/a.pex", b"before command"))


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
    monkeypatch.setattr(snapshot.backup, "_copy_hashed", lambda s, p, **k: reads.append(os.path.basename(s)) or real(s, p, **k))
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

    def torn(src, part, **k):
        want, copied, digest = real(src, part, **k)
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
    """s0 .. s8, each a minute newer than the one before (s8 the newest)."""
    base = time.time() - 3600
    for i in range(9):
        p = put(tmp_path, "Data/Scripts/s{}.pex".format(i), b"body %d" % i)
        os.utime(str(p), (base + 60 * i, base + 60 * i))


def test_a_baseline_reads_the_newest_scripts_first(tmp_path, monkeypatch):
    """Review I3: the scripts being experimented on are the recent ones; a time-boxed baseline must reach them first,
    not after the alphabetical long tail."""
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    snapshot.take(str(tmp_path), audit_mod.Audit(str(tmp_path)), "first", box=2.5)
    (n1,) = snapshot.manifest_names(store(tmp_path))
    assert sorted(snapshot.resolve(store(tmp_path), n1)) == ["Data/Scripts/s6.pex", "Data/Scripts/s7.pex",
                                                             "Data/Scripts/s8.pex"]


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
    snapshot.take(str(tmp_path), au, "first", box=2.5)        # s8..s6 read (newest first)
    put(tmp_path, "Data/Scripts/s8.pex", b"s8, edited")
    snapshot.take(str(tmp_path), au, "second", box=2.5)
    names = snapshot.manifest_names(store(tmp_path))
    assert len(names) == 2 and snapshot.resolve(store(tmp_path), names[-1])["Data/Scripts/s8.pex"] == sha(b"s8, edited")


def test_a_deletion_during_a_baseline_is_snapshotted_at_once(tmp_path, monkeypatch):
    nine(tmp_path)
    monkeypatch.setattr(snapshot, "time", Clock())
    au = audit_mod.Audit(str(tmp_path))
    snapshot.take(str(tmp_path), au, "first", box=2.5)        # s8..s6 read (newest first)
    os.unlink(str(tmp_path / "Data" / "Scripts" / "s7.pex"))  # the test's own tmp_path file
    snapshot.take(str(tmp_path), au, "second", box=1.5)
    names = snapshot.manifest_names(store(tmp_path))
    assert len(names) == 2 and "Data/Scripts/s7.pex" not in snapshot.resolve(store(tmp_path), names[-1])


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
    monkeypatch.setattr(snapshot, "prune", lambda *a, **k: calls.append(1) or real(*a, **k))
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


def test_pruning_retires_what_it_removes_and_deletes_nothing(tmp_path, monkeypatch):
    """Review L2 LOW-7: the user's Recycle Bin rule has no carve-out for the toolkit's own store. A pruned manifest,
    blob and bash-era copy folder are moved whole into <project>/.claude/retired (housekeep recycles them)."""
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    m1, _m2 = snapshot.manifest_names(store(tmp_path))
    legacy = store(tmp_path) / "20200101_000000"
    (legacy / "source").mkdir(parents=True)
    (legacy / "source" / "x.psc").write_bytes(b"bash-era copy")
    os.utime(str(legacy), (at(30),) * 2)
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert not legacy.exists() and snapshot.manifest_names(store(tmp_path)) == [_m2]
    moved = {p.name.split("-", 1)[1]: p for p in (tmp_path / ".claude" / "retired").glob("*/*")}
    assert sorted(moved) == sorted([m1, sha(b"a1"), legacy.name])
    assert (moved[legacy.name] / "source" / "x.psc").read_bytes() == b"bash-era copy"
    assert moved[sha(b"a1")].read_bytes() == b"a1"


@pytest.mark.parametrize("inside", ["source/x.ess", "deploy/SKSE/x.skse", "source/backups/x.txt"])
def test_an_old_bash_era_stamp_holding_a_save_or_a_backup_is_kept(tmp_path, monkeypatch, inside):
    """guard3 (parent ruling): a 3.x stamp is retired whole, so like housekeep.protected_inside a save or a backup
    folder anywhere in it keeps all of it. A plain old stamp beside it is still retired."""
    full_maps(monkeypatch)
    put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    kept = store(tmp_path) / "20200101_000000"
    (kept / inside).parent.mkdir(parents=True)
    (kept / inside).write_bytes(b"keep me")
    plain = store(tmp_path) / "20200102_000000"
    (plain / "source").mkdir(parents=True)
    (plain / "source" / "x.psc").write_bytes(b"bash-era copy")
    for d in (kept, plain):
        os.utime(str(d), (at(30),) * 2)
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert (kept / inside).read_bytes() == b"keep me"
    assert not plain.exists()


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


# ---------------------------------------------------------------- fix round 1 (review I1, I2, fsync ruling, minors)

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions and listing semantics")


@pytest.fixture
def junction():
    """make(link, target) creates an NTFS junction; teardown removes the LINK only (os.rmdir on a junction never
    touches its target), before pytest's own cleanup could walk through it."""
    import _winapi
    made = []

    def make(link, target):
        _winapi.CreateJunction(str(target), str(link))
        made.append(link)
        return link
    yield make
    for link in reversed(made):
        try:
            os.rmdir(str(link))
        except OSError:
            pass


@needs_windows
def test_a_junction_out_of_the_tree_is_not_followed(tmp_path, junction):
    put(tmp_path, "Data/Scripts/mine.pex", b"mine")
    put(tmp_path, "outside/foreign.pex", b"foreign")
    junction(tmp_path / "Data" / "Scripts" / "jn", tmp_path / "outside")
    snap(tmp_path)
    assert sorted(latest(tmp_path)) == ["Data/Scripts/mine.pex"]
    log = log_text(tmp_path)
    assert "LINKS: 1 linked folder(s) under the script trees are NOT followed" in log and "Data/Scripts/jn" in log
    assert "1 linked folder(s) not followed" in log


@needs_windows
def test_a_junction_loop_is_not_followed(tmp_path, junction):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    put(tmp_path, "Data/Source/Scripts/b.psc", b"b")
    junction(tmp_path / "Data" / "Scripts" / "loop", tmp_path / "Data")
    files, linked, truncated = snapshot.scan(tmp_path)
    assert sorted(files) == ["Data/Scripts/a.pex", "Data/Source/Scripts/b.psc"]
    assert linked == ["Data/Scripts/loop"] and truncated is False


def test_a_symlinked_folder_is_not_followed(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    put(tmp_path, "outside/foreign.pex", b"foreign")
    try:
        os.symlink(str(tmp_path / "outside"), str(tmp_path / "Data" / "Scripts" / "sl"), target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip("cannot create a directory symlink here: {}".format(e))
    files, linked, _t = snapshot.scan(tmp_path)
    assert sorted(files) == ["Data/Scripts/a.pex"] and linked == ["Data/Scripts/sl"]


def test_the_walk_stops_at_its_time_box_and_deletes_nothing(tmp_path, monkeypatch):
    """No tree can hold a hook past the walk's box; a cut-short walk is PARTIAL and never reads as deletions."""
    put(tmp_path, "Data/Scripts/top.pex", b"top")
    for d in ("b", "c"):
        put(tmp_path, "Data/Scripts/{}/x.pex".format(d), d.encode())
    snap(tmp_path)
    ticks = iter(range(1000))
    monkeypatch.setattr(snapshot, "_walk_clock", lambda: float(next(ticks)))
    monkeypatch.setattr(snapshot, "WALK_BOX", 2.0)    # one folder listed, then the box is spent
    put(tmp_path, "Data/Scripts/top.pex", b"top, edited")
    snap(tmp_path, now=later())
    log = log_text(tmp_path)
    assert "PARTIAL: the walk stopped at its time box" in log and "AUTO-SNAPSHOT PARTIAL" in log
    m = latest(tmp_path)
    assert m["Data/Scripts/top.pex"] == sha(b"top, edited")
    assert sorted(m) == ["Data/Scripts/b/x.pex", "Data/Scripts/c/x.pex", "Data/Scripts/top.pex"], \
        "a folder the walk did not reach must not count as deleted"


@needs_windows
def test_a_hard_link_edited_through_its_other_path_is_seen(tmp_path, monkeypatch):
    """Review I2 (MEASURED by the reviewer): the listing of one hard link keeps the size/mtime of its own directory
    entry after an edit through the OTHER link. Vortex deploys by hard link by default. The single-link sweep (re-review
    N3) is switched off here: on a real tree it reaches a given file only every ~18 comparisons, so the link count
    recorded at hash time is what makes the NEXT comparison re-stat it."""
    monkeypatch.setattr(snapshot, "_sweep_links", lambda *a: 0)
    staging = put(tmp_path, "staging/mine.pex", b"original")
    (tmp_path / "Data" / "Scripts").mkdir(parents=True)
    os.link(str(staging), str(tmp_path / "Data" / "Scripts" / "mine.pex"))
    snap(tmp_path)
    assert latest(tmp_path)["Data/Scripts/mine.pex"] == sha(b"original")
    with open(str(staging), "wb") as fh:              # in place: the same file, through the staging link
        fh.write(b"edited in the staging folder")
    snap(tmp_path, now=later())
    assert latest(tmp_path)["Data/Scripts/mine.pex"] == sha(b"edited in the staging folder")


@needs_windows
def test_the_hard_link_recheck_is_capped_and_resumes_where_it_stopped(tmp_path, monkeypatch):
    """Gate ruling, condition 3: the re-stat pass has its own cap (a fully hard-linked install measured 186-442 ms),
    and the next comparison carries on from where it stopped, so every linked file is re-checked in turn."""
    (tmp_path / "Data" / "Scripts").mkdir(parents=True)
    names = ["l{}.pex".format(i) for i in range(6)]
    for n in names:
        s = put(tmp_path, "staging/" + n, b"v1 " + n.encode())
        os.link(str(s), str(tmp_path / "Data" / "Scripts" / n))
    snap(tmp_path)
    for n in names:                                    # every file edited through the OTHER link
        with open(str(tmp_path / "staging" / n), "wb") as fh:
            fh.write(b"version two of " + n.encode())
    monkeypatch.setattr(snapshot, "RESTAT_BOX", 0.0)   # one re-stat per comparison
    t = time.time()

    def edited():
        m = latest(tmp_path)
        return [n for n in names if m["Data/Scripts/" + n] == sha(b"version two of " + n.encode())]
    snap(tmp_path, now=t + 1 * (snapshot.RATE + 60))
    assert len(edited()) == 1, "the cap must stop the pass after one file"
    assert "RESTAT-PARTIAL: 1 of 6 hard-linked script(s) re-checked" in log_text(tmp_path)
    for k in range(2, 7):
        snap(tmp_path, now=t + k * (snapshot.RATE + 60))
    assert sorted(edited()) == sorted(names), "the next comparisons resume where the last one stopped"


def test_blobs_are_not_fsynced_but_the_index_and_manifests_are(tmp_path, monkeypatch):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    put(tmp_path, "Data/Scripts/b.pex", b"b")
    monkeypatch.setattr(snapshot, "PRUNE_EVERY", 10 ** 12)   # pruning rewrites the index too; not under test here
    synced = []
    real = os.fsync

    def spy(fd):
        synced.append(fd)
        return real(fd)
    monkeypatch.setattr(os, "fsync", spy)
    snap(tmp_path)
    assert len(synced) == 2, "one fsync for the manifest and one for the index, none for the two blobs"


def test_a_torn_blob_is_replaced_not_trusted(tmp_path):
    """Blobs are not fsynced: after a power loss a blob can sit under its hash name with the wrong bytes. A blob of the
    wrong size is replaced the next time that content is stored."""
    put(tmp_path, "Data/Scripts/a.pex", b"the real bytes")
    (store(tmp_path) / "blobs").mkdir(parents=True)
    (store(tmp_path) / "blobs" / sha(b"the real bytes")).write_bytes(b"")
    snap(tmp_path)
    assert (store(tmp_path) / "blobs" / sha(b"the real bytes")).read_bytes() == b"the real bytes"


def test_materialise_refuses_a_damaged_blob(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"good bytes")
    snap(tmp_path)
    (store(tmp_path) / "blobs" / sha(b"good bytes")).write_bytes(b"bad bytes!")
    with pytest.raises(ValueError, match="damaged"):
        snapshot.materialise(str(tmp_path), snapshot.manifest_names(store(tmp_path))[-1], str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("rel", ["../escape.pex", "Data/../../escape.pex", "C:/x.pex", "Data\\x.pex", "/abs.pex"],
                         ids=["dotdot", "nested-dotdot", "drive", "backslash", "absolute"])
def test_materialise_refuses_a_path_that_leaves_its_target(tmp_path, rel):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    name = snap(tmp_path) or snapshot.manifest_names(store(tmp_path))[-1]
    p = store(tmp_path) / "manifests" / name
    m = json.loads(p.read_bytes().decode("utf-8"))
    m["set"] = {rel: sha(b"a")}
    p.write_bytes(json.dumps(m).encode("ascii"))
    with pytest.raises(ValueError, match="refusing"):
        snapshot.materialise(str(tmp_path), name, str(tmp_path / "out"))


def test_materialise_refuses_a_malformed_hash(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    snap(tmp_path)
    name = snapshot.manifest_names(store(tmp_path))[-1]
    p = store(tmp_path) / "manifests" / name
    m = json.loads(p.read_bytes().decode("utf-8"))
    m["set"] = {"Data/Scripts/a.pex": "../../../../outside"}
    p.write_bytes(json.dumps(m).encode("ascii"))
    (store(tmp_path) / "blobs" / ".." / ".." / ".." / ".." / "outside").write_bytes(b"x")   # a file the hash would reach
    with pytest.raises(ValueError, match="refusing"):
        snapshot.materialise(str(tmp_path), name, str(tmp_path / "out"))


@needs_windows
def test_materialise_replaces_a_hard_linked_target_instead_of_writing_through(tmp_path):
    """Explicit replacement breaks the Vortex link; default materialisation refuses it."""
    put(tmp_path, "Data/Scripts/a.pex", b"snapshot content")
    snap(tmp_path)
    out = tmp_path / "out"
    staging = put(tmp_path, "staging/a.pex", b"staging must stay")
    (out / "Data" / "Scripts").mkdir(parents=True)
    os.link(str(staging), str(out / "Data" / "Scripts" / "a.pex"))
    name = snapshot.manifest_names(store(tmp_path))[-1]
    with pytest.raises(OSError):
        snapshot.materialise(str(tmp_path), name, str(out))
    assert (out / "Data" / "Scripts" / "a.pex").read_bytes() == b"staging must stay"
    files = snapshot.resolve(store(tmp_path), name)
    snapshot.write_blobs(store(tmp_path), [(out / rel, sha) for rel, sha in files.items()], replace=True)
    assert (out / "Data" / "Scripts" / "a.pex").read_bytes() == b"snapshot content"
    assert staging.read_bytes() == b"staging must stay"


def test_a_tool_call_waits_only_briefly_for_the_store_lock(tmp_path, monkeypatch):
    """Review M6: a call that finds another session's baseline holding the lock gives up within CALL_LOCK_WAIT, not
    backup's 2 s, so it stays inside the 1 s per-wrapper gate."""
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    (store(tmp_path) / "blobs").mkdir(parents=True)
    with snapshot.backup._exclusive(store(tmp_path) / ".lock"):
        t = time.monotonic()
        snap(tmp_path)
        took = time.monotonic() - t
    assert "SKIPPED" in log_text(tmp_path) and took < 1.0, took


def test_pruning_stops_at_its_deadline(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    a.write_bytes(b"a333")
    snap(tmp_path, now=at(20))
    before = snapshot.manifest_names(store(tmp_path))
    # One tick per reading: the three manifests load inside the box (ticks 0-2), the first deletion meets it (tick 3).
    ticks = iter(range(1000))
    monkeypatch.setattr(snapshot, "_walk_clock", lambda: float(next(ticks)))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)), deadline=3.0)
    assert snapshot.manifest_names(store(tmp_path)) == before
    assert "PRUNE-PARTIAL" in log_text(tmp_path)


def test_pruning_loads_no_manifest_once_its_deadline_has_passed(tmp_path, monkeypatch):
    """Task 14 re-review N1: loading every manifest ran before the first deadline check (~5 ms each, cold), so a
    store with hundreds of snapshots could hold one tool call for seconds."""
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    loads = []
    real = snapshot._load_manifest
    monkeypatch.setattr(snapshot, "_load_manifest", lambda s, n: loads.append(n) or real(s, n))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)), deadline=0.0)   # already past
    assert loads == [] and "PRUNE-PARTIAL" in log_text(tmp_path)


def test_materialise_refuses_a_non_string_hash_with_a_valueerror(tmp_path):
    """Task 14 re-review N2: a tampered manifest holding a number raised TypeError, which no caller expects."""
    put(tmp_path, "Data/Scripts/a.pex", b"a")
    snap(tmp_path)
    name = snapshot.manifest_names(store(tmp_path))[-1]
    p = store(tmp_path) / "manifests" / name
    m = json.loads(p.read_bytes().decode("utf-8"))
    m["set"] = {"Data/Scripts/a.pex": 12345}
    p.write_bytes(json.dumps(m).encode("ascii"))
    with pytest.raises(ValueError, match="refusing"):
        snapshot.materialise(str(tmp_path), name, str(tmp_path / "out"))


@needs_windows
def test_a_file_hard_linked_after_it_was_indexed_is_rechecked(tmp_path):
    """Task 14 re-review N3: a path indexed with one link was never re-stat'd, so a hard link made LATER (mklink /H
    into a staging folder) and edited through hid the edit: the Data listing keeps its own stale size/mtime."""
    p = put(tmp_path, "Data/Scripts/mine.pex", b"original")
    snap(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    os.link(str(p), str(staging / "mine.pex"))
    with open(str(staging / "mine.pex"), "wb") as fh:
        fh.write(b"edited through a link made after indexing")
    snap(tmp_path, now=later())
    assert latest(tmp_path)["Data/Scripts/mine.pex"] == sha(b"edited through a link made after indexing")


def test_a_link_count_found_by_the_sweep_is_kept_in_the_index(tmp_path):
    """The sweep's finding must persist even when the content did not change, or the next comparisons never re-stat
    the file and an edit through the other link waits for the sweep to come round again."""
    p = put(tmp_path, "Data/Scripts/mine.pex", b"original")
    snap(tmp_path)
    (tmp_path / "staging").mkdir()
    os.link(str(p), str(tmp_path / "staging" / "mine.pex"))
    snap(tmp_path, now=later())
    idx = json.loads((store(tmp_path) / "index.json").read_bytes().decode("utf-8"))
    assert idx["files"]["Data/Scripts/mine.pex"][3] == 2


def test_the_sweep_is_capped_per_comparison_and_resumes(tmp_path, monkeypatch):
    """The sweep stops after SWEEP_MAX paths (a count, not only a time: time.monotonic ticks every ~15.6 ms on
    Windows) and the next comparison starts where it stopped."""
    for n in ("a", "b", "c"):
        put(tmp_path, "Data/Scripts/{}.pex".format(n), n.encode())
    files, seen = {}, {}
    for rel, (size, mt) in snapshot.walk(tmp_path).items():
        files[rel], seen[rel] = [size, mt, "0" * 64, 1], (size, mt)
    st = tmp_path / "store"
    st.mkdir()
    monkeypatch.setattr(snapshot, "SWEEP_MAX", 1)
    monkeypatch.setattr(snapshot, "SWEEP_BOX", 60.0)
    snapshot._sweep_links(tmp_path, st, files, seen)
    assert (st / snapshot.SWEEP_CURSOR).read_bytes() == b"1"
    snapshot._sweep_links(tmp_path, st, files, seen)
    assert (st / snapshot.SWEEP_CURSOR).read_bytes() == b"2"


def _linked_pair(tmp_path):
    (tmp_path / "Data" / "Scripts").mkdir(parents=True)
    for n in ("l0.pex", "l1.pex"):
        s = put(tmp_path, "staging/" + n, b"v1 " + n.encode())
        os.link(str(s), str(tmp_path / "Data" / "Scripts" / n))


def test_a_garbled_restat_cursor_restarts_the_pass(tmp_path):
    """Re-review N6: the cursor is a small sidecar written without fsync; torn or garbled, it must restart the pass,
    never raise into the hook."""
    _linked_pair(tmp_path)
    snap(tmp_path)
    (store(tmp_path) / snapshot.RESTAT_CURSOR).write_bytes(b"\xff\xfe\x00 not utf-8")
    for n in ("l0.pex", "l1.pex"):
        with open(str(tmp_path / "staging" / n), "wb") as fh:
            fh.write(b"version two of " + n.encode())
    snap(tmp_path, now=later())
    m = latest(tmp_path)
    assert m["Data/Scripts/l0.pex"] == sha(b"version two of l0.pex")


def test_a_completed_restat_pass_removes_its_cursor(tmp_path, monkeypatch):
    _linked_pair(tmp_path)
    snap(tmp_path)
    monkeypatch.setattr(snapshot, "RESTAT_BOX", 0.0)   # one re-stat per comparison: the pass stops and leaves a cursor
    snap(tmp_path, now=later(1))
    cursor = store(tmp_path) / snapshot.RESTAT_CURSOR
    assert cursor.is_file()
    monkeypatch.setattr(snapshot, "RESTAT_BOX", 60.0)
    snap(tmp_path, now=later(2))
    assert not cursor.exists(), "a finished pass must not leave a cursor the next pass would start from"


def test_pruning_orders_snapshots_by_their_recorded_time_not_their_name(tmp_path, monkeypatch):
    """Review M2: names are local-time stamps (a DST fall-back hour reorders them); `t` is the epoch."""
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    older, newer = snapshot.manifest_names(store(tmp_path))
    mdir = store(tmp_path) / "manifests"
    # swap the names: the NEWER snapshot now carries the earlier stamp
    (mdir / older).rename(mdir / "tmp.json")
    (mdir / newer).rename(mdir / older)
    (mdir / "tmp.json").rename(mdir / newer)
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    (left,) = snapshot.manifest_names(store(tmp_path))
    assert snapshot.resolve(store(tmp_path), left) == {"Data/Scripts/a.pex": sha(b"a22")}


def test_pruning_removes_stale_part_files_beside_manifests_and_index(tmp_path):
    put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path)
    old_m = store(tmp_path) / "manifests" / "20200101_000000__snapshot.json.part-99999"
    old_i = store(tmp_path) / "index.json.part-99999"
    new_i = store(tmp_path) / "index.json.part-99998"
    for p in (old_m, old_i, new_i):
        p.write_bytes(b"x")
    t = at(2)
    os.utime(str(old_m), (t, t))
    os.utime(str(old_i), (t, t))
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert not old_m.exists() and not old_i.exists() and new_i.exists()


def test_an_unreadable_index_stops_blob_pruning(tmp_path, monkeypatch):
    full_maps(monkeypatch)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))
    (store(tmp_path) / "index.json").write_bytes(b"not json")
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert sha(b"a1") in blobs(tmp_path)
    assert "PRUNE-SKIPPED: the index is unreadable" in log_text(tmp_path)


def test_a_delta_whose_base_is_unreadable_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "PRUNE_EVERY", 10 ** 12)
    a = put(tmp_path, "Data/Scripts/a.pex", b"a1")
    put(tmp_path, "Data/Scripts/b.pex", b"b1")
    snap(tmp_path, now=at(40))
    a.write_bytes(b"a22")
    snap(tmp_path, now=at(30))                         # a delta on the first
    put(tmp_path, "Data/Scripts/b.pex", b"b22")
    snap(tmp_path, now=at(20))                         # another delta on the first
    base, d1, d2 = snapshot.manifest_names(store(tmp_path))
    (store(tmp_path) / "manifests" / base).write_bytes(b"not json")
    snapshot.prune(store(tmp_path), time.time(), audit_mod.Audit(str(tmp_path)))
    assert snapshot.manifest_names(store(tmp_path)) == [base, d1, d2], "never guess what a base-less delta held"
