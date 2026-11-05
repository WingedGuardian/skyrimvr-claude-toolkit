"""Test context: synthetic game folders only. Nothing here starts a process but tasklist, and nothing touches a real game."""
import json
import msvcrt
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context"))
import context_store as tc

OWNER = "owner-a"
REAL_RUNNING = tc.running_processes


def write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        # tasklist costs half a second a call; the tests about processes put the real one back.
        patcher = mock.patch.object(tc, "running_processes", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        base = Path(self._tmp.name).resolve()
        self.game, self.state, self.store = base / "game", base / "state", base / "store"
        self.config = write(self.game / "Data/N/config/a.yaml", b"top:\n  enabled: true\n")
        self.db = write(self.game / "Data/N/data/n.db", b"database")
        self.sidecar = write(self.game / "Data/N/data/n.db-wal", b"wal")
        self.outside = write(self.game / "Data/other.ini", b"not in scope")
        self.plugin_source = write(base / "build/Probe.dll", b"probe")
        self.status = write(base / "status.json", b'{\n  "shared": null,\n  "live": null\n}\n')
        self.profile = {
            "schema": 1, "roots": ["Data/N/config", "Data/N/data"],
            "edits": [{"path": "Data/N/config/a.yaml", "old": "enabled: true", "new": "enabled: false"}],
            "additions": [{"source": str(self.plugin_source), "sha256": tc.sha256(self.plugin_source), "target": "Data/Plugins/Probe.dll"}],
            "cold_processes": ["no-such-image-for-this-test.exe"],
            "slots": {"file": str(self.status), "keys": ["shared", "live"]},
        }

    def prepare(self, owner=OWNER, run="r1", profile=None):
        return tc.prepare(self.game, self.state, self.store, owner, run, profile or self.profile)

    def restore(self, owner=OWNER):
        return tc.restore(self.game, self.state, self.store, owner)

    def tree(self):
        return {p.relative_to(self.game).as_posix(): p.read_bytes() for p in self.game.rglob("*") if p.is_file()}

    def slots(self):
        return json.loads(self.status.read_text(encoding="utf-8"))


class Prepare(Base):
    def test_prepare_applies_edit_and_addition_and_takes_slots(self):
        self.prepare()
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n")
        self.assertEqual((self.game / "Data/Plugins/Probe.dll").read_bytes(), b"probe")
        self.assertEqual(self.slots(), {"shared": OWNER, "live": OWNER})
        self.assertEqual(tc.status(self.state)["phase"], "prepared")

    def test_identical_bytes_are_stored_once_across_runs(self):
        self.prepare()
        self.restore()
        blobs = sorted(p.name for p in self.store.iterdir())
        self.prepare(run="r2")
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), blobs)

    def test_blobs_are_read_only(self):
        self.prepare()
        for blob in self.store.iterdir():
            self.assertFalse(blob.stat().st_mode & stat.S_IWRITE)

    def test_no_link_is_made_to_a_live_file(self):
        self.prepare()
        self.assertEqual(self.db.stat().st_nlink, 1)

    def test_second_owner_is_refused(self):
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "active"):
            self.prepare(owner="owner-b", run="r2")

    def test_foreign_slot_is_refused_and_nothing_changes(self):
        self.status.write_bytes(b'{\n  "shared": "other",\n  "live": null\n}\n')
        before = self.tree()
        with self.assertRaisesRegex(tc.Refused, "held"):
            self.prepare()
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.status.read_bytes(), b'{\n  "shared": "other",\n  "live": null\n}\n')
        self.assertIsNone(tc.status(self.state))

    def test_running_process_is_refused(self):
        profile = dict(self.profile, cold_processes=[Path(sys.executable).name])
        before = self.tree()
        with mock.patch.object(tc, "running_processes", REAL_RUNNING):
            with self.assertRaisesRegex(tc.Refused, "running"):
                self.prepare(profile=profile)
        self.assertEqual(self.tree(), before)
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_unknown_process_state_is_refused(self):
        with mock.patch.object(tc.subprocess, "run", side_effect=OSError("no tasklist")):
            with self.assertRaisesRegex(tc.Refused, "cannot tell"):
                REAL_RUNNING(["a.exe"])

    def test_process_started_between_the_checks_is_seen_after_taking_ownership(self):
        with mock.patch.object(tc, "running_processes", side_effect=[[], ["SkyrimVR.exe"]]):
            with self.assertRaisesRegex(tc.Refused, "running"):
                self.prepare()
        self.assertEqual(self.slots(), {"shared": None, "live": None})
        self.assertIsNone(tc.status(self.state))

    def test_run_name_is_never_reused(self):
        self.prepare()
        self.restore()
        with self.assertRaisesRegex(tc.Refused, "reused"):
            self.prepare()
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_empty_process_list_is_refused(self):
        with self.assertRaisesRegex(tc.Refused, "cold_processes"):
            self.prepare(profile=dict(self.profile, cold_processes=[]))

    def test_file_held_open_by_a_writer_is_refused(self):
        with open(self.db, "r+b"):
            with self.assertRaisesRegex(tc.Refused, "in use"):
                self.prepare()
        self.assertEqual(self.slots(), {"shared": None, "live": None})
        self.assertIsNone(tc.status(self.state))

    def test_missing_root_is_refused(self):
        with self.assertRaisesRegex(tc.Refused, "root"):
            self.prepare(profile=dict(self.profile, roots=["Data/absent"]))

    def test_path_escapes_are_refused(self):
        for bad in ("../outside", "C:/Windows", "Data/../../outside"):
            with self.assertRaisesRegex(tc.Refused, "inside"):
                self.prepare(profile=dict(self.profile, roots=[bad]))

    def test_store_or_state_inside_the_game_is_refused(self):
        with self.assertRaisesRegex(tc.Refused, "game"):
            tc.prepare(self.game, self.game / "state", self.store, OWNER, "r", self.profile)
        with self.assertRaisesRegex(tc.Refused, "game"):
            tc.prepare(self.game, self.state, self.game / "Data/store", OWNER, "r", self.profile)

    def test_hard_link_in_scope_is_refused(self):
        os.link(self.db, self.game / "Data/N/data/linked.db")
        with self.assertRaisesRegex(tc.Refused, "link"):
            self.prepare()

    def test_junction_in_scope_is_refused(self):
        target = self.game / "Data/elsewhere"
        target.mkdir()
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.game / "Data/N/data/j"), str(target)], capture_output=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        with self.assertRaisesRegex(tc.Refused, "link"):
            self.prepare()

    def test_addition_through_a_junction_is_refused(self):
        target = self.game / "Data/elsewhere"
        target.mkdir()
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.game / "Data/Plugins"), str(target)], capture_output=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        with self.assertRaisesRegex(tc.Refused, "link"):
            self.prepare()
        self.assertFalse((target / "Probe.dll").exists())

    def test_edit_that_does_not_match_exactly_once_changes_nothing(self):
        before = self.tree()
        profile = dict(self.profile, edits=[{"path": "Data/N/config/a.yaml", "old": "absent", "new": "y"}])
        with self.assertRaisesRegex(tc.Refused, "exactly once"):
            self.prepare(profile=profile)
        self.assertEqual(self.tree(), before)
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_edit_outside_the_captured_roots_is_refused(self):
        profile = dict(self.profile, edits=[{"path": "Data/other.ini", "old": "not", "new": "now"}])
        with self.assertRaisesRegex(tc.Refused, "captured"):
            self.prepare(profile=profile)
        self.assertEqual(self.outside.read_bytes(), b"not in scope")

    def test_addition_over_an_existing_file_is_refused(self):
        write(self.game / "Data/Plugins/Probe.dll", b"someone else's")
        before = self.tree()
        with self.assertRaisesRegex(tc.Refused, "exists"):
            self.prepare()
        self.assertEqual(self.tree(), before)

    def test_addition_with_the_wrong_hash_is_refused(self):
        profile = dict(self.profile, additions=[dict(self.profile["additions"][0], sha256="0" * 64)])
        with self.assertRaisesRegex(tc.Refused, "declared hash"):
            self.prepare(profile=profile)
        self.assertFalse((self.game / "Data/Plugins/Probe.dll").exists())

    def test_damaged_reference_is_refused_at_capture(self):
        self.prepare()
        self.restore()
        blob = self.store / tc.sha256_bytes(b"database")
        os.chmod(blob, stat.S_IWRITE)
        blob.write_bytes(b"rotten")
        with self.assertRaisesRegex(tc.Refused, "damaged"):
            self.prepare(run="r2")
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_refusal_while_applying_rolls_back(self):
        before = self.tree()
        real = tc.replace_verified
        calls = {"n": 0}

        def refuses_second(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise tc.Refused("simulated: addition could not be placed")
            return real(*args, **kwargs)
        with mock.patch.object(tc, "replace_verified", refuses_second):
            with self.assertRaisesRegex(tc.Refused, "simulated"):
                self.prepare()
        self.assertEqual(self.tree(), before)
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_manifest_exists_before_the_first_live_change(self):
        seen = []
        real = tc.replace_verified

        def spy(*args, **kwargs):
            seen.append((self.state / "r1" / tc.MANIFEST).is_file())
            return real(*args, **kwargs)
        with mock.patch.object(tc, "replace_verified", spy):
            self.prepare()
        self.assertTrue(seen)
        self.assertTrue(all(seen))


class Restore(Base):
    def test_full_cycle_restores_exact_bytes_and_times(self):
        before = self.tree()
        time_before = self.db.stat().st_mtime_ns
        self.prepare()
        self.db.write_bytes(b"changed by the game")
        os.utime(self.config, ns=(1, 1))
        receipt = self.restore()
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.db.stat().st_mtime_ns, time_before)
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.slots(), {"shared": None, "live": None})
        self.assertTrue(receipt["verified"])

    def test_file_created_during_the_test_is_moved_to_evidence(self):
        self.prepare()
        created = write(self.game / "Data/N/data/new/n2.db", b"new")
        receipt = self.restore()
        self.assertFalse(created.exists())
        self.assertFalse(created.parent.exists(), "a folder the test created goes too")
        self.assertEqual((self.state / "r1/evidence/created/Data/N/data/new/n2.db").read_bytes(), b"new")
        self.assertEqual(receipt["created"], ["Data/N/data/new/n2.db"])

    def test_changed_file_is_kept_as_evidence_before_restoring(self):
        self.prepare()
        self.db.write_bytes(b"changed")
        receipt = self.restore()
        self.assertEqual((self.state / "r1/evidence/changed/Data/N/data/n.db").read_bytes(), b"changed")
        self.assertIn("Data/N/data/n.db", receipt["changed"])
        self.assertEqual(self.db.read_bytes(), b"database")

    def test_deleted_file_comes_back_and_is_reported(self):
        self.prepare()
        self.sidecar.unlink()
        receipt = self.restore()
        self.assertEqual(self.sidecar.read_bytes(), b"wal")
        self.assertIn("Data/N/data/n.db-wal", receipt["missing"])

    def test_deleted_folder_comes_back(self):
        self.prepare()
        for p in self.db.parent.iterdir():
            p.unlink()
        self.db.parent.rmdir()
        self.restore()
        self.assertEqual(self.db.read_bytes(), b"database")

    def test_files_outside_scope_are_left_alone(self):
        self.prepare()
        self.outside.write_bytes(b"an unrelated change")
        unrelated = write(self.game / "Data/unrelated-new.txt", b"keep me")
        self.restore()
        self.assertEqual(self.outside.read_bytes(), b"an unrelated change")
        self.assertEqual(unrelated.read_bytes(), b"keep me")

    def test_addition_is_removed_and_its_folder_if_created(self):
        self.prepare()
        self.restore()
        self.assertFalse((self.game / "Data/Plugins").exists())

    def test_addition_folder_that_already_existed_stays(self):
        keep = write(self.game / "Data/Plugins/Keep.dll", b"keep")
        self.prepare()
        self.restore()
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertFalse((self.game / "Data/Plugins/Probe.dll").exists())

    def test_altered_addition_goes_to_evidence_not_left_live(self):
        self.prepare()
        target = self.game / "Data/Plugins/Probe.dll"
        target.write_bytes(b"altered")
        receipt = self.restore()
        self.assertFalse(target.exists())
        self.assertEqual((self.state / "r1/evidence/altered-additions/Data/Plugins/Probe.dll").read_bytes(), b"altered")
        self.assertEqual(receipt["altered_additions"], ["Data/Plugins/Probe.dll"])

    def test_another_owner_cannot_restore(self):
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "belongs"):
            self.restore(owner="owner-b")
        self.assertEqual(tc.status(self.state)["owner"], OWNER)
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n", "nothing was restored for the wrong owner")

    def test_restore_is_refused_while_the_game_runs(self):
        self.prepare()
        manifest = self.state / "r1" / tc.MANIFEST
        with mock.patch.object(tc, "running_processes", return_value=["SkyrimVR.exe"]):
            with self.assertRaisesRegex(tc.Refused, "running"):
                self.restore()
        self.assertTrue(manifest.is_file())
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n")

    def test_restore_is_refused_while_a_writer_holds_a_file(self):
        self.prepare()
        with open(self.db, "r+b"):
            with self.assertRaisesRegex(tc.Refused, "in use"):
                self.restore()
        self.assertEqual(tc.status(self.state)["phase"], "prepared")

    def test_tampered_manifest_is_refused(self):
        self.prepare()
        manifest = self.state / "r1" / tc.MANIFEST
        os.chmod(manifest, stat.S_IWRITE)
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(OWNER, OWNER + " "), encoding="utf-8")
        with self.assertRaisesRegex(tc.Refused, "manifest"):
            self.restore()

    def test_corrupt_blob_is_refused_for_that_file_and_keeps_recovery(self):
        self.prepare()
        self.db.write_bytes(b"changed")
        blob = self.store / tc.sha256_bytes(b"database")
        os.chmod(blob, stat.S_IWRITE)
        blob.write_bytes(b"rotten")
        receipt = self.restore()
        self.assertFalse(receipt["verified"])
        self.assertIn("Data/N/data/n.db", receipt["failed"])
        self.assertEqual(self.db.read_bytes(), b"changed", "a bad reference must never overwrite the live file")
        self.assertEqual(tc.status(self.state)["phase"], "restore-incomplete")
        self.assertEqual(self.slots(), {"shared": OWNER, "live": OWNER}, "ownership stays until recovery succeeds")

    def test_read_only_original_comes_back_read_only(self):
        os.chmod(self.config, stat.S_IREAD)
        self.prepare()
        self.assertFalse(self.config.stat().st_mode & stat.S_IWRITE, "the edit keeps the attribute")
        self.assertTrue(self.restore()["verified"])
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: true\n")
        self.assertFalse(self.config.stat().st_mode & stat.S_IWRITE)
        os.chmod(self.config, stat.S_IWRITE)

    def test_crash_after_slots_released_still_finishes(self):
        self.prepare()
        record = tc.status(self.state)
        tc.change_slots(record["slots"], OWNER, release=True)
        self.assertTrue(self.restore()["verified"])
        self.assertIsNone(tc.status(self.state))

    def test_crash_before_slots_taken_does_not_strand_recovery(self):
        real = tc.change_slots

        def dies(*args, **kwargs):
            if not kwargs.get("release"):
                raise KeyboardInterrupt
            return real(*args, **kwargs)
        with mock.patch.object(tc, "change_slots", dies):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertEqual(tc.status(self.state)["phase"], "preparing", "it cannot know whether the slots were written")
        self.restore()
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_crash_between_manifest_and_its_hash_recovers(self):
        before = self.tree()
        real = tc.write_durable

        def dies(path, data):
            if path.name == tc.MANIFEST_HASH:
                raise KeyboardInterrupt
            return real(path, data)
        with mock.patch.object(tc, "write_durable", dies):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertEqual(tc.status(self.state)["phase"], "preparing")
        self.restore()
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.tree(), before)

    def test_foreign_holder_stops_the_release(self):
        self.prepare()
        self.status.write_bytes(self.status.read_bytes().replace(OWNER.encode(), b"thief"))
        with self.assertRaisesRegex(tc.Refused, "held"):
            self.restore()
        self.assertIsNotNone(tc.status(self.state))

    def test_interrupted_restore_can_run_again(self):
        self.prepare()
        self.db.write_bytes(b"changed")
        self.sidecar.write_bytes(b"changed too")
        real = tc.replace_verified
        calls = {"n": 0}

        def dies_once(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return real(*args, **kwargs)
        with mock.patch.object(tc, "replace_verified", dies_once):
            with self.assertRaises(KeyboardInterrupt):
                self.restore()
        self.assertIsNotNone(tc.status(self.state))
        receipt = self.restore()
        self.assertTrue(receipt["verified"])
        self.assertEqual(self.db.read_bytes(), b"database")
        self.assertEqual(self.sidecar.read_bytes(), b"wal")

    def test_interrupted_prepare_after_capture_recovers_through_restore(self):
        before = self.tree()
        real = tc.replace_verified

        def dies(*args, **kwargs):
            raise KeyboardInterrupt
        with mock.patch.object(tc, "replace_verified", dies):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertEqual(tc.status(self.state)["phase"], "captured")
        with self.assertRaisesRegex(tc.Refused, "active"):
            self.prepare(run="r2")
        self.assertIs(real, tc.replace_verified)
        self.assertTrue(self.restore()["verified"])
        self.assertEqual(self.tree(), before)

    def test_interrupted_prepare_before_capture_releases(self):
        before = self.tree()
        with mock.patch.object(tc, "capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertEqual(tc.status(self.state)["phase"], "preparing")
        receipt = self.restore()
        self.assertEqual(receipt["phase_at_restore"], "preparing")
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.slots(), {"shared": None, "live": None})
        self.assertIsNone(tc.status(self.state))

    def test_failed_prepare_after_capture_rolls_back_by_itself(self):
        before = self.tree()
        profile = dict(self.profile, edits=[self.profile["edits"][0], {"path": "Data/N/config/a.yaml", "old": "absent-now", "new": "y"}])
        with self.assertRaisesRegex(tc.Refused, "exactly once"):
            self.prepare(profile=profile)
        self.assertEqual(self.tree(), before)
        self.assertIsNone(tc.status(self.state))

    def test_receipt_survives_and_names_the_manifest(self):
        self.prepare()
        self.restore()
        receipt = json.loads((self.state / "r1" / tc.RECEIPT).read_text(encoding="utf-8"))
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["manifest_sha256"], tc.sha256(self.state / "r1" / tc.MANIFEST))


