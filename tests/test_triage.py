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


def test_crash_flags_a_dump_the_pattern_did_not_match(crash_dir):
    """The input-set failure, distinct from a parsing failure: a real dump whose
    EXTENSION drifted out of CRASH_NAME_RE is silently outside the denominator. This
    is what produced "8 log(s) [OK]" while 20 newer dumps sat unread in the same
    folder."""
    (crash_dir / "crash-2027-01-01-00-00-00.dmp").write_text(
        crash_log("0B84F05", "hkpContactSolver"), encoding="utf-8")
    r = run(CRASH, str(crash_dir))
    assert "near misses   : 1" in r.stdout, r.stdout
    assert "crash-2027-01-01-00-00-00.dmp" in r.stdout, r.stdout
    assert "RESULT: INPUT SET INCOMPLETE" in r.stdout, r.stdout
    assert r.returncode == 1


def test_crash_does_not_flag_ordinary_mod_logs_as_near_misses(crash_dir):
    """Priced against real input. The first version of this guard keyed on the word
    "crash" anywhere in the filename and fired on LeveledListCrashPrevention.log and
    CrashLogger.log -- ordinary mod logs that sit in the same folder on every
    install. A guard that trips on healthy input gets ignored."""
    (crash_dir / "LeveledListCrashPrevention.log").write_text("mod log", encoding="utf-8")
    (crash_dir / "CrashLogger.log").write_text("the plugin's own log", encoding="utf-8")
    r = run(CRASH, str(crash_dir))
    assert "near misses   : 0" in r.stdout, r.stdout
    assert "RESULT: INPUT SET INCOMPLETE" not in r.stdout, r.stdout
    assert r.returncode == 0


# --------------------------------------------------------------------------
# papyrus-triage -- staleness
#
# Three outcomes, not two: an answer, a non-answer, and an answer about a world
# that has moved on. Triaging a months-old log as if it were the current session
# is the third, and it reads exactly like the first.
# --------------------------------------------------------------------------

def _papyrus_log() -> str:
    return "\n".join([
        "[01/01/2026 - 12:00:00AM] Papyrus log opened",
        "[01/01/2026 - 12:00:01AM] error: Cannot call Foo() on a None object",
        "[01/01/2026 - 12:00:02AM] warning: Property Bar has no value",
    ])


def test_papyrus_reports_the_age_of_an_auto_selected_log(tmp_path, monkeypatch):
    """The log's age has to be on screen. It is the difference between a report on
    this session and a confident report on one from months ago."""
    import os, time
    d = tmp_path / "Logs" / "Script"
    d.mkdir(parents=True)
    log = d / "Papyrus.0.log"
    log.write_text(_papyrus_log(), encoding="utf-8")
    old = time.time() - 60 * 86400
    os.utime(log, (old, old))
    monkeypatch.setenv("PAPYRUS_LOG_DIR", str(d))
    r = run(PAPYRUS)
    assert "log age" in r.stdout, r.stdout
    assert "STALE" in r.stdout, r.stdout


def test_papyrus_does_not_call_a_fresh_log_stale(tmp_path, monkeypatch):
    """The control. A guard that calls every log stale says nothing."""
    d = tmp_path / "Logs" / "Script"
    d.mkdir(parents=True)
    (d / "Papyrus.0.log").write_text(_papyrus_log(), encoding="utf-8")
    monkeypatch.setenv("PAPYRUS_LOG_DIR", str(d))
    r = run(PAPYRUS)
    assert "log age" in r.stdout, r.stdout
    assert "STALE" not in r.stdout, r.stdout


def test_papyrus_does_not_second_guess_an_explicitly_named_log(tmp_path, monkeypatch):
    """Staleness applies only to the log the tool CHOSE. Naming a file is a
    deliberate act and must not be argued with."""
    import os, time
    d = tmp_path / "Logs" / "Script"
    d.mkdir(parents=True)
    log = d / "Papyrus.0.log"
    log.write_text(_papyrus_log(), encoding="utf-8")
    old = time.time() - 60 * 86400
    os.utime(log, (old, old))
    r = run(PAPYRUS, str(log))
    assert "log age" not in r.stdout, r.stdout


def crash_log_empty_stack(offset: str) -> str:
    """A COMPLETE dump whose stack section is genuinely empty.

    Real shape, not invented: a jump to an address inside no loaded module has no
    return chain for CrashLogger to walk, so it writes the CALL STACK header and
    REGISTERS with nothing between them. One of the 28 dumps on the dev install is
    exactly this (crash-2026-06-28-14-50-36, 1,753 lines).
    """
    return "\n".join([
        "Skyrim VR v1.4.15",
        "CrashLoggerSSE v1-15-0-0",
        "",
        f'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x{offset}',
        "",
        "PROBABLE CALL STACK:",
        "",
        "REGISTERS:",
        "\tRAX 0x0",
    ])


