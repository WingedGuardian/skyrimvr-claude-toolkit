"""A result counts only for the exact inputs and outputs it ran against."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provenance"))
import receipts  # noqa: E402


class Binding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "a.cpp"
        self.source.write_text("one", encoding="utf-8")
        (self.root / "gen.h").write_text("g", encoding="utf-8")
        (self.root / "a.dll").write_bytes(b"binary")
        self.inputs = receipts.inputs_digest([self.source], self.root)
        self.configured = receipts.record_configure(self.inputs, self.root, ["gen.h"])
        self.built = receipts.record_build(self.configured, self.root, ["a.dll"])

    def check(self, current=None):
        receipts.require_current(self.inputs if current is None else current, self.configured, self.built, self.root)

    def test_matching_build_passes(self):
        self.check()

    def test_no_inputs_fails(self):
        with self.assertRaisesRegex(ValueError, "no inputs"):
            self.check({})

    def test_changed_source_fails(self):
        self.source.write_text("two", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configure inputs differ"):
            self.check(receipts.inputs_digest([self.source], self.root))

    def test_reconfigure_new_test_old_fails(self):
        self.source.write_text("two", encoding="utf-8")
        current = receipts.inputs_digest([self.source], self.root)
        self.configured = receipts.record_configure(current, self.root, ["gen.h"])
        with self.assertRaisesRegex(ValueError, "matching successful build"):
            self.check(current)

    def test_modified_output_fails(self):
        (self.root / "a.dll").write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "output changed"):
            self.check()

    def test_missing_output_fails(self):
        (self.root / "a.dll").unlink()
        with self.assertRaisesRegex(ValueError, "output changed"):
            self.check()

    def test_changed_generated_input_fails(self):
        (self.root / "gen.h").write_text("h", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "generated input changed"):
            self.check()

    def test_failed_build_fails(self):
        self.built = dict(self.built, passed=False)
        with self.assertRaisesRegex(ValueError, "matching successful build"):
            self.check()

    def test_build_with_no_outputs_is_refused(self):
        with self.assertRaises(ValueError):
            receipts.record_build(self.configured, self.root, [])

    def test_missing_input_is_an_error_not_an_omission(self):
        with self.assertRaises(OSError):
            receipts.inputs_digest([self.root / "gone.cpp"], self.root)


class Drift(unittest.TestCase):
    def test_passing_run_with_stable_inputs_passes(self):
        self.assertEqual(receipts.result_code(0, True), 0)

    def test_post_run_drift_fails_a_passing_run(self):
        self.assertEqual(receipts.result_code(0, False), 1)

    def test_a_failing_run_keeps_its_own_code(self):
        self.assertEqual(receipts.result_code(7, True), 7)


if __name__ == "__main__":
    unittest.main()
