"""The native image reference, against synthetic PE files only: no game bytes are used or needed."""
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provenance"))
import native_image as ni  # noqa: E402

TEXT_RVA, BIND_RVA, PDATA_RVA, UNWIND_RVA = 0x1000, 0x8000, 0x4000, 0x5000
TOOL = {"name": "tool", "commit": "c" * 40, "sha256": "d" * 64, "source_sha256": ""}


def make_pe(entry=TEXT_RVA + 0x10, code=b"\x4c\x89\x4c\x24\x20\xc3" + b"\x90" * 0x1FA, base=0x140000000, image_size=0x9000):
    sections = [(b".text", TEXT_RVA, code, 0x60000020), (b".pdata", PDATA_RVA, struct.pack("<III", TEXT_RVA, TEXT_RVA + 0x40, UNWIND_RVA) +
                b"\0" * 0x1F4, 0x40000040), (b".unw", UNWIND_RVA, bytes([1 | (4 << 3), 5, 3, 0]) + b"\0" * 0x1FC, 0x40000040),
                (b".bind", BIND_RVA, b"\xcc" * 0x200, 0x60000020)]
    header = 0x400
    pe = b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x80) + b"\0" * 0x40
    pe += b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664, len(sections), 0, 0, 0, 240, 0x22)
    optional = bytearray(240)
    struct.pack_into("<H", optional, 0, 0x20B)
    struct.pack_into("<I", optional, 16, entry)
    struct.pack_into("<Q", optional, 24, base)
    struct.pack_into("<I", optional, 56, image_size)
    struct.pack_into("<I", optional, 108, 16)
    struct.pack_into("<II", optional, 112 + 3 * 8, PDATA_RVA, 12)
    pe += bytes(optional)
    pointer, body = header, b""
    for name, rva, data, flags in sections:
        data = data.ljust(0x200, b"\0")
        pe += name.ljust(8, b"\0") + struct.pack("<IIII", len(data), rva, len(data), pointer) + b"\0" * 12 + struct.pack("<I", flags)
        body += data
        pointer += len(data)
    return pe.ljust(header, b"\0") + body


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"
        self.source = self.root / "wrapped.exe"
        self.image = self.root / "derived.exe"
        self.source.write_bytes(make_pe(entry=BIND_RVA + 0x10))
        self.image.write_bytes(make_pe())

    def _cleanup(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                os.chmod(path, stat.S_IWRITE)
        self.tmp.cleanup()

    def register(self, **kwargs):
        args = dict(store=self.store, source=self.source, image=self.image, owner="tester", tool=TOOL)
        args.update(kwargs)
        return ni.register(**args)


class Register(Base):
    def test_records_identities_and_provenance(self):
        entry = self.register()
        self.assertEqual(entry["image_sha256"], ni.sha256(self.image))
        self.assertEqual(entry["source_sha256"], ni.sha256(self.source))
        self.assertEqual(entry["tool_declared"], TOOL)
        self.assertEqual(entry["owner"], "tester")
        self.assertIn("retention", entry)
        self.assertTrue((self.store / entry["image_sha256"] / ".housekeep-keep").is_file())

    def test_second_consumer_shares_the_one_copy(self):
        first = self.register()
        second = self.register(owner="another")
        self.assertEqual(second["owner"], "tester")
        self.assertEqual(sum(1 for p in self.store.rglob(ni.IMAGE)), 1)
        self.assertEqual(first["image_sha256"], second["image_sha256"])

    def test_same_image_from_a_different_source_refuses(self):
        self.register()
        other = self.root / "other.exe"
        other.write_bytes(make_pe(entry=BIND_RVA + 0x20))
        with self.assertRaisesRegex(ni.Refused, "different source"):
            self.register(source=other)

    def test_store_in_a_game_folder_refuses(self):
        (self.root / "SkyrimVR.exe").write_bytes(b"")
        with self.assertRaisesRegex(ni.Refused, "game folder"):
            self.register()

    def test_different_layout_refuses(self):
        self.image.write_bytes(make_pe(image_size=0xA000))
        with self.assertRaisesRegex(ni.Refused, "size_of_image"):
            self.register()

    def test_different_image_base_refuses(self):
        self.image.write_bytes(make_pe(base=0x150000000))
        with self.assertRaisesRegex(ni.Refused, "image_base"):
            self.register()

    def test_entry_still_in_bind_refuses(self):
        self.image.write_bytes(make_pe(entry=BIND_RVA + 0x30))
        with self.assertRaisesRegex(ni.Refused, "SteamStub"):
            self.register()

    def test_unchanged_entry_refuses(self):
        self.source.write_bytes(make_pe())
        with self.assertRaisesRegex(ni.Refused, "did not change"):
            self.register()

    def test_entry_outside_code_refuses(self):
        self.image.write_bytes(make_pe(entry=PDATA_RVA))
        with self.assertRaisesRegex(ni.Refused, "not in a code section"):
            self.register()

    def test_not_a_pe_refuses(self):
        self.image.write_bytes(b"not a program")
        with self.assertRaises(ni.Refused):
            self.register()

    def test_missing_input_refuses(self):
        self.image.unlink()
        with self.assertRaisesRegex(ni.Refused, "not a file"):
            self.register()


class Verify(Base):
    def setUp(self):
        super().setUp()
        self.digest = self.register()["image_sha256"]
        self.stored = self.store / self.digest / ni.IMAGE

    def test_clean_reference_verifies(self):
        self.assertEqual(ni.verify(self.store, self.digest), [])

    def test_stored_image_is_read_only(self):
        self.assertFalse(os.access(self.stored, os.W_OK))

    def test_writable_image_fails(self):
        os.chmod(self.stored, stat.S_IWRITE)
        self.assertIn("writable", ni.verify(self.store, self.digest)[0])

    def test_tampered_image_fails(self):
        os.chmod(self.stored, stat.S_IWRITE)
        self.stored.write_bytes(b"x")
        os.chmod(self.stored, stat.S_IREAD)
        self.assertIn("no longer hashes", ni.verify(self.store, self.digest)[0])

    def test_missing_image_fails(self):
        os.chmod(self.stored, stat.S_IWRITE)
        self.stored.unlink()
        self.assertIn("missing", ni.verify(self.store, self.digest)[0])

    def test_installed_source_matches(self):
        self.assertEqual(ni.verify(self.store, self.digest, self.source), [])

    def test_game_update_is_detected(self):
        updated = self.root / "updated.exe"
        updated.write_bytes(b"new game build")
        self.assertIn("game changed", ni.verify(self.store, self.digest, updated)[0])

    def test_unknown_reference_refuses(self):
        with self.assertRaises(ni.Refused):
            ni.verify(self.store, "0" * 64)

    def test_tampered_image_is_never_opened(self):
        os.chmod(self.stored, stat.S_IWRITE)
        self.stored.write_bytes(self.stored.read_bytes() + b"x")
        os.chmod(self.stored, stat.S_IREAD)
        with self.assertRaises(ni.Refused):
            ni.inspect(self.store, self.digest, TEXT_RVA, 5)


class Inspect(Base):
    def setUp(self):
        super().setUp()
        self.digest = self.register()["image_sha256"]

    def test_reads_bytes_and_section(self):
        result = ni.inspect(self.store, self.digest, TEXT_RVA, 5)
        self.assertEqual(result["bytes"], "4c894c2420")
        self.assertEqual(result["section"], ".text")

    def test_reads_unwind_facts(self):
        function = ni.inspect(self.store, self.digest, TEXT_RVA + 4, 1)["runtime_function"]
        self.assertEqual((function["begin"], function["end"], function["unwind_rva"]), (TEXT_RVA, TEXT_RVA + 0x40, UNWIND_RVA))
        self.assertTrue(function["chained"])
        self.assertEqual(function["prolog_size"], 5)

    def test_outside_any_function_has_no_entry(self):
        self.assertIsNone(ni.inspect(self.store, self.digest, TEXT_RVA + 0x41, 1)["runtime_function"])

    def test_rva_in_no_section_refuses(self):
        with self.assertRaisesRegex(ni.Refused, "no section"):
            ni.inspect(self.store, self.digest, 0x7000, 1)

    def test_read_past_section_refuses(self):
        with self.assertRaisesRegex(ni.Refused, "past"):
            ni.inspect(self.store, self.digest, TEXT_RVA + 0x1FF, 16)

    def test_oversized_read_refuses(self):
        with self.assertRaises(ni.Refused):
            ni.inspect(self.store, self.digest, TEXT_RVA, ni.MAX_READ + 1)

    def test_matching_site_passes(self):
        descriptor = {"sites": [{"name": "drive", "rva": hex(TEXT_RVA), "expected_hex": "4c894c2420"}]}
        self.assertEqual(ni.check_sites(self.store, self.digest, descriptor), [])

    def test_changed_site_is_named(self):
        descriptor = {"sites": [{"name": "drive", "rva": hex(TEXT_RVA), "expected_hex": "4053554156"}]}
        self.assertIn("drive", ni.check_sites(self.store, self.digest, descriptor)[0])

    def test_empty_descriptor_refuses(self):
        with self.assertRaises(ni.Refused):
            ni.check_sites(self.store, self.digest, {"sites": []})


class Cli(Base):
    def test_no_store_refuses_with_no_default(self):
        os.environ.pop("SKYRIM_NATIVE_IMAGE_STORE", None)
        self.assertEqual(ni.main(["verify", "--sha256", "0" * 64]), 2)

    def test_verify_reports_through_json(self):
        digest = self.register()["image_sha256"]
        self.assertEqual(ni.main(["verify", "--store", str(self.store), "--sha256", digest]), 0)

    def test_verify_fails_when_tampered(self):
        digest = self.register()["image_sha256"]
        stored = self.store / digest / ni.IMAGE
        os.chmod(stored, stat.S_IWRITE)
        self.assertEqual(ni.main(["verify", "--store", str(self.store), "--sha256", digest]), 1)

    def test_manifest_is_valid_json(self):
        digest = self.register()["image_sha256"]
        self.assertEqual(json.loads((self.store / digest / ni.MANIFEST).read_text(encoding="utf-8"))["schema"], 1)


if __name__ == "__main__":
    unittest.main()
