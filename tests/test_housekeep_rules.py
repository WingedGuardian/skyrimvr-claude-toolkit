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
import housekeep_daily as hd  # noqa: E402

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


class NoWindows(unittest.TestCase):
    """2026-10-29: a pythonw-hosted run opened one visible PowerShell window per recycle call and stole focus every
    half second. Every process housekeep starts must carry NO_WINDOW."""

    def test_every_child_process_is_started_without_a_window(self):
        import ast
        here = Path(h.__file__).resolve().parent
        bare = []
        for name in ("housekeep.py", "housekeep_daily.py", "housekeep_archive.py"):
            tree = ast.parse((here / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in ("run", "Popen", "call", "check_call", "check_output") \
                        and getattr(node.func.value, "id", "") == "subprocess":
                    if not any(k.arg is None and "NO_WINDOW" in ast.unparse(k.value) or k.arg == "creationflags"
                               for k in node.keywords):
                        bare.append("{}:{}".format(name, node.lineno))
        self.assertEqual([], bare)
        self.assertEqual({"creationflags": 0x08000000} if os.name == "nt" else {}, h.NO_WINDOW)


class DailyRunGovernance(Fixture):
    """User ruling 2026-10-29: cleanup runs only from a Claude Code session, is announced in it BEFORE it starts,
    applies only approved code, is time-boxed, and never makes Windows ask "delete permanently?"."""

    def setUp(self):
        super().setUp()
        self.cfg["approved_sha256"] = hd.code_sha256()
        touch(self.out / "task" / "build" / "a.obj", time.time() - 5 * DAY)
        touch(self.out / "task" / "build" / "CMakeCache.txt", time.time() - 5 * DAY)

    def quiet(self):
        return patch.object(hd, "running_games", lambda names: []), patch.object(hd, "low_priority", lambda: None)

    def test_a_run_is_due_only_once_per_window(self):
        g, l = self.quiet()
        with g, l:
            self.assertEqual("", hd.due(self.cfg))
            hd.f(self.cfg, "STAMP").parent.mkdir(parents=True, exist_ok=True)
            hd.f(self.cfg, "STAMP").write_text(json.dumps({"started": time.time() - 3600}), encoding="utf-8")
            self.assertIn("ran", hd.due(self.cfg))

    def test_a_running_game_postpones_it(self):
        with patch.object(hd, "running_games", lambda names: ["SkyrimVR.exe"]):
            self.assertIn("SkyrimVR.exe", hd.due(self.cfg))
            self.assertEqual("", hd.announcement(self.cfg))

    def test_the_announcement_names_what_starts_and_who_starts_it(self):
        g, l = self.quiet()
        with g, l:
            line = hd.announcement(self.cfg)
        self.assertIn("HOUSEKEEP", line)
        self.assertIn(self.cfg["approved_sha256"][:8], line)
        self.assertIn(str(hd.f(self.cfg, "LOG")), line)

    def test_changed_code_is_announced_as_plan_only_and_never_applies(self):
        self.cfg["approved_sha256"] = "0" * 64
        g, l = self.quiet()
        with g, l, patch.object(h, "apply") as apply, patch.object(h, "recycle", return_value={}):
            self.assertIn("only PLANS", hd.announcement(self.cfg))
            hd.run(self.cfg)
        apply.assert_not_called()
        self.assertTrue(hd.f(self.cfg, "REVIEW").is_file())

    def test_approved_code_applies_within_the_time_box(self):
        g, l = self.quiet()
        with g, l, patch.object(h, "apply", return_value={"gib": 0, "failed": 0, "deferred": 0}) as apply, \
                patch.object(h, "recycle", return_value={}):
            summary = hd.run(self.cfg)
        apply.assert_called_once()
        self.assertLessEqual(apply.call_args.kwargs["deadline"], time.time() + self.cfg["daily_minutes"] * 60)
        self.assertIn("today's run", summary)

    def test_session_runs_the_daily_only_when_due_and_leaves_its_summary_as_the_next_note(self):
        state = self.d / "note.txt"
        g, l = self.quiet()
        with g, l, patch.object(h, "_daily_module", lambda: hd), patch.object(h, "sweep_temp",
                lambda cfg: {"rows": {"recycled": 0, "failed": 0}}), \
                patch.object(h, "apply", return_value={"gib": 1.5, "failed": 0, "deferred": 0}) as apply:
            h.session(self.cfg, state)
            self.assertIn("1.50 GiB done", state.read_text(encoding="utf-8"))
            h.session(self.cfg, state)                 # within the window: nothing runs a second time
        apply.assert_called_once()

    def test_the_deadline_defers_instead_of_running_long(self):
        doc = h.plan(self.cfg)
        self.assertTrue(doc["actions"])
        res = h.apply(doc, self.cfg, recycler=lambda ps: {str(x): None for x in ps}, deadline=time.time() - 1,
                      summary=True)
        self.assertEqual(len(doc["actions"]), res["deferred"])
        self.assertTrue((self.out / "task" / "build" / "a.obj").exists())

    def test_recycling_is_batched_not_one_shell_call_per_item(self):
        calls = []
        acts = [{"class": "build", "path": str(self.d / "f{}.obj".format(i)), "bytes": 1, "hash": "", "task": ""}
                for i in range(7)]
        with patch.object(h, "RECYCLE_BATCH", 3), patch.object(h, "check", lambda *a, **k: ""):
            h.apply({"actions": acts}, self.cfg, recycler=lambda ps: (calls.append(len(ps)), {p: None for p in ps})[1])
        self.assertEqual([3, 3, 1], calls)


class NeverAskDeletePermanently(unittest.TestCase):
    """Items the bin cannot take made Windows pop "delete permanently?" on the user's desktop (2026-10-29)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_network_path_is_refused(self):
        # A UNC path only means something on Windows (POSIX abspath makes it relative); pin abspath so the
        # network check itself is what is tested on every runner.
        with patch.object(h.os.path, "abspath", lambda s: s):
            self.assertIn("network", h.bin_refusal("\\\\server\\share\\x.txt"))

    def test_a_folder_holding_an_over_long_path_is_refused(self):
        deep = self.d.joinpath(*(["a" * 40] * 7), "f.txt")
        touch(deep)
        self.assertIn("longer than", h.bin_refusal(self.d / ("a" * 40)))

    def test_something_bigger_than_the_bin_is_refused(self):
        touch(self.d / "big.bin", data=b"x" * 4096)
        with patch.object(h, "bin_capacity", lambda p: 1024):
            self.assertIn("bigger than", h.bin_refusal(self.d / "big.bin"))
        self.assertEqual("", h.bin_refusal(self.d / "big.bin") if os.name == "nt" else "")

    @unittest.skipUnless(os.name == "nt", "the Recycle Bin path is Windows-only")
    def test_a_refused_item_never_reaches_the_shell(self):
        touch(self.d / "big.bin", data=b"x" * 4096)
        sent = []
        with patch.object(h, "bin_capacity", lambda p: 1024), \
                patch.object(h.subprocess, "run", lambda *a, **k: sent.append(k.get("input")) or None):
            res = h.recycle([self.d / "big.bin"])
        self.assertEqual([], sent)
        self.assertIn("bigger than", res[str(self.d / "big.bin")])


if __name__ == "__main__":
    unittest.main()
