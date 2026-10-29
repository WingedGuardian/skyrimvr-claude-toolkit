"""housekeep archive: a DONE task folder is zipped to the NAS, verified there, stubbed, then its payload recycled.

    python -m pytest -q tests/test_housekeep_archive.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from conftest import REPO  # noqa: E402

sys.path.insert(0, str(REPO / "tools"))
import housekeep as h  # noqa: E402
import housekeep_archive as ha  # noqa: E402

DAY = 86400


def touch(p: Path, data=b"payload " * 32, mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))


def tree_hash(root: Path, skip=(ha.STUB,)):
    d = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in skip:
            d.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
    return d.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()             # same-run scratch, removed at exit
        self.d = Path(self.tmp.name)
        self.nas = self.d / "nas"
        self.bin = self.d / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def recycler(self, paths):                               # a stand-in Recycle Bin: tests never touch the real one
        out = {}
        for p in paths:
            dest = self.bin / (str(len(list(self.bin.iterdir()))) + "-" + Path(p).name)
            shutil.move(str(p), str(dest))
            out[str(p)] = None
        return out

    def task(self, name="task", mtime=None):
        t = self.d / "out" / name
        touch(t / "README.md", b"# notes", mtime)
        touch(t / "evidence" / "run.json", b"{}" * 400, mtime)
        touch(t / "build" / "big.bin", os.urandom(4096), mtime)
        return t


class ArchiveFolder(Base):
    def test_archive_roundtrip(self):
        t = self.task()
        before = tree_hash(t)
        rec = ha.archive_folder(t, self.nas, self.recycler)
        self.assertTrue(Path(rec["zip"]).is_file())
        self.assertIsNone(zipfile.ZipFile(rec["zip"]).testzip())
        self.assertEqual({"README.md", ha.STUB}, {p.name for p in t.iterdir()})   # payload gone, notes + stub kept
        ha.restore_archive(t / ha.STUB)
        self.assertEqual(before, tree_hash(t))

    def test_a_kept_file_deep_inside_survives(self):
        t = self.task()
        tracked = t / "evidence" / "engine-07" / "README.md"
        touch(tracked, b"committed receipt")
        touch(t / "evidence" / "engine-07" / "big.json", b"x" * 5000)
        ha.archive_folder(t, self.nas, self.recycler, extra_keep=[os.path.normcase(str(tracked))])
        self.assertEqual(b"committed receipt", tracked.read_bytes())
        self.assertFalse((t / "evidence" / "engine-07" / "big.json").exists())
        self.assertFalse((t / "evidence" / "run.json").exists())
        self.assertFalse((t / "build").exists())

    def test_nas_failure_leaves_local(self):
        t = self.task()
        before = tree_hash(t)
        with patch.object(zipfile.ZipFile, "testzip", return_value="build/big.bin"):
            with self.assertRaises(ha.ArchiveError):
                ha.archive_folder(t, self.nas, self.recycler)
        self.assertEqual(before, tree_hash(t))
        self.assertFalse((t / ha.STUB).exists())
        self.assertEqual([], list(self.nas.glob("*.partial")))
        self.assertEqual([], list(self.bin.iterdir()))

    def test_save_inside_refused(self):
        t = self.task()
        touch(t / "fixtures" / "x.ess")
        with self.assertRaises(ha.ArchiveError):
            ha.archive_folder(t, self.nas, self.recycler)
        self.assertFalse(self.nas.exists() and any(self.nas.iterdir()))

    def test_restore_refuses_existing(self):
        t = self.task()
        ha.archive_folder(t, self.nas, self.recycler)
        touch(t / "evidence" / "run.json", b"recreated, different")
        with self.assertRaises(ha.ArchiveError):
            ha.restore_archive(t / ha.STUB)

    def test_partial_recycle_is_written_into_the_stub(self):
        t = self.task()
        rec = ha.archive_folder(t, self.nas, lambda ps: {str(p): "in use" for p in ps})
        self.assertTrue(rec["recycle_failed"])
        self.assertIn("Still here", (t / ha.STUB).read_text(encoding="utf-8"))


class DoneTest(Base):
    def setUp(self):
        super().setUp()
        (self.d / "Data").mkdir()
        cfg_file = self.d / "hk.json"
        cfg_file.write_text(json.dumps({"agent_output": [str(self.d / "out")], "live_roots": [str(self.d / "Data")],
                                        "root": str(self.d), "archive_root": str(self.nas)}), encoding="utf-8")
        self.cfg = h.load_config(cfg_file)
        self.guard = h.Guard(self.cfg)

    def done(self, t, tokens=(), cwds=()):
        return h.is_done(t, 30, set(tokens), list(cwds), self.guard)

    def test_an_idle_plain_folder_is_done(self):
        self.assertEqual("", self.done(self.task()))

    def test_keep_pin_blocks(self):
        t = self.task()
        touch(t / h.KEEP_PIN, b"")
        self.assertIn("pinned", self.done(t))

    def test_live_state_entry_blocks(self):
        self.assertIn("Live state", self.done(self.task("souls-run"), tokens={"souls-run"}))

    def test_live_state_reads_entry_lines_only(self):
        (self.d / "CLAUDE.md").write_text(
            "## Live state\n\nInstructions mention `_CLAUDE_OUTPUT` as an example.\n\n"
            "- **Souls (date):** holding `souls-run`, restore before play.\n\n## Next section\n- `not-live`\n",
            encoding="utf-8")
        self.assertEqual({"souls-run"}, h.live_state_tokens(self.cfg))

    def test_rb_cwd_blocks(self):
        t = self.task()
        self.assertIn("rb job", self.done(t, cwds=[h._key(t / "build")]))

    def test_git_folder_never(self):
        t = self.task()
        (t / "repo" / ".git").mkdir(parents=True)
        self.assertIn("git", self.done(t))

    def test_save_blocks(self):
        t = self.task()
        touch(t / "a.skse")
        self.assertIn("save", self.done(t))

    def test_protected_glob_blocks(self):
        t = self.task()
        self.cfg["protected_globs"] = [str(t / "build" / "big.bin").replace("\\", "/").lower()]
        self.assertIn("protected_globs", h.is_done(t, 30, set(), [], h.Guard(self.cfg)))


class Selection(DoneTest):
    reads = True          # the probe's answer, pinned: CI runners may not record last-access times

    def plan_paths(self):
        with patch.object(h, "rb_job_cwds", lambda: []), patch.object(h, "live_state_tokens", lambda c: set()), \
                patch.object(h, "reads_recorded", lambda folder: self.reads):
            doc = h.plan(self.cfg, only={"archive"})
        return [Path(a["path"]).name for a in doc["actions"]], doc

    def test_without_read_times_only_closed_folders_archive(self):
        self.reads = False
        self.task("idle-month", mtime=time.time() - 30 * DAY)
        t = self.task("closed")
        touch(t / h.CLOSED_MARK, b"")
        for p in t.rglob("*"):
            if p.is_file():
                os.utime(p, (time.time() - 3 * DAY, time.time() - 3 * DAY))
        self.assertEqual(["closed"], self.plan_paths()[0])

    def test_the_read_probe_leaves_every_file_as_it_found_it(self):
        h._READS_RECORDED.clear()
        t = self.task("probed", mtime=time.time() - 30 * DAY)
        before = {p: (os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns) for p in t.rglob("*") if p.is_file()}
        self.assertIsInstance(h.reads_recorded(t), bool)
        after = {p: (os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns) for p in t.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_budget_orders_oldest_first(self):
        now = time.time()
        for name, age in (("a8", 8), ("b9", 9), ("c10", 10)):
            self.task(name, mtime=now - age * DAY)
        size = h.fingerprint(self.d / "out" / "a8")[1]
        self.cfg["budget_gib"] = (size * 1.5) / h.GIB          # only one folder fits: the two oldest must go
        names, _ = self.plan_paths()
        self.assertEqual(["c10", "b9"], names)

    def test_a_recent_read_keeps_a_folder(self):
        # 2026-10-28: a build reading (linking) a library leaves no mtime. Last-access time does count.
        t = self.task("read-yesterday", mtime=time.time() - 30 * DAY)
        lib = t / "build" / "big.bin"
        os.utime(lib, (time.time() - 1 * DAY, time.time() - 30 * DAY))
        self.assertEqual([], self.plan_paths()[0])

    def test_housekeep_hashing_does_not_count_as_a_read(self):
        # Asserted on the restore call itself: a filesystem that does not record reads (CI runners) would let a
        # missing restore pass unseen if we only compared access times.
        f = self.d / "x.bin"
        touch(f, mtime=time.time() - 30 * DAY)
        st = os.stat(f)
        calls = []
        real = os.utime
        with patch.object(h.os, "utime", lambda *a, **k: (calls.append((a, k)), real(*a, **k))[1]):
            h.blake(f)
        self.assertIn(((f,), {"ns": (st.st_atime_ns, st.st_mtime_ns)}), calls)
        self.assertEqual(st.st_atime_ns, os.stat(f).st_atime_ns)

    def test_nothing_below_min_idle_even_over_budget(self):
        # A root configured with a shorter age than min_idle_days must still wait min_idle_days.
        root = self.d / "fastroot"
        touch(root / "two-days" / "x.bin", mtime=time.time() - 2 * DAY)
        self.cfg["archive_roots"] = {str(root): 1}
        self.cfg["budget_gib"] = 1e-9
        self.assertEqual([], self.plan_paths()[0])

    def test_budget_never_shortens_an_archive_roots_age(self):
        root = self.d / "backups"                            # root age 14 > min_idle_days 7
        touch(root / "ten-days" / "x.bin", mtime=time.time() - 10 * DAY)
        touch(root / "twenty-days" / "x.bin", mtime=time.time() - 20 * DAY)
        self.cfg["archive_roots"] = {str(root): 14}
        self.cfg["budget_gib"] = 1e-9
        self.assertEqual(["twenty-days"], self.plan_paths()[0])

    def test_closed_marker_overrides_age(self):
        # Written 1 day ago (inside min_idle_days), last read 3 days ago: closed lets it go now.
        t = self.task("closed")
        touch(t / h.CLOSED_MARK, b"")
        for p in t.rglob("*"):
            if p.is_file():
                os.utime(p, (time.time() - 3 * DAY, time.time() - 1 * DAY))
        self.assertEqual(["closed"], self.plan_paths()[0])

    def test_closed_marker_still_waits_for_readers(self):
        t = self.task("closed-but-read")
        touch(t / h.CLOSED_MARK, b"")                       # just touched: read "now"
        self.assertEqual([], self.plan_paths()[0])

    def test_off_when_disabled(self):
        self.task("old", mtime=time.time() - 30 * DAY)
        self.cfg["archive_enabled"] = False
        self.assertEqual([], self.plan_paths()[0])

    def test_an_old_sweep_read_does_not_block(self):
        # A disk scan read every file 3 days ago; nothing wrote for 30. The 48 h read veto has passed.
        t = self.task("swept", mtime=time.time() - 30 * DAY)
        for p in t.rglob("*"):
            if p.is_file():
                os.utime(p, (time.time() - 3 * DAY, time.time() - 30 * DAY))
        self.assertEqual(["swept"], self.plan_paths()[0])

    def test_no_nas_means_a_local_archive(self):
        cfg_file = self.d / "local.json"
        cfg_file.write_text(json.dumps({"agent_output": [str(self.d / "out")], "root": str(self.d)}),
                            encoding="utf-8")
        cfg = h.load_config(cfg_file)
        self.assertEqual(self.d / ".claude" / "archive", Path(cfg["archive_root"]))
        self.assertEqual(90, cfg["archive_retention_days"])

    def plan_expire(self, days_old, retention):
        z = Path(self.cfg["archive_root"]) / "out" / "old.zip"
        touch(z, mtime=time.time() - days_old * DAY)
        self.cfg["archive_retention_days"] = retention
        return [Path(a["path"]).name for a in h.plan(self.cfg, only={"expire"})["actions"]]

    def test_local_zips_expire(self):
        self.assertEqual(["old.zip"], self.plan_expire(100, 90))

    def test_young_zips_and_keep_forever_stay(self):
        self.assertEqual([], self.plan_expire(30, 90))
        self.assertEqual([], self.plan_expire(400, 0))


if __name__ == "__main__":
    unittest.main()
