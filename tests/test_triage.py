"""Behaviour of the two triage tools.

Both exist to answer "what is actually wrong in this log", and both make one
promise that is easy to write and easy to get wrong: the rows they print add up
to the input they read. A triage tool that quietly drops a shape reports a
cleaner log than the one you have, which is the failure mode with no symptom.

The logs here are hand-authored rather than copied from a real install: real
ones carry usernames and mod names, and a fixture that must be scrubbed before
it can be committed will eventually be committed unscrubbed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

PAPYRUS = REPO / "tools" / "papyrus-triage.py"
CRASH = REPO / "tools" / "crash-triage.py"


def run(script: Path, *args: str):
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, timeout=120)


# --------------------------------------------------------------------------
# papyrus-triage
# --------------------------------------------------------------------------

PAPYRUS_LOG = "\n".join([
    "[08/26/2026 - 11:22:33PM] Papyrus log opened",
    "[08/26/2026 - 11:22:34PM] error: Cannot call Foo() on a None object",
    "\tstack:",
    "\t[alias Bar on quest Baz (00001234)].SomeScript.Foo() - \"SomeScript.psc\" Line 12",
    "[08/26/2026 - 11:22:35PM] error: Cannot call Foo() on a None object",
    "\tstack:",
    "\t[alias Bar on quest Baz (00005678)].SomeScript.Foo() - \"SomeScript.psc\" Line 12",
    "[08/26/2026 - 11:22:36PM] warning: Property Thing on script OtherScript has no value",
    "[08/26/2026 - 11:22:37PM] WARNING: Property Thing on script OtherScript has no value",
    "[08/26/2026 - 11:22:38PM] error: File \"Ghost.esp\" does not exist or is not currently loaded.",
    "",
])


@pytest.fixture
def papyrus_log(tmp_path) -> Path:
    p = tmp_path / "Papyrus.0.log"
    p.write_text(PAPYRUS_LOG, encoding="utf-8")
    return p


def test_papyrus_counts_entries_not_lines(papyrus_log):
    """A Papyrus entry spans several lines; counting lines overstates by ~37%."""
    r = run(PAPYRUS, str(papyrus_log))
    assert "timestamped entries  : 6" in r.stdout, r.stdout
    assert "lines read           : 10" in r.stdout, r.stdout
    assert "continuation lines   : 4" in r.stdout, r.stdout


def test_papyrus_counts_both_spellings_of_warning(papyrus_log):
    """The same log spells it `warning:` and `WARNING:`. Case-sensitivity loses most of them."""
    r = run(PAPYRUS, str(papyrus_log))
    assert "warning  : 2" in r.stdout, r.stdout


def test_papyrus_accounting_balances_and_reports_ok(papyrus_log):
    r = run(PAPYRUS, str(papyrus_log))
    assert "RESULT: OK" in r.stdout, r.stdout
    assert "MISMATCH" not in r.stdout, r.stdout
    assert r.returncode == 0


def test_papyrus_groups_identical_shapes_across_differing_formids(papyrus_log):
    """Two entries differing only by FormID are one shape, counted twice."""
    r = run(PAPYRUS, str(papyrus_log))
    assert "2  " in r.stdout and "Cannot call Foo() on a None object" in r.stdout, r.stdout


def test_papyrus_reports_the_not_shown_remainder_even_at_zero(papyrus_log):
    """Silence about the remainder is how a triage tool starts lying."""
    r = run(PAPYRUS, str(papyrus_log), "--top", "1")
    assert "(not shown above)" in r.stdout, r.stdout


def test_papyrus_attributes_a_missing_plugin_to_its_name(papyrus_log):
    r = run(PAPYRUS, str(papyrus_log))
    assert "Ghost.esp" in r.stdout, r.stdout


def test_papyrus_rejects_a_path_that_is_not_a_file(tmp_path):
    r = run(PAPYRUS, str(tmp_path / "nope.log"))
    assert r.returncode == 2


# --------------------------------------------------------------------------
# crash-triage
# --------------------------------------------------------------------------

def crash_log(offset: str, symbol: str) -> str:
    return "\n".join([
        "Skyrim VR v1.4.15",
        "CrashLoggerSSE v1-0-0",
        "",
        f'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF6A1{offset} '
        f"SkyrimVR.exe+{offset}\tmov rax, [rcx] |  {symbol})",
        "",
        "PROBABLE CALL STACK:",
        f"\t[0]\t0x7FF6A1{offset} SkyrimVR.exe+{offset}",
        "\t[1]\t0x7FF6A1000000 SkyrimVR.exe+1000000",
        "",
        "REGISTERS:",
        "\tRAX 0x0",
    ])


@pytest.fixture
def crash_dir(tmp_path) -> Path:
    d = tmp_path / "SKSE"
    d.mkdir()
    # Two of a signature the knowledgebase has already ruled on, one it has not.
    (d / "crash-2026-08-26-01.txt").write_text(crash_log("0B84F05", "hkpContactSolver"), encoding="utf-8")
    (d / "crash-2026-08-26-02.txt").write_text(crash_log("0B84F05", "hkpContactSolver"), encoding="utf-8")
    (d / "crash-2026-08-26-03.txt").write_text(crash_log("1234567", "SomethingNew"), encoding="utf-8")
    return d


def test_crash_groups_repeats_into_one_ranked_signature(crash_dir):
    r = run(CRASH, str(crash_dir))
    assert "3 log(s)" in r.stdout, r.stdout
    assert "2 distinct" in r.stdout, r.stdout
    assert "2 x  SkyrimVR.exe+0B84F05" in r.stdout, r.stdout


def test_crash_labels_an_accepted_signature_from_the_knowledgebase(crash_dir):
    """The point of the tool: an accepted crash must not read as a new finding."""
    r = run(CRASH, str(crash_dir))
    assert "[ACCEPTED - do not re-investigate]" in r.stdout, r.stdout


def test_crash_flags_an_unrecognised_signature_as_unknown(crash_dir):
    r = run(CRASH, str(crash_dir))
    assert "[** UNKNOWN **]" in r.stdout, r.stdout
    assert "UNKNOWN        : 1" in r.stdout, r.stdout


def test_crash_shows_stack_frames_only_for_the_unknown_ones(crash_dir):
    """Frames are for triage; printing them for a settled crash is just noise."""
    r = run(CRASH, str(crash_dir))
    assert "top 4 frames" in r.stdout, r.stdout
    assert r.stdout.count("top 4 frames") == 1, r.stdout


def test_crash_accounting_balances_and_reports_ok(crash_dir):
    r = run(CRASH, str(crash_dir))
    assert "accounted     : 3   [OK vs 3 found]" in r.stdout, r.stdout
    assert "RESULT: OK" in r.stdout
    assert r.returncode == 0


def test_crash_counts_a_log_with_no_exception_line_rather_than_dropping_it(crash_dir):
    (crash_dir / "crash-2026-08-26-04.txt").write_text("Skyrim VR v1.4.15\nnothing here\n",
                                                      encoding="utf-8")
    r = run(CRASH, str(crash_dir))
    assert "no exception  : 1" in r.stdout, r.stdout
    assert "accounted     : 4   [OK vs 4 found]" in r.stdout, r.stdout


def test_crash_finds_dot_log_files_not_only_dot_txt(crash_dir):
    """CrashLoggerSSE writes `.LOG`, not `.txt` -- KNOWLEDGEBASE.md says so.

    A `crash-*.txt` glob does not fail on a modern install, it reports a
    confident, complete-looking total made only of old logs. Measured on the
    dev install: 8 `.txt` (all June 2025) sitting beside 20 `.log` (2026), and
    the tool said "8 log(s)".
    """
    (crash_dir / "crash-2026-08-27-05.log").write_text(
        crash_log("ABCDEF0", "NewOne"), encoding="utf-8")
    (crash_dir / "crash-2026-08-27-06.LOG").write_text(
        crash_log("ABCDEF0", "NewOne"), encoding="utf-8")
    r = run(CRASH, str(crash_dir))
    assert "5 log(s)" in r.stdout, r.stdout
    assert "2 x  SkyrimVR.exe+ABCDEF0" in r.stdout, r.stdout
    assert "accounted     : 5   [OK vs 5 found]" in r.stdout, r.stdout


def test_crash_ignores_the_crashlogger_plugin_log(crash_dir):
    """`CrashLogger.log` is the plugin's own log, not a dump; it sits in the
    same folder and must not become a phantom crash."""
    (crash_dir / "CrashLogger.log").write_text("[info] loaded\n", encoding="utf-8")
    r = run(CRASH, str(crash_dir))
    assert "3 log(s)" in r.stdout, r.stdout


def test_crash_rejects_a_directory_with_no_logs(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(CRASH, str(empty)).returncode == 2