def test_crash_does_not_call_one_frameless_dump_a_format_change(tmp_path):
    """A SINGLE dump proves nothing about frame syntax. Before the floor, this dump
    alone reported that the stack header had changed and told the reader to go edit
    FRAME_RE -- a prescription that would break real parsing. The unparsed guard has
    a >= 2 floor for the same reason; this one needed it too."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-2026-06-28-14-50-36.log").write_text(
        crash_log_empty_stack("000000000001"), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "ZERO stack frames" not in r.stdout, r.stdout
    assert "RESULT: OK" in r.stdout, r.stdout
    assert r.returncode == 0


def test_crash_still_flags_a_format_change_across_several_dumps(tmp_path):
    """The control for the floor above: two or more frameless COMPLETE dumps is a
    format change and must still fire. A floor that silenced the guard entirely
    would look identical in the test above."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for n, off in enumerate(("0B84F05", "1234567", "0AAAAAA"), start=1):
        (d / f"crash-2026-08-26-{n:02d}.log").write_text(
            crash_log_stack_header_moved(off), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "ZERO stack frames" in r.stdout, r.stdout
    assert r.returncode == 1


def crash_log_unreadable_shape() -> str:
    """A file the exception matcher cannot key on at all."""
    return "this file is named like a dump but carries no exception line\n" * 5


def test_crash_annotates_an_unparsed_dump_below_the_format_change_floor(tmp_path):
    """1 unparsed of 2 is 50% -- over the ratio, under the >= 2 floor -- and so
    printed a bare "1/2 (50%)" above RESULT: OK with nothing saying those bytes
    went unread. The floor is right and stays: one unparsed dump is not evidence
    of a format change, and lowering it re-introduces the false positive that
    fired on 5 of 28 real dumps. But "not a format change" is not "nothing to
    see". The count must never be silent."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-a.log").write_text(crash_log("0B84F05", "hkpContactSolver"), encoding="utf-8")
    (d / "crash-b.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "unparsed      : 1/2 (50%)" in r.stdout, r.stdout
    assert "NOT read" in r.stdout, r.stdout
    # The verdict gate is deliberately untouched -- this is an annotation, not an
    # escalation.
    assert "RESULT: OK" in r.stdout, r.stdout
    assert r.returncode == 0


def test_crash_leaves_a_clean_unparsed_line_unannotated(crash_dir):
    """The control for the annotation above. A guard that fires on everything is
    indistinguishable from one that works, and an arrow on "0/3 (0%)" would be a
    contradiction on one line."""
    r = run(CRASH, str(crash_dir))
    line = [l for l in r.stdout.splitlines() if l.startswith("  unparsed")]
    assert len(line) == 1, r.stdout
    assert "0/3 (0%)" in line[0], line[0]
    assert "<--" not in line[0], line[0]
    assert r.returncode == 0


def test_crash_verdict_names_the_short_input_set_as_well_as_the_parser(tmp_path):
    """Both conditions firing used to print only the parser verdict. Nothing was
    hidden -- the near-miss count is in the header -- but the verdict line is the
    part people read, and a reader pointed at a parser problem has no reason to
    suspect the input set was also short. The two are fixed in different places."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for i in range(3):
        (d / f"crash-junk-{i}.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    (d / "crash-ok.log").write_text(crash_log("1234567", "Foo"), encoding="utf-8")
    (d / "crash-report.md").write_text("prefix matches, extension does not\n", encoding="utf-8")
    r = run(CRASH, str(d))
    verdict = r.stdout.split("RESULT:")[-1]
    assert "PARSER DEGRADED" in verdict, r.stdout
    assert "ALSO" in verdict, verdict
    assert "did not match the pattern" in verdict, verdict
    assert r.returncode == 1


def test_crash_degraded_verdict_stays_quiet_when_the_input_set_is_complete(tmp_path):
    """The control: no near misses, no ALSO clause. Without this, a verdict that
    always appended the clause would pass the test above."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for i in range(3):
        (d / f"crash-junk-{i}.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    (d / "crash-ok.log").write_text(crash_log("1234567", "Foo"), encoding="utf-8")
    r = run(CRASH, str(d))
    verdict = r.stdout.split("RESULT:")[-1]
    assert "PARSER DEGRADED" in verdict, r.stdout
    assert "ALSO" not in verdict, verdict
    assert r.returncode == 1


def test_crash_says_none_matched_rather_than_none_found(tmp_path):
    """"No dumps found" and "no dumps MATCHED" are different answers, and the tool
    printed the first when the second was true -- the near-miss failure in its
    purest form, telling the reader a folder is empty of dumps while dumps sit in
    it under an unmatched extension. Exit 2 was never the problem; the sentence
    was."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-2026-01-01.md").write_text("a dump under an extension we do not match\n", encoding="utf-8")
    (d / "ordinary.log").write_text("not a dump at all\n", encoding="utf-8")
    r = run(CRASH, str(d))
    assert r.returncode == 2, r.stdout + r.stderr
    both = r.stdout + r.stderr
    assert "crash-2026-01-01.md" in both, both
    assert "crash-/crash_ prefix" in both, both
    # The ordinary log must NOT be dragged in: the prefix is the whole point.
    assert "ordinary.log" not in both, both


def test_crash_prints_the_frames_denominator_even_when_the_guard_stays_quiet(tmp_path):
    """`frameless` is the sibling of `unparsed` and was printed under NO condition at
    all. So a single complete dump whose stack header this parser no longer knows was
    reported as an UNKNOWN crash "worth looking at", with no frames, no explanation,
    RESULT: OK and exit 0 -- the word "frame" appearing nowhere in the output. The
    >= 2 floor decides the VERDICT; it was never a licence to omit the fact."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-a.log").write_text(crash_log_stack_header_moved("7654321"), encoding="utf-8")
    r = run(CRASH, str(d))
    assert "with frames   : 0/1" in r.stdout, r.stdout
    assert "too few complete dumps" in r.stdout, r.stdout
    # The floor is untouched: this is an annotation, not an escalation.
    assert "RESULT: OK" in r.stdout, r.stdout
    assert r.returncode == 0


def test_crash_frames_denominator_is_unannotated_when_every_dump_has_frames(crash_dir):
    """The control. An annotation that always fires cannot be told apart from one that
    works, and "3/3 complete dumps <-- the rest did" would be nonsense."""
    r = run(CRASH, str(crash_dir))
    line = [l for l in r.stdout.splitlines() if l.startswith("  with frames")]
    assert len(line) == 1, r.stdout
    assert "3/3" in line[0], line[0]
    assert "<--" not in line[0], line[0]


def test_crash_unparsed_note_names_the_clause_that_actually_held(tmp_path):
    """The gate is a conjunction -- >= 2 AND > 25% -- and the first version of this
    message said "too few" unconditionally. At 4 unparsed of 20 the count clause is
    SATISFIED and the ratio is what keeps the guard quiet, so the sentence named the
    wrong cause and would send a reader to lower a floor that is not responsible."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for i in range(16):
        (d / f"crash-ok-{i:02d}.log").write_text(crash_log(f"{i:07d}", "Foo"), encoding="utf-8")
    for i in range(4):
        (d / f"crash-bad-{i}.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    r = run(CRASH, str(d))
    line = [l for l in r.stdout.splitlines() if l.startswith("  unparsed")][0]
    assert "4/20 (20%)" in line, line
    assert "too few" not in line, line
    assert "under the 25% mark" in line, line
    assert r.returncode == 0


def test_crash_unparsed_note_still_says_too_few_when_that_is_the_reason(tmp_path):
    """The control for the wording above: at 1 of 2 the count clause IS the reason."""
    d = tmp_path / "SKSE"
    d.mkdir()
    (d / "crash-a.log").write_text(crash_log("0B84F05", "hkpContactSolver"), encoding="utf-8")
    (d / "crash-b.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    r = run(CRASH, str(d))
    line = [l for l in r.stdout.splitlines() if l.startswith("  unparsed")][0]
    assert "too few" in line, line


def test_crash_verdict_names_a_simultaneous_frames_break(tmp_path):
    """CrashLoggerSSE 1.2x changed the exception line AND the stack header at once, so
    both guards fire. The unparsed branch wins the if/elif and told the reader the
    surviving dumps carried usable signatures -- they carry no call stacks at all."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for i in range(4):
        (d / f"crash-junk-{i}.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    for i, off in enumerate(("1111111", "2222222")):
        (d / f"crash-moved-{i}.log").write_text(crash_log_stack_header_moved(off), encoding="utf-8")
    r = run(CRASH, str(d))
    verdict = r.stdout.split("RESULT:")[-1]
    assert "PARSER DEGRADED" in verdict, r.stdout
    assert "zero stack frames" in verdict.lower(), verdict
    assert r.returncode == 1


def test_crash_verdict_makes_no_frames_claim_when_frames_are_fine(tmp_path):
    """The control: dumps that parse and DO yield frames must not attract the clause."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for i in range(4):
        (d / f"crash-junk-{i}.log").write_text(crash_log_unreadable_shape(), encoding="utf-8")
    for i, off in enumerate(("3333333", "4444444")):
        (d / f"crash-ok-{i}.log").write_text(crash_log(off, "Foo"), encoding="utf-8")
    r = run(CRASH, str(d))
    verdict = r.stdout.split("RESULT:")[-1]
    assert "PARSER DEGRADED" in verdict, r.stdout
    assert "zero stack frames" not in verdict.lower(), verdict


def test_crash_caps_a_runaway_near_miss_listing(tmp_path):
    """A folder whose extension pattern broke would print one line per file, and the
    dev install holds 341. The COUNT is the finding; the names are a sample."""
    d = tmp_path / "SKSE"
    d.mkdir()
    for i in range(25):
        (d / f"crash-{i:03d}.md").write_text("x\n", encoding="utf-8")
    r = run(CRASH, str(d))
    both = r.stdout + r.stderr
    assert both.count("    !! ") == 10, both
    assert "and 15 more" in both, both
