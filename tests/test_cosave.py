"""cosave-info's exit contract.

Every path used to fall off the end at exit 0, so a caller gating on `$?` alone
saw "success" for a file that is not a cosave at all -- a non-answer wearing the
shape of an answer.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COSAVE = ROOT / "tools" / "cosave-info.py"


def run(*args):
    return subprocess.run([sys.executable, str(COSAVE), *args],
                          capture_output=True, text=True)


def test_cosave_refuses_a_file_that_is_not_a_cosave(tmp_path):
    """Exit 2, not 0. This is the whole point: a garbage file must not be
    indistinguishable from a clean parse."""
    f = tmp_path / "notacosave.txt"
    f.write_text("this is plainly not an SKSE co-save", encoding="utf-8")
    r = run(str(f))
    assert r.returncode == 2, r.stdout
    assert '"ok": false' in r.stdout.lower(), r.stdout


def test_cosave_refuses_with_no_arguments():
    r = run()
    assert r.returncode == 2, r.stdout


def test_cosave_accepts_a_well_formed_cosave(tmp_path):
    """The control. Without it, "everything exits 2" and "the check works" look
    identical."""
    f = tmp_path / "ok.skse"
    # SKSE magic + a plausible header; enough for the magic check to pass.
    f.write_bytes(b"SKSE" + bytes(64))
    r = run(str(f))
    assert r.returncode in (0, 1), f"rc={r.returncode} out={r.stdout}"
    assert '"ok": true' in r.stdout.lower(), r.stdout
