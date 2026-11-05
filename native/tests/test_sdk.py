"""Portable checks of the SDK contract. The real link is `sdk.py qualify`; these prove its refusals can fire."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sdk", HERE.parent / "sdk" / "sdk.py")
sdk = importlib.util.module_from_spec(spec)
sys.modules["sdk"] = sdk
spec.loader.exec_module(sdk)


class Packages(unittest.TestCase):
    def test_matching_set_is_coherent(self):
        self.assertEqual(sdk.check_packages({"fmt": "12.2.0#1"}, {"fmt": "12.2.0#1"}), [])

    def test_moved_version_is_refused(self):
        self.assertEqual(len(sdk.check_packages({"fmt": "9.1.0"}, {"fmt": "12.2.0#1"})), 1)

    def test_package_the_pins_do_not_name_is_refused(self):
        self.assertIn("extra", sdk.check_packages({"extra": "1"}, {})[0])

    def test_pinned_package_not_installed_is_refused(self):
        self.assertIn("fmt", sdk.check_packages({}, {"fmt": "12.2.0#1"})[0])

    def test_pins_cover_what_the_library_requires(self):
        pins = sdk.load_pins()
        for name in ("fmt", "spdlog", "directxtk", "xbyak"):
            self.assertIn(name, pins["packages"])
        self.assertEqual(len(pins["commonlib"]["commit"]), 40)
        self.assertEqual(len(pins["minhook"]["commit"]), 40)
        self.assertEqual(len(pins["commonlib"]["vcpkg_baseline"]), 40)


class Baseline(unittest.TestCase):
    def test_pinned_baseline_passes(self):
        sdk.check_baseline({"default-registry": {"baseline": "abc"}}, "abc")

    def test_different_baseline_refuses(self):
        with self.assertRaises(sdk.Refused):
            sdk.check_baseline({"default-registry": {"baseline": "other"}}, "abc")

    def test_missing_baseline_refuses(self):
        with self.assertRaises(sdk.Refused):
            sdk.check_baseline({}, "abc")


class Receipt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = Path(self.tmp.name) / "p"
        (self.prefix / "lib").mkdir(parents=True)
        (self.prefix / "lib" / "a.lib").write_bytes(b"one")
        self.pins = Path(self.tmp.name) / "pins.json"
        self.pins.write_text("{}", encoding="utf-8")
        self.native = Path(self.tmp.name) / "native"
        (self.native / "observation/include").mkdir(parents=True)
        (self.native / "observation/asm").mkdir(parents=True)
        (self.native / "sdk").mkdir()
        self.header = self.native / "observation/include/a.hpp"
        self.header.write_text("one", encoding="utf-8")
        for name in ("SkyrimNativeSdkConfig.cmake", "THIRD-PARTY-NOTICES.md"):
            (self.native / "sdk" / name).write_text("x", encoding="utf-8")
        self._write_receipt()

    def verify(self):
        return sdk.verify(self.prefix, self.pins, self.native)

    def _write_receipt(self):
        receipt = {"pins_sha256": sdk.sha256(self.pins), "sources_sha256": sdk.sources_digest(self.native),
                   "files": sdk.manifest(self.prefix)}
        (self.prefix / sdk.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")

    def test_clean_prefix_verifies(self):
        self.assertEqual(self.verify(), [])

    def test_changed_file_fails(self):
        (self.prefix / "lib" / "a.lib").write_bytes(b"two")
        self.assertEqual(self.verify(), ["changed: lib/a.lib"])

    def test_missing_file_fails(self):
        (self.prefix / "lib" / "a.lib").unlink()
        self.assertEqual(self.verify(), ["missing: lib/a.lib"])

    def test_added_file_fails(self):
        (self.prefix / "lib" / "b.lib").write_bytes(b"new")
        self.assertEqual(self.verify(), ["unexpected: lib/b.lib"])

    def test_changed_pins_make_the_sdk_stale(self):
        self.pins.write_text('{"x":1}', encoding="utf-8")
        self.assertIn("stale", self.verify()[0])

    def test_changed_observation_header_makes_the_sdk_stale(self):
        self.header.write_text("two", encoding="utf-8")
        self.assertIn("stale", self.verify()[0])

    def test_changed_sdk_config_makes_the_sdk_stale(self):
        (self.native / "sdk/SkyrimNativeSdkConfig.cmake").write_text("y", encoding="utf-8")
        self.assertIn("stale", self.verify()[0])

    def test_no_receipt_fails(self):
        (self.prefix / sdk.RECEIPT).unlink()
        self.assertIn("no receipt", self.verify()[0])

    def test_unreadable_receipt_fails(self):
        (self.prefix / sdk.RECEIPT).write_text("{", encoding="utf-8")
        self.assertIn("unreadable", self.verify()[0])


class GameRoot(unittest.TestCase):
    def test_inside_a_game_folder_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "SkyrimVR.exe").write_bytes(b"")
            with self.assertRaises(sdk.Refused):
                sdk.refuse_game_root(Path(tmp, "tools", "sdk"))

    def test_elsewhere_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdk.refuse_game_root(Path(tmp, "sdk"))


class LinkOutput(unittest.TestCase):
    EXPORTS = "1 0 00001 SKSEPlugin_Load\n2 1 00002 SKSEPlugin_Query\n"

    def test_good_link_passes(self):
        self.assertEqual(sdk.check_link_output(self.EXPORTS, "KERNEL32.dll\nVCRUNTIME140.dll\n"), [])

    def test_missing_query_fails(self):
        self.assertIn("SKSEPlugin_Query", sdk.check_link_output("SKSEPlugin_Load", "")[0])

    def test_a_dependency_dll_fails(self):
        self.assertIn("fmt", sdk.check_link_output(self.EXPORTS, "fmt.dll")[0])


if __name__ == "__main__":
    unittest.main()