class Check(Base):
    def everything(self):
        return self.tree(), self.status.read_bytes(), sorted(p.name for p in self.state.parent.iterdir())

    def check(self, profile=None, owner=OWNER, run="r1"):
        return tc.check(self.game, self.state, self.store, owner, run, profile or self.profile)

    def test_a_passing_check_writes_nothing_anywhere(self):
        before = self.everything()
        report = self.check()
        self.assertEqual(self.everything(), before)
        self.assertEqual(report, {"files": 3, "bytes": 32, "edits": ["Data/N/config/a.yaml"], "additions": ["Data/Plugins/Probe.dll"], "would_start": True})

    def test_a_passing_check_is_followed_by_a_prepare_that_passes(self):
        self.check()
        self.prepare()

    def test_check_refuses_a_running_game(self):
        with mock.patch.object(tc, "running_processes", return_value=["SkyrimVR.exe"]):
            with self.assertRaisesRegex(tc.Refused, "running"):
                self.check()

    def test_check_refuses_an_active_context(self):
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "active"):
            self.check(run="r2")

    def test_check_refuses_a_foreign_slot(self):
        self.status.write_bytes(b'{\n  "shared": "other",\n  "live": null\n}\n')
        with self.assertRaisesRegex(tc.Refused, "held"):
            self.check()

    def test_check_refuses_a_held_file(self):
        with open(self.db, "r+b"):
            with self.assertRaisesRegex(tc.Refused, "in use"):
                self.check()

    def test_check_refuses_an_edit_that_would_refuse(self):
        profile = dict(self.profile, edits=[{"path": "Data/N/config/a.yaml", "old": "absent", "new": "y"}])
        with self.assertRaisesRegex(tc.Refused, "exactly once"):
            self.check(profile=profile)


