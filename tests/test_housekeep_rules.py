"""housekeep rules after the 2026-10-28 incident: a 12-hour "idle subfolder" rule recycled a CommonLibSSE.lib that a
live build was still linking, on the rule's first, unreviewed run. These pin the three fixes.

    python -m pytest -q tests/test_housekeep_rules.py
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from conftest import REPO  # noqa: E402

sys.path.insert(0, str(REPO / "tools"))
import housekeep as h  # noqa: E402
import housekeep_scheduled as hs  # noqa: E402

DAY = 86400


def touch(p: Path, mtime=None, data=b"x" * 64):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()          # same-run scratch, removed at exit
        self.d = Path(self.tmp.name)
        self.out = self.d / "out"
        (self.d / "Data").mkdir()
        cfg_file = self.d / "hk.json"
        cfg_file.write_text(json.dumps({"agent_output": [str(self.out)], "live_roots": [str(self.d / "Data")],
                                        "root": str(self.d)}), encoding="utf-8")
        self.cfg = h.load_config(cfg_file)
        h._CMAKE_DIRS.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def planned(self):
        return {Path(a["path"]).name for a in h.plan(self.cfg)["actions"]}


class BuildProductsAreNeverIntermediates(Fixture):
    def test_a_library_in_an_idle_cmake_tree_is_kept(self):
        old = time.time() - 5 * DAY
        tree = self.out / "task" / "deps" / "sdk-build"
        for name in ("CMakeCache.txt", "CommonLibSSE.lib", "Actor.cpp.obj", "plugin.dll", "plugin.pdb"):
            touch(tree / name, old)
        self.assertEqual({"Actor.cpp.obj"}, self.planned())

    def test_build_exts_in_config_cannot_bring_lib_back(self):
        old = time.time() - 5 * DAY
        touch(self.out / "task" / "build" / "x.lib", old)
        self.cfg["build_exts"] = self.cfg["build_exts"] + [".lib"]
        self.assertEqual(set(), self.planned())


class NothingInsideAnActiveTask(Fixture):
    def test_an_idle_subfolder_of_an_active_task_is_kept(self):
        old = time.time() - 20 * 3600                      # idle 20 h: past the withdrawn 12 h rule, inside 48 h
        touch(self.out / "task" / "README.md")              # fresh write: task active
        touch(self.out / "task" / "idle" / "build" / "a.obj", old)
        touch(self.out / "task" / "idle" / "build" / "CMakeCache.txt", old)
        self.assertEqual(set(), self.planned())

    def test_an_old_plan_carrying_idle_hours_is_refused_on_an_active_task(self):
        old = time.time() - 20 * 3600
        f = self.out / "task" / "idle" / "build" / "a.obj"
        touch(f, old)
        touch(self.out / "task" / "README.md")
        action = {"class": "build", "path": str(f), "bytes": f.stat().st_size, "hash": h.blake(f),
                  "task": str(f.parent.parent), "idle_hours": 12}
        action_task = dict(action, task=str(self.out / "task"))
        self.assertIn("active", h.check(action_task, self.cfg, h.Guard(self.cfg), time.time()))


class ReadsCountAsUse(Fixture):
    def test_build_output_a_live_build_is_reading_is_kept(self):
        old = time.time() - 5 * DAY
        tree = self.out / "task" / "build"
        touch(tree / "CMakeCache.txt", old)
        touch(tree / "a.obj", old)
        os.utime(tree / "a.obj", (time.time() - 3600, old))   # read an hour ago, written 5 days ago
        self.assertEqual(set(), self.planned())


class UnreviewedCodeOnlyPlans(Fixture):
    def run_scheduled(self, approved):
        diag = self.d / "diag"
        self.cfg["approved_sha256"] = hs.code_sha256() if approved else "0" * 64
        touch(self.out / "task" / "build" / "a.obj", time.time() - 5 * DAY)
        with patch.object(hs, "DIAG", diag), patch.object(hs, "LOG", diag / "log"), \
                patch.object(hs, "PLAN", diag / "plan.json"), patch.object(hs, "REVIEW", diag / "REVIEW-NEEDED.md"), \
                patch.object(hs, "ALERTS", diag / "alerts.txt"), patch.object(hs, "SIZES", diag / "sizes.json"), \
                patch.object(hs, "running_games", lambda names: []), patch.object(hs, "low_priority", lambda: None), \
                patch.object(hs.housekeep, "load_config", lambda p: self.cfg), \
                patch.object(hs.housekeep, "apply", return_value=0) as apply, \
                patch.object(hs.housekeep, "recycle", return_value={}):
            hs.main([])
        return apply, diag

    def test_changed_code_plans_and_writes_the_review_but_never_applies(self):
        apply, diag = self.run_scheduled(approved=False)
        apply.assert_not_called()
        self.assertTrue((diag / "REVIEW-NEEDED.md").is_file())
        self.assertIn("a.obj", (diag / "plan.json").read_text(encoding="utf-8"))

    def test_approved_code_applies(self):
        apply, diag = self.run_scheduled(approved=True)
        apply.assert_called_once()
        self.assertFalse((diag / "REVIEW-NEEDED.md").exists())


if __name__ == "__main__":
    unittest.main()
