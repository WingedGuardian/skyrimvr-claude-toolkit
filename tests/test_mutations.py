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
    # Put the SUBSTRING path test back. It asked "does this command mention a path
    # containing the word Skyrim", which matched the session scratchpad, this repo's
    # own checkout, C:/Temp/skyrim-notes.txt and a git URL -- refusing ordinary work
    # as "deleting the game". The named row below must go red, or nothing stops that
    # rule quietly returning.
    pytest.param(
        "tools/skyguard/paths.py",
        "    _add(game, root_posix)\n    _add(game, root_win)\n",
        "    game.append(" + 'r"[A-Za-z]:[/' + BS * 2 + '].*Skyrim")' + "\n",
        # Task 7: a resolved delete operand no longer uses this regex; clause (b)'s root MENTION test still does.
        "tests/test_skyguard_deletes.py::test_a_path_that_merely_contains_skyrim_is_not_a_root_mention",
        id="install-root-resolved-not-substring-matched",
    ),
    # And the other direction: a guard that cannot work out which directory the install
    # is must REFUSE, not shrug. Deleting the fail-closed branch has to break the rows
    # that prove the install still cannot be deleted.
    pytest.param(
        "tools/skyguard/shell_rules.py",
        'if g == "":',
        'if False:',
        "tests/test_hooks.py::test_the_delete_guard_refuses_when_it_cannot_locate_the_install",
        id="delete-guard-fails-closed-when-no-root-resolves",
    ),
    # Stop recording the resolved paths. The hooks keep their self-derived root, so
    # the install itself is still guarded and nothing looks broken -- but the CONFIG
    # directory silently stops being guarded, which is the quiet half of the change.
    pytest.param(
        "setup.sh",
        '} > "$GAME_DIR/.claude/skyrim-paths.env"',
        "} > /dev/null",
        "tests/test_setup_paths.py::test_setup_writes_a_usable_paths_file_for_the_hooks",
        id="setup-records-paths-for-the-hooks",
    ),
    pytest.param(
        "setup.sh",
        "| tr -d '" + BS + "r' | tr '" + BS + "134' '/')",
        "| tr -d '" + BS + "r')",
        "tests/test_python_path.py::test_backslash_python_path_reaches_hooks_intact",
        id="python-path-normalization",
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
    # --- the thin Claude wrappers ------------------------------------------
    # Pass ANY engine output through as the verdict. An engine that exits 0 with a stray line on
    # stdout then reaches the runtime as "no decision" -- an allow -- instead of being refused.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        '    "{"*) [ "$RC" -eq 0 ]',
        '    *) [ "$RC" -eq 0 ]',
        "tests/test_hooks.py::test_wrapper_denies_on_non_json_output",
        id="wrapper-passes-non-json-engine-output-through",
    ),
    # Trust JSON on stdout even when the engine exited non-zero (it crashed after printing).
    pytest.param(
        ".claude/hooks/protect-files.sh",
        '    "{"*) [ "$RC" -eq 0 ] &&',
        '    "{"*) true &&',
        "tests/test_hooks.py::test_wrapper_denies_when_engine_prints_a_verdict_but_exits_nonzero",
        id="wrapper-ignores-the-engine-exit-status",
    ),
    # A non-blocking hook that swallows an engine crash leaves an audit log implying a backup exists.
    pytest.param(
        ".claude/hooks/backup-before-edit.sh",
        'if [ "$RC" -ne 0 ]; then',
        'if false; then',
        "tests/test_hooks.py::test_nonblocking_wrapper_logs_when_engine_crashes",
        id="nonblocking-wrapper-hides-an-engine-crash",
    ),
    pytest.param(
        ".claude/hooks/snapshot-before-tool.sh",
        'if ! command -v "$PY" >/dev/null 2>&1; then',
        'if false; then',
        "tests/test_hooks.py::test_nonblocking_wrapper_logs_without_python",
        id="nonblocking-wrapper-hides-a-missing-python",
    ),
    # Put the single-backslash `_SELF` back. In bash `${v//\/}` DELETES every `/` rather than turning
    # `\` into `/`, so a hook launched as settings.json launches it (forward or mixed slashes) resolved
    # the wrong install root and every blocking hook denied every call -- while the suite, which passed
    # all-backslash paths, stayed green.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        '_SELF="${BASH_SOURCE[0]//' + BS * 2 + '//}"',
        '_SELF="${BASH_SOURCE[0]//' + BS + '//}"',
        "tests/test_hooks.py::test_protect_bash_resolves_its_install_however_it_is_launched",
        id="wrapper-self-path-normalisation-deletes-slashes",
    ),
    pytest.param(
        ".claude/hooks/backup-before-edit.sh",
        '_SELF="${BASH_SOURCE[0]//' + BS * 2 + '//}"',
        '_SELF="${BASH_SOURCE[0]//' + BS + '//}"',
        "tests/test_hooks.py::test_nonblocking_hooks_find_their_engine_however_launched",
        id="nonblocking-wrapper-self-path-normalisation",
    ),
    pytest.param(
        ".claude/hooks/session-kb-guard.sh",
        '_SELF="${BASH_SOURCE[0]//' + BS * 2 + '//}"',
        '_SELF="${BASH_SOURCE[0]//' + BS + '//}"',
        "tests/test_hooks.py::test_session_kb_guard_finds_its_engine_however_it_is_launched",
        id="session-wrapper-self-path-normalisation",
    ),
    # The fork-free location block (latency fix). Drop a character from the textual /c/x -> C:/x
    # conversion: python.exe is then handed `C:Users/...` (drive-relative) and cannot open the engine.
    # Git Bash's own argument conversion would mask it, so the named test switches that off.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        'ROOT_WIN="${ROOT_WIN^}:${ROOT:2}"',
        'ROOT_WIN="${ROOT_WIN^}:${ROOT:3}"',
        "tests/test_hooks.py::test_a_drive_root_is_converted_to_its_windows_spelling",
        id="wrapper-drive-root-spelling",
    ),
    # Lose the `pwd -W` fallback for a root on an MSYS mount (/tmp/...), which has no textual spelling.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        '    msys*:/*)          ROOT_WIN=',
        '    msys*:/__never__)  ROOT_WIN=',
        "tests/test_hooks.py::test_a_root_on_an_msys_mount_still_gets_its_windows_spelling",
        id="wrapper-msys-mount-root-spelling",
    ),
    # The same block must be the same CODE in every hook, or the protect-bash tests above stop covering
    # the other four.
    pytest.param(
        ".claude/hooks/snapshot-before-tool.sh",
        '    msys*:/*)          ROOT_WIN=',
        '    msys*:/__never__)  ROOT_WIN=',
        "tests/test_repo_invariants.py::test_every_hook_locates_its_install_with_the_same_block",
        id="wrapper-location-block-drift",
    ),
    # mkdir runs only when the heartbeat directory is missing; make it never run. A fresh install then
    # gets no heartbeat and hook-canary reports a live hook as dead.
    pytest.param(
        ".claude/hooks/backup-before-edit.sh",
        '[ -d "$HB_DIR" ] || mkdir -p "$HB_DIR"',
        '[ -d "$PROJECT_DIR" ] || mkdir -p "$HB_DIR"',
        "tests/test_hooks.py::test_a_fresh_install_gets_its_heartbeat_directory",
        id="wrapper-heartbeat-dir-on-fresh-install",
    ),
    # setup.sh: stop recognising the Store alias, so `py -3` -> WindowsApps wins again and every hook
    # call pays ~135 ms more interpreter start.
    pytest.param(
        "setup.sh",
        '            */[Ww]indows[Aa]pps/*) if',
        '            */__never__/*) if',
        "tests/test_setup_paths.py::test_hooks_prefer_a_regular_python_over_the_store_alias",
        id="setup-prefers-regular-python",
    ),
    # ...and the other clause: when the alias is ALL there is, it must still be written. Dropping the
    # fallback leaves the hooks unconfigured, so they refuse every command.
    pytest.param(
        "setup.sh",
        '        PYTHON_PATH="$PY_STORE"\n',
        '        :\n',
        "tests/test_setup_paths.py::test_hooks_prefer_a_regular_python_over_the_store_alias",
        id="setup-falls-back-to-store-python",
    ),
    # A re-run of setup.sh must repair a hook configured for an interpreter that has since moved.
    pytest.param(
        "setup.sh",
        'elif [ -n "$PYTHON_PATH" ] && ! grep -qxF',
        'elif false && ! grep -qxF',
        "tests/test_python_path.py::test_rerunning_setup_repairs_a_stale_interpreter_path",
        id="setup-rerun-repairs-a-stale-interpreter",
    ),
    # Stop escaping sed's replacement metacharacters in the interpreter path.
    pytest.param(
        "setup.sh",
        "PY_SED=$(printf '%s' " + '"$PYTHON_PATH"' + " | sed -e 's/[" + BS * 2 + "|&]/" + BS * 2 + "&/g')",
        'PY_SED=$PYTHON_PATH',
        "tests/test_python_path.py::test_a_path_containing_an_ampersand_is_not_mangled_by_sed",
        id="setup-escapes-sed-replacement-metacharacters",
    ),
    # An alarm from tools/kb-guard.sh must reach the session as TEXT. If the engine instead fails on a
    # non-zero guard status, the wrapper reports NOT RUN and the alarm itself is lost.
    pytest.param(
        "tools/skyguard/session.py",
        '    raw = r.stdout.rstrip(b"' + BS + 'n")',
        '    r.check_returncode()' + "\n" + '    raw = r.stdout.rstrip(b"' + BS + 'n")',
        "tests/test_kb_guard.py::test_the_hook_never_fails_the_session_even_when_the_guard_alarms",
        id="kb-guard-alarm-status-must-not-fail-the-engine",
    ),
    # --- repo invariants ---------------------------------------------------
    # Drop the release gate that keeps the USER's knowledgebase out of the payload.
    # Everything still looks right -- CLAUDE.md still says where to write, .gitignore
    # still keeps it untracked -- but nothing would stop a release shipping it and
    # overwriting their notes on update. A convention with no gate cannot go red.
    pytest.param(
        ".github/workflows/release.yml",
        'test ! -e "/tmp/$NAME/KNOWLEDGEBASE.local.md"',
        "true",
        "tests/test_repo_invariants.py::test_the_users_knowledgebase_is_never_shipped_and_is_where_writes_go",
        id="users-knowledgebase-stays-out-of-the-payload",
    ),
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
        'f"{also_frames}{also_near}")',
        'f"{also_frames}")',
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
        # Stop printing the frames denominator. `frameless` then has no output at all
        # under any condition, and a lone dump with a changed stack header reads as an
        # ordinary UNKNOWN crash with RESULT: OK.
        "tools/crash-triage.py",
        '    print(f"  with frames   : {len(complete) - frameless}/{len(complete)} complete dump(s)"',
        '    print(f"" if True else f"  with frames   : {len(complete) - frameless}"',
        "tests/test_triage.py::test_crash_prints_the_frames_denominator_even_when_the_guard_stays_quiet",
        id="crash-frames-denominator-not-printed",
    ),

    pytest.param(
        # Go back to naming the count clause unconditionally. At 4 unparsed of 20 the
        # message then blames a floor that is satisfied, while the ratio is the cause.
        "tools/crash-triage.py",
        '        why = ("too few to call a format change" if unparsed < 2',
        '        why = ("too few to call a format change" if True',
        "tests/test_triage.py::test_crash_unparsed_note_names_the_clause_that_actually_held",
        id="crash-unparsed-note-blames-the-wrong-clause",
    ),

    pytest.param(
        # Drop the frames clause from the unparsed verdict. Both formats moved, only
        # one is named, and the reader trusts signatures that carry no call stacks.
        "tools/crash-triage.py",
        'f"{also_frames}{also_near}")',
        'f"{also_near}")',
        "tests/test_triage.py::test_crash_verdict_names_a_simultaneous_frames_break",
        id="crash-degraded-verdict-hides-frames-break",
    ),

    pytest.param(
        # Remove the listing cap. A folder with a broken extension pattern prints one
        # line per file; the dev install holds 341.
        "tools/crash-triage.py",
        "LIST_CAP = 10",
        "LIST_CAP = 10_000",
        "tests/test_triage.py::test_crash_caps_a_runaway_near_miss_listing",
        id="crash-near-miss-listing-uncapped",
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
        # Remove the not-ok refusal entirely, so a file that is not a cosave stops
        # being reported as one. (Anchored on the `if`, not on `sys.exit(2)` --
        # that line appears twice and str.replace would revert the survey()
        # try/except as well, crediting one mutation with two reverted fixes.)
        "tools/cosave-info.py",
        '    if not _r.get("ok"):',
        "    if False:",
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

    pytest.param(
        # Put the defect back: a hook reading through /dev/stdin. This is the one
        # that shipped and made every guard in the bundle decoration. Aimed at the
        # TEXT-level check because that one is platform-independent -- the
        # behavioural suite catches this on Windows but not on Linux, so pointing
        # the mutation there would make the gate itself platform-dependent.
        ".claude/hooks/protect-bash.sh",
        "INPUT=$(cat)",
        "INPUT=$(cat /dev/stdin)",
        "tests/test_repo_invariants.py::test_no_shipped_hook_reads_stdin_through_dev_stdin",
        id="hook-reads-dev-stdin-and-is-inert",
    ),

    pytest.param(
        # Remove a hook's liveness heartbeat. hook-canary then has nothing to read,
        # and the only detector that survives the harness/production gap is gone.
        ".claude/hooks/protect-files.sh",
        'HB_DIR="$PROJECT_DIR/.claude/backups/.hook-heartbeat"',
        'HB_DIR="$PROJECT_DIR/.claude/backups/.disabled"',
        "tests/test_repo_invariants.py::test_every_hook_writes_a_heartbeat_and_handles_an_empty_payload",
        id="hook-heartbeat-removed",
    ),

    pytest.param(
        # A second {{PYTHON_PATH}} in a hook. setup.sh rewrites every occurrence, so the
        # one-per-hook property that makes an UNconfigured hook detectable is lost.
        ".claude/hooks/protect-bash.sh",
        "# SETUP: setup.sh fills in the Python path below",
        "# SETUP: setup.sh fills in {{PYTHON_PATH}} below",
        "tests/test_repo_invariants.py::test_every_hook_is_configured_by_setup_and_carries_one_placeholder",
        id="hook-carries-a-second-python-placeholder",
    ),

    pytest.param(
        # Let a blocking hook fall through when it cannot see its input -- exit 0,
        # which is indistinguishable from deciding the call is fine.
        "tools/skyguard/write_rules.py",
        'if file_path == "":',
        'if False:',
        "tests/test_hooks.py::test_a_blocking_hook_refuses_when_it_cannot_see_its_input",
        id="blocking-hook-silent-when-blind",
    ),

    pytest.param(
        # Go back to anchoring the game-directory deny on one spelling of rm. Eleven
        # other spellings then reach the install, while the README says they cannot.
        # (Task 7: the deny now keys on the executed program, so the one spelling is the program list.)
        "tools/skyguard/deletes.py",
        'DELETE_PROGRAMS = frozenset(("rm", "rmdir", "unlink", "del", "erase", "rd", "remove-item", "rimraf"))',
        'DELETE_PROGRAMS = frozenset(("rm",))',
        "tests/test_hooks.py::test_the_install_cannot_be_deleted_however_it_is_spelled",
        id="delete-guard-anchored-on-one-spelling-of-rm",
    ),

    pytest.param(
        # Remove the "no Python" refusal from protect-bash. The wrapper then falls through to the
        # engine call, which cannot start, and the refusal no longer says WHY.
        ".claude/hooks/protect-bash.sh",
        'command -v "$PY" >/dev/null 2>&1 || inert',
        'true || inert',
        "tests/test_hooks.py::test_a_blocking_hook_refuses_when_python_is_unusable",
        id="blocking-hook-silent-when-python-is-missing",
    ),

    pytest.param(
        # Put the bare `Skyrim` back in the catch-all. Every edit to a checkout of
        # this toolkit is then annotated as an edit inside a live game install.
        "tools/skyguard/write_rules.py",
        'Data[/' + BS * 2 + ']+|My Games[/' + BS * 2 + ']+Skyrim"',
        'Data[/' + BS * 2 + ']+|Skyrim|My Games[/' + BS * 2 + ']+Skyrim"',
        "tests/test_hooks.py::test_editing_this_toolkits_own_checkout_is_not_editing_a_game_install",
        id="catch-all-matches-a-bare-skyrim",
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

    # --- what the v3.9 release review found ---------------------------------
    # Put back the single-character separator class. Doubled separators name the same
    # directory and are the canonical Python/PowerShell spelling, and every one of them
    # walked past the guard with NO output -- which the runtime reads as allow.
    pytest.param(
        "tools/skyguard/paths.py",
        'SEP = r"[/' + BS * 2 + ']+"',
        'SEP = r"[/' + BS * 2 + ']"',
        # Task 7: a resolved delete operand no longer uses this regex; clause (b)'s root MENTION test still does.
        "tests/test_skyguard_deletes.py::test_mention_with_doubled_separators_still_names_the_root",
        id="separator-class-accepts-doubled-paths",
    ),
    # Stop escaping regex metacharacters in the resolved root. `C:/Program Files (x86)/`
    # is the commonest real Steam layout there is, and `(x86)` unescaped is a valid ERE
    # group that simply never matches the actual directory -- inert, with GAME_PATH
    # still non-empty so the fail-closed branch does not save it.
    pytest.param(
        "tools/skyguard/paths.py",
        '("' + BS * 2 + '" + c if c in _META else c)',
        "c",
        # Task 7: a resolved delete operand no longer uses this regex; clause (b)'s root MENTION test still does.
        "tests/test_skyguard_deletes.py::test_mention_of_a_root_with_regex_metacharacters_still_names_it",
        id="path-to-ere-escapes-regex-metacharacters",
    ),
    # Stop honouring the CONFIGURED config directory. MO2/Wabbajack put the INIs in the
    # instance profile, outside Documents/My Games, so for those users this arm is the
    # only thing guarding their config -- and it had no test at all.
    pytest.param(
        "tools/skyguard/paths.py",
        "        _add(config, v)",
        "    pass",
        "tests/test_hooks.py::test_a_configured_config_directory_outside_documents_is_guarded",
        id="configured-config-directory-is-guarded",
    ),
    # Put the 5s timeouts back. A PreToolUse refusal that arrives late is DISCARDED,
    # and this release leads with that fix -- guarded, until now, by nothing.
    pytest.param(
        ".claude/settings.json",
        '"command": "bash \\"$CLAUDE_PROJECT_DIR/.claude/hooks/protect-bash.sh\\"",\n            "timeout": 60,',
        '"command": "bash \\"$CLAUDE_PROJECT_DIR/.claude/hooks/protect-bash.sh\\"",\n            "timeout": 5,',
        "tests/test_repo_invariants.py::test_every_registered_hook_has_a_timeout_that_is_not_the_old_five_seconds",
        id="hook-timeouts-are-not-the-old-five-seconds",
    ),
    # Hand-edit a generated file. agent/ is the only source; an edit made to the generated
    # copy would be silently overwritten by the next regeneration, so CI must refuse it.
    pytest.param(
        ".claude/skills/skyrim-bsa/SKILL.md",
        "# BSA/BA2 Archive Operations",
        "# BSA/BA2 Archive Operations (hand-edited)",
        "tests/test_gen_agent_trees.py::test_repo_is_in_sync",
        id="generated-files-cannot-be-hand-edited",
    ),
    # Pin the canary red forever again: report BLIND on the mere existence of a
    # marker, without asking whether a good payload arrived since. An instrument
    # that cannot go back to green gets ignored, which is the same end state as
    # having no instrument at all.
    pytest.param(
        "tools/hook-canary.sh",
        'if [ -n "$good_ts" ] && [[ "$good_ts" > "$blind_ts" ]]; then blind_current=0; fi',
        'if false; then blind_current=0; fi',
        "tests/test_hook_canary.py::test_a_proven_recovery_reads_recovered_not_alive",
        id="canary-can-go-back-to-green",
    ),
    # Swallow a truncated .blind marker instead of failing loud on it. The one
    # signal this tool exists to give would then depend on the marker being
    # well-formed at the moment the hook was starved.
    pytest.param(
        "tools/hook-canary.sh",
        '      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9])',
        '      *)',
        "tests/test_hook_canary.py::test_an_unproven_recovery_stays_blind",
        id="canary-malformed-marker-cannot-clear",
    ),
    # Third clause: make the comparison non-strict, so EQUAL timestamps count as
    # proof of recovery. Same second proves no ordering at all.
    pytest.param(
        "tools/hook-canary.sh",
        '[[ "$good_ts" > "$blind_ts" ]]',
        '[[ ! "$good_ts" < "$blind_ts" ]]',
        "tests/test_hook_canary.py::test_an_unproven_recovery_stays_blind",
        id="canary-a-tie-is-not-a-recovery",
    ),
    # Write the paths file double-quoted again: a `$` in a legal Windows folder name
    # then vanishes at source time and the install is silently unguarded.
    pytest.param(
        "setup.sh",
        "    printf 'SKYRIM_GAME_ROOT=%s" + BS + "n' " + '"$(sq "$GAME_ROOT_WIN")"',
        "    printf 'SKYRIM_GAME_ROOT=" + '"%s"' + BS + "n' " + '"$GAME_ROOT_WIN"',
        "tests/test_setup_paths.py::test_the_paths_file_survives_a_dollar_sign_in_the_install_path",
        id="paths-file-is-single-quoted",
    ),
    # Gate the GONE alarm on the newest stored copy again, so a file emptied in one
    # session and deleted in the next reports a clean install.
    pytest.param(
        "tools/kb-guard.sh",
        '        if [ "$LARGEST_SIZE" -gt 0 ]; then',
        '        if [ -n "$prior" ] && [ "$(bytes "$prior")" -gt 0 ]; then',
        "tests/test_kb_guard.py::test_a_file_emptied_then_deleted_still_alarms",
        id="gone-alarm-uses-the-largest-copy",
    ),
    # Swallow copy failures again: a run that stored nothing then says "unchanged".
    pytest.param(
        "tools/kb-guard.sh",
        '        failed="$failed $name(copy)"',
        '        :',
        "tests/test_kb_guard.py::test_a_copy_that_could_not_be_written_is_never_reported_as_unchanged",
        id="kb-guard-reports-a-failed-copy",
    ),
    # Shrink WATCHED to the one file every other test happens to create.
    pytest.param(
        "tools/kb-guard.sh",
        'WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md"',
        'WATCHED="KNOWLEDGEBASE.md"',
        "tests/test_kb_guard.py::test_every_watched_file_is_actually_snapshotted",
        id="every-watched-file-is-snapshotted",
    ),

    # --- the knowledgebase guard --------------------------------------------
    # Put back the collision: the snapshot directory is named to the second, so two
    # runs inside one second shared it and the second `cp` overwrote the first. Found
    # by a probe while building the tool, which is the only reason it is a test.
    pytest.param(
        "tools/kb-guard.sh",
        'while [ -e "$STORE/$STAMP" ]; do',
        "while false; do",
        "tests/test_kb_guard.py::test_two_runs_in_the_same_second_do_not_overwrite_each_other",
        id="kb-guard-snapshot-stamps-are-unique",
    ),
    # Let the rotation prune the largest stored copy. Every session after an
    # extract-over snapshots the truncated file, so an oldest-first prune walks the
    # one full-size copy off the end a few sessions later -- a backup store that
    # destroys the thing it exists to protect while still looking like a store.
    pytest.param(
        "tools/kb-guard.sh",
        '        [ -n "$best" ] && protected+=("$best")',
        "        :",
        "tests/test_kb_guard.py::test_rotation_never_prunes_the_largest_copy",
        id="kb-guard-rotation-protects-the-largest-copy",
    ),
    # The other clause of the same compound rule, which needs its own mutation for
    # the same reason it needs its own test: each protection shadows the others, so
    # one twin only ever proves the first one it trips.
    pytest.param(
        "tools/kb-guard.sh",
        'local protected=("${dirs[0]}" "${dirs[$(( ${#dirs[@]} - 1 ))]}")',
        'local protected=("${dirs[$(( ${#dirs[@]} - 1 ))]}")',
        "tests/test_kb_guard.py::test_rotation_never_prunes_the_oldest_copy",
        id="kb-guard-rotation-protects-the-oldest-copy",
    ),
    # Let the guard's exit status reach the session. SessionStart runs this, so a
    # lost knowledgebase would become a session that reports an error at startup --
    # the guard is allowed to be loud, not to break the thing it guards.
    pytest.param(
        ".claude/hooks/session-kb-guard.sh",
        '[ -n "$OUT" ] && printf ' + "'%s" + BS + "n' " + '"$OUT"',
        '[ -n "$OUT" ] && { printf ' + "'%s" + BS + "n' " + '"$OUT"; exit 1; }',
        "tests/test_kb_guard.py::test_the_hook_never_fails_the_session_even_when_the_guard_alarms",
        id="kb-guard-hook-never-fails-the-session",
    ),
    # Take away the words. The exit code still says 2, so anything reading the status
    # is unaffected -- but a user watching a session start sees nothing at all, which
    # is the silence this whole class of bug hides in.
    pytest.param(
        "tools/kb-guard.sh",
        '    echo "KB GUARD: NOT RUN -- none of the watched files exist under $ROOT ($WATCHED)" >&2',
        "    :",
        "tests/test_kb_guard.py::test_an_install_with_nothing_to_watch_says_NOT_RUN_rather_than_nothing",
        id="kb-guard-says-not-run-out-loud",
    ),
    # Unregister the hook. It still ships, still reads stdin correctly, still writes a
    # heartbeat, is still named in setup.sh -- and never runs, because no event points
    # at it. hook-canary.sh reports that as NOT EXERCISED, indistinguishable from an
    # Edit hook in a Bash-only session.
    pytest.param(
        ".claude/settings.json",
        "session-kb-guard.sh",
        "protect-bash.sh",
        "tests/test_repo_invariants.py::test_every_hook_is_registered_in_settings_json",
        id="every-hook-is-wired-into-settings-json",
    ),
    # Read the declared env file from the POSIX --root again. Native Windows python only opens
    # /c/... because Git Bash rewrites the argument; with conversion off the read fails silently and a
    # declared game root outside the install drops out, so deleting it is allowed.
    pytest.param(
        "tools/skyguard/run.py",
        "Path(a.root_win or a.root)",
        "Path(a.root)",
        "tests/test_hooks.py::test_a_declared_root_is_read_with_path_conversion_off",
        id="engine-reads-declared-env-from-the-windows-root",
    ),
    # An env file that exists but cannot be read must refuse, not quietly drop the declared roots.
    pytest.param(
        "tools/skyguard/run.py",
        "                env = None" + chr(10),
        "                env = {}" + chr(10),
        "tests/test_hooks.py::test_an_unreadable_declared_env_file_fails_closed",
        id="unreadable-declared-env-fails-closed",
    ),
    # Rule trace (Plan 5 Task 1): the rule id is recorded where the verdict is made. Drop the audit.rule
    # call in deny() and the log line survives (hook-canary stays green) while the trace goes silent.
    pytest.param(
        "tools/skyguard/shell_rules.py",
        'audit.rule(HOOK, rid, "deny", reason)',          # Task 7: the delete deny records its own id
        'audit.log(HOOK, "DENY", reason)',
        "tests/test_skyguard_shell.py::test_rule_trace_records_the_rule_id",
        id="rule-trace-records-id",
    ),
    pytest.param(
        "tools/skyguard/write_rules.py",
        'audit.rule(HOOK, "W-PLUGIN", "deny", DENY_PLUGIN)',
        'audit.log(HOOK, "DENY", DENY_PLUGIN)',
        "tests/test_skyguard_write.py::test_rule_trace_records_the_rule_id",
        id="rule-trace-records-id-write",
    ),
    # Fix round 1: the basename splitter lost a backslash and raised re.error on every real message.
    pytest.param(
        "tools/skyguard/rules.py",
        'r"[/' + BS*2 + ']+", str(value)',
        'r"[/' + BS + ']+", str(value)',
        "tests/test_rule_messages.py::test_a_non_legacy_rule_renders_through_message",
        id="rule-basename-splits-on-backslash",
    ),
    # Plan 5 Task 2: attribute every newly-fired rule to the first rule the item fires. Counts still
    # total correctly; the per-rule table lies, which is the one thing the pricing tool is for.
    pytest.param(
        "scripts/guard-corpus/price.py",
        'out.append((rid, "newly-" + dec))',
        'out.append((new_fired[0][0], "newly-" + dec))',
        "tests/test_price_tool.py::test_a_broadened_rule_moves_only_that_rule",
        id="price-attribution-per-rule",
    ),
    # Fix round 1: compare only whether a rule id fired, never its decision. A promotion advise->deny on
    # a rule firing in both engines then prices as nothing.
    pytest.param(
        "scripts/guard-corpus/price.py",
        "if rid not in old_dec or old_dec[rid] != dec:",
        "if rid not in old_dec:",
        "tests/test_price_tool.py::test_a_decision_change_on_the_same_rule_is_priced",
        id="price-decision-change-on-same-rule",
    ),
    # Plan 5 Task 3: a supersession that never checks the engine still differs from the legacy verdict.
    # A stale one then sits in intended.jsonl forever, hiding the regression that brings the old verdict back.
    pytest.param(
        "scripts/guard-corpus/record.py",
        'if (now["decision"], set(now["rules"])) == was:',
        "if False:",
        "tests/test_guard_corpus.py::test_a_stale_supersession_is_caught",
        id="corpus-supersession-must-be-live",
    ),
    # Plan 5 Task 4 (controller ruling): a supersession may not weaken a verdict unless it declares and explains it.
    pytest.param(
        "scripts/guard-corpus/record.py",
        'if STRENGTH[row["decision"]] < STRENGTH[old["decision"]] and not (',
        'if False and not (',
        "tests/test_guard_corpus.py::test_a_supersession_that_loosens_the_decision_needs_loosens_and_a_reason",
        id="corpus-supersession-no-silent-loosening",
    ),
    pytest.param(
        "scripts/guard-corpus/record.py",
        'if row["supersedes"] in seen:',
        'if False:',
        "tests/test_guard_corpus.py::test_a_legacy_row_is_superseded_by_at_most_one_intended_row",
        id="corpus-supersession-unique",
    ),
    # Compare decisions and rule ids only: an intended row whose `text` is wrong stays green, and the exact
    # wording the spec fixes is no longer held by anything.
    pytest.param(
        "scripts/guard-corpus/record.py",
        'if got["text"] != row["text"]:',
        "if False:",
        "tests/test_guard_corpus.py::test_intended_rows_reproduce",
        id="corpus-intended-text-exact",
    ),
]


_SR = "tools/skyguard/shell_rules.py"
# Plan 5 Task 6: S-RESAVER-APPLY re-based on shellparse. The Task 4 regex mutations went with the regexes (the
# wrappers, -c strings, heredoc consumers, continuations and quoting they covered are now parser branches, each with
# its own shellparse-* mutation). One mutation per clause of the parser rule; each names the ROW that must go red.
# (id suffix, find, replace, row in test_resaver_rows)
_SR_CLAUSES = [
    ("program", "    if e.program in RESAVER_PROGRAMS:", "    if False:", "t04-apply"),
    ("java", 'return e.program == "java" and any(', 'return False and any(', "t04-java"),
    ("apply-same-cmd", "    if RESAVER_APPLY not in words:", "    if False:", "t04-apply-elsewhere"),
    ("whole-word-op", 'return next((a for a in words if a in RESAVER_OPS), "")',
     'return next((o for a in words for o in RESAVER_OPS if o in a), "")', "t04-op-in-filename"),
    ("op-set-global", '("set-global", "set-var", "clean",', '("set-var", "clean",', "t04-set-global"),
    ("op-set-var", '("set-global", "set-var", "clean",', '("set-global", "clean",', "t04-set-var"),
    ("op-clean", '("set-global", "set-var", "clean",', '("set-global", "set-var",', "t04-clean"),
    ("op-reset-havok", '"reset-havok", "cleanse-formlists",', '"cleanse-formlists",', "t04-apply"),
    ("op-cleanse-formlists", '"reset-havok", "cleanse-formlists",', '"reset-havok",', "t04-apply-env"),
    ("op-list", '"cleanse-formlists", "remove-created")', '"cleanse-formlists")', "t04-apply-chain"),
    ("save-from-segment", 'or (lambda m: m.group(0) if m else "")(RESAVER_TEXT_SAVE.search(e.segment))', 'or ""',
     "t04-find-exec"),
    ("opaque-is-unresolved", "    if not parsed.ok or opaque:", "    if not parsed.ok:", "t06-resaver-variable-program"),
    ("parse-fail-falls-back", "    if not parsed.ok or opaque:", "    if opaque:", "t06-resaver-parse-fail"),
    ("fallback-needs-apply", 'if "resaver" not in low or RESAVER_APPLY not in low:', 'if "resaver" not in low:',
     "t06-resaver-variable-dry"),
    ("fallback-op-lookbehind", "re.compile(r\"(?<![^\\s\\\"'])(\" + \"|\".join(RESAVER_OPS)",
     "re.compile(r\"(\" + \"|\".join(RESAVER_OPS)", "t06-resaver-variable-op-suffix"),
    ("fallback-op-lookahead", "+ r\")(?![^\\s\\\"'])\", re.ASCII", "+ r\")\", re.ASCII",
     "t06-resaver-variable-op-in-filename"),
    # Task 7: decide() parses once (with the dialect) and hands the parse to the ReSaver rule.
    ("dialect", "    parsed = shellparse.parse(command, shell)\n    dels", "    parsed = shellparse.parse(command)\n    dels",
     "t04-heredoc-py"),
]
MUTATIONS += [
    pytest.param(_SR, find, rep, "tests/test_skyguard_shell.py::test_resaver_rows[" + row + "]",
                 id="rule-S-RESAVER-APPLY-" + suffix)
    for suffix, find, rep, row in _SR_CLAUSES
]
MUTATIONS += [
    # R7 now lives in the parser: a heredoc body fed to a shell is commands. Treat every body as data and the
    # ReSaver run inside `bash <<'EOF'` must stop asking (the Task 5 entry names the parser test; this one the rule).
    pytest.param("tools/skyguard/shellparse.py", "if self.bodies and program in ALL_SHELLS and self.recurse:",
                 "if False:", "tests/test_skyguard_shell.py::test_resaver_rows[t04-bash-heredoc]",
                 id="rule-S-RESAVER-APPLY-heredoc-consumer"),
]
# Task 4 fix rounds 2-3: a hook that answers after its timeout is dropped, so each cause of superlinear matching is
# put back and the timing test must go red. (id suffix, file, find, replace, timing shape that must fail)
# Task 6 removed the entries whose regexes no longer exist (env-ambiguity, unbounded-path, java/find lazy bounds) and
# the resaver-prefilter one: the parser rejects or finishes every adversarial shape in milliseconds (measured: 80 KB
# of `(` 3 ms, 40,000 words 6 ms), so the early return is an optimisation no longer guarding a timeout.
_SR_TIMING = [
    # Task 7: the `rm ... .*root` advice is gone (R10); the mv/cp move advice still relies on the split.
    ("timing-dotstar-scan", "tools/skyguard/grep.py", "parts = split_dotstar(pattern)", "parts = None", "mv-repeat"),
    # fix round 3: a separator run must start a match only at its first character, in the install-path regex
    # (shared by every delete/move/sed/redirect rule), and the relative-Data rule must not rescan a run.
    ("timing-lead-lookbehind-rm", "tools/skyguard/paths.py", r'LEAD = r"(?<![/\\])[/\\]+"', r'LEAD = r"[/\\]+"',
     "rm-slash-run"),
    ("timing-lead-lookbehind-sed", "tools/skyguard/paths.py", r'LEAD = r"(?<![/\\])[/\\]+"', r'LEAD = r"[/\\]+"',
     "sed-backslash-run"),
    # (Task 7 removed timing-relative-data-run: RELATIVE_DATA_DELETE is gone; relative deletes resolve from cwd.)
    ("timing-registry-split", _SR,
     'has(r"reg(\\.exe)?\\s+delete") or has(r"Remove-ItemProperty.*Bethesda")',
     'has(r"(reg(\\.exe)?\\s+delete|Remove-ItemProperty.*Bethesda)")',
     "tests/test_skyguard_timing.py::test_the_registry_rule_is_linear"),
    ("split-safe-list", "tools/skyguard/grep.py", "or pattern[:cut] not in SPLIT_SAFE_FIRST", "",
     "tests/test_skyguard_timing.py::test_split_dotstar_refuses_what_it_cannot_prove_equivalent"),
]
MUTATIONS += [
    pytest.param(f, find, rep,
                 shape if shape.startswith("tests/") else
                 "tests/test_skyguard_timing.py::test_shell_rules_stay_fast_on_adversarial_input[" + shape + "]",
                 id="rule-S-RESAVER-APPLY-" + suffix if f == _SR else "guard-" + suffix)
    for suffix, f, find, rep, shape in _SR_TIMING
]
# Plan 5 Task 6: targets (operand resolution, zones) and skyrim-paths.env read as data.
_TG = "tools/skyguard/targets.py"
_PA = "tools/skyguard/paths.py"
MUTATIONS += [
    pytest.param(_TG, "    base = normalise_cwd(cwd)\n    try:", "    base = None\n    try:",
                 "tests/test_targets.py::test_relative_word_resolves_against_the_payload_cwd",
                 id="targets-cwd-used"),
    pytest.param(_PA, 'return posixpath.normpath("/" + "/".join(parts).lstrip("/")).lstrip("/")',
                 'return "/".join(parts).lstrip("/")',
                 "tests/test_targets.py::test_absolute_spellings_bash", id="targets-dotdot"),
    pytest.param(_PA, "        m = _MSYS_DRIVE.match(p)", "        m = None",
                 "tests/test_targets.py::test_absolute_spellings_bash", id="targets-msys"),
    pytest.param(_TG, "    if any(_has_glob(c, dialect) for c in comps[:-1]):", "    if False:",
                 "tests/test_targets.py::test_unresolvable_is_none", id="targets-glob-dir-unresolvable"),
    pytest.param(_TG, '        raise _Unresolvable(word)                        # $(...), $1, ${x:-y}, a stray backtick',
                 "        pass", "tests/test_targets.py::test_unresolvable_is_none", id="targets-unknown-var-unresolvable"),
    pytest.param(_TG, '            return "scratch"', '            return "install"',
                 "tests/test_targets.py::test_zone", id="targets-scratch-allowlist"),
    pytest.param(_TG, "if _under(p, dl.rstrip(\"/\") or dl) and len(dl) > best_len:",
                 "if _under(p, dl.rstrip(\"/\") or dl) and len(dl) >= 0:",
                 "tests/test_targets.py::test_mo2_nested_inside_the_install_wins", id="targets-most-specific-wins"),
    pytest.param(_TG, '            return paths.norm_abs(s, msys=True)          # /c/x (Git Bash drive), /tmp/x (POSIX)',
                 "            return None", "tests/test_targets.py::test_absolute_spellings_bash",
                 id="targets-bash-posix-absolute"),
    pytest.param(_PA, "        for v in ([self.env[key]] if self.env.get(key) else []) + list(self.file.get(key, ())):",
                 "        for v in (list(self.file.get(key, ())) or ([self.env[key]] if self.env.get(key) else [])):",
                 "tests/test_skyguard_core.py::test_env_and_file_are_a_union_and_both_quote_styles_parse",
                 id="paths-union"),
    pytest.param(_PA, "                    bad.append((path.name, key))", "                    pass",
                 "tests/test_skyguard_core.py::test_a_malformed_protection_value_is_reported_by_key_not_skipped",
                 id="paths-malformed-visible"),
    pytest.param(_PA, "                if c in \"$`\":", "                if False:",
                 "tests/test_skyguard_core.py::test_a_malformed_protection_value_is_reported_by_key_not_skipped",
                 id="paths-strict-dq-expansion"),
    pytest.param(_PA, "                value = None                                          # a relative root protects nothing",
                 "                pass",
                 "tests/test_skyguard_core.py::test_a_malformed_protection_value_is_reported_by_key_not_skipped",
                 id="paths-strict-relative-root"),
    pytest.param(_PA, "            value = None if (m.group(4) or not flags_ok) else parse_value(m.group(5))",
                 "            value = None if (not flags_ok) else parse_value(m.group(5))",
                 "tests/test_skyguard_core.py::test_a_malformed_protection_value_is_reported_by_key_not_skipped",
                 id="paths-strict-space-before-equals"),
    pytest.param(_PA, "            if rest == \"\" or rest.startswith(\"#\"):\n                break\n            return None",
                 "            break",
                 "tests/test_skyguard_core.py::test_a_malformed_protection_value_is_reported_by_key_not_skipped",
                 id="paths-strict-unquoted-space"),
    pytest.param(_PA, "    bad = env.malformed if isinstance(env, PathsConfig) else ()", "    bad = ()",
                 "tests/test_skyguard_core.py::test_a_malformed_value_denies_naming_the_key",
                 id="rule-S-INERT-CONFIG-roots-carry"),
    pytest.param(_SR, "    if roots.malformed:", "    if False:",
                 "tests/test_skyguard_core.py::test_a_malformed_value_denies_naming_the_key",
                 id="rule-S-INERT-CONFIG-deny"),
    pytest.param(_PA, "    if environ.get(\"PLUGINS_TXT\"):", "    if False:",
                 "tests/test_paths_precedence.py::test_python_lookup", id="paths-plugins-txt-wins"),
    pytest.param("tools/xelib/active-plugins.js", "    if (process.env.PLUGINS_TXT) return process.env.PLUGINS_TXT;",
                 "", "tests/test_paths_precedence.py::test_node_agrees_with_python", id="paths-js-plugins-txt-wins"),
    pytest.param("tools/skyguard/run.py", '                                       cwd=p.get("cwd", ""), shell=p.get("shell", ""))',
                 '                                       cwd=p.get("cwd", ""))',
                 "tests/test_skyguard_claude_adapter.py::test_run_passes_the_shell_dialect", id="run-passes-shell"),
    pytest.param("tools/skyguard/adapters/claude.py", '        "cwd": cwd.replace("\\x00", "") if isinstance(cwd, str) else "",',
                 '        "cwd": "",', "tests/test_skyguard_claude_adapter.py::test_cwd_is_passed_raw",
                 id="adapter-extracts-cwd"),
    pytest.param("setup.sh", "    printf 'SKYRIM_MO2_MODS=%s" + BS + "n' \"$(sq \"$MO2_MODS\")\"", "",
                 "tests/test_setup_paths.py::test_setup_writes_the_mo2_roots_the_guard_reads",
                 id="setup-writes-mo2-keys"),
]
# Task 6 fix round 1 (adversarial review): one mutation per new clause. (id, file, find, replace, test)
_T6R1 = [
    ("targets-covers-ancestor", _TG, "if len(pc) <= len(rc) and all(", "if len(pc) == len(rc) and all(",
     "tests/test_targets.py::test_a_path_that_contains_or_globs_a_protected_root_covers_it"),
    ("targets-covers-glob", _TG,
     "return any(_glob_match(alt, comp) if (_GLOB_CHARS & set(alt)) else alt == comp for alt in _braces(pat))",
     "return pat == comp", "tests/test_targets.py::test_a_path_that_contains_or_globs_a_protected_root_covers_it"),
    ("targets-covers-braces", _TG, "else alt == comp for alt in _braces(pat))", "else alt == comp for alt in [pat])",
     "tests/test_targets.py::test_a_path_that_contains_or_globs_a_protected_root_covers_it"),
    ("targets-covers-documents", _TG, '    if len(pc) >= 2 and pc[-1] == "documents":', "    if False:",
     "tests/test_targets.py::test_covers_protected_includes_the_root_itself_and_documents_ancestors"),
    ("targets-short-name", _TG, "    if _SHORT_NAME.search(p):", "    if False:",
     "tests/test_targets.py::test_an_8dot3_short_name_is_unresolvable"),
    ("targets-short-name-cwd", _TG, "return None if p is None or _SHORT_NAME.search(p) else p", "return p",
     "tests/test_targets.py::test_a_short_name_cwd_makes_relative_words_unresolvable"),
    ("targets-loopback-share", _TG, "if _LOOPBACK.fullmatch(host) or (me and host.lower() == me):", "if False:",
     "tests/test_targets.py::test_a_loopback_admin_share_is_the_drive"),
    ("targets-other-host-share", _TG, "            return None\n    return p", "    return p",
     "tests/test_targets.py::test_another_hosts_admin_share_is_unresolvable_and_a_plain_share_is_outside"),
    ("targets-literal-dollar", _TG, "    if _LIVE_DOLLAR.search(out) or", '    if "$" in out or',
     "tests/test_targets.py::test_a_loopback_admin_share_is_the_drive"),
    ("targets-ntfs-stream", _PA, '        if ":" in c:', "        if False:",
     "tests/test_targets.py::test_an_ntfs_stream_suffix_is_stripped"),
    ("targets-scratch-not-tools-data", _TG, "rel[0] not in paths.SCRATCH_EXCLUDED_TOP and ", "",
     "tests/test_targets.py::test_scratch_never_covers_tools_or_data"),
    ("paths-bom", _PA, 'decode("utf-8-sig")', 'decode("utf-8")',
     "tests/test_skyguard_core.py::test_a_bom_does_not_hide_the_first_key"),
    ("paths-lone-cr", _PA, '        for line in text.split("\\n"):', "        for line in text.splitlines():",
     "tests/test_skyguard_core.py::test_a_lone_cr_does_not_split_a_line"),
    ("paths-declare", _PA, "(export|declare|typeset|readonly)", "(export)",
     "tests/test_skyguard_core.py::test_declare_forms_are_read_like_bash"),
    ("paths-declare-flags", _PA, 'flags_ok = m.group(1) in (None, "export") or _SAFE_FLAGS.fullmatch(m.group(2) or "")',
     "flags_ok = True", "tests/test_skyguard_core.py::test_a_declare_that_changes_the_value_is_malformed"),
    ("paths-protection-key-mention", _PA, "k = _PROT_MENTION.search(line)", "k = None",
     "tests/test_skyguard_core.py::test_a_declare_that_changes_the_value_is_malformed"),
    ("paths-hash-mid-word", _PA, "[A-Za-z0-9_./:,+@%=#-]+", "[A-Za-z0-9_./:,+@%=-]+",
     "tests/test_skyguard_core.py::test_value_grammar_reads_what_bash_reads"),
    ("paths-mo2-overwrite", _PA, '_values(env, "SKYRIM_MO2_MODS") + _values(env, "SKYRIM_MO2_OVERWRITE")',
     '_values(env, "SKYRIM_MO2_MODS")', "tests/test_skyguard_core.py::test_mo2_keys_become_mo2_dirs"),
    ("paths-mo2-mods", _PA, '_values(env, "SKYRIM_MO2_MODS") + _values(env, "SKYRIM_MO2_OVERWRITE")',
     '_values(env, "SKYRIM_MO2_OVERWRITE")', "tests/test_skyguard_core.py::test_mo2_keys_become_mo2_dirs"),
    ("timing-braces-linear", _TG, "            j = next_close[i + 1]",
     "            j = comp.find('}', i + 1)\n            j = n if j < 0 else j",
     "tests/test_skyguard_timing.py::test_brace_expansion_is_linear"),
    # fix round 2
    ("targets-covers-literal-first", _TG, "    if pat == comp:\n        return True", "    if False:\n        return True",
     "tests/test_targets.py::test_a_root_with_literal_glob_characters_covers_itself"),
    ("targets-brace-separator-unresolvable", _TG, 'if dialect == "bash" and any("," in g and "/" in g',
     'if False and any("," in g and "/" in g', "tests/test_targets.py::test_a_brace_group_holding_a_separator_is_unresolvable"),
    ("targets-scratch-not-claude", _PA, '("tools", "data", ".claude")', '("tools", "data")',
     "tests/test_targets.py::test_claude_dir_is_never_scratch"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T6R1]
for _suffix, _find, _rep in [
    ("claude", r'r"(^|(?<![/\\])[/\\]+)\.claude', r'r"(^|[/\\]+)\.claude'),
    ("node-modules", r'r"(?<![/\\])[/\\]+node_modules', r'r"[/\\]+node_modules'),
    ("skse-ini", r'r"(?<![/\\])[/\\]+Data[/\\]+SKSE', r'r"[/\\]+Data[/\\]+SKSE'),
    ("install", r'r"(^|(?<![/\\])[/\\]+)Data', r'r"(^|[/\\]+)Data'),
]:
    MUTATIONS.append(pytest.param(
        "tools/skyguard/write_rules.py", _find, _rep,
        "tests/test_skyguard_timing.py::test_write_rules_stay_fast_on_adversarial_paths[separator-run]",
        id="guard-timing-separator-run-lookbehind-" + _suffix,
    ))
# Plan 5 Task 5: shellparse. A misparse is a silent allow once rules sit on the parser, so every executed-code
# branch is deleted here and the named test must go red. (id suffix, find, replace, test in tests/test_shellparse.py)
_SP = "tools/skyguard/shellparse.py"
_SP_CLAUSES = [
    # the seven the plan names
    ("subst-recursion", 'self._push(f, "subst", i, True)                     # bash $( ... ): commands, parsed in place',
     'i = self._pairs()[i + 1] - 1; w.add("$(...)")', "test_bash_substitutions_are_parsed"),
    ("heredoc-consumer", "if self.bodies and program in ALL_SHELLS and self.recurse:", "if False:",
     "test_t04_rows_come_out_as_executed_commands[t04-bash-heredoc]"),
    ("pwsh-alias", '"ri": "remove-item", "rm": "remove-item"', '"rm": "remove-item"', "test_pwsh_aliases"),
    ("find-delete", 'if w == "-delete":', "if False:", "test_find_delete_is_a_delete_on_the_start_paths"),
    ("bash-c", 'self.reparse(v[j], "bash")', "pass", "test_bash_shell_strings_reparsed_in_their_dialect"),
    ("pwsh-command", 'self.reparse(" ".join(rest), "powershell")', "pass",
     "test_bash_shell_strings_reparsed_in_their_dialect"),
    ("never-raises", "raise ParseError(\"unterminated ' quote\")    # bash single quote",
     "raise ValueError(\"unterminated ' quote\")", "test_never_raises_on_random_text_or_corpus_commands"),
    # executed-code paths
    ("backtick-recursion", '_sub("".join(parts), "bash", self.depth + 1, f.cd, self.ctx)', "pass",
     "test_bash_substitutions_are_parsed"),
    ("procsubst", 'self._push(f, "proc", i, True)', 'i = self._pairs()[i + 1] - 1; self._word(f).add("<(...)")',
     "test_bash_substitutions_are_parsed"),
    ("heredoc-pipe-to-shell", "            if not readers:\n                continue",
     "            if True:\n                continue", "test_heredoc_piped_into_a_shell_is_parsed"),
    ("herestring", 'if op.endswith("<<<"):', "if False:", "test_heredoc_piped_into_a_shell_is_parsed"),
    ("interpreter-kind", "if program in INTERPRETERS or _PYTHON.fullmatch(program):", "if False:",
     "test_heredoc_to_an_interpreter_is_code_not_commands"),
    ("find-exec", 'if w in ("-exec", "-execdir", "-ok", "-okdir"):', "if False:",
     "test_find_exec_runs_its_command_on_the_start_paths"),
    ("cmd-c", 'self.reparse(" ".join(rest), "cmd")', "pass", "test_bash_shell_strings_reparsed_in_their_dialect"),
    ("eval", 'self.reparse(" ".join(v[k + 1:]), "bash")', "pass",
     "test_bash_shell_strings_reparsed_in_their_dialect"),
    ("iex", 'self.reparse(s, "powershell")', "pass", "test_pwsh_blocks_and_subexpressions_parsed"),
    ("encoded-command", 'self.reparse(text, "powershell")', "pass", "test_pwsh_encoded_command_is_decoded"),
    ("script-file", "self.emit(program_name(v[j], self.d, self.y[j]), j + 1)", "self.emit(p, k + 1)",
     "test_bash_script_file_runs_the_script"),
    ("wrap-sudo", 'if p in ("sudo", "doas"):', "if False:", "test_bash_wrappers_unwrapped"),
    ("wrap-env", 'if p == "env" and self.d == "bash":', "if False:", "test_bash_wrappers_unwrapped"),
    ("wrap-command", 'if p in ("command", "builtin") and self.d == "bash":', "if False:", "test_bash_wrappers_unwrapped"),
    ("wrap-exec", 'if p == "exec" and self.d == "bash":', "if False:", "test_bash_wrappers_unwrapped"),
    ("wrap-nohup-time", 'if p in ("nohup", "winpty", "chronic") or (p == "time" and self.d == "bash"):', "if False:",
     "test_bash_wrappers_unwrapped"),
    ("wrap-nice", 'if p == "nice":', "if False:", "test_bash_wrappers_unwrapped"),
    ("wrap-timeout", 'if p == "timeout" and self.d == "bash":', "if False:", "test_bash_wrappers_unwrapped"),
    ("wrap-xargs", 'if p == "xargs":', "if False:", "test_bash_xargs_runs_its_command_on_stdin"),
    ("wrap-source", 'if p in ("source", ".") and self.d == "bash" or (p == "." and self.d == "powershell"):',
     "if False:", "test_bash_script_file_runs_the_script"),
    ("assign-skip", "ws = ws[k:]", "ws = ws[0:]", "test_bash_assignment_prefixes_skipped"),
    ("r6-literal-reading", "if d == \"bash\" and any(w.bs for w in ws):", "if False:",
     "test_unquoted_backslash_is_read_both_ways_r6"),
    ("comment", 'if c == "#" and f.word is None:                     # bash comment', "if False:",
     "test_comments_dropped"),
    ("keyword-then", '_BASH_DROP = frozenset(("if", "then",', '_BASH_DROP = frozenset(("if",',
     "test_bash_compound_commands"),
    ("case", 'if v == "case":', "if False:", "test_bash_compound_commands"),
    ("function-def", 'if j < self.n and t[j] == ")":                      # name() -- a function definition',
     "if False:", "test_bash_compound_commands"),
    ("pipe-stdin", 'stdin = "pipe" if rec.stage > 0 else ""', 'stdin = ""', "test_pipeline_stage_reads_the_pipe"),
    # cd_before
    ("cd-tracked", "                    self.track_cd(f, cmd)", "                    pass", "test_bash_cd_before"),
    ("cd-not-from-a-pipeline", 'if sep not in ("|", "|&") and cmd.stage == 0:', "if True:", "test_bash_cd_before"),
    ("cd-subshell-scope", '        self.frames.pop()\n        p = self.frames[-1]\n        if f.kind in ("subst", "proc"):',
     '        self.frames.pop()\n        p = self.frames[-1]\n        p.cd = f.cd\n        if f.kind in ("subst", "proc"):',
     "test_bash_cd_before"),
    # fail closed (R4)
    ("token-cap", "if self.tokens > MAX_TOKENS:", "if False:", "test_too_many_tokens_fails_closed"),
    ("depth-cap-frames", "if len(self.frames) + self.depth > MAX_DEPTH:", "if False:",
     "test_nesting_deeper_than_eight_fails_closed"),
    ("depth-cap-reparse", "    if depth > MAX_DEPTH:", "    if False:", "test_nested_reparse_counts_toward_the_depth"),
    ("unterminated-heredoc", "raise ParseError(\"unterminated heredoc (no line '%s')\" % h.delim[:40])",
     "h.body = t[start:]; pos = n + 1; break", "test_unparseable_is_ok_false_with_a_reason"),
    ("unterminated-dq", "raise ParseError('unterminated \" quote')            # bash double quote", "pass",
     "test_unparseable_is_ok_false_with_a_reason"),
    ("unbalanced-subst", 'raise ParseError("unbalanced ( or $( or <(")', "self.frames = self.frames[:1]",
     "test_unparseable_is_ok_false_with_a_reason"),
    ("stray-paren", 'raise ParseError("unbalanced )")', "self.i += 1; return",
     "test_unparseable_is_ok_false_with_a_reason"),
    # PowerShell
    ("pwsh-block", 'self._push(f, "block", self.i, True)', 'self._push(f, "hash", self.i, True)',
     "test_pwsh_blocks_and_subexpressions_parsed"),
    ("pwsh-subexpression", 'self._push(f, "subst", i, True)                     # PowerShell $( ... ) subexpression',
     'i = self._pairs()[i + 1] - 1; w.add("$(...)")', "test_pwsh_blocks_and_subexpressions_parsed"),
    ("pwsh-io-file", "io = _io_program(m.group(1), m.group(2))", 'io = ""',
     "test_pwsh_io_file_calls_are_synthetic_execs"),
    ("pwsh-param-prefix", "if key and want.startswith(key):", "if key and want == key:",
     "test_pwsh_named_parameters"),
    ("pwsh-call-operator", "self._cmd(f, i).call = True                    # & call operator", "pass",
     "test_pwsh_separators_and_call"),
    ("pwsh-expression", 'if v[k][:1] == "{" or not self.call and (', "if False and (",
     "test_pwsh_expressions_and_here_strings_are_not_commands"),
    ("pwsh-hash-discarded", "drop = set(id(r) for r in mine)", "drop = set()",
     "test_pwsh_expressions_and_here_strings_are_not_commands"),
    # fix round 1 (adversarial review): one per new clause
    # fix round 3
    ("r3-cast-not-call", "call = bool(m or tail)", "call = True", "test_r3_pwsh_cast_is_a_command_group_not_a_call"),
    ("r3-saps-array", 'if a.startswith("@(") and a.endswith(")"):', "if False:",
     "test_r3_start_process_array_argument"),
    ("r3-saps-dynamic-opaque", "elif dyn:                                           # -ArgumentList $args: not known",
     "elif False:", "test_r3_start_process_unknown_arguments_are_opaque"),
    ("r3-ps-array-literal-only",
     "if sc.synth or any(w.dyn for w in words) or any(r.redirs or r.heredocs for r in sc.recs):", "if False:",
     "test_r3_start_process_unknown_arguments_are_opaque"),
    ("r3-printf-not-emulated", '_TEXT_SOURCES = frozenset(("echo", "write-output", "write-host", ""))',
     '_TEXT_SOURCES = frozenset(("echo", "printf", "write-output", "write-host", ""))',
     "test_r3_unreconstructable_stdin_into_a_shell_or_interpreter_is_opaque"),
    ("r3-echo-e", ' and not (e.program == "echo" and e.argv and _ECHO_OPTS.match(e.argv[0]))', "",
     "test_r3_unreconstructable_stdin_into_a_shell_or_interpreter_is_opaque"),
    ("r3-interpreter-reader",
     'e.program in ALL_SHELLS or (consumer_kind(e.program) == "interpreter" and _reads_code(e.argv)))]',
     "e.program in ALL_SHELLS)]", "test_r3_unreconstructable_stdin_into_a_shell_or_interpreter_is_opaque"),
    ("r3-literal-code-to-body", r'ctx.out[j] = reader._replace(stdin_body="\n".join(texts))', "pass",
     "test_r3_literal_stdin_into_an_interpreter_is_code_not_opaque"),
    ("r3-reads-code", "    for a in argv:\n        if a in _CODE_OPTS:",
     "    return True\n    for a in argv:\n        if a in _CODE_OPTS:",
     "test_r3_literal_stdin_into_an_interpreter_is_code_not_opaque"),
    ("r3-xargs-opaque",
     'if program in ALL_SHELLS or (consumer_kind(program) == "interpreter" and _reads_code(e.argv)):',
     "if False:", "test_r3_xargs_input_is_arguments_not_a_script"),
    ("r3-via-xargs-skip", "id(e) not in ctx.via_xargs and (", "True and (",
     "test_r3_xargs_input_is_arguments_not_a_script"),
    ("r3-c-without-string",
     "self.opaque(k)                      # `xargs -0 sh -c`: the command string comes from elsewhere", "pass",
     "test_r3_unreconstructable_stdin_into_a_shell_or_interpreter_is_opaque"),
    ("r3-env-cluster", 'if len(w) > 2 and w[0] == "-" and w[1] != "-":  # clustered', "if False:  # clustered",
     "test_r3_env_combined_flags"),
    ("r3-cmd-pre-switch", "while j < len(v) and _CMD_PRE_SWITCH.match(v[j]):", "while False:",
     "test_r3_cmd_switches_before_c"),
    ("r3-input-cap", "if len(command) > MAX_INPUT_CHARS:", "if False:",
     "test_r3_input_larger_than_the_cap_is_unsure"),
    ("r1-cmd-eq-token",'if c == "=" and t.startswith("==", self.i):', "if False:",
     "test_r1_cmd_if_condition_is_not_the_program"),
    ("r1-cmd-slash-split", "if _CMD_NAME.fullmatch(head):", "if False:", "test_r1_cmd_forms"),
    ("r1-cmd-dot", 'b = b.rstrip(".")', "b = b", "test_r1_cmd_forms"),
    ("r1-cmd-for-body", 'if first == "for" and last == "in" and f.word is None:',
     'if first == "for" and f.word is None:', "test_r1_cmd_forms"),
    ("r1-cmd-for-command", 'self.reparse(inner[1:-1], "cmd")', "pass", "test_r1_cmd_forms"),
    ("r1-cmd-rem-ends-at-amp", "e = _CMD_REM_END.search(t, self.i)", "e = None", "test_r1_cmd_forms"),
    ("r1-comspec", 'if p == "?" and _cmd_c_follows(v, k + 1):', "if False:", "test_r1_cmd_forms"),
    ("r1-time-keyword", 'if v == "time":', "if False:", "test_r1_bash_time_and_coproc"),
    ("r1-time-p", 'if after_time and v in ("-p", "--"):', "if False:", "test_r1_bash_time_and_coproc"),
    ("r1-coproc", 'if v == "coproc":', "if False:", "test_r1_bash_time_and_coproc"),
    ("r1-coproc-name", "if coproc and _VARNAME.fullmatch(v):", "if False:", "test_r1_bash_time_and_coproc"),
    ("r1-skipped-subst", "m = _SUBST_START.search(t, j, b)", "m = None",
     "test_r1_skipped_spans_with_substitutions_are_parsed"),
    ("r1-skipped-array", "self._skipped(f, i + 1, k, False)", "pass",
     "test_r1_array_literals_are_scanned_for_substitutions"),
    ("r1-skipped-brace", "self._skipped(f, i + 2, end - 1, False)", "pass",
     "test_r1_skipped_spans_with_substitutions_are_parsed"),
    ("r1-skipped-dollar-arith", "self._skipped(f, i + 3, k - 1, True)", "pass",
     "test_r1_skipped_spans_with_substitutions_are_parsed"),
    ("r1-skipped-dparen", "self._skipped(f, i + 2, k - 1, True)", "pass",
     "test_r1_double_paren_that_reads_as_a_command_is_unsure"),
    ("r1-arith-cmdlike", "if not found and arith and _CMDLIKE.match(t, a, b):", "if False:",
     "test_r1_double_paren_that_reads_as_a_command_is_unsure"),
    ("r1-pwsh-herestring", 'if nx in _DQ and "$(" in t[j:k]:', "if False:",
     "test_r1_pwsh_here_string_with_a_subexpression_is_unsure"),
    ("r1-pwsh-opaque-method", "if tail and tail.group(1).lower() in _PS_OPAQUE_METHODS:", "if False:",
     "test_r1_unknown_execution_is_opaque_not_missing"),
    ("r1-pwsh-dashes", 'vals = ["-" + v[1:] if v[:1] in _PS_DASHES', "vals = [v if v[:1] in _PS_DASHES",
     "test_r1_pwsh_dash_variants_are_parameters"),
    ("r1-pwsh-stop-parsing", 'keep = [j for j, w in enumerate(ws) if not (vals[j] == "--%" and not w.quoted)]',
     "keep = list(range(len(ws)))", "test_r1_more_wrappers"),
    ("r1-pipe-into-shell-opaque", "if not known:", "if False:", "test_r1_unknown_execution_is_opaque_not_missing"),
    ("r1-shell-reads-a-file-opaque", 'elif program in ALL_SHELLS and any(op in ("<", "0<") for op, _ in self.redirs):',
     "elif False:", "test_r1_unknown_execution_is_opaque_not_missing"),
    ("r1-env-split-string", "if split is not None:", "if False:", "test_r1_more_wrappers"),
    ("r1-wrappers", "if p in _WRAPPERS:", "if False:", "test_r1_more_wrappers"),
    ("r1-watch-reparse", 'if p == "watch":', "if False:", "test_r1_wsl_and_watch_run_their_words_as_bash"),
    ("r1-wsl-runs-bash", 'if p == "wsl":', "if False:", "test_r1_wsl_and_watch_run_their_words_as_bash"),
    ("r1-wsl-management", "else:                                       # --import, --list, --shutdown ...: no command",
     "else:\n                        k += 1\n                        continue", "test_r1_wsl_management_and_quoted_words"),
    ("r1-flock-c", 'if p == "flock" and self._reparse_after_c(k):', "if False:", "test_r1_more_wrappers"),
    ("r1-su-script-c", "if not self._reparse_after_c(k + 1):", "if True:", "test_r1_more_wrappers"),
    ("r1-trap", 'self.reparse(v[h], "bash")                  # the handler runs on the signal', "pass",
     "test_r1_more_wrappers"),
    ("r1-alias", 'self.reparse(w.split("=", 1)[1], "bash")', "pass", "test_r1_more_wrappers"),
    ("r1-parallel", 'if p == "parallel":', "if False:", "test_r1_more_wrappers"),
    ("r1-iex-opaque", "if not lits or any(self.y[k + 1:]):", "if False:",
     "test_r1_unknown_execution_is_opaque_not_missing"),
    ("r1-saps-args-alias", 'if name and ("argumentlist".startswith(name) or name == "args"):',
     'if name and "argumentlist".startswith(name):', "test_r1_start_process_runs_its_argument_list"),
    ("r1-saps-reparse", "self.reparse('\"' + fp[0].replace('\"', \"\") + '\" ' + \" \".join(words), \"cmd\")", "pass",
     "test_r1_start_process_runs_its_argument_list"),
    ("r1-bash-stdin-dash", 'sflag = sflag or w == "-"', "sflag = sflag", "test_r1_stdin_dash_and_command_prefix"),
    ("r1-pwsh-stdin-dash", 'if w == "-":                                        # `pwsh -`: commands from stdin',
     "if False:",
     "test_r1_stdin_dash_and_command_prefix"),
    ("r1-pwsh-co-prefix", '(len(name) >= 2 and "command".startswith(name))',
     '(len(name) >= 3 and "command".startswith(name))', "test_r1_stdin_dash_and_command_prefix"),
    # cmd and R5
    ("cmd-if", 'if p == "if":', "if False:", "test_cmd_commands"),
    ("dialect-union", 'a, b = _parse_one(command, "bash"), _parse_one(command, "powershell")',
     'a = b = _parse_one(command, "bash")', "test_unknown_dialect_unions_bash_and_powershell"),
]
MUTATIONS += [
    pytest.param(_SP, find, rep, "tests/test_shellparse.py::" + test, id="shellparse-" + suffix)
    for suffix, find, rep, test in _SP_CLAUSES
]
# Plan 5 Task 5 fix round 2: the timing harness itself. CPU (not wall) time, and the 80/20 linearity ratio.
_TT = "tests/test_skyguard_timing.py"
MUTATIONS += [
    pytest.param(_TT, "if large >= RATIO_FLOOR_MS:", "if False:",
                 _TT + "::test_the_ratio_clause_alone_catches_quadratic_work_under_the_ceilings",
                 id="timing-harness-ratio-clause"),
    pytest.param(_TT, "spent = (time.process_time() - t0) * 1000", "spent = (time.perf_counter() - w0) * 1000",
                 _TT + "::test_cpu_time_ignores_time_spent_waiting", id="timing-harness-cpu-not-wall"),
]
# ---- Plan 5 Task 7: delete rules on the parser (one twin per clause; each names the t07 row it must turn red,
# fired in-process by tests/test_skyguard_deletes.py::test_t07_row).
_DL = "tools/skyguard/deletes.py"


def _t07(row):
    return "tests/test_skyguard_deletes.py::test_t07_row[t07-" + row + "]"


_DEL_CLAUSES = [
    # (id, file, find, replace, t07 row that must go red)
    # clause (a): an operand that RESOLVES into a protected zone
    ("rule-S-DEL-INSTALL-resolved", _DL, "                if kind and resolved is None:\n",
     "                if False:\n", "rel-rm-data"),
    ("rule-S-DEL-INSTALL-scratch", "tools/skyguard/paths.py",
     'SCRATCH_GLOBS = ("**/__pycache__/**", "**/node_modules/**", "**/.pytest_cache/**")', "SCRATCH_GLOBS = ()",
     "pycache"),
    ("rule-S-DEL-INSTALL-covers-ancestor", _DL, "    return _covers_kind(abs_path, roots)", '    return ""',
     "abs-ancestor"),
    ("rule-S-DEL-CONFIG-zone", _DL,
     'RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG", "mo2": "S-DEL-MO2"}',
     'RULE_OF = {"install": "S-DEL-INSTALL", "mo2": "S-DEL-MO2"}', "config-pwsh"),
    ("rule-S-DEL-MO2-zone", _DL,
     'RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG", "mo2": "S-DEL-MO2"}',
     'RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG"}', "mo2-bash"),
    ("rule-S-DEL-INSTALL-interpreter-code", _DL,
     "        if code and resolved is None and (has(DESTROYER, code) or has(INTERP_DELETE, code)):",
     "        if False:", "python-rmtree"),
    ("rule-S-DEL-INSTALL-interpreter-api", _DL,
     "(has(DESTROYER, code) or has(INTERP_DELETE, code))", "(has(DESTROYER, code))", "node-rmsync"),
    # clause (b): an UNRESOLVED operand
    ("rule-S-DEL-UNRESOLVED-needs-root-mention", _DL, "    if unresolved_any and names_root(command, roots, has):",
     "    if unresolved_any:", "tmp-var"),
    ("rule-S-DEL-UNRESOLVED-needs-unresolved", _DL, "    if unresolved_any and names_root(command, roots, has):",
     "    if names_root(command, roots, has):", "grep-control"),
    ("rule-S-DEL-UNRESOLVED-raw-glob", _DL,
     "            if raw_reaches(w, ecwd, e.dialect, roots) and unresolved_reach is None:",
     "            if False:", "glob-middle"),
    ("rule-S-DEL-UNRESOLVED-short-name", _DL,
     '    return bool(m) and re.sub(r"[\\s.]", "", comp).startswith(m.group(1))', "    return False", "short-name"),
    ("rule-S-DEL-UNRESOLVED-pipe-input", _DL,
     '    if not words and e.stdin_from == "pipe":', "    if False:", "pipe-remove-item"),
    # clause (c): the parser could not be sure
    ("rule-S-DEL-FALLBACK-on-parse-fail", _DL,
     "    if not parsed.ok and has(DESTROYER, command) and names_root(command, roots, has):", "    if False:",
     "parse-fail"),
    ("rule-S-DEL-FALLBACK-opaque", _DL,
     "    if opaque and any(_loose_hit(e, roots, has) for e in parsed.execs):", "    if False:", "opaque-printf-sh"),
    ("rule-S-DEL-FALLBACK-opaque-per-exec", _DL,
     "    if opaque and any(_loose_hit(e, roots, has) for e in parsed.execs):",
     "    if opaque and has(DESTROYER_LOOSE, command) and names_root(command, roots, has):", "opaque-elsewhere"),
    # R9: no cwd -> advice, never a guess
    ("rule-S-RELATIVE-UNKNOWN-advises", _DL, "            if nocwd and not relative and _is_data_word(w) and",
     "            if False and", "relative-unknown"),
    # primitives
    ("pwsh-remove-item-alias", _DL,
     'DELETE_PROGRAMS = frozenset(("rm", "rmdir", "unlink", "del", "erase", "rd", "remove-item", "rimraf"))',
     'DELETE_PROGRAMS = frozenset(("rm", "rmdir", "unlink", "del", "erase", "rd", "rimraf"))', "rel-pwsh-ri"),
    ("git-clean-cwd", _DL, "if specs else [root]", "if specs else []", "git-clean"),
    ("git-clean-dry-run", _DL, '            if "n" in (cluster if e < 0 else cluster[:e]):',
     '            if "Q" in (cluster if e < 0 else cluster[:e]):', "git-clean-dry-run"),
    ("git-rm", _DL, 'argv[k] not in ("clean", "rm")', 'argv[k] not in ("clean",)', "git-rm"),
    ("git-rm-cached", _DL, 'w in ("--dry-run", "--cached")', 'w in ("--dry-run",)', "git-rm-cached"),
    ("find-delete", _DL, '    if p == "find.delete":\n        return list(e.argv)',
     '    if p == "find.delete":\n        return None', "rel-find-delete"),
    ("iodirectory-delete", _DL, '    if p in ("io.file.delete", "io.directory.delete"):', "    if False:", "iodir-delete"),
    ("robocopy-mir", _DL, '    if flags & {"MIR", "PURGE"} and len(pos) > 1:', "    if False:", "robocopy-mir"),
    ("rimraf-via-npx", _DL, "    if p in NPX or p in PKG_RUNNERS:", "    if False:", "rimraf"),
    ("pwsh-valued-param-not-operand", _DL,
     "                k += 1                                    # its value is a pattern / option, not a path",
     "                pass", "pwsh-include-not-operand"),
    ("rule-S-DEL-INSTALL-tools-pycache-scratch", "tools/skyguard/targets.py",
     '        tools_cache = rel[0] == "tools" and any(c in paths.SCRATCH_UNDER_TOOLS for c in rel[1:])',
     "        tools_cache = False", "tools-pycache"),
    ("rule-S-DEL-INSTALL-tools-node-modules-protected", "tools/skyguard/paths.py",
     'SCRATCH_UNDER_TOOLS = ("__pycache__",)', 'SCRATCH_UNDER_TOOLS = ("__pycache__", "node_modules")',
     "tools-node-modules"),
    ("option-skip", _DL, '        if opts and w.startswith("-") and len(w) > 1:\n            continue',
     '        if False:\n            continue', "compound-tmp"),
]
MUTATIONS += [
    pytest.param(f, find, rep, _t07(row), id="deletes-" + i if not i.startswith("rule-") else i)
    for i, f, find, rep, row in _DEL_CLAUSES
]
MUTATIONS.append(pytest.param(   # a quoted `[x]` in the install's own name is literal, not a glob: still the install
    _DL, '                kind = protected_kind(lit, roots) if lit else ""', '                kind = ""',
    "tests/test_guard_corpus.py::test_hook_reproduces_the_recorded_decision_and_rules[root-metachar-self]",
    id="rule-S-DEL-INSTALL-literal-quoted-glob",
))

MUTATIONS.append(pytest.param(   # the pricing driver stops asking the parser: the R4 gate would read 0 forever
    "scripts/guard-corpus/engine_eval.py", "parse_ok = parse_ok and p.ok", "parse_ok = parse_ok",
    "tests/test_price_tool.py::test_parse_failures_are_reported_per_tool_when_the_engine_has_the_parser",
    id="price-reports-parse-failures",
))
MUTATIONS.append(pytest.param(
    "scripts/guard-corpus/record.py",
    'isinstance(row.get("reason"), str) and row["reason"].strip()',
    'str(row.get("reason") or "").strip()',
    "tests/test_guard_corpus.py::test_loosens_reason_must_be_a_non_empty_string",
    id="corpus-supersession-reason-is-a-string",
))


def _mutate(tmp_path: Path, relpath: str, find: str, replace: str) -> Path:
    work = tmp_path / "mutated"
    shutil.copytree(REPO, work, ignore=MUTATION_IGNORE)
    target = work / relpath
    # io.open, not Path.read_text: the `newline` kwarg only exists on Path in
    # 3.13+, and line endings must be preserved or the mutation rewrites the
    # whole file and masks what it changed.
    text = io.open(target, encoding="utf-8", newline="").read()
    # EXACTLY ONCE, not merely present.
    #
    # `str.replace` rewrites EVERY occurrence, so an anchor matching two sites
    # silently mutates both -- reverting two independent fixes while the mutation
    # claims, and the kill is attributed to, only one. It is the other half of the
    # pair from the exit-code check below: that one catches an anchor whose partner
    # TEST went stale, this one catches an anchor that grew to match TOO MUCH.
    #
    # MEASURED when this went in: `        sys.exit(2)` in tools/cosave-info.py had
    # been unique when its mutation was written and became ambiguous two commits
    # later, in the very fix commit a review round produced. Nothing was falsely
    # green -- the kill still came through the intended site -- but the gate could
    # not have told us, which is the whole complaint.
    n = text.count(find)
    assert n == 1, (
        f"mutation anchor must appear EXACTLY ONCE in {relpath}; found {n}.\n"
        + ("The code moved and this gate is now testing nothing.\n"
           + ("HINT: this anchor spans lines, and the target file uses CRLF. "
              "_mutate preserves line endings deliberately, so a '\\n' in the "
              "anchor cannot match. Use a single unique line instead.\n"
              if ("\n" in find and "\r\n" in text) else "")
           if n == 0 else
           "str.replace would mutate every occurrence, so this reverts more than "
           "the one fix it names. Narrow the anchor (include a neighbouring line) "
           "until it is unique.\n")
        + f"  {find!r}"
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
