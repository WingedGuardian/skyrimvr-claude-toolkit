"""cosave-info's exit contract: 0 parsed / 1 degraded / 2 not-a-cosave.

Every path used to fall off the end at exit 0, so a caller gating on `$?` alone saw
"success" for a file that is not a cosave at all. The first version of these tests
then certified the hole: its "well-formed cosave" fixture was four magic bytes and
64 zeros, which parses zero chunks at 47% coverage and exited 0 -- and the assertion
`rc in (0, 1)` could not tell the two apart.
"""
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COSAVE = ROOT / "tools" / "cosave-info.py"


def run(*args):
    return subprocess.run([sys.executable, str(COSAVE), *args],
                          capture_output=True, text=True)


def _valid_cosave() -> bytes:
    """A structurally valid co-save: 32-byte header, then one well-formed chunk.

    Opcodes are stored little-endian and reversed for display, so "ATAD" reads back
    as DATA. 100% coverage, one chunk -- the healthy case.
    """
    header = b"SKSE" + struct.pack("<IIIIIII", 1, 0x02000600, 0x01040F00, 0, 0, 0, 0)
    payload = bytes(200)
    return header + b"ATAD" + struct.pack("<II", 1, len(payload)) + payload


def test_cosave_accepts_a_structurally_valid_cosave(tmp_path):
    """The control that makes the refusals below mean something. Without a case that
    genuinely exits 0, "everything is refused" and "the check works" are the same
    result."""
    f = tmp_path / "ok.skse"
    f.write_bytes(_valid_cosave())
    r = run(str(f))
    assert r.returncode == 0, r.stdout
    assert '"chunkCount": 1' in r.stdout, r.stdout
    assert '"coveragePct": 100.0' in r.stdout, r.stdout


def test_cosave_degrades_on_a_stub_that_parses_no_chunks(tmp_path):
    """Magic bytes and nothing else. It is not a parse failure -- the header reads --
    but the inventory is empty, and reporting that as a trustworthy result is the
    lie. Exit 1: parsed, not trustworthy."""
    f = tmp_path / "stub.skse"
    f.write_bytes(b"SKSE" + bytes(64))
    r = run(str(f))
    assert r.returncode == 1, r.stdout
    assert '"chunkCount": 0' in r.stdout, r.stdout


def test_cosave_refuses_a_file_that_is_not_a_cosave(tmp_path):
    f = tmp_path / "notacosave.txt"
    f.write_text("this is plainly not an SKSE co-save", encoding="utf-8")
    r = run(str(f))
    assert r.returncode == 2, r.stdout
    assert '"ok": false' in r.stdout.lower(), r.stdout


def test_cosave_refuses_a_file_too_short_to_unpack(tmp_path):
    """survey() raises on anything shorter than its header reads. A traceback exits
    1, which this contract defines as "parsed but degraded" -- so the exception has
    to be caught and reported as the refusal it is."""
    f = tmp_path / "tiny.skse"
    f.write_bytes(b"SKSE")
    r = run(str(f))
    assert r.returncode == 2, r.stdout


def test_cosave_refuses_a_missing_file():
    r = run("/nonexistent/nope.skse")
    assert r.returncode == 2, r.stdout


def test_cosave_refuses_with_no_arguments():
    r = run()
    assert r.returncode == 2, r.stdout
