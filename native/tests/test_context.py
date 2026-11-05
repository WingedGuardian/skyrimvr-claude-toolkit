"""Test context: synthetic game folders only. Nothing here starts a process but tasklist, and nothing touches a real game."""
import json
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
            "slots": {"file": str(self.status), "lock": str(base / "status.lock"), "keys": ["shared", "live"]},
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
        self.assertIsNone(tc.status(self.state), "the failed begin leaves nothing active")

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