class Verify(Base):
    def test_verify_reports_every_difference_without_changing_anything(self):
        self.prepare()
        self.db.write_bytes(b"changed")
        self.sidecar.unlink()
        write(self.game / "Data/N/data/extra", b"extra")
        report = tc.verify(self.game, self.state)
        self.assertEqual(report["changed"], ["Data/N/config/a.yaml", "Data/N/data/n.db"])
        self.assertEqual(report["missing"], ["Data/N/data/n.db-wal"])
        self.assertEqual(report["created"], ["Data/N/data/extra"])
        self.assertEqual(self.db.read_bytes(), b"changed")

    def test_verify_without_an_active_context_is_refused(self):
        with self.assertRaisesRegex(tc.Refused, "no active"):
            tc.verify(self.game, self.state)


class Slots(Base):
    def test_unexpected_layout_is_refused(self):
        self.status.write_bytes(b'{"shared":null,"live":null}')
        with self.assertRaisesRegex(tc.Refused, "layout"):
            self.prepare()

    def test_missing_key_is_refused(self):
        self.status.write_bytes(b'{\n  "shared": null\n}\n')
        with self.assertRaisesRegex(tc.Refused, "held"):
            self.prepare()

    def test_other_fields_are_untouched(self):
        self.status.write_bytes(b'{\n  "note":  "spacing   kept",\n  "shared": null,\n  "live": null\n}\n')
        self.prepare()
        self.restore()
        self.assertEqual(self.status.read_bytes(), b'{\n  "note":  "spacing   kept",\n  "shared": null,\n  "live": null\n}\n')

    def test_a_context_without_slots_is_allowed(self):
        profile = dict(self.profile)
        del profile["slots"]
        self.prepare(profile=profile)
        self.assertTrue(self.restore()["verified"])


