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


# --------------------------------------------------------------------------
# crash-triage -- CrashLoggerSSE 1.2x format
#
# The fixtures above are the 1-0-0 shape. 1.2x changed three things in the part
# of the dump the parser reads, and a C++ throw exercises all of them at once.
# --------------------------------------------------------------------------

def crash_log_cpp(info: str) -> str:
    """A C++ throw as CrashLoggerSSE 1.2x writes it.

    The exception line reports the THROW SITE, which is the same KERNELBASE
    address for every C++ crash on a machine -- so `info` is the only thing
    that distinguishes one of these from another.
    """
    return "\n".join([
        "Skyrim SSE v1.6.1170",
        "CrashLoggerSSE v1-23-1-0",
        "",
        'Unhandled exception "C++ Exception" at 0x7FFF55741000 '
        "KERNELBASE.dll+00C1000\tnop [rax+rax*1], eax",
        "",
        "C++ EXCEPTION:",
        "\tType: (std::invalid_argument*)",
        f"\tInfo: {info}",
        "\tThrow Location: MSVCP140.dll+0044E92",
        "",
        "CALL STACK ([P]robable / [S]tack scan):",
        "\t[ 0][P] 0x7FFF55741000    KERNELBASE.dll+00C1000\tnop [rax+rax*1], eax",
        "\t[ 1][P] 0x7FF700000001    SkyrimSE.exe+0000001\tmov rdi, rax",
        "\t[ 2][S] 0x7FF700000002    ScannedOnly.dll+0000002\tcmp ebx, eax",
        "",
        "REGISTERS:",
        "\tRAX 0x0",
    ])


@pytest.fixture
def cpp_crash_dir(tmp_path) -> Path:
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-2026-09-05-21-55-53.log").write_text(
        crash_log_cpp("invalid stoull argument"), encoding="utf-8")
    (d / "crash-2026-09-05-22-09-04.log").write_text(
        crash_log_cpp("invalid stoull argument"), encoding="utf-8")
    (d / "crash-2026-09-05-22-55-51.log").write_text(
        crash_log_cpp("bad allocation"), encoding="utf-8")
    return d


def test_crash_parses_a_cpp_exception_rather_than_calling_it_no_exception(cpp_crash_dir):
    """`C++ Exception` has a `+` and lowercase letters in the type. A parser
    that assumes `[A-Z_]+` reads a heavy modlist's most common crash class as
    "no exception" -- a clean report of a log you do not have."""
    r = run(CRASH, str(cpp_crash_dir))
    assert "no exception  : 0" in r.stdout, r.stdout
    assert "accounted     : 3   [OK vs 3 found]" in r.stdout, r.stdout


def test_crash_separates_cpp_throws_by_their_info_not_the_throw_site(cpp_crash_dir):
    """All three share one throw site. Keying on module+offset alone would
    merge two unrelated crashes into one ranked row."""
    r = run(CRASH, str(cpp_crash_dir))
    assert "2 distinct" in r.stdout, r.stdout
    assert "2 x  KERNELBASE.dll+00C1000  invalid stoull argument" in r.stdout, r.stdout
    assert "1 x  KERNELBASE.dll+00C1000  bad allocation" in r.stdout, r.stdout


def test_crash_reports_the_cpp_exception_block(cpp_crash_dir):
    """Type and throw location are what a reader needs; the exception line
    alone says only that something threw."""
    r = run(CRASH, str(cpp_crash_dir))
    assert "(std::invalid_argument*)" in r.stdout, r.stdout
    assert "MSVCP140.dll+0044E92" in r.stdout, r.stdout


def test_crash_reads_frames_from_the_1_2x_stack_header(cpp_crash_dir):
    """1.2x heads the stack `CALL STACK ([P]robable / [S]tack scan):`. Matching
    only `PROBABLE CALL STACK` leaves every signature frameless without saying so."""
    r = run(CRASH, str(cpp_crash_dir))
    assert "top 4 frames" in r.stdout, r.stdout
    assert "KERNELBASE.dll+00C1000" in r.stdout, r.stdout
    assert "SkyrimSE.exe+0000001" in r.stdout, r.stdout


def test_crash_drops_stack_scan_frames_and_keeps_probable_ones(cpp_crash_dir):
    """An [S] frame is a stack scan, not a call. It is not solid enough to key
    a signature on, and printing it as one invites a wrong conclusion."""
    r = run(CRASH, str(cpp_crash_dir))
    assert "SkyrimSE.exe+0000001" in r.stdout, r.stdout   # the [P] frame is kept
    assert "ScannedOnly.dll" not in r.stdout, r.stdout    # the [S] one is not


def crash_log_truncated_before_exception() -> str:
    """A dump CrashLogger never finished: header written, then nothing.

    This is the shape that genuinely cannot parse. The no-MODULE+OFFSET line used
    to serve this role in these fixtures, but it now parses as `(no module)+ADDR`,
    so tests that need an unparseable dump had to stop borrowing it.
    """
    return "Skyrim VR v1.4.15\nCrashLoggerSSE v1-15-0-0\n\n"


