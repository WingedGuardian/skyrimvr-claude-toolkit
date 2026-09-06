"""The gate that makes every other test in this suite mean something.

A regression test written *after* its bug was fixed passes on the first run,
which proves nothing at all -- it may be asserting the wrong thing, or nothing.
The only proof is to put the bug back and watch the test fail.

So each mutation below reverts one shipped fix on a throwaway copy of the repo,
runs the test that is supposed to guard it, and asserts that test **fails**. If
someone later rewrites a guard into something that no longer bites, this goes
red. Modelled on the X4 toolkit's `mutation_probe` gate.

The working tree is never touched: every mutation is applied to a fresh copy
under pytest's tmp_path, and pytest is re-invoked inside that copy.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

BS = chr(92)

# Copies for mutation must INCLUDE tests/ (unlike conftest.copy_repo, which
# deliberately excludes it) -- we re-run the suite inside the copy.
MUTATION_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"
)

# (id, relative file, text to find, replacement, test that must then fail)
MUTATIONS = [
    pytest.param(
        "setup.sh",
        "JQ_PATH=$(printf '%s' " + '"$JQ_PATH"' + " | tr '" + BS + "134' '/')",
        "",
        "tests/test_jq_path.py",
        id="jq-path-normalization",
    ),
    pytest.param(
        "setup.sh",
        "      | sed 's/^@ByteArray(" + BS + "(.*" + BS + "))$/" + BS + "1/'",
        "",
        "tests/test_setup_paths.py::test_byte_array_wrapping_is_unwrapped",
        id="byte-array-unwrap",
    ),
    pytest.param(
        "setup.sh",
        '               "$GAME_DIR"/../*/ModOrganizer.ini; do',
        "               ; do",
        "tests/test_setup_paths.py::test_portable_mo2_instance_is_detected",
        id="portable-sibling-probe",
    ),
    pytest.param(
        "setup.sh",
        "LOCALAPPDATA_DIR=$(printf '%s' " + '"$LOCALAPPDATA_DIR"' + " | tr '" + BS + "134' '/')",
        "",
        "tests/test_setup_paths.py::test_paths_are_not_backslash_corrupted",
        id="localappdata-normalization",
    ),
    # --- tools/devbench-cli.sh -------------------------------------------
    pytest.param(
        "tools/devbench-cli.sh",
        'if [ "${pend:-0}" -gt 0 ] 2>/dev/null && [ "$t1" = "$t2" ]; then',
        "if false; then",
        "tests/test_devbench_cli.py::test_starved_main_thread_is_reported_as_hung",
        id="hang-vs-pause-discrimination",
    ),
    pytest.param(
        "tools/devbench-cli.sh",
        'case "$f2" in -*)',
        'case "$f2" in __never_matches__)',
        "tests/test_devbench_cli.py::test_no_save_loaded_is_its_own_state",
        id="no-save-loaded-detection",
    ),
    pytest.param(
        # Reproduce the actual pre-v3.5.1 bug: every HTTP status counted as
        # success, so a 504 printed as though the call had worked. Perturbing
        # only the `504)` arm is not enough -- it falls through to the generic
        # error arm, which still reports failure and still mentions 504.
        "tools/devbench-cli.sh",
        "        2*) return 0 ;;",
        "        *) return 0 ;;",
        "tests/test_devbench_cli.py::test_504_is_reported_as_a_busy_main_thread",
        id="http-status-is-honored",
    ),
    # --- repo invariants ---------------------------------------------------
    pytest.param(
        # Reproduce the real drift: README's copy of the setup prompt loses the
        # DevBench sentence while the canonical copy keeps it.
        "README.md",
        "Separately, TELL me about DevBench but do NOT install it:",
        "",
        "tests/test_repo_invariants.py::test_setup_prompt_is_identical_everywhere",
        id="setup-prompt-drift",
    ),
    pytest.param(
        # Neuter the node tests without deleting the file: node --test still
        # finds and runs it, but it registers nothing, so the run reports
        # "pass 0" -- the vacuous-green state the guard exists to catch.
        "tools/xelib/active-plugins.test.js",
        "const test = require('node:test');",
        "const test = () => {};",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
        id="npm-test-vacuity",
    ),
    # --- plugins.txt ambiguity (found live 2026-08-27) ----------------------
    # All three run the node suite, which test_npm_test_actually_runs_tests
    # gates on returncode 0, so a failing node test fails that pytest test.
    pytest.param(
        # Stop reporting the rival candidates. findPluginsTxt then looks
        # correct -- it still picks the right file -- but the caller can no
        # longer tell that a second, possibly stale, plugins.txt exists.
        "tools/xelib/active-plugins.js",
        "return { chosen: existing[0], others: existing.slice(1) };",
        "return { chosen: existing[0], others: [] };",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
        id="plugins-txt-rivals-hidden",
    ),
    pytest.param(
        # Keep the detection, drop the warning: the exact silent-pick bug.
        "tools/xelib/active-plugins.js",
        "    if (others.length) {",
        "    if (false) {",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
        id="plugins-txt-warning-silenced",
    ),
    pytest.param(
        # The other direction, and the reason it is here: "an unambiguous
        # choice stays silent" passed BEFORE the feature existed, when nothing
        # warned at all. Warning unconditionally must break it, or that test is
        # still vacuous and only looks like coverage.
        "tools/xelib/active-plugins.js",
        "    if (others.length) {",
        "    if (true) {",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
        id="plugins-txt-warning-always-fires",
    ),

    # --- skyrim-winner (node; gated via the node suite) ---------------------
    pytest.param(
        # Put back the sanitising parse. "Shinso.esp/000843" then reduces to
        # "esp000843", which is valid hex and a real, different record -- so the
        # tool answers a question nobody asked and prints RESULT: OK.
        "tools/skyrim-winner.js",
        "    if (!/^[0-9a-fA-F]{1,8}$/.test(hex)) {",
        "    if (false) {",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
        id="formid-sanitised-instead-of-rejected",
    ),
    pytest.param(
        "tools/skyrim-winner.js",
        "    return (last === path.posix.sep || last === path.win32.sep) ? dir : dir + path.sep;",
        "    return dir;",
        "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
        id="game-path-trailing-separator-dropped",
    ),

    # --- papyrus-triage -----------------------------------------------------
    pytest.param(
        # Case-sensitive severity matching. The same log spells it `warning:`
        # (99) and `WARNING:` (662); this loses 87% of them silently.
        "tools/papyrus-triage.py",
        r'SEVERITY_RE = re.compile(r"^(error|warning)\s*:\s*(.*)$", re.I)',
        r'SEVERITY_RE = re.compile(r"^(error|warning)\s*:\s*(.*)$")',
        "tests/test_triage.py::test_papyrus_counts_both_spellings_of_warning",
        id="papyrus-severity-case-sensitive",
    ),
    pytest.param(
        # Stop counting what actually bucketed. This is the independent counter
        # that replaced the tautological `total - shown`; if it stops being
        # independent, the accounting check goes back to being x == x.
        "tools/papyrus-triage.py",
        "            bucketed += 1",
        "            pass",
        "tests/test_triage.py::test_papyrus_accounting_balances_and_reports_ok",
        id="papyrus-accounting-counter-neutered",
    ),

    # --- crash-triage -------------------------------------------------------
    pytest.param(
        # Drop the knowledgebase lookup: every crash reads as a new finding,
        # including the one already ruled ACCEPTED three investigations ago.
        "tools/crash-triage.py",
        '        status, note = KNOWN.get(r["offset"], ("UNKNOWN", ""))',
        '        status, note = ("UNKNOWN", "")',
        "tests/test_triage.py::test_crash_labels_an_accepted_signature_from_the_knowledgebase",
        id="crash-known-signatures-ignored",
    ),
    pytest.param(
        # Count only the logs that parsed. A log with no exception line then
        # vanishes from the denominator, and the report claims full coverage.
        "tools/crash-triage.py",
        "    accounted = len(parsed) + len(no_exception) + len(unreadable)",
        "    accounted = len(parsed)",
        "tests/test_triage.py::test_crash_counts_a_log_with_no_exception_line_rather_than_dropping_it",
        id="crash-unparsed-logs-dropped-from-denominator",
    ),

    pytest.param(
        # Go back to matching only `.txt`. On a modern install CrashLoggerSSE
        # writes `.LOG`, so the tool reports a complete-looking total built
        # entirely from years-old logs -- the failure mode with no error.
        "tools/crash-triage.py",
        'CRASH_NAME_RE = re.compile(r"^crash-.+' + BS + '.(txt|log)$", re.I)',
        'CRASH_NAME_RE = re.compile(r"^crash-.+' + BS + '.txt$")',
        "tests/test_triage.py::test_crash_finds_dot_log_files_not_only_dot_txt",
        id="crash-log-extension-half-matched",
    ),
    pytest.param(
        # Match any `.log` in the folder, not just `crash-` dumps. Every SKSE
        # plugin's own log then becomes a phantom crash in the denominator.
        "tools/crash-triage.py",
        'CRASH_NAME_RE = re.compile(r"^crash-.+' + BS + '.(txt|log)$", re.I)',
        'CRASH_NAME_RE = re.compile(r".+' + BS + '.(txt|log)$", re.I)',
        "tests/test_triage.py::test_crash_ignores_the_crashlogger_plugin_log",
        id="crash-prefix-not-required",
    ),

    pytest.param(
        # Stop treating a mostly-unparsed folder as a finding. This is the exact
        # state CrashLoggerSSE 1.23.1 produced -- 1 of 7 dumps read, RESULT: OK --
        # where the evidence was on screen and nothing called it a problem.
        "tools/crash-triage.py",
        "    parse_degraded = (total_break",
        "    parse_degraded = False and (total_break",
        "tests/test_triage.py::test_crash_flags_a_degraded_parser_rather_than_reporting_ok",
        id="crash-degraded-parser-not-flagged",
    ),

    pytest.param(
        # Lower the floor so a single unparsed dump trips the guard. A healthy VR
        # install has exactly one (a crash inside no loaded module, which can never
        # parse), so this is the false positive that would get the guard ignored.
        "tools/crash-triage.py",
        "                      or (unparsed >= 2 and unparsed_pct > 25.0)",
        "                      or (unparsed >= 1 and unparsed_pct > 5.0)",
        "tests/test_triage.py::test_crash_does_not_flag_one_legitimate_unparsed_dump",
        id="crash-degraded-threshold-too-eager",
    ),

    pytest.param(
        # Stop noticing that every parsed dump yielded zero frames. This is the half
        # of the CrashLoggerSSE 1.2x break the unparsed count cannot see: the
        # exception line still parses, so nothing else complains.
        "tools/crash-triage.py",
        "    frames_broken = len(complete) >= 2 and frameless == len(complete)",
        "    frames_broken = False",
        "tests/test_triage.py::test_crash_flags_a_stack_header_change_that_leaves_dumps_nominally_parsed",
        id="crash-frames-break-not-flagged",
    ),

    pytest.param(
        # Count truncated dumps as evidence about frame syntax. They have no stack
        # section at all, so this turns 5 of 28 real dumps into a format-break verdict
        # -- the false positive the guard shipped with.
        "tools/crash-triage.py",
        "    complete = [r for r in parsed if r[\"complete\"]]",
        "    complete = list(parsed)",
        "tests/test_triage.py::test_crash_does_not_flag_truncated_dumps_as_a_format_change",
        id="crash-frames-counts-truncated-dumps",
    ),

    pytest.param(
        # Require MODULE+OFFSET again. A crash at an address inside no loaded module
        # then becomes permanently unparseable and is counted as "no exception" --
        # a dump we read, reported as one we did not.
        "tools/crash-triage.py",
        "    r'(?:\\s+(?P<module>[^\\s+]+)\\+(?P<offset>[0-9A-Fa-f]+))?'",
        "    r'\\s+(?P<module>[^\\s+]+)\\+(?P<offset>[0-9A-Fa-f]+)'",
        "tests/test_triage.py::test_crash_parses_an_exception_with_no_module_attribution",
        id="crash-requires-module-attribution",
    ),

    pytest.param(
        # Stop noticing dumps the pattern missed. This is the "8 log(s) [OK]" failure:
        # a confident, internally consistent total built from whichever half of the
        # folder happened to match.
        "tools/crash-triage.py",
        "    all_ok = ok and sig_ok and not parse_degraded and not near_misses",
        "    all_ok = ok and sig_ok and not parse_degraded",
        "tests/test_triage.py::test_crash_flags_a_dump_the_pattern_did_not_match",
        id="crash-near-misses-not-gated",
    ),

    pytest.param(
        # Widen the near-miss detector back to "crash" anywhere in the name, which
        # fires on ordinary mod logs that sit beside real dumps on every install.
        "tools/crash-triage.py",
        'CRASH_PREFIX_RE = re.compile(r"^crash[-_]", re.I)',
        'CRASH_PREFIX_RE = re.compile(r"crash", re.I)',
        "tests/test_triage.py::test_crash_does_not_flag_ordinary_mod_logs_as_near_misses",
        id="crash-near-miss-detector-too-broad",
    ),

    pytest.param(
        # Remove the population floor from the frames guard. A single complete dump
        # with an empty stack section -- a real shape, a jump with no return chain --
        # then reports that the stack format changed.
        "tools/crash-triage.py",
        "    frames_broken = len(complete) >= 2 and frameless == len(complete)",
        "    frames_broken = bool(complete) and frameless == len(complete)",
        "tests/test_triage.py::test_crash_does_not_call_one_frameless_dump_a_format_change",
        id="crash-frames-floor-removed",
    ),

    pytest.param(
        # Annotate only when the verdict gate fires. A non-zero unparsed count
        # below the floor then prints bare above RESULT: OK, which is the silence
        # the two-tier annotation exists to remove.
        "tools/crash-triage.py",
        "    elif unparsed:",
        "    elif False:",
        "tests/test_triage.py::test_crash_annotates_an_unparsed_dump_below_the_format_change_floor",
        id="crash-unparsed-annotation-silent-below-floor",
    ),

    pytest.param(
        # Drop the near-miss clause from the degraded verdict. Both problems are
        # present, only one is named, and they are fixed in different places.
        "tools/crash-triage.py",
        'f"changed; compare a failing dump against EXC_RE and FRAME_RE.{also_near}")',
        'f"changed; compare a failing dump against EXC_RE and FRAME_RE.")',
        "tests/test_triage.py::test_crash_verdict_names_the_short_input_set_as_well_as_the_parser",
        id="crash-degraded-verdict-hides-near-misses",
    ),

    pytest.param(
        # Go back to "No crash-*.txt or crash-*.log found" with nothing else --
        # a folder holding unmatched dumps then reads as a folder holding none.
        "tools/crash-triage.py",
        "        if missed:",
        "        if False:",
        "tests/test_triage.py::test_crash_says_none_matched_rather_than_none_found",
        id="crash-none-found-hides-near-misses",
    ),

    pytest.param(
        # Gate the coverage check on resyncs again. The chunk walk breaks out without
        # incrementing resyncs when it finds nothing, so this suppressed the guard on
        # exactly the degenerate input it exists for.
        "tools/cosave-info.py",
        '    if _r.get("coveragePct", 0) < 90 or _r.get("chunkCount", 0) == 0:',
        '    if _r.get("resyncs", 0) and _r.get("coveragePct", 100) < 90:',
        "tests/test_cosave.py::test_cosave_degrades_on_a_stub_that_parses_no_chunks",
        id="cosave-coverage-guard-self-suppressing",
    ),

    pytest.param(
        # Drop the try/except around survey(). A missing file then exits 1, which this
        # tool's own contract defines as "parsed but degraded".
        "tools/cosave-info.py",
        "    except Exception as e:",
        "    except ZeroDivisionError as e:",
        "tests/test_cosave.py::test_cosave_refuses_a_missing_file",
        id="cosave-survey-exceptions-unhandled",
    ),

    pytest.param(
        # Stop reporting how old the auto-selected log is. A months-old log then
        # reads exactly like the current session -- an answer about a world that has
        # moved on, presented as an answer.
        "tools/papyrus-triage.py",
        "    STALE_DAYS = 7",
        "    STALE_DAYS = 99999",
        "tests/test_triage.py::test_papyrus_reports_the_age_of_an_auto_selected_log",
        id="papyrus-staleness-never-fires",
    ),

    pytest.param(
        # Go back to exiting 0 for a file that is not a cosave at all.
        "tools/cosave-info.py",
        "        sys.exit(2)",
        "        sys.exit(0)",
        "tests/test_cosave.py::test_cosave_refuses_a_file_that_is_not_a_cosave",
        id="cosave-non-cosave-exits-zero",
    ),

    pytest.param(
        # Widen the --help line range back over the code. A banner that prints too
        # much still looks like a banner, which is why this rotted unnoticed.
        "tools/esp-verify-wrapper.sh",
        "    sed -n '2,40p' \"$0\"",
        "    sed -n '2,44p' \"$0\"",
        "tests/test_repo_invariants.py::test_help_banner_range_stays_inside_the_comment_header",
        id="help-banner-leaks-source",
    ),

    # --- skyrim_paths -------------------------------------------------------
    pytest.param(
        "tools/skyrim_paths.py",
        "    return existing[0], existing[1:]",
        "    return existing[0], []",
        "tests/test_skyrim_paths.py::test_every_rival_candidate_is_reported",
        id="game-dir-rivals-hidden",
    ),
    pytest.param(
        "tools/skyrim_paths.py",
        "    if others:",
        "    if False:",
        "tests/test_skyrim_paths.py::test_ambiguity_warns_naming_winner_loser_and_the_override",
        id="game-dir-warning-silenced",
    ),
]


def _mutate(tmp_path: Path, relpath: str, find: str, replace: str) -> Path:
    work = tmp_path / "mutated"
    shutil.copytree(REPO, work, ignore=MUTATION_IGNORE)
    target = work / relpath
    # io.open, not Path.read_text: the `newline` kwarg only exists on Path in
    # 3.13+, and line endings must be preserved or the mutation rewrites the
    # whole file and masks what it changed.
    text = io.open(target, encoding="utf-8", newline="").read()
    assert find in text, (
        f"mutation anchor not found in {relpath} -- the code moved and this "
        f"gate is now testing nothing:\n  {find!r}"
    )
    io.open(target, "w", encoding="utf-8", newline="").write(text.replace(find, replace))
    return work


@pytest.mark.parametrize("relpath,find,replace,test_id", MUTATIONS)
def test_guard_actually_bites(tmp_path, relpath, find, replace, test_id):
    """Revert one fix; the test guarding it must fail."""
    work = _mutate(tmp_path, relpath, find, replace)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(work), capture_output=True, text=True, timeout=600,
    )
    # EXIT 1 SPECIFICALLY, not merely non-zero.
    #
    # pytest exits 1 for "a test failed", but 2 for a collection error, 4 for a
    # usage error and 5 for "no tests collected". A mutation naming a test that was
    # since renamed or deleted therefore satisfied `!= 0` and passed forever while
    # proving nothing -- the stale-ANCHOR problem on the other side of the pair,
    # and only the anchor half was asserted. Two anchors went stale during one
    # release arc, so this is a demonstrated shape here, not a hypothetical.
    #
    # MEASURED at the time this went in: all 38 mutations named a collectable
    # test, so nothing was actually rotten -- the gate simply could not have told
    # us if it were.
    assert result.returncode == 1, (
        f"expected {test_id} to FAIL (pytest exit 1) with the fix reverted, got "
        f"exit {result.returncode}.\n"
        + ("The test still PASSED -- it does not guard this bug and must be "
           "rewritten.\n" if result.returncode == 0 else
           "That is not a test failure: 2 = collection error, 4 = usage error, "
           "5 = nothing collected. Most likely the test was renamed or removed "
           "and this mutation now proves nothing.\n")
        + f"--- mutation ---\n{find!r} -> {replace!r}\n"
        f"--- pytest output ---\n{result.stdout[-2000:]}"
    )