class ReviewFindings(Base):
    """Each of these passed unnoticed before the independent review of 2026-11-04."""

    def test_restore_against_another_game_is_refused(self):
        other = self.game.parent / "other-game"
        keep = write(other / "Data/N/config/keep.ini", b"keep")
        write(other / "Data/N/data/keep.db", b"keep")
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "game"):
            tc.restore(other, self.state, self.store, OWNER)
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n")
        self.assertEqual(self.slots(), {"shared": OWNER, "live": OWNER})

    def test_verify_against_another_game_is_refused(self):
        other = self.game.parent / "other-game"
        write(other / "Data/N/config/keep.ini", b"keep")
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "game"):
            tc.verify(other, self.state)

    def test_restore_from_another_store_is_refused(self):
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "store"):
            tc.restore(self.game, self.state, self.store.parent / "other-store", OWNER)
        self.assertIsNotNone(tc.status(self.state))

    def test_second_state_folder_on_the_same_game_is_refused(self):
        self.prepare()
        with self.assertRaisesRegex(tc.Refused, "active"):
            tc.prepare(self.game, self.state.parent / "state2", self.store, OWNER, "r2", self.profile)
        self.assertIsNone(tc.status(self.state.parent / "state2"))

    def test_second_state_folder_without_slots_is_refused_for_another_owner(self):
        profile = dict(self.profile)
        del profile["slots"]
        self.prepare(profile=profile)
        with self.assertRaisesRegex(tc.Refused, "active"):
            tc.prepare(self.game, self.state.parent / "state2", self.store, "owner-b", "r2", profile)

    def test_the_game_marker_exists_only_while_active(self):
        marker = self.game / tc.GAME_MARKER
        self.prepare()
        self.assertTrue(marker.is_file())
        self.restore()
        self.assertFalse(marker.exists())

    def test_interrupted_prepare_leaves_a_marker_that_restore_clears(self):
        with mock.patch.object(tc, "capture", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertTrue((self.game / tc.GAME_MARKER).exists())
        self.restore()
        self.assertFalse((self.game / tc.GAME_MARKER).exists())

    def test_restore_with_a_marker_from_another_state_folder_is_refused(self):
        self.prepare()
        marker = self.game / tc.GAME_MARKER
        marker.write_text(json.dumps({"owner": "owner-b", "run": "r9", "state": str(self.state.parent / "state2")}), encoding="utf-8")
        with self.assertRaisesRegex(tc.Refused, "marker"):
            self.restore()
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n")

    def test_slot_already_held_by_this_owner_is_refused_and_kept(self):
        self.status.write_bytes(b'{\n  "shared": "owner-a",\n  "live": null\n}\n')
        with self.assertRaisesRegex(tc.Refused, "held"):
            self.prepare()
        self.assertEqual(self.slots(), {"shared": OWNER, "live": None}, "a slot this context did not take is not its to release")

    def test_foreign_holder_refuses_before_any_restoring(self):
        self.prepare()
        self.db.write_bytes(b"holder's work")
        created = write(self.game / "Data/N/data/holder.db", b"holder's file")
        self.status.write_bytes(self.status.read_bytes().replace(OWNER.encode(), b"thief"))
        with self.assertRaisesRegex(tc.Refused, "held"):
            self.restore()
        self.assertEqual(self.db.read_bytes(), b"holder's work")
        self.assertEqual(created.read_bytes(), b"holder's file")

    def test_deleted_empty_folder_comes_back(self):
        empty = self.game / "Data/N/data/empty"
        empty.mkdir()
        self.prepare()
        empty.rmdir()
        self.assertEqual(tc.verify(self.game, self.state)["missing_folders"], ["Data/N/data/empty"])
        self.assertTrue(self.restore()["verified"])
        self.assertTrue(empty.is_dir())

    def test_removed_root_comes_back_whole(self):
        before = self.tree()
        self.prepare()
        for path in self.db.parent.iterdir():
            path.unlink()
        self.db.parent.rmdir()
        self.assertTrue(self.restore()["verified"])
        self.assertEqual(self.tree(), before)

    def test_cleared_read_only_attribute_on_an_unedited_file_is_restored(self):
        os.chmod(self.db, stat.S_IREAD)
        self.prepare()
        os.chmod(self.db, stat.S_IWRITE)
        self.assertEqual(tc.verify(self.game, self.state)["attributes"], ["Data/N/data/n.db"])
        self.assertTrue(self.restore()["verified"])
        self.assertFalse(self.db.stat().st_mode & stat.S_IWRITE)
        os.chmod(self.db, stat.S_IWRITE)

    def test_stranded_addition_temporary_file_is_removed(self):
        self.prepare()
        stray = write(self.game / "Data/Plugins/Probe.dll.context-incoming", b"partial")
        os.chmod(stray, stat.S_IREAD)
        self.assertTrue(self.restore()["verified"])
        self.assertFalse(stray.exists())

    def test_file_replaced_by_a_folder_heals_on_the_first_run(self):
        self.prepare()
        self.db.unlink()
        inner = write(self.db / "inner.txt", b"inner")
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.db.read_bytes(), b"database")
        self.assertEqual((self.state / "r1/evidence/created/Data/N/data/n.db/inner.txt").read_bytes(), b"inner")
        self.assertFalse(inner.exists())

    def test_folder_replaced_by_a_file_heals_on_the_first_run(self):
        nested = write(self.game / "Data/N/data/sub/x.db", b"x")
        self.prepare()
        nested.unlink()
        nested.parent.rmdir()
        write(self.game / "Data/N/data/sub", b"now a file")
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(nested.read_bytes(), b"x")

    def test_root_replaced_by_a_file_heals(self):
        before = self.tree()
        self.prepare()
        for path in self.db.parent.iterdir():
            path.unlink()
        self.db.parent.rmdir()
        write(self.db.parent, b"now a file")
        self.assertIn("Data/N/data", tc.verify(self.game, self.state)["created"])
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.tree(), before)

    def test_addition_replaced_by_a_folder_heals(self):
        self.prepare()
        target = self.game / "Data/Plugins/Probe.dll"
        target.unlink()
        write(target / "inner.txt", b"inner")
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertFalse(target.exists())
        self.assertEqual((self.state / "r1/evidence/altered-additions/Data/Plugins/Probe.dll/inner.txt").read_bytes(), b"inner")

    def test_read_only_file_created_during_the_test_is_removed(self):
        self.prepare()
        created = write(self.game / "Data/N/data/locked.db", b"new")
        os.chmod(created, stat.S_IREAD)
        self.assertTrue(self.restore()["verified"])
        self.assertFalse(created.exists())

    def test_unreadable_active_record_refuses_every_mutation(self):
        self.prepare()
        (self.state / tc.ACTIVE).write_bytes(b"")
        with self.assertRaisesRegex(tc.Refused, "unreadable"):
            tc.status(self.state)
        with self.assertRaisesRegex(tc.Refused, "unreadable"):
            self.restore()
        with self.assertRaisesRegex(tc.Refused, "unreadable"):
            self.prepare(run="r2")
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n")

    def test_active_record_appears_whole_or_not_at_all(self):
        real = os.rename

        def dies(*args, **kwargs):
            raise KeyboardInterrupt
        with mock.patch.object(tc.os, "rename", dies):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertFalse((self.state / tc.ACTIVE).exists())
        self.assertIs(real, os.rename)

    def test_receipt_keeps_what_an_interrupted_attempt_saw(self):
        self.prepare()
        self.db.write_bytes(b"changed")
        created = write(self.game / "Data/N/data/new.db", b"new")
        real = tc.replace_verified

        def dies(*args, **kwargs):
            raise KeyboardInterrupt
        with mock.patch.object(tc, "replace_verified", dies):
            with self.assertRaises(KeyboardInterrupt):
                self.restore()
        self.assertIs(real, tc.replace_verified)
        receipt = self.restore()
        self.assertTrue(receipt["verified"])
        self.assertIn("Data/N/data/n.db", receipt["changed"])
        self.assertIn("Data/N/data/new.db", receipt["created"])
        self.assertEqual(receipt["attempts"], 2)
        self.assertFalse(created.exists())

    def test_slots_must_name_keys(self):
        profile = dict(self.profile, slots={"file": str(self.status), "keys": []})
        with self.assertRaisesRegex(tc.Refused, "keys"):
            self.prepare(profile=profile)

    def test_slots_accept_no_other_settings(self):
        profile = dict(self.profile, slots=dict(self.profile["slots"], lock="elsewhere.lock"))
        with self.assertRaisesRegex(tc.Refused, "unknown"):
            self.prepare(profile=profile)

    def test_the_lock_is_the_one_other_owners_use(self):
        path = self.status.with_suffix(".gateway.lock")
        with open(path, "w+b") as stream:
            stream.write(b"\0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                with self.assertRaisesRegex(tc.Refused, "locked"):
                    self.prepare()
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_check_refuses_a_layout_prepare_would_refuse(self):
        self.status.write_bytes(b'{"shared":null,"live":null}')
        with self.assertRaisesRegex(tc.Refused, "layout"):
            tc.check(self.game, self.state, self.store, OWNER, "r1", self.profile)

    def test_check_refuses_a_slot_this_owner_already_holds(self):
        self.status.write_bytes(b'{\n  "shared": "owner-a",\n  "live": null\n}\n')
        with self.assertRaisesRegex(tc.Refused, "held"):
            tc.check(self.game, self.state, self.store, OWNER, "r1", self.profile)

    def test_check_refuses_a_marker_from_another_state_folder(self):
        tc.prepare(self.game, self.state.parent / "state2", self.store, "owner-b", "r9", self.profile)
        with self.assertRaisesRegex(tc.Refused, "active"):
            tc.check(self.game, self.state, self.store, OWNER, "r1", self.profile)

    def test_verify_command_exits_nonzero_on_differences(self):
        self.prepare()
        self.assertEqual(tc.main(["verify", "--game", str(self.game), "--state", str(self.state)]), 1)

    def test_verify_reports_a_clean_context_and_records_it(self):
        self.prepare()
        report = tc.verify(self.game, self.state, record=True)
        self.assertEqual(report["additions_present"], ["Data/Plugins/Probe.dll"])
        self.assertTrue((self.state / "r1/verify-1.json").is_file())

    def test_a_root_that_is_the_whole_game_or_data_is_refused(self):
        for bad in (".", "Data", "./Data"):
            with self.assertRaisesRegex(tc.Refused, "whole|inside"):
                self.prepare(profile=dict(self.profile, roots=[bad], edits=[]))

    def test_a_save_inside_a_root_is_refused(self):
        for name in ("s.ess", "s.SKSE"):
            save = write(self.game / "Data/N/data" / name, b"save")
            with self.assertRaisesRegex(tc.Refused, "save"):
                self.prepare()
            save.unlink()
        self.assertIsNone(tc.status(self.state))

    def test_a_case_only_rename_is_not_a_new_file(self):
        self.prepare()
        os.rename(self.db, self.db.with_name("N.DB"))
        report = tc.verify(self.game, self.state)
        self.assertEqual(report["created"], [])
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(self.db.read_bytes(), b"database")
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir() if p.name.lower() == "n.db"), ["n.db"], "the original name comes back")
        self.assertEqual(receipt["renamed"], ["Data/N/data/n.db"])

    def test_a_case_rename_of_a_folder_comes_back(self):
        self.prepare()
        os.rename(self.db.parent, self.db.parent.with_name("DATA"))
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertIn("data", [p.name for p in self.db.parent.parent.iterdir()])
        self.assertNotIn("DATA", [p.name for p in self.db.parent.parent.iterdir()])

    def test_a_name_that_only_folds_alike_is_a_new_file(self):
        original = write(self.game / "Data/N/data/stra\u00dfe.db", b"original")
        self.prepare()
        created = write(self.game / "Data/N/data/strasse.db", b"new")
        self.assertEqual(tc.verify(self.game, self.state)["created"], ["Data/N/data/strasse.db"])
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertFalse(created.exists())
        self.assertEqual(original.read_bytes(), b"original")

    def test_a_name_lowering_alike_but_distinct_on_disk_is_a_new_file(self):
        # U+212A KELVIN SIGN lowers to k in Python, but NTFS keeps it a different name from k.
        original = write(self.game / "Data/N/data/k.db", b"original")
        self.prepare()
        created = write(self.game / "Data/N/data/\u212a.db", b"new")
        self.assertEqual(original.read_bytes(), b"original", "this filesystem would have to keep the two names apart")
        self.assertEqual(tc.verify(self.game, self.state)["created"], ["Data/N/data/\u212a.db"])
        self.assertTrue(self.restore()["verified"])
        self.assertFalse(created.exists())
        self.assertEqual(original.read_bytes(), b"original")

    def test_restore_will_not_delete_through_a_junction(self):
        self.prepare()
        target = self.game / "Data/Plugins/Probe.dll"
        elsewhere = self.game / "Data/elsewhere"
        elsewhere.mkdir()
        (elsewhere / "Probe.dll").write_bytes(b"probe")
        target.unlink()
        target.parent.rmdir()
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(target.parent), str(elsewhere)], capture_output=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        with self.assertRaisesRegex(tc.Refused, "link"):
            self.restore()
        self.assertEqual((elsewhere / "Probe.dll").read_bytes(), b"probe")

    def test_interrupted_slot_write_does_not_strand_ownership(self):
        real = tc.write_durable

        def writes_then_dies(path, data):
            real(path, data)
            if path == self.status:
                raise KeyboardInterrupt
        with mock.patch.object(tc, "write_durable", writes_then_dies):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertEqual(self.slots(), {"shared": OWNER, "live": OWNER})
        self.restore()
        self.assertEqual(self.slots(), {"shared": None, "live": None})
        self.assertIsNone(tc.status(self.state))

    def test_a_finished_restore_is_never_redone(self):
        self.prepare()
        real = tc.release_clean

        def dies(*args, **kwargs):
            raise KeyboardInterrupt
        with mock.patch.object(tc, "release_clean", dies):
            with self.assertRaises(KeyboardInterrupt):
                self.restore()
        self.assertIs(real, tc.release_clean)
        self.db.write_bytes(b"later play")
        receipt = self.restore()
        self.assertTrue(receipt["verified"])
        self.assertEqual(self.db.read_bytes(), b"later play", "the restore had finished: this run only finishes the cleanup")
        self.assertIsNone(tc.status(self.state))
        self.assertEqual(self.slots(), {"shared": None, "live": None})

    def test_an_unfinished_receipt_does_not_stop_the_retry(self):
        self.prepare()
        self.db.write_bytes(b"changed")
        blob = self.store / tc.sha256_bytes(b"database")
        good = blob.read_bytes()
        os.chmod(blob, stat.S_IWRITE)
        blob.write_bytes(b"rotten")
        self.assertFalse(self.restore()["verified"])
        blob.write_bytes(good)
        self.assertTrue(self.restore()["verified"])
        self.assertEqual(self.db.read_bytes(), b"database")

    def test_read_only_new_folder_is_removed(self):
        self.prepare()
        folder = self.game / "Data/N/data/locked"
        folder.mkdir()
        os.chmod(folder, stat.S_IREAD)
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertFalse(folder.exists())

    def test_a_new_folder_that_cannot_go_is_reported_not_passed(self):
        self.prepare()
        folder = self.game / "Data/N/data/new"
        folder.mkdir()
        with mock.patch.object(tc.Path, "rmdir", side_effect=PermissionError("held")):
            receipt = self.restore()
        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["after"]["created_folders"], ["Data/N/data/new"])
        self.assertTrue(self.restore()["verified"])

    def test_stray_marker_temporary_is_cleared_with_the_context(self):
        self.prepare()
        stray = write(self.game / (tc.GAME_MARKER + ".dead.tmp"), b"partial")
        self.restore()
        self.assertFalse(stray.exists())

    def test_a_marker_of_the_wrong_shape_is_unreadable(self):
        marker = self.game / tc.GAME_MARKER
        for text in ("[]", "{}", ""):
            marker.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(tc.Refused, "unreadable"):
                self.prepare()
        marker.unlink()

    def test_an_active_record_of_the_wrong_shape_is_unreadable(self):
        self.state.mkdir(parents=True)
        (self.state / tc.ACTIVE).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(tc.Refused, "unreadable"):
            tc.status(self.state)

    def test_receipt_keeps_an_altered_addition_seen_by_an_interrupted_attempt(self):
        self.prepare()
        target = self.game / "Data/Plugins/Probe.dll"
        target.write_bytes(b"altered")
        real = tc.release_clean

        def dies(*args, **kwargs):
            raise KeyboardInterrupt
        real_receipt = tc.write_json

        def no_receipt(path, value):
            if path.name == tc.RECEIPT:
                raise KeyboardInterrupt
            return real_receipt(path, value)
        with mock.patch.object(tc, "write_json", no_receipt):
            with self.assertRaises(KeyboardInterrupt):
                self.restore()
        self.assertIs(real, tc.release_clean)
        self.assertEqual(self.restore()["altered_additions"], ["Data/Plugins/Probe.dll"])
        self.assertIsNotNone(dies)

    def test_an_addition_may_not_be_a_save(self):
        profile = dict(self.profile, additions=[dict(self.profile["additions"][0], target="Data/Plugins/z.ess")])
        with self.assertRaisesRegex(tc.Refused, "save"):
            tc.check(self.game, self.state, self.store, OWNER, "r1", profile)

    def test_a_root_spelled_differently_from_the_disk_is_refused(self):
        profile = dict(self.profile, roots=["Data/n/config", "Data/N/data"], edits=[])
        for call in (lambda: self.prepare(profile=profile), lambda: tc.check(self.game, self.state, self.store, OWNER, "r1", profile)):
            with self.assertRaisesRegex(tc.Refused, "spelled"):
                call()
        self.assertEqual([p.name for p in (self.game / "Data").iterdir() if p.name.lower() == "n"], ["N"])

    def test_a_case_rename_above_the_roots_is_left_alone(self):
        self.prepare()
        os.rename(self.game / "Data/N", self.game / "Data/n")
        receipt = self.restore()
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(receipt["renamed"], [])
        self.assertEqual([p.name for p in (self.game / "Data").iterdir() if p.name.lower() == "n"], ["n"], "outside the roots is not ours")

    def test_a_case_rename_of_a_root_comes_back(self):
        self.prepare()
        os.rename(self.game / "Data/N/data", self.game / "Data/N/DATA")
        self.assertTrue(self.restore()["verified"])
        self.assertIn("data", [p.name for p in (self.game / "Data/N").iterdir()])

    def test_a_missing_ownership_file_refuses_and_leaves_nothing_active(self):
        profile = dict(self.profile, slots={"file": str(self.status) + ".typo", "keys": ["shared"]})
        for call in (lambda: self.prepare(profile=profile), lambda: tc.check(self.game, self.state, self.store, OWNER, "r1", profile)):
            with self.assertRaisesRegex(tc.Refused, "ownership file"):
                call()
        self.assertIsNone(tc.status(self.state))
        self.assertFalse((self.game / tc.GAME_MARKER).exists())

    def test_a_corrupt_ownership_file_refuses_and_leaves_nothing_active(self):
        for bad in (b"{not json", b"[]", b"3"):
            self.status.write_bytes(bad)
            with self.assertRaisesRegex(tc.Refused, "ownership file"):
                self.prepare()
            self.assertIsNone(tc.status(self.state))
            self.assertFalse((self.game / tc.GAME_MARKER).exists())
            self.assertEqual(self.status.read_bytes(), bad)

    def test_a_lost_ownership_file_stops_restore_and_says_which(self):
        self.prepare()
        keep = self.status.read_bytes()
        self.status.unlink()
        with self.assertRaisesRegex(tc.Refused, "ownership file"):
            self.restore()
        self.assertEqual(self.config.read_bytes(), b"top:\n  enabled: false\n")
        self.status.write_bytes(keep)
        self.assertTrue(self.restore()["verified"])

    def test_prepare_records_a_receipt(self):
        self.prepare()
        receipt = json.loads((self.state / "r1" / tc.PREPARE_RECEIPT).read_text(encoding="utf-8"))
        self.assertEqual(receipt["manifest_sha256"], tc.sha256(self.state / "r1" / tc.MANIFEST))
        self.assertEqual(receipt["edits"], ["Data/N/config/a.yaml"])

    def test_copies_are_flushed_to_disk(self):
        target = self.state.parent / "copied"
        with mock.patch.object(tc.os, "fsync", wraps=os.fsync) as fsync:
            tc.copy_durable(self.db, target)
        self.assertTrue(fsync.called)
        self.assertEqual(target.read_bytes(), b"database")


class Interface(Base):
    def test_module_cannot_launch(self):
        source = (Path(tc.__file__)).read_text(encoding="utf-8").lower()
        for word in ("vr-headless", "skse_loader", "steam://", "applaunch", "startfile"):
            self.assertNotIn(word, source)

    def test_profile_with_an_unknown_key_is_refused(self):
        with self.assertRaisesRegex(tc.Refused, "unknown"):
            self.prepare(profile=dict(self.profile, launch=True))

    def test_owner_must_be_named(self):
        with self.assertRaisesRegex(tc.Refused, "owner"):
            self.prepare(owner="")


if __name__ == "__main__":
    unittest.main()