def test_crash_flags_a_degraded_parser_rather_than_reporting_ok(tmp_path):
    """The failure this exists for: CrashLoggerSSE 1.23.1 changed format and the
    tool read 1 of 7 dumps while printing RESULT: OK. The share of unparsed dumps
    is the format-agnostic symptom, so it is the thing that must go red."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-2026-01-01-00-00-01.log").write_text(
        "Skyrim SSE v1.6.1170\n"
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x1 SkyrimSE.exe+0000001\tmov\n',
        encoding="utf-8")
    for n in range(2, 6):
        (d / f"crash-2026-01-01-00-00-0{n}.log").write_text(
            crash_log_truncated_before_exception(), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "unparsed      : 4/5 (80%)" in r.stdout, r.stdout
    assert "RESULT: PARSER DEGRADED" in r.stdout, r.stdout
    assert r.returncode == 1


def test_crash_does_not_flag_one_legitimate_unparsed_dump(tmp_path):
    """Priced against real data: a healthy VR install has 1 unparsed dump in 28.
    A guard that fires on that gets ignored, so it must stay quiet here.

    The first version of this test took the `crash_dir` fixture, which contains
    three VALID logs and no unparsed dump at all -- it never built the case it
    names, and passed with the threshold mutated in both directions. It now
    constructs the real shape: one dump CrashLogger truncated before it wrote an
    exception line at all, among nine that parse cleanly. (It used to use a dump
    with no MODULE+OFFSET; that shape now parses as `(no module)+ADDR`, so it is
    no longer an example of anything unparseable.)
    """
    d = tmp_path / "SKSE"
    d.mkdir()
    for n in range(1, 10):
        (d / f"crash-2026-08-26-{n:02d}.txt").write_text(
            crash_log("0B84F05", "hkpContactSolver"), encoding="utf-8")
    (d / "crash-2026-08-26-99.txt").write_text(
        crash_log_truncated_before_exception(), encoding="utf-8")

    r = run(CRASH, str(d))
    assert "unparsed      : 1/10 (10%)" in r.stdout, r.stdout
    assert "RESULT: PARSER DEGRADED" not in r.stdout, r.stdout
    assert r.returncode == 0


# --------------------------------------------------------------------------
# crash-triage -- frames coverage
#
# The unparsed count only sees dumps whose EXCEPTION LINE failed. A changed
# stack header leaves every dump nominally "parsed" with zero frames and no
# complaint, which is two of the three CrashLoggerSSE 1.2x changes.
# --------------------------------------------------------------------------

def crash_log_no_stack(offset: str) -> str:
    """A TRUNCATED dump: CrashLoggerSSE stopped writing before the stack.

    Measured on a real install: 5 of 28 dumps look like this -- 46-74 lines, no
    CALL STACK and no REGISTERS. They have no stack section to read, so they are
    not evidence about frame syntax and must not trip the frames guard.
    """
    return "\n".join([
        "Skyrim VR v1.4.15",
        "CrashLoggerSSE v1-24-0-0",
        "",
        f'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x1 SkyrimVR.exe+{offset}\tmov rax, rcx',
        "",
    ])


def crash_log_stack_header_moved(offset: str) -> str:
    """A COMPLETE dump whose stack header this parser does not recognise."""
    return "\n".join([
        "Skyrim VR v1.4.15",
        "CrashLoggerSSE v9-99-9-9",
        "",
        f'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x1 SkyrimVR.exe+{offset}\tmov rax, rcx',
        "",
        "BACKTRACE (probable / scan):",
        f"\t[ 0] 0x7FF700000001    SkyrimVR.exe+{offset}\tmov rax, rcx",
        "",
        "REGISTERS:",
        "\tRAX 0x0",
    ])


def test_crash_flags_a_stack_header_change_that_leaves_dumps_nominally_parsed(tmp_path):
    """The blind spot the unparsed count cannot see: every dump parses, every dump
    yields zero frames. One dump lacking frames is ordinary; all of them lacking
    them is the header having moved."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for n, off in enumerate(("0B84F05", "1234567", "0AAAAAA"), start=1):
        (d / f"crash-2026-08-26-{n:02d}.log").write_text(
            crash_log_stack_header_moved(off), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "unparsed      : 0/3 (0%)" in r.stdout, r.stdout
    assert "ZERO stack frames" in r.stdout, r.stdout
    assert r.returncode == 1


def test_crash_does_not_flag_truncated_dumps_as_a_format_change(tmp_path):
    """The false positive this guard shipped with: 5 of 28 real dumps are truncated
    writes with no stack section at all. Triaged alone they reported that the format
    had changed. A guard that fires on healthy input gets ignored."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for n, off in enumerate(("0B84F05", "1234567"), start=1):
        (d / f"crash-2026-08-26-{n:02d}.log").write_text(
            crash_log_no_stack(off), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "ZERO stack frames" not in r.stdout, r.stdout
    assert "RESULT: OK" in r.stdout, r.stdout
    assert r.returncode == 0


def test_crash_parses_an_exception_with_no_module_attribution(tmp_path):
    """A jump to an address inside no loaded module leaves CrashLogger nothing to
    attribute, and the line ends after the address. Requiring MODULE+OFFSET made
    such a dump unparseable forever and counted it as "no exception" -- reporting a
    crash we DID read as one we did not. Found in 1 of 28 real dumps."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-2026-06-28-14-50-36.log").write_text(
        "Skyrim VR v1.4.15\n"
        "CrashLoggerSSE v1-15-0-0\n"
        "\n"
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x000000000001\n',
        encoding="utf-8")
    r = run(CRASH, str(d))
    assert "no exception  : 0" in r.stdout, r.stdout
    assert "(no module)+000000000001" in r.stdout, r.stdout


def test_crash_still_reads_the_module_when_there_is_one(crash_dir):
    """The control for the case above: making MODULE+OFFSET optional must not stop
    it being captured when present. Without this, 'optional' could silently become
    'ignored' and every signature would collapse to (no module)."""
    r = run(CRASH, str(crash_dir))
    assert "SkyrimVR.exe+0B84F05" in r.stdout, r.stdout
    assert "(no module)" not in r.stdout, r.stdout
