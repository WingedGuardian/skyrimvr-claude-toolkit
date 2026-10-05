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
        'if roots.game == "":',
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
        '    "{"*) printf',
        '    *) printf',
        "tests/test_hooks.py::test_wrapper_denies_on_non_json_output",
        id="wrapper-passes-non-json-engine-output-through",
    ),
    # Trust JSON on stdout even when the engine exited non-zero (it crashed after printing).
    pytest.param(
        ".claude/hooks/protect-files.sh",
        'if [ "$RC" -ne 0 ]; then',
        'if false; then',
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
        'if ! command -v "$PY" >/dev/null 2>&1 && ! py_fallback; then',
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
        "PY_SED=$(printf '%s' " + '"$PY_Q"' + " | sed -e 's/[" + BS * 2 + "|&]/" + BS * 2 + "&/g')",
        'PY_SED=$PY_Q',
        "tests/test_python_path.py::test_a_path_containing_an_ampersand_is_not_mangled_by_sed",
        id="setup-escapes-sed-replacement-metacharacters",
    ),
    # Plan 4 Task 11: setup.sh configures Codex. One mutant per safety-relevant clause.
    # PowerShell escapes a quote in '...' by doubling it. The bash '\'' form ends the string: the entry
    # cannot parse, and a Codex hook that cannot start lets the command RUN.
    pytest.param(
        "setup.sh",
        'PY_PS="${PYTHON_PATH//' + BS + "'/" + BS + "'" + BS + "'}" + '"',
        'PY_PS="${PYTHON_PATH//' + BS + "'/" + BS + "'" + BS * 2 + BS + "'" + BS + "'}" + '"',
        "tests/test_setup_paths.py::test_setup_configures_the_codex_entry_scripts",
        marks=pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")),
                                 reason="needs PowerShell to parse the entry"),
        id="setup-ps1-python-path-uses-powershell-quoting",
    ),
    # The Codex entries must get the interpreter at all (else every Codex call is the INERT deny).
    pytest.param(
        "setup.sh",
        "            .codex/hooks/skyguard-entry.ps1 .codex/hooks/skyguard-entry.sh; do",
        "            ; do",
        "tests/test_setup_paths.py::test_setup_configures_the_codex_entry_scripts",
        id="setup-configures-the-codex-entries-interpreter",
    ),
    # ...and a re-run must repair a moved interpreter in the .ps1 too ($PY = '...', not PY='...').
    pytest.param(
        "setup.sh",
        "H_RE='^[$]PY = '",
        "H_RE='^PY='",
        "tests/test_setup_paths.py::test_rerunning_setup_repairs_the_codex_entries_interpreter",
        id="setup-rerun-repairs-the-ps1-interpreter",
    ),
    # The PowerShell entry hands the engine a bash; an MSYS /usr/bin/bash is not one it can start.
    pytest.param(
        "setup.sh",
        "if command -v cygpath >/dev/null 2>&1; then",
        "if false; then",
        "tests/test_setup_paths.py::test_setup_configures_the_codex_entry_scripts",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="cygpath exists on Git Bash only"),
        id="setup-codex-bash-path-is-a-windows-path",
    ),
    # Git Bash rewrites a /c/... argument to C:/... for a native python: the POSIX root changes, so the
    # renderer refuses it and Codex is left unconfigured on every Windows install.
    pytest.param(
        "setup.sh",
        "CODEX_ERR=$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $PY_FOUND",
        "CODEX_ERR=$($PY_FOUND",
        "tests/test_setup_paths.py::test_setup_fills_the_codex_definitions_with_the_install_root",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="Codex definitions are rendered on Windows"),
        id="setup-codex-render-keeps-the-posix-root",
    ),
    # A refused root must say WHY, loudly: otherwise Codex runs unguarded and setup looks clean.
    pytest.param(
        "setup.sh",
        '            echo "  !! ${CODEX_ERR:-the renderer failed (exit $CODEX_RC)}"',
        "            :",
        "tests/test_setup_paths.py::test_setup_refuses_a_root_it_cannot_put_in_json",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="Codex definitions are rendered on Windows"),
        id="setup-says-why-codex-is-unconfigured",
    ),
    # Definitions rendered for another folder must not pass as "Already configured".
    pytest.param(
        "setup.sh",
        "elif codex_same_root; then",
        "elif true; then",
        "tests/test_setup_paths.py::test_setup_warns_when_codex_definitions_point_elsewhere",
        id="setup-warns-when-codex-definitions-point-elsewhere",
    ),
    # The approval step is the one thing that makes the Codex hooks run at all.
    pytest.param(
        "setup.sh",
        'if [ "$CODEX_STATE" = "configured" ]; then',
        "if false; then",
        "tests/test_setup_paths.py::test_setup_prints_the_codex_approval_step",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="Codex definitions are rendered on Windows"),
        id="setup-prints-the-codex-approval-step",
    ),
    # AGENTS.md is what Codex reads: unconfigured, it names {{GAME_ROOT}} instead of the install.
    pytest.param(
        "setup.sh",
        "for DOC in CLAUDE.md AGENTS.md; do",
        "for DOC in CLAUDE.md; do",
        "tests/test_setup_paths.py::test_agents_md_gets_the_same_paths_as_claude_md",
        id="setup-configures-agents-md",
    ),
    # kb-guard must snapshot AGENTS.md: it carries the install's filled-in paths, and an update replaces it.
    pytest.param(
        "tools/kb-guard.sh",
        'WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md AGENTS.md"',
        'WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md"',
        "tests/test_kb_guard.py::test_every_watched_file_is_actually_snapshotted",
        id="kb-guard-watches-agents-md",
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
        "    sed -n '2,42p' \"$0\"",
        "    sed -n '2,46p' \"$0\"",
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
        'HB_DIR="$PROJECT_DIR/.claude/backups/.hook-heartbeat/claude"',
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
        'command -v "$PY" >/dev/null 2>&1 || py_fallback || inert',
        'true || inert',
        "tests/test_hooks.py::test_a_blocking_hook_refuses_when_python_is_unusable",
        id="blocking-hook-silent-when-python-is-missing",
    ),

    pytest.param(
        # Put the bare `Skyrim` back in the catch-all. Every edit to a checkout of
        # this toolkit is then annotated as an edit inside a live game install. (Plan 5 Task 9: the
        # catch-all is decided on the resolved zone, so the bare word goes back in THERE.)
        "tools/skyguard/write_rules.py",
        "        install = a is not None and in_install_area(a, roots)",
        '        install = a is not None and (in_install_area(a, roots) or "skyrim" in a.lower())',
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
    # The same, for the Codex tree: the project_doc_max_bytes margin is the easiest line to "tidy" by hand.
    pytest.param(
        ".codex/config.toml",
        "project_doc_max_bytes = 65536",
        "project_doc_max_bytes = 32768",
        "tests/test_gen_agent_trees.py::test_repo_is_in_sync",
        id="generated-codex-files-cannot-be-hand-edited",
    ),
    # Two targets owning one path would let the later silently overwrite the earlier.
    pytest.param(
        "scripts/gen_agent_trees.py",
        "            if rel in out:",
        "            if False:",
        "tests/test_gen_agent_trees.py::test_no_path_is_owned_by_two_targets",
        id="two-targets-cannot-own-one-path",
    ),
    # `*.ps1` is pinned CRLF; without the path rule the generated entry script would check out CRLF and the
    # byte-compare drift gate would go red on every Windows checkout.
    pytest.param(
        ".gitattributes",
        ".codex/** text eol=lf",
        ".codex/** text",
        "tests/test_gen_agent_trees.py::test_codex_tree_lf_pin_is_a_path_rule_that_comes_after_the_ps1_rule",
        id="codex-tree-is-pinned-lf",
    ),
    # The skill sources feed .agents/skills/ too, so a Claude-only construct in ANY of them is a defect.
    pytest.param(
        "agent/skills/skyrim-bsa/SKILL.md",
        "# BSA/BA2 Archive Operations",
        "# BSA/BA2 Archive Operations -- ask Claude to run $ARGUMENTS",
        "tests/test_instruction_neutrality.py::test_every_skill_body_is_neutral",
        id="every-skill-body-is-neutral",
    ),
    # Pin the canary red forever again: never let a completed evaluation clear a failure. An instrument
    # that cannot go back to green gets ignored, which is the same end state as having no instrument.
    pytest.param(
        "tools/skyguard/health.py",
        "    if failed and not cleared:",
        "    if failed:",
        "tests/test_skyguard_health.py::test_a_newer_completed_evaluation_clears_it_and_says_so",
        id="canary-can-go-back-to-green",
    ),
    # A malformed stamp must fail loud. Python's float() reads `20260909_15` (a 3.x stamp) as 2026090915.0,
    # so without the strict pattern it parses as a time and decides recovery order by accident.
    # (The twin carries enough fields that only the timestamp check can reject it.)
    pytest.param(
        "tools/skyguard/health.py",
        "        if not _TIME.match(fields[0]):",
        "        if False:",
        "tests/test_skyguard_health.py::test_a_malformed_timestamp_with_enough_fields_is_unmeasurable",
        id="canary-malformed-stamp-cannot-clear",
    ),
    # And the field count: a record cut off mid-write must not parse.
    pytest.param(
        "tools/skyguard/health.py",
        "    if len(fields) < _MIN_FIELDS[stage]:",
        "    if False:",
        "tests/test_skyguard_health.py::test_unparseable_records_are_unmeasurable",
        id="canary-truncated-record-cannot-clear",
    ),
    # Third clause: make the comparison non-strict, so EQUAL timestamps count as proof of recovery.
    # Same instant proves no ordering at all.
    pytest.param(
        "tools/skyguard/health.py",
        "else done > failed[0]",
        "else done >= failed[0]",
        "tests/test_skyguard_health.py::test_equal_timestamps_prove_no_recovery",
        id="canary-a-tie-is-not-a-recovery",
    ),
    # Recovery must be proven by a COMPLETED evaluation. Accept a newer receipt instead and a guard whose
    # engine crashes on every call reads HEALTHY as soon as the next payload arrives.
    pytest.param(
        "tools/skyguard/health.py",
        '    if ev and em and ev[2][0] == "evaluated" and em[1] == ev[1]:\n        return ev[0]',
        '    if recs.get("received"):\n        return recs["received"][0]',
        "tests/test_skyguard_health.py::test_a_newer_receipt_does_not_clear_a_failure",
        id="health-recovery-needs-a-completed-evaluation",
    ),
    # Forget the never-finished rule and a hook killed by the agent's timeout -- on Codex, a command
    # that RAN unchecked -- leaves no trace in the report.
    pytest.param(
        "tools/skyguard/health.py",
        "if _never_finished(",
        "if False and _never_finished(",
        "tests/test_skyguard_health.py::test_an_invocation_that_never_finished_is_degraded",
        id="health-reports-a-killed-invocation",
    ),
    # Order a failure against a completion by the wrapper's invocation SEQUENCE, not the wall clock alone.
    # Compare stamps only and a clock stepped back makes a later failure look older than an earlier success.
    pytest.param(
        "tools/skyguard/health.py",
        "cleared = ds > fs if (ds is not None and fs is not None) else done > failed[0]",
        "cleared = done > failed[0]",
        "tests/test_skyguard_health.py::test_a_clock_stepped_back_cannot_hide_a_failure",
        id="health-orders-by-invocation-sequence",
    ),
    pytest.param(
        "tools/skyguard/health.py",
        "cleared = ds > fs if",
        "cleared = ds >= fs if",
        "tests/test_skyguard_health.py::test_the_same_sequence_proves_no_recovery",
        id="health-same-sequence-is-not-recovery",
    ),
    # A stamp from the future outranks every real record: a completion stamped years ahead never goes stale.
    pytest.param(
        "tools/skyguard/health.py",
        "        if rec[0] > now + SKEW_S:",
        "        if False:",
        "tests/test_skyguard_health.py::test_a_completion_stamped_in_the_future_is_unmeasurable_not_fresh_forever",
        id="health-rejects-future-stamps",
    ),
    # SessionStart always reported `evaluated`, even when kb-guard.sh was missing and nothing was snapshotted.
    pytest.param(
        "tools/skyguard/run.py",
        "_telemetry(a, Verdict(status=status, code=code))",
        "_telemetry(a, Verdict(code=code))",
        "tests/test_hooks.py::test_a_session_start_that_could_not_run_kb_guard_is_degraded",
        id="session-status-reaches-the-canary",
    ),
    # Killed-run detection: the next call overwrites `invoked`, so without it a kill leaves no trace.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        "[ $((T_S - O_S)) -gt 60 ]",
        "[ $((T_S - O_S)) -gt 99999999 ]",
        "tests/test_hooks.py::test_a_killed_run_is_reported_and_stays_degraded_until_a_newer_completion",
        id="wrapper-reports-a-killed-run",
    ),
    # ... and its twin: with no grace period a parallel call still in flight is called killed.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        "[ $((T_S - O_S)) -gt 60 ]",
        "[ $((T_S - O_S)) -gt -1 ]",
        "tests/test_hooks.py::test_a_run_still_inside_the_grace_period_is_not_called_killed",
        id="wrapper-killed-run-has-a-grace-period",
    ),
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        '[ "$OINV" != "$FINV" ] && ',
        "",
        "tests/test_hooks.py::test_a_failed_run_is_not_also_called_killed",
        id="wrapper-does-not-double-report-a-failed-run",
    ),
    # A record rewritten by a Windows-native writer ends in CR; without stripping it every id looks different
    # and an invocation that recorded its own failure is reported a second time as "killed".
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        "OINV=" + chr(34) + "${OINV%$" + chr(39) + chr(92) + "r" + chr(39) + "}" + chr(34),
        "OINV=" + chr(34) + "$OINV" + chr(34),
        "tests/test_hooks.py::test_a_failed_run_is_not_also_called_killed",
        id="wrapper-strips-cr-from-previous-records",
    ),
    # A telemetry directory the hooks cannot write: every wrapper swallows the failure, so the report would
    # show frozen records as HEALTHY, then STALE.
    pytest.param(
        "tools/skyguard/health.py",
        "unwritable = d.is_dir() and not _writable(d)",
        "unwritable = False",
        "tests/test_hook_canary.py::test_a_telemetry_directory_hooks_cannot_write_to_is_unmeasurable",
        id="canary-reports-an-unwritable-telemetry-dir",
    ),
    # A payload-less call is still evaluated and emitted by the engine. Drop the wrapper's trailing
    # failure record and that completed evaluation clears the very failure it accompanies.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        '    [ -z "$NOPAY" ] || _fail receipt "NO PAYLOAD"',
        '    [ -z "$NOPAY" ] || :',
        "tests/test_hooks.py::test_an_empty_payload_stays_failed_even_though_the_engine_then_evaluates",
        id="wrapper-empty-payload-cannot-clear-itself",
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
        'WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md AGENTS.md"',
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
        "        return None" + chr(10),
        "        return {}" + chr(10),
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
    # ---- Plan 4 (master) entries, merged 2026-10-04 ----
    # --- instruction budget and neutrality (Plan 3) ------------------------
    # Lift the limit: an AGENTS.md that Codex silently truncates would pass.
    pytest.param("scripts/gen_agent_trees.py", "INSTRUCTION_BUDGET = 32768", "INSTRUCTION_BUDGET = 10 ** 9",
                 "tests/test_gen_agent_trees.py::test_budget_rejects_an_oversize_core",
                 id="instruction-budget-is-enforced"),
    # Lift it a LITTLE: a test that only checks a grossly oversize core misses a limit nudged to 33000.
    pytest.param("scripts/gen_agent_trees.py", "INSTRUCTION_BUDGET = 32768", "INSTRUCTION_BUDGET = 33000",
                 "tests/test_gen_agent_trees.py::test_budget_rejects_an_oversize_core",
                 id="instruction-budget-is-exactly-the-codex-limit"),
    # Stop holding room for the Codex addendum: Plan 4 would land over budget on day one.
    pytest.param("scripts/gen_agent_trees.py", "CODEX_ADDENDUM_RESERVE = 2048", "CODEX_ADDENDUM_RESERVE = 0",
                 "tests/test_gen_agent_trees.py::test_budget_holds_room_for_the_codex_addendum",
                 id="budget-reserves-the-codex-addendum"),
    # Measure the repo file instead of the installed one.
    pytest.param("scripts/gen_agent_trees.py", "INSTALL_EXPANSION_RESERVE = 2560", "INSTALL_EXPANSION_RESERVE = 0",
                 "tests/test_gen_agent_trees.py::test_budget_holds_room_for_install_expansion",
                 id="budget-measures-the-installed-size"),
    # Unwire the budget from --check: every test of budget_problems() stays green, CI stops enforcing it.
    pytest.param("scripts/gen_agent_trees.py", '        problems += budget_problems(a.root / "agent")',
                 "        pass",
                 "tests/test_gen_agent_trees.py::test_check_reports_an_over_budget_core",
                 id="check-enforces-the-budget"),
    # Grow the real core past the limit.
    pytest.param("agent/instructions/core.md", "## INI Config Hierarchy", "## INI Config Hierarchy\n\n" + "x" * 9000,
                 "tests/test_instruction_budget.py::test_repo_instructions_fit_the_budget",
                 id="real-core-is-within-budget"),
    # Put a Claude-only construct back into the shared core.
    pytest.param("agent/instructions/core.md", "## INI Config Hierarchy",
                 "## INI Config Hierarchy (edit them with the Edit tool)",
                 "tests/test_instruction_neutrality.py::test_core_is_agent_neutral",
                 id="core-stays-agent-neutral"),
    pytest.param("agent/skills/devbench/SKILL.md", "# DevBench — the live in-game test channel",
                 "# DevBench — the live in-game test channel ($CLAUDE_PROJECT_DIR)",
                 "tests/test_instruction_neutrality.py::test_new_skills_are_agent_neutral",
                 id="new-skills-stay-agent-neutral"),
    # Drop the fallback instruction: an agent with no session hook never sees the user's preferences.
    pytest.param("agent/instructions/core.md", "session and follow it**.", "session**.",
                 "tests/test_instruction_neutrality.py::test_core_tells_every_agent_to_read_my_preferences",
                 id="core-keeps-the-preferences-fallback"),
    # --- preferences injection (Plan 3, D15) --------------------------------
    pytest.param("tools/skyguard/prefs.py", "        elif not in_fence and _HEADING.match(line):",
                 "        elif _HEADING.match(line):",
                 "tests/test_skyguard_prefs.py::test_heading_inside_a_code_fence_is_not_the_section",
                 id="prefs-ignore-a-fenced-heading"),
    pytest.param("tools/skyguard/prefs.py", "    if len(data) > CAP:", "    if False:",
                 "tests/test_skyguard_prefs.py::test_long_section_is_capped_on_a_character_boundary",
                 id="prefs-are-capped"),
    pytest.param("tools/skyguard/session.py",
                 '        return _with_prefs(banners, project, audit), "evaluated", "session"',
                 '        return banners, "evaluated", "session"',
                 "tests/test_skyguard_prefs.py::test_compact_still_injects_preferences_but_takes_no_snapshot",
                 id="prefs-survive-compaction"),
    pytest.param("tools/skyguard/session.py", '    return _with_prefs("\\n".join(lines), project, audit)',
                 '    return "\\n".join(lines)',
                 "tests/test_skyguard_prefs.py::test_preferences_follow_a_clean_kb_guard_run",
                 id="session-start-injects-preferences-after-a-kb-guard-run"),
    # One twin per return site: each site shadows the others, so one mutation proves only one site.
    # (The message is bound to `missing` first so this mutation is still a valid program: cutting
    # `, project, audit)` off a one-line call would be a SyntaxError -- a kill for the wrong reason.)
    pytest.param("tools/skyguard/session.py",
                 '        return _with_prefs("\\n".join(t for t in (banners, missing) if t), project, audit), "failed"',
                 '        return "\\n".join(t for t in (banners, missing) if t), "failed"',
                 "tests/test_skyguard_prefs.py::test_the_real_hook_delivers_preferences",
                 id="session-start-injects-preferences-when-kb-guard-is-missing"),
    # An INERT refusal must be recorded as "nothing was checked", not as a rule decision. Fold the
    # status back into the decision and the telemetry would read a blind guard as a working one.
    pytest.param(
        "tools/skyguard/codes.py",
        '    status = "failed" if _inert(code) else v.status',
        "    status = v.status",
        "tests/test_skyguard_codes.py::test_inert_refusals_are_failed_not_evaluated",
        id="inert-refusal-is-status-failed",
    ),
    # run.py's unreadable-env refusal bypasses the rule modules; classify cannot recover it, so the
    # construction itself must say "nothing was checked".
    pytest.param(
        "tools/skyguard/run.py",
        'v = Verdict("deny", ENV_UNREADABLE, status="failed", code="run.ENV_UNREADABLE")',
        'v = Verdict("deny", ENV_UNREADABLE)',
        "tests/test_skyguard_codes.py::test_unreadable_env_refusal_is_failed_and_coded",
        id="env-unreadable-refusal-is-status-failed",
    ),
    # Folding zero verdicts must not read as "allow": it means nothing was evaluated.
    pytest.param(
        "tools/skyguard/engine.py",
        "    if not vs:   # nothing was evaluated; an empty fold must never read as \"allow\"",
        "    if False:",
        "tests/test_skyguard_engine.py::test_combine_of_nothing_is_a_failed_deny_not_an_allow",
        id="combine-of-nothing-is-not-an-allow",
    ),
    # --- apply_patch parser (Plan 4 Task 3 fix round): each clause has its own twin ---------------
    pytest.param(
        "tools/skyguard/patch_paths.py", "        line = raw.strip()", "        line = raw",
        "tests/test_patch_paths.py::test_indented_headers_are_headers_like_codex_reads_them",
        id="patch-headers-are-matched-on-the-trimmed-line",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", '        if not line.startswith("***"):', '        if not line.startswith("*** "):',
        "tests/test_patch_paths.py::test_any_other_star_line_is_refused",
        id="patch-any-star-line-is-refused",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", " or rest[h.end():].strip():", ":",
        "tests/test_patch_paths.py::test_text_the_parser_does_not_inspect_is_refused",
        id="patch-text-after-the-heredoc-terminator-is-refused",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", ' or h.group("tail").strip()', "",
        "tests/test_patch_paths.py::test_text_the_parser_does_not_inspect_is_refused",
        id="patch-text-after-the-heredoc-tag-is-refused",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", "if rest[:h.start()].strip()", "if False",
        "tests/test_patch_paths.py::test_text_the_parser_does_not_inspect_is_refused",
        id="patch-text-before-the-heredoc-is-refused",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py",
        "(?:[A-Za-z_][A-Za-z0-9_]*=\\S*|sudo|env|", "(?:sudo|env|",
        "tests/test_patch_paths.py::test_every_way_to_run_it_is_attributed_or_refused",
        id="patch-env-assignment-prefix-is-a-run",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", '_NAME = r"(?:[^\\s\'\\"`;&|()<>]*[/\\\\])?', '_NAME = r"',
        "tests/test_patch_paths.py::test_every_way_to_run_it_is_attributed_or_refused",
        id="patch-path-prefix-is-a-run",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", "(?:\\.exe)?(?=[\\s;", "(?=[\\s;",
        "tests/test_patch_paths.py::test_every_way_to_run_it_is_attributed_or_refused",
        id="patch-exe-suffix-is-a-run",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", "or (_QUOTED_POSITION.search(cmd) and _WRAPPER.search(cmd))", "",
        "tests/test_patch_paths.py::test_every_way_to_run_it_is_attributed_or_refused",
        id="patch-run-inside-a-wrapped-shell-string",
    ),
    pytest.param(
        "agent/guards/adapters/claude/protect-bash.sh",
        '*/[Ww]indows[Aa]pps/*) [ -z "$PY_ALIAS" ] && PY_ALIAS="$c"; continue ;;',
        '*/[Ww]indows[Aa]pps/*) ;;',
        "tests/test_python_fallback.py::test_the_store_alias_is_used_only_last",
        id="python-fallback-uses-the-store-alias-last",
    ),
    pytest.param(
        "agent/guards/adapters/claude/protect-files.sh",
        r'''[ "${o%$'\r'}" = "skyguard-selftest:$n" ]''',
        'true',
        "tests/test_python_fallback.py::test_a_candidate_that_echoes_a_stale_nonce_is_rejected_and_the_next_is_used",
        id="python-fallback-accepts-a-candidate-without-the-engine-nonce",
    ),
    pytest.param(
        "agent/guards/adapters/claude/protect-files.sh",
        'TM=(); command -v timeout >/dev/null 2>&1 && TM=(timeout -k 2 "$t")',
        'TM=()',
        "tests/test_python_fallback.py::test_a_hanging_fallback_engine_is_cut_off_by_timeout",
        id="fallback-engine-call-has-no-time-limit",
    ),
    pytest.param(
        "tools/skyguard/run.py",
        "faulthandler.dump_traceback_later(_engine_budget(), exit=True)",
        "pass",
        "tests/test_python_fallback.py::test_a_catastrophic_regex_inside_the_engine_is_killed_at_the_budget",
        id="engine-watchdog-disarmed",
    ),
    pytest.param(
        "agent/guards/adapters/claude/protect-files.sh",
        'for ((m = 30; m >= 8; m--)); do',
        'for ((m = 8; m <= 30; m++)); do',
        "tests/test_python_fallback.py::test_known_locations_are_tried_newest_version_first",
        id="python-fallback-known-locations-oldest-first",
    ),
    pytest.param(
        "setup.sh",
        r"""sys.version_info[:2]>=(3,8) else 1)' >/dev/null 2>&1; then""",
        r"""sys.version_info[0]==3 else 1)' >/dev/null 2>&1; then""",
        "tests/test_python_path.py::test_setup_refuses_a_python_older_than_3_8",
        id="setup-accepts-python-older-than-3-8",
    ),
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        "PY='{{PYTHON_PATH}}'",
        'PY="{{PYTHON_PATH}}"',
        "tests/test_python_path.py::test_a_path_with_shell_metacharacters_reaches_the_hook_verbatim",
        id="hook-python-path-in-double-quotes",
    ),
    pytest.param(
        "setup.sh",
        r'''PY_Q="${PYTHON_PATH//\'/\'\\\'\'}"''',
        'PY_Q="$PYTHON_PATH"',
        "tests/test_python_path.py::test_a_path_with_a_single_quote_survives_the_quoting",
        id="setup-does-not-escape-a-single-quote-in-the-python-path",
    ),
    # --- Codex adapter (Plan 4 Task 6): only a JSON deny blocks on Codex; one twin per clause -----
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '"permissionDecision": "deny",',
        '"permissionDecision": v.decision if v.decision == "ask" else "deny",',
        'tests/test_skyguard_codex_adapter.py::test_ask_never_reaches_codex',
        id='codex-ask-reaches-codex-as-ask',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '    if v.decision == "allow":',
        '    if v.decision != "deny" and v.decision != "ask":',
        'tests/test_skyguard_codex_adapter.py::test_an_unknown_decision_is_refused_not_allowed',
        id='codex-unknown-decision-is-allowed',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '    if len(data) <= CONTEXT_LIMIT:',
        '    if len(text) <= CONTEXT_LIMIT:',
        'tests/test_skyguard_codex_adapter.py::test_the_cap_counts_bytes_not_characters',
        id='codex-context-cap-counts-characters',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '            sp = patch_paths.shell_patch(cmd)',
        '            sp = None',
        'tests/test_skyguard_codex_adapter.py::test_a_patch_through_the_shell_carries_its_paths',
        id='codex-shell-patch-paths-dropped',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '    if req.failure:',
        '    if False:',
        'tests/test_skyguard_codex_adapter.py::test_an_untranslatable_request_is_a_failed_deny',
        id='codex-untranslatable-request-allowed',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '    elif req.event != "pre_tool":',
        '    elif False:',
        'tests/test_skyguard_codex_adapter.py::test_an_unexpected_event_is_a_failed_deny',
        id='codex-unexpected-event-allowed',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '        if req.paths:',
        '        if False:',
        'tests/test_skyguard_codex_adapter.py::test_a_shell_patch_is_checked_as_shell_and_as_write',
        id='codex-shell-patch-not-write-checked',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '    return v._replace(status="partial") if partial and v.status == "evaluated" else v',
        '    return v',
        'tests/test_skyguard_codex_adapter.py::test_a_side_effect_exception_is_partial_and_keeps_the_decision',
        id='codex-side-effect-failure-reads-as-clean',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '_BACKUP_OPS = ("update", "delete", "move_from")',
        '_BACKUP_OPS = ("update",)',
        'tests/test_skyguard_codex_adapter.py::test_a_delete_of_an_existing_file_is_backed_up',
        id='codex-delete-not-backed-up',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '    if _unhooked(req):',
        '    if False:',
        'tests/test_skyguard_codex_adapter.py::test_an_mcp_tool_is_allowed_and_recorded',
        id='codex-unhooked-tools-not-recorded',
    ),
    pytest.param(
        'tools/skyguard/run.py',
        '    if (a.agent == "codex") != (a.role in CODEX_ROLES):',
        '    if False:',
        'tests/test_skyguard_codex_adapter.py::test_cli_codex_roles_are_refused_for_claude_and_vice_versa',
        id='codex-roles-accepted-for-the-wrong-agent',
    ),
    # --- Codex fix round 1 ---
    pytest.param(
        'tools/skyguard/run.py',
        'codex.session_body(out)',
        'codex.BANNER + (chr(10) + out if out else "")',
        'tests/test_skyguard_codex_adapter.py::test_run_session_start_output_is_capped',
        id='codex-session-body-uncapped',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '    kept = detail.encode("utf-8", "replace")[:room]',
        '    kept = detail.encode("utf-8", "replace")[:room + 10**6]',
        'tests/test_skyguard_codex_adapter.py::test_session_body_is_capped_keeping_banner_and_preferences',
        id='codex-session-cap-ignores-room',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '    i = out.find(_PREFS_MARK)',
        '    i = -1',
        'tests/test_skyguard_codex_adapter.py::test_session_body_is_capped_keeping_banner_and_preferences',
        id='codex-session-cap-drops-preferences-with-detail',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '    if not name.startswith("mcp__") and isinstance(cmd, str):',
        '    if False:',
        'tests/test_skyguard_codex_adapter.py::test_an_unknown_tool_with_a_command_string_gets_the_shell_rules',
        id='codex-unknown-tool-command-unguarded',
    ),
    pytest.param(
        'tools/skyguard/engine.py',
        '    if req.failure or req.event != "pre_tool":',
        '    if False:',
        'tests/test_skyguard_codex_adapter.py::test_a_refused_call_is_not_logged_as_an_unhooked_tool',
        id='codex-refused-call-logged-as-unhooked',
    ),
    pytest.param(
        'tools/skyguard/run.py',
        'raise engine.RootsUnavailable(codex.ENV_UNREADABLE)',
        'raise engine.RootsUnavailable(ENV_UNREADABLE)',
        'tests/test_skyguard_codex_adapter.py::test_cli_env_unreadable_text_is_agent_neutral',
        id='codex-env-refusal-names-the-claude-hook',
    ),
    pytest.param(
        'tools/skyguard/adapters/codex.py',
        '    if text.startswith("\\ufeff"):',
        '    if False:',
        'tests/test_skyguard_codex_adapter.py::test_a_leading_bom_is_ignored',
        id='codex-leading-bom-not-stripped',
    ),
] + [
    # Plan 4 Task 7: the Codex entry wrappers. One mutant per safety clause (each fault-matrix row): on Codex
    # every one of these, removed, is a command that RUNS (a non-deny answer) or a deny for the wrong reason.
    # The PowerShell entry is the Windows entry (R2); its tests run only on Windows.
    pytest.param(
        "agent/guards/adapters/codex/skyguard-entry.ps1", *m[:3],
        marks=pytest.mark.skipif(sys.platform != "win32", reason="the PowerShell entry is tested on Windows"),
        id=m[3])
    for m in [
        (r'''    if ($text -cne 'SKYGUARD-OK' -and -not $text.StartsWith("SKYGUARD-OK`n", [System.StringComparison]::Ordinal)) {''',
         r'''    if ($false) {''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-hi]", "codex-ps1-sentinel-unchecked"),
        (r'''    if ($r.Code -ne 0) { Send-Failure 'engine' ('engine exited ' + $r.Code); return }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_engine_crash_is_a_deny[pwsh]", "codex-ps1-exit-code-unchecked"),
        (r'''        if (-not $proc.WaitForExit($left)) { $timedOut = $true }''',
         r'''        [void]$proc.WaitForExit($left)''',
         "tests/test_codex_entry.py::test_a_hung_engine_is_a_deny[pwsh]", "codex-ps1-no-deadline"),
        (r'''        if ($body.Contains("`n")) { Send-Failure 'emission' 'engine printed more than one body line'; return }''',
         r'''        if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-two-json]", "codex-ps1-lines-unchecked"),
        (r'''        if ($k -ceq '"permissionDecision"' -and $v -cne '"deny"') { return 'engine answered a permissionDecision other than deny' }''',
         r'''        if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-allow]", "codex-ps1-allow-passes"),
        (r'''    if (-not $m.Success) { return 'engine output is not a verdict JSON' }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-x1]", "codex-ps1-grammar-unchecked"),
        (r'''    if ($payload.Length -eq 0) { Send-Failure 'receipt' 'NO PAYLOAD on stdin'; return }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[powershell-pre-tool-empty-stdin]",
         "codex-ps1-empty-stdin-reaches-engine"),
        (r'''    if (-not [System.IO.File]::Exists($script:RunPy)) { Send-Failure 'engine' 'engine missing (tools/skyguard/run.py)'; return }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-deleted]", "codex-ps1-missing-engine"),
        (r'''    if ($null -eq $pyc) { Send-Failure 'python' 'cannot run Python -- install Python 3 and run setup.sh'; return }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-no-python]", "codex-ps1-no-python"),
        (r'''    Send-Failure 'wrapper' ('wrapper error: ' + $_.Exception.GetType().Name)''',
         r'''    $null = 0''',
         "tests/test_codex_entry.py::test_a_wrapper_exception_is_a_deny[pwsh]", "codex-ps1-catch-silent"),
        (r'''        if ($seen.ContainsKey($k)) { return 'engine output repeats a key' }''',
         r'''        if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-dup-key]", "codex-ps1-duplicate-key"),
        (r'''[^"\\\x00-\x1f]''',
         r'''[^"\\]''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-raw-tab]", "codex-ps1-raw-control-char"),
        (r'''        if (-not $body.StartsWith('SKYGUARD: ', [System.StringComparison]::Ordinal)) {''',
         r'''        if ($false) {''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-session-start-x1]", "codex-ps1-session-banner"),
        # Task 8 item 0 (the PowerShell entry is CRLF on disk: every anchor here is one line)
        (r'''    if (-not $seen.ContainsKey('"hookEventName"')) { return 'engine output names no event' }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-no-event]", "codex-ps1-event-optional"),
        (r'''    if ($body -match '(?<!\\)(?:\\\\)*\\u[dD][89abAB]') { return 'engine output has a surrogate escape' }''',
         r'''    if ($false) { }''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-surrogate]", "codex-ps1-surrogate-passes"),
        (r'''(?<!\\)(?:\\\\)*\\u[dD][89abAB]''',
         r'''\\u[dD][89abAB]''',
         "tests/test_codex_entry.py::test_an_escaped_backslash_before_u_is_not_a_surrogate[pwsh]",
         "codex-ps1-surrogate-ignores-escaped-backslash"),
        (r'''(?<!\\)(?:\\\\)*\\u[dD][89abAB]''',
         r'''(?<!\\)\\u[dD][89abAB]''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-surrogate-after-escape]",
         "codex-ps1-surrogate-after-escaped-backslash"),
        (r'''    if (-not $outTask.Wait($left)) { Stop-Tree $proc; return @{ Code = -1; Out = ''; TimedOut = $true } }''',
         r'''    [void]$outTask.Wait()''',
         "tests/test_codex_entry.py::test_a_grandchild_holding_stdout_does_not_outlast_the_deadline[pwsh]",
         "codex-ps1-unbounded-drain"),
    ]
] + [
    pytest.param("agent/guards/adapters/codex/skyguard-entry.sh", *m[:3], id=m[3])
    for m in [
        (r'''    TM=(); command -v timeout >/dev/null 2>&1 && TM=(timeout -k 2 "$lim")''',
         r'''    TM=()''',
         "tests/test_codex_entry.py::test_a_hung_engine_is_a_deny[bash]", "codex-sh-no-deadline"),
        (r'''        [ "$K" != '"permissionDecision"' ] || [ "$V" = '"deny"' ] || deny emission "engine answered a permissionDecision other than deny"''',
         r'''        true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-allow]", "codex-sh-allow-passes"),
        (r'''    *) deny emission "engine output lacks the SKYGUARD-OK line" ;;''',
         r'''    *) BODY="$OUT" ;;''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-hi]", "codex-sh-sentinel-unchecked"),
        (r'''    *) deny engine "engine exited $RC" ;;''',
         r'''    *) ;;''',
         "tests/test_codex_entry.py::test_engine_crash_is_a_deny[bash]", "codex-sh-exit-code-unchecked"),
        (r'''[ -n "$INPUT" ] || deny receipt "NO PAYLOAD on stdin"''',
         r'''true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-empty-stdin]",
         "codex-sh-empty-stdin-reaches-engine"),
        (r'''    [[ $BODY =~ $RE ]] || deny emission "engine output is not a verdict JSON"''',
         r'''    [[ $BODY =~ $RE ]]''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-x1]", "codex-sh-grammar-unchecked"),
        (r'''    case "$BODY" in *$'\n'*) deny emission "engine printed more than one body line" ;; esac''',
         r'''    true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-two-json]", "codex-sh-lines-unchecked"),
        (r'''        case "$SEEN" in *" $K "*) deny emission "engine output repeats a key" ;; esac''',
         r'''        true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-dup-key]", "codex-sh-duplicate-key"),
        (r'''[^"\\\\\x01-\x1f]''',
         r'''[^"\\\\]''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-raw-tab]", "codex-sh-raw-control-char"),
        (r'''[ -f "$ROOT/tools/skyguard/run.py" ] || deny engine "engine missing (tools/skyguard/run.py)"''',
         r'''true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-deleted]", "codex-sh-missing-engine"),
        (r'''    case "$BODY" in "SKYGUARD: "*) ;; *) deny emission "engine session text lacks the SKYGUARD banner" ;; esac''',
         r'''    true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-session-start-x1]", "codex-sh-session-banner"),
        # Task 8 item 0
        (r'''    case "$SEEN" in *' "hookEventName" '*) ;; *) deny emission "engine output names no event" ;; esac''',
         r'''    true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-no-event]", "codex-sh-event-optional"),
        (r'''    [[ $BODY =~ $SUR ]] && deny emission "engine output has a surrogate escape"''',
         r'''    true''',
         "tests/test_codex_entry.py::test_every_path_exits_zero[bash-pre-tool-surrogate]", "codex-sh-surrogate-passes"),
        (r'''    SUR='(^|[^\\])(\\\\)*\\u[dD][89abAB]''' + "'",
         r'''    SUR='\\u[dD][89abAB]''' + "'",
         "tests/test_codex_entry.py::test_an_escaped_backslash_before_u_is_not_a_surrogate[bash]",
         "codex-sh-surrogate-ignores-escaped-backslash"),
        ('\nrun_engine > "$OUTF"\nRC=$?\n',
         '\nOUT=$(run_engine)\nRC=$?\nprintf \'%s\' "$OUT" > "$OUTF"\n',
         "tests/test_codex_entry.py::test_a_grandchild_holding_stdout_does_not_outlast_the_deadline[bash]",
         "codex-sh-engine-output-through-a-pipe"),
        (r'''_utf8_ok "$OUT" || deny emission "engine output is not valid UTF-8"''',
         r'''true''',
         "tests/test_codex_entry.py::test_invalid_utf8_from_the_engine_never_reaches_codex[pre-tool-bash]",
         "codex-sh-invalid-utf8-passes"),
        (r'''IFS= read -r -d '' OUT < "$OUTF" && deny emission "engine output has a NUL byte"''',
         r'''IFS= read -r -d '' OUT < "$OUTF"''',
         "tests/test_codex_entry.py::test_a_nul_byte_in_the_engine_output_is_refused[pre-tool-bash]",
         "codex-sh-nul-truncates-to-allow"),
    ]
] + [
    # Plan 4 Task 8: the frozen Codex hook definitions, the freeze gate and the trust reader.
    # Change one definition field (E18): Codex silently stops running every approved copy of this hook.
    pytest.param(
        "agent/targets/codex/hooks.json",
        '"timeout": 60,',
        '"timeout": 61,',
        "tests/test_codex_definitions.py::test_codex_hook_definitions_are_frozen",
        id="codex-definitions-are-frozen",
    ),
] + [
    pytest.param(*m[:4], id=m[4])
    for m in [
        # R2: a leading quote / bash on Windows each fail to launch, and Codex then RUNS the command
        ("agent/targets/codex/hooks.json",
         r'''"command": "bash \"{{CODEX_ROOT_POSIX}}/.codex/hooks/skyguard-entry.sh\" pre-tool",''',
         r'''"command": "\"bash\" \"{{CODEX_ROOT_POSIX}}/.codex/hooks/skyguard-entry.sh\" pre-tool",''',
         "tests/test_codex_definitions.py::test_no_command_starts_with_a_quote", "codex-definition-leading-quote"),
        ("agent/targets/codex/hooks.json",
         "pre-tool || powershell", "pre-tool || bash",
         "tests/test_codex_definitions.py::test_windows_commands_never_call_bash", "codex-definition-bash-on-windows"),
        # a wrapper deadline too close to the definition timeout: Codex gives up first and RUNS the command
        ("agent/guards/adapters/codex/skyguard-entry.sh",
         'LIMIT=40; [ "$ROLE" = "session-start" ] && LIMIT=95', 'LIMIT=45; [ "$ROLE" = "session-start" ] && LIMIT=95',
         "tests/test_codex_definitions.py::test_timeouts_exceed_the_wrappers_own_deadlines", "codex-deadline-too-close"),
        ("agent/guards/adapters/codex/skyguard-entry.ps1",
         "if ($script:Role -ceq 'session-start') { $limit = 95 }", "if ($script:Role -ceq 'session-start') { $limit = 105 }",
         "tests/test_codex_definitions.py::test_timeouts_exceed_the_wrappers_own_deadlines",
         "codex-ps1-session-deadline-too-close"),
        # Fix round 1: the freeze gate cannot pass vacuously
        ("tests/test_codex_definitions.py",
         "    if ledger[0][0] != v1:", "    if False:",
         "tests/test_codex_definitions.py::test_the_freeze_gate_can_fail", "codex-freeze-ledger-rewritable"),
        ("tests/test_codex_definitions.py",
         '    if re.search(r"^## v[0-9]", changelog, re.M) and not tags_seen:', "    if False:",
         "tests/test_codex_definitions.py::test_the_freeze_gate_can_fail", "codex-freeze-tagless-checkout-passes"),
        # Fix round 1: the renderer is frozen with the definitions (the Task 11 contract)
        ("tools/skyguard/codex_definitions.py",
         '    r = r.rstrip("/")\n    return r\n\n\ndef normalise_posix',
         '    return r\n\n\ndef normalise_posix',
         "tests/test_codex_definitions.py::test_the_real_renderer_produces_the_measured_hashes",
         "codex-render-keeps-trailing-slash"),
        ("tools/skyguard/codex_definitions.py",
         "    r = str(root).replace(chr(92), \"/\")", "    r = str(root)",
         "tests/test_codex_definitions.py::test_the_real_renderer_produces_the_measured_hashes",
         "codex-render-keeps-backslashes"),
        ("tools/skyguard/codex_definitions.py",
         "    bad = sorted({c for c in str(root) if c in BAD_ROOT_CHARS})", "    bad = []",
         "tests/test_codex_definitions.py::test_the_renderer_refuses_a_root_the_hooks_cannot_run_from",
         "codex-render-accepts-unrunnable-root"),
        # Fix round 1: a case-variant key is never read as trusted
        ("tools/skyguard/codex_trust.py",
         "        if variants != [key]:", "        if False:",
         "tests/test_codex_trust.py::test_a_key_stored_only_in_another_case_is_unknown_not_trusted",
         "codex-trust-casefold-reads-trusted"),
        ("tools/skyguard/codex_trust.py",
         "        variants = sorted(k for k in state if k.lower() == key.lower())",
         "        variants = [key] if key in state else sorted(k for k in state if k.lower() == key.lower())[:1]",
         "tests/test_codex_trust.py::test_case_variant_duplicates_are_not_collapsed",
         "codex-trust-case-duplicates-collapsed"),
        ("tools/skyguard/codex_trust.py",
         '        report["overall"] = "unrunnable"', "        pass",
         "tests/test_codex_trust.py::test_a_root_the_hooks_cannot_run_from_is_never_reported_as_protected",
         "codex-trust-unrunnable-root-reads-trusted"),
        # the freeze gate's CHANGELOG clauses
        ("tests/test_codex_definitions.py",
         "    if len(ledger) > 1 and unreleased_change and REAPPROVE not in changelog_top:", "    if False:",
         "tests/test_codex_definitions.py::test_the_freeze_gate_can_fail", "codex-freeze-ledger-growth-unannounced"),
        ("tests/test_codex_definitions.py",
         "    if released is not None and _lf_sha(released[1]) != now and REAPPROVE not in changelog_top:",
         "    if False:",
         "tests/test_codex_definitions.py::test_the_freeze_gate_can_fail", "codex-freeze-release-diff-unannounced"),
        # the hash scheme (each field Codex hashes)
        ("tools/skyguard/codex_trust.py",
         '    if windows and handler.get("commandWindows") is not None:', "    if False:",
         "tests/test_codex_trust.py::test_every_measured_vector_reproduces", "codex-trust-ignores-command-windows"),
        ("tools/skyguard/codex_trust.py",
         '         "async": bool(handler.get("async", False))}', "         }",
         "tests/test_codex_trust.py::test_every_measured_vector_reproduces", "codex-trust-drops-async"),
        ("tools/skyguard/codex_trust.py",
         "    if acl is not None and acl != DEFAULT_CONTEXT_LIMIT:", "    if acl is not None:",
         "tests/test_codex_trust.py::test_every_measured_vector_reproduces", "codex-trust-hashes-default-context-limit"),
        # the statuses: never a guess
        ("tools/skyguard/codex_trust.py",
         '            if r["status"] == "modified":', "            if False:",
         "tests/test_codex_trust.py::test_unknown_when_no_stored_hash_matches", "codex-trust-guesses-modified"),
        ("tools/skyguard/codex_trust.py",
         '        if ent.get("enabled") is False:', "        if False:",
         "tests/test_codex_trust.py::test_disabled_when_enabled_is_false", "codex-trust-disabled-reads-trusted"),
        ("tools/skyguard/codex_trust.py",
         '            raise Unreadable("config.toml line {} is not understood".format(n))', "            continue",
         "tests/test_codex_trust.py::test_narrow_reader_refuses_what_it_cannot_parse",
         "codex-trust-narrow-reader-skips-unknown-lines"),
        ("tools/skyguard/codex_trust.py",
         """            if watched and re.match(r\"\"\"^(?:hooks|projects|state|["'])\"\"\", line):""",
         "            if False:",
         "tests/test_codex_trust.py::test_narrow_reader_refuses_a_dotted_root_key",
         "codex-trust-narrow-reader-skips-dotted-keys"),
        # a root the hooks cannot run from (% ! $ `): never "protected"
        ("tools/skyguard/codex_trust.py",
         '    report["root_problem"] = codex_definitions.root_problem(str(project_root))',
         '    report["root_problem"] = None',
         "tests/test_codex_trust.py::test_a_root_the_hooks_cannot_run_from_is_never_reported_as_protected",
         "codex-trust-unrunnable-root-unflagged"),
    ]
] + [
    # Plan 4 Task 9: the Codex .rules file. A prefix broader than its hook deny refuses work the hooks
    # allow; a wrong or missing spelling leaves the command unguarded while the hooks are unapproved; an
    # unclassified verdict is one nobody decided about.
    pytest.param(*m[:4], id=m[4])
    for m in [
        ("tools/skyguard/codex_rules.py", '{"pattern": [prog, "delete", key],', '{"pattern": [prog, "delete"],',
         "tests/test_codex_rules.py::test_every_prefix_rule_is_a_subset_of_its_hook_deny",
         "codex-rules-prefix-widened"),
        ("tools/skyguard/codex_rules.py", '"decision": "forbidden", "code": "shell.DENY_REGISTRY",',
         '"decision": "prompt", "code": "shell.DENY_REGISTRY",',
         "tests/test_codex_rules.py::test_buckets_sum", "codex-rules-decision-drifts-from-bucket"),
        ("tools/skyguard/codex_rules.py", '    "shell.DENY_REGISTRY": (\n        "forbidden",',
         '    "shell.DENY_REGISTRY": (\n        "hook_only",',
         "tests/test_codex_rules.py::test_buckets_sum", "codex-rules-forbidden-bucket-without-rule"),
        ("tools/skyguard/codex_rules.py",
         '    "shell.NO_ROOT_DENY": ("hook_only", _INERT),\n', "",
         "tests/test_codex_rules.py::test_every_shell_verdict_is_classified", "codex-rules-verdict-unclassified"),
        ("tools/skyguard/codex_rules.py",
         '        out.append("    not_match = {},".format(_s(r["not_match"])))\n', "",
         "tests/test_codex_rules.py::test_render_shape", "codex-rules-render-drops-not-match"),
        ("tools/skyguard/codex_rules.py", 'format(_s(r["decision"]))', 'format(_s("prompt"))',
         "tests/test_codex_rules.py::test_rules_file_passes_codex_execpolicy", "codex-rules-render-wrong-decision"),
    ]
] + [
    # Plan 4 Task 12 (spec 7.4): conformance from captured payloads, and the adapter / wrapper clauses that
    # decide whether a Codex command RUNS. Each id names the real defect it stands for.
    pytest.param(*m[:4], id=m[4])
    for m in [
        # A delete patch stops being read: `apply_patch` deleting a plugin parses as an unknown header (or,
        # worse, as nothing) and the plugin rule never sees the path. Killed through a DERIVED variant: the
        # captured Delete shape with only its path replaced by Data/Decoy.esp.
        ("tools/skyguard/patch_paths.py",
         ''', ("*** Delete File:", "delete"))''', ")",
         "tests/test_codex_conformance.py::test_conformance[derived:codex-PreToolUse-apply_patch-3:Data/Decoy.esp]",
         "codex-adapter-drops-delete-paths"),
        # The adapter emits an ALLOW where the rule said deny. The wrapper refuses a non-deny decision (an
        # INERT deny), so the test must assert the RULE's text, not merely "deny".
        ("tools/skyguard/adapters/codex.py",
         '"permissionDecision": "deny",', '"permissionDecision": "allow",',
         "tests/test_codex_entry.py::test_plugin_write_is_denied", "codex-deny-becomes-allow"),
        # The adapter hands Codex the engine's own decision: an `ask` reaches Codex, which RUNS the command.
        ("tools/skyguard/adapters/codex.py",
         '"permissionDecision": "deny",', '"permissionDecision": v.decision,',
         "tests/test_skyguard_codex_adapter.py::test_ask_never_reaches_codex", "codex-adapter-emits-ask"),
        # The adapter's JSON loses its last brace: Codex cannot parse the body and the command RUNS.
        ("tools/skyguard/adapters/codex.py",
         'return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)',
         'return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)[:-1]',
         "tests/test_codex_entry.py::test_plugin_write_is_denied", "codex-adapter-malformed-json"),
        # A patch run THROUGH the shell (a Bash call) is no longer parsed: the plugin it writes goes unseen.
        ("tools/skyguard/adapters/codex.py",
         "sp = patch_paths.shell_patch(cmd)", "sp = None",
         "tests/test_skyguard_codex_adapter.py::test_a_patch_through_the_shell_carries_its_paths",
         "codex-shell-patch-not-parsed"),
        # A relative patch path is judged as written, not against the session's cwd: `../x.esp` escapes.
        ("tools/skyguard/adapters/codex.py",
         "if not mod.isabs(p) and base:", "if False:",
         "tests/test_skyguard_codex_adapter.py::test_a_relative_patch_path_resolves_against_the_session_cwd",
         "codex-relative-path-not-resolved"),
        # A shell verdict nobody classified for the Codex .rules file (the trip-wire Plan 5 relies on).
        ("tools/skyguard/codex_rules.py",
         '    "shell.S-DEL-MO2": ("hook_only", _PATH),\n', "",
         "tests/test_codex_rules.py::test_every_shell_verdict_is_classified", "codex-unclassified-shell-verdict"),
        # The conformance harness itself: an `ask` would pass as a deny if the shape gate stopped checking.
        ("tests/test_codex_conformance.py",
         '        assert body["permissionDecision"] == "deny", (where, "only deny blocks on Codex", body)',
         "        pass",
         "tests/test_codex_conformance.py::test_the_shape_check_can_fail", "codex-conformance-shape-gate-passes-ask"),
        # The plugin rule stops covering .esm: a patch heredoc onto Data/Skyrim.esm (a Bash call carrying a
        # patch) runs. Killed by the shell-patch DERIVED variant of the captured heredoc shape.
        ("tools/skyguard/targets.py",
         r'''PLUGIN_FILE = r"\.(esp|esm|esl|bsa|ba2)[\s.]*$"''',
         r'''PLUGIN_FILE = r"\.(esp|esl|bsa|ba2)[\s.]*$"''',
         "tests/test_codex_conformance.py::test_conformance[derived:0.160.0/codex-PreToolUse-Bash-2:Data/Skyrim.esm]",
         "codex-plugin-rule-misses-esm"),
        # An `ask` loses the text that tells the model the user must run it themselves.
        ("tools/skyguard/adapters/codex.py",
         'ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- Codex cannot ask',
         'ASK_PREFIX = ("Codex cannot ask',
         "tests/test_codex_conformance.py::test_conformance[derived:codex-PreToolUse-Bash-1:resaver-apply]",
         "codex-ask-prefix-dropped"),
        # An Update patch is no longer backed up before it lands (the advise row also demands a backup file).
        ("tools/skyguard/engine.py",
         '_BACKUP_OPS = ("update", "delete", "move_from")', '_BACKUP_OPS = ("delete", "move_from")',
         "tests/test_codex_conformance.py::test_conformance[derived:0.160.0/codex-PreToolUse-apply_patch-2:Data/SKSE/Plugins/x.ini]",
         "codex-update-patch-not-backed-up"),
        # A tool nothing guards by name stops being recorded: the allow becomes invisible.
        ("tools/skyguard/engine.py",
         '    return req.kind in ("mcp", "other") or (req.kind == "shell" and req.tool_name != "Bash")',
         "    return False",
         "tests/test_codex_conformance.py::test_captured_payload[0.160.0/codex-PreToolUse-mcp__probe__echo-1]",
         "codex-mcp-call-not-recorded"),
    ]
] + [
    # The same wrapper clauses, on the PowerShell entry (the Windows entry; its tests run only on Windows).
    pytest.param(
        "agent/guards/adapters/codex/skyguard-entry.ps1", *m[:3],
        marks=pytest.mark.skipif(sys.platform != "win32", reason="the PowerShell entry is tested on Windows"),
        id=m[3])
    for m in [
        # A non-zero engine exit is swallowed into an empty answer: on Codex an empty answer is ALLOW.
        (r'''    if ($r.Code -ne 0) { Send-Failure 'engine' ('engine exited ' + $r.Code); return }''',
         r'''    if ($r.Code -ne 0) { Send-Text ''; return }''',
         "tests/test_codex_entry.py::test_engine_crash_is_a_deny", "codex-wrapper-swallows-engine-errors"),
        # Exit 2 on the way out: Codex reads exit 2 as a hook failure and lets the command RUN (MEASURED).
        ("\r\nexit 0", "\r\nexit 2",
         "tests/test_codex_entry.py::test_every_path_exits_zero", "codex-wrapper-exits-2-on-deny"),
        # No deadline: a hung engine outlives Codex's own timeout, which then RUNS the command.
        (r'''        if (-not $proc.WaitForExit($left)) { $timedOut = $true }''',
         r'''        [void]$proc.WaitForExit()''',
         "tests/test_codex_entry.py::test_a_hung_engine_is_a_deny", "codex-wrapper-has-no-deadline"),
    ]
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
    # Task 9: the mv/cp text note is gone; the registry text fallback (`Remove-ItemProperty.*Bethesda`) still splits.
    ("timing-dotstar-scan", "tools/skyguard/grep.py", "parts = split_dotstar(pattern)", "parts = None",
     "registry-parse-fail-words"),
    # fix round 3: a separator run must start a match only at its first character, in the install-path regex
    # (shared by every delete/move/sed/redirect rule), and the relative-Data rule must not rescan a run.
    ("timing-lead-lookbehind-rm", "tools/skyguard/paths.py", r'LEAD = r"(?<![/\\])[/\\]+"', r'LEAD = r"[/\\]+"',
     "rm-slash-run"),
    # Task 9: the sed note no longer reads the install-path regex; its backslash-run twin is the delete fallback.
    ("timing-lead-lookbehind-sed", "tools/skyguard/paths.py", r'LEAD = r"(?<![/\\])[/\\]+"', r'LEAD = r"[/\\]+"',
     "rm-backslash-run"),
    # (Task 7 removed timing-relative-data-run: RELATIVE_DATA_DELETE is gone; relative deletes resolve from cwd.)
    # Task 9: the registry text test lives on as anchored.py's fallback; still two greps, never one alternation.
    ("timing-registry-split", "tools/skyguard/anchored.py",
     "    return grep(REG_DELETE_TEXT, text) or grep(REG_PROPERTY_TEXT, text)",
     '    return grep("(" + REG_DELETE_TEXT + "|" + REG_PROPERTY_TEXT + ")", text)',
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
    pytest.param("tools/skyguard/engine.py", 'cwd=req.cwd or "", shell=req.shell or ""))',
                 'cwd=req.cwd or ""))',
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
        # Task 8: the SKSE-ini pattern moved to targets.py (one classifier for both guards)
        "tools/skyguard/targets.py" if _suffix == "skse-ini" else "tools/skyguard/write_rules.py", _find, _rep,
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

# ---- Plan 5 Task 8: shell writes (E21). One twin per primitive class and per clause; each names the t08 row it must
# turn red, fired in-process by tests/test_skyguard_writes.py::test_t08_row.
_WR = "tools/skyguard/writes.py"
_TGT = "tools/skyguard/targets.py"


def _t08(row):
    return "tests/test_skyguard_writes.py::test_t08_row[t08-" + row + "]"


_WRITE_CLAUSES = [
    # (id, file, find, replace, t08 row that must go red)
    ("rule-S-WRITE-PLUGIN-redirect", _WR, "    out = _redirect_targets(e)\n", "    out = []\n", "redirect-esp"),
    ("rule-S-WRITE-PLUGIN-tee", _WR, '    if p == "tee":', "    if False:", "tee-esm"),
    ("rule-S-WRITE-PLUGIN-set-content", _WR, '("set-content", "add-content", "clear-content", "new-item")',
     '("add-content", "clear-content", "new-item")', "set-content-pwsh"),
    ("rule-S-WRITE-PLUGIN-out-file", _WR, '("tee-object", "out-file")', '("tee-object",)', "out-file-esl"),
    ("rule-S-WRITE-PLUGIN-iofile", _WR, '("io.file.write", "io.file.append")', '("io.file.append",)', "iofile-esp"),
    ("rule-S-WRITE-PLUGIN-sed-i", _WR, "        out += _sed_files(e.argv)", "        out += []", "sed-i-esp"),
    ("rule-S-WRITE-PLUGIN-dd", _WR, 'w.startswith("of=")]', 'w.startswith("xx=")]', "dd-of-esp"),
    ("rule-S-WRITE-PLUGIN-wired", "tools/skyguard/shell_rules.py", "    if wr.deny:", "    if False:", "redirect-esp"),
    ("rule-S-WRITE-PLUGIN-parse-fail", _WR, "texts = [command] if not parsed.ok else", "texts = [] if not parsed.ok else",
     "parse-fail-esp"),
    ("rule-S-WRITE-PLUGIN-opaque-upstream", _WR, '        if e.stdin_from == "pipe":', "        if False:",
     "opaque-pipe-esp"),
    ("rule-S-WRITE-PLUGIN-opaque-not-whole-command", _WR,
     "texts = [command] if not parsed.ok else _opaque_texts(parsed.execs)",
     'texts = [command] if not parsed.ok or any(e.program == "?" for e in parsed.execs) else []',
     "harness-data-esp"),
    # S-COPY-PLUGIN (advise)
    ("rule-S-COPY-PLUGIN-destination-only", _WR, "            a = targets.resolve_operand(e, cand, cwd, env)",
     "            a = targets.resolve_operand(e, srcs[0] if srcs else cand, cwd, env)", "cp-source-only"),
    ("rule-S-COPY-PLUGIN-zone", _WR, "if (a is not None and game_loads(a, roots))", "if (a is not None)",
     "cp-outside"),
    ("rule-S-COPY-PLUGIN-data-root-only", _WR, 'len(rel) == 2 and rel[0] == "data"', "len(rel) >= 1",
     "cp-to-backups"),
    ("rule-S-COPY-PLUGIN-dir-destination", _WR, "            elif _looks_like_dir(dst):", "            elif False:",
     "cp-into-data-dir"),
    ("rule-S-COPY-PLUGIN-unresolved-data", _WR, "(a is None and _DATA_CHILD.search(cand))", "(a is None and False)",
     "cp-var-data"),
    # the class notes
    ("rule-S-WRITE-SKSE-INI-class", _TGT, ', ("skse-ini", SKSE_INI, "S-WRITE-SKSE-INI"))', ")",
     "out-file-skse-ini"),
    ("rule-S-WRITE-INI-class", _TGT, '("ini", SKYRIM_INI, "S-WRITE-INI"), ', "", "sed-i-skyrim-ini"),
    ("rule-S-WRITE-LOADORDER-write-only", _WR, "        for w in write_targets(e):",
     "        for w in write_targets(e) + list(e.argv):", "loadorder-read"),
    ("rule-S-WRITE-LOADORDER-copy", _WR, '            if kind and kind != "plugin":', "            if False:",
     "cp-onto-plugins-txt"),
    ("rule-S-WRITE-INSTALL-zone", _WR, '    return bool(rel) and rel[0] == "data"', "    return bool(rel)",
     "redirect-install-root"),
    ("rule-S-WRITE-INSTALL-resolved", _WR,
     "            elif not sed_note and a is not None and in_install_area(a, roots):",
     "            elif False:", "redirect-data-file"),
]
MUTATIONS += [pytest.param(f, find, rep, _t08(row), id=i) for i, f, find, rep, row in _WRITE_CLAUSES]


# ---- Plan 5 Task 9: every advice anchored to the executed program. Each names the t09 row it must turn red, fired
# in-process by tests/test_skyguard_t09.py::test_t09_row.
def _t09(row):
    return "tests/test_skyguard_t09.py::test_t09_row[t09-" + row + "]"


_AN = "tools/skyguard/anchored.py"
_WRR = "tools/skyguard/write_rules.py"


_T9_CLAUSES = [
    # item 0 (Task 8 review minors)
    ("rule-S-WRITE-PLUGIN-fallback-first-word", _WR,
     'words += [m.group(1) or m.group(2) or m.group(3) or "" for m in _REDIRECT_TARGET.finditer(seg)]',
     'words += _words(seg[seg.index(">") + 1:])', "fallback-interp-gt"),
    ("rule-S-WRITE-PLUGIN-fallback-arrow", _WR, r'''_REDIRECT_TARGET = re.compile(r"""[0-9&*]?(?<![=\-])>''',
     r'''_REDIRECT_TARGET = re.compile(r"""[0-9&*]?>''', "fallback-arrow"),
    ("rule-S-WRITE-PLUGIN-fallback-quoted", _WR, '''(?:"([^"\\n]*)"|'([^'\\n]*)'|''',
     '''(?:"()"|'()'|''', "fallback-redirect-esp"),
    ("rule-S-WRITE-PLUGIN-copy-null", _WR,
     '            if kind == "plugin" and any(s.lower() in NULL_TARGETS for s in srcs):',
     "            if False:", "cp-devnull-esp"),
    ("rule-S-WRITE-SKSE-INI-copy-resolved", _WR, '            kind = file_class(dst, a_dst or "")',
     "            kind = file_class(dst)", "cp-skse-ini-resolved"),
    # S-REGISTRY (deny): the executed reg / registry cmdlet, a Bethesda key or an ancestor
    ("rule-S-REGISTRY-anchor", _AN, "    if not parsed.ok:\n        return _legacy_registry(command)",
     "    if True:\n        return _legacy_registry(command)", "reg-printf-note"),
    ("rule-S-REGISTRY-parse-fail", _AN, "        return _legacy_registry(command)\n", "        return False\n",
     "reg-parse-fail"),
    ("rule-S-REGISTRY-opaque", _AN, "    if any(_legacy_registry(t) for t in _opaque_texts(parsed.execs)):",
     "    if False:", "reg-opaque-cmd"),
    ("rule-S-REGISTRY-opaque-pipe", _AN, "    if any(_legacy_registry(t) for t in _opaque_texts(parsed.execs)):",
     "    if any(_legacy_registry(t) for t in _opaque_texts([e for e in parsed.execs if e.stdin_from != 'pipe'])):",
     "reg-pipe-sh"),
    ("rule-S-REGISTRY-unrelated-key", _AN, "if len(e.argv) < 2 or bethesda_key(e.argv[1], subst):", "if True:",
     "reg-other-key"),
    ("rule-S-REGISTRY-ancestor", _AN, "    return any(b.startswith(", "    return False and any(b.startswith(",
     "reg-ancestor"),
    ("rule-S-REGISTRY-powershell", _AN, "        elif e.program in PS_REG_PROGRAMS:", "        elif False:",
     "reg-ps-remove-item"),
    ("rule-S-REGISTRY-registry-path", _AN, "if _is_reg_path(w) and bethesda_key(", "if bethesda_key(", "reg-file-bethesda-folder"),
    ("rule-S-REGISTRY-interpreter", _AN, "if code and code_deletes_bethesda(code):",
     "if False and code and code_deletes_bethesda(code):", "reg-python-code"),
    ("rule-S-REGISTRY-prefilter", _AN, '_REG_WORDS = ("reg", "hklm", "hkcu", "hkey_")', '_REG_WORDS = ("reg",)',
     "reg-ps-remove-item"),
    # S-PSC-OUT (deny)
    ("rule-S-PSC-OUT-anchor", _AN, "    if not parsed.ok:\n        m = re.search(", "    if True:\n        m = re.search(",
     "psc-echo"),
    ("rule-S-PSC-OUT-text-programs", _AN, "        if e.program in TEXT_PROGRAMS:\n            continue\n", "",
     "psc-grep-o"),
    ("rule-S-PSC-OUT-eq-form", _AN, 'elif "=" in lw and lw.split("=", 1)[0] in flags:', "elif False:",
     "psc-output-eq"),
    # S-CHAMPOLLION (deny, cwd-resolved)
    ("rule-S-CHAMPOLLION-anchor", _AN, "    if not parsed.ok:\n        return text_hit(command)",
     "    if True:\n        return text_hit(command)", "champ-echo"),
    ("rule-S-CHAMPOLLION-cwd", _AN, "            a = targets.resolve_operand(e, w, cwd, env)",
     '            a = targets.resolve_operand(e, w, "", env)', "champ-relative-outside"),
    ("rule-S-CHAMPOLLION-data-scripts", _AN, 'return rel[2:] if rel[:2] == ["data", "scripts"] else None',
     "return rel", "champ-install-not-scripts"),
    ("rule-S-CHAMPOLLION-wired-cwd", "tools/skyguard/shell_rules.py",
     "champ = anchored.champollion(command, parsed, roots, cwd, env)",
     'champ = anchored.champollion(command, parsed, roots, "", env)', "champ-relative-outside"),
    # (S-MOVE-INSTALL removed in fix round 1; its rows are allow pins.)
    # S-REGISTRY fix round 1: unknown keys, same-command assignments, interpreter-code keys
    ("rule-S-REGISTRY-unknown-key-denies", _AN,
     "        if word is None or _NONLITERAL.search(word):\n            return True",
     "        if word is None or _NONLITERAL.search(word):\n            return False", "reg-var-unknown"),
    # Task 7b: the key is resolved by the parser's Exec.subst (one resolver with the delete rules)
    ("rule-S-REGISTRY-assignment-resolved", _AN, "        word = subst.get(word)", "        word = None",
     "reg-var-assigned-unrelated"),
    ("rule-S-REGISTRY-assignment-bash", "tools/skyguard/shellparse.py",
     "                out.append((_VARNAME.match(lhs).group(), value))", "                pass",
     "reg-var-assigned-unrelated"),
    ("rule-S-REGISTRY-assignment-powershell", "tools/skyguard/shellparse.py",
     "        a = assign.ps_assignment(self.t[cmd.start:cmd.end])", "        a = None", "reg-ps-var-unrelated"),
    ("rule-S-REGISTRY-ps-unknown-hive", _AN, "                        if names_hive:\n                            return True",
     "                        if False:\n                            return True", "reg-ps-unknown-hive"),
    ("rule-S-REGISTRY-ps-unknown-needs-hive", _AN, "                        if names_hive:\n",
     "                        if True:\n", "reg-ps-unknown-nohive"),
    ("rule-S-REGISTRY-code-key", _AN,
     '        if (listed and not quoted) or not key or "{" in key or bethesda_key(key, {}):',
     '        if (listed and not quoted) or not key or "{" in key or "bethesda" in key.lower():',
     "reg-python-ancestor"),
    ("rule-S-REGISTRY-code-literal-unrelated", _AN,
     '        if (listed and not quoted) or not key or "{" in key or bethesda_key(key, {}):',
     "        if True:", "reg-python-unrelated"),
    ("rule-S-REGISTRY-code-list-variable", _AN, '        if (listed and not quoted) or not key or',
     "        if not key or", "reg-python-list-var"),
    ("rule-S-REGISTRY-code-list-form", _AN, '''r"""\\breg(?:\\.exe)?\\b['"]?(?:\\s*,\\s*|\\s+)''',
     '''r"""\\breg(?:\\.exe)?\\b['"]?(?:\\s+)''', "reg-python-list"),
    ("rule-S-REGISTRY-code-joined-key", _AN, "    if _CODE_REG_DELETE_JOINED.search(code):\n        return True",
     "    if False:\n        return True", "reg-python-var"),
    # S-SED-WINPATH (advise): a backslash path in the replacement, on an install/config file; replaces S-WRITE-INSTALL
    ("rule-S-SED-WINPATH-anchor", _WR,
     "winpath = bool(sed_files) and any(sed_win_replacement(s) for s in _sed_scripts(e.argv))",
     "winpath = bool(sed_files)", "sed-doubled"),
    ("rule-S-SED-WINPATH-single-backslash", _WR, '                shape.append("/" if r[i + 1] ==',
     '                shape.append("' + BS + 'x00" + r[i + 1] if r[i + 1] ==', "sed-doubled"),
    ("rule-S-SED-WINPATH-zone", _WR, " and targets.zone(a, roots) in SED_ZONES", "", "sed-winpath-outside"),
    ("rule-S-SED-WINPATH-replaces-install-note", _WR, "            elif not sed_note and a is not None",
     "            elif a is not None", "sed-winpath"),
    ("rule-S-SED-WINPATH-script-option", _WR, '        if w in ("-e", "--expression") and k + 1 < len(argv):',
     "        if False:", "sed-winpath-e"),
    # S-AUTOMOD-* (advise): AutoMod RUNS, module + command among its own arguments
    ("rule-S-AUTOMOD-ESP-anchor", _AN, "    if e.program in AUTOMOD_PROGRAMS:\n        return e.argv",
     "    if True:\n        return e.argv", "automod-echo"),
    ("rule-S-AUTOMOD-ESP-module", _AN, 'if module == "esp" and cmd in ESP_WRITES:', "if cmd in ESP_WRITES:",
     "automod-archive-merge"),
    ("rule-S-AUTOMOD-ESP-dotnet", _AN, '    if e.program == "dotnet":\n        for i, w in enumerate(e.argv):',
     '    if False:\n        for i, w in enumerate(e.argv):', "automod-dotnet"),
    ("rule-S-AUTOMOD-NIF-branch", _AN, 'elif module == "nif" and cmd in NIF_WRITES:', "elif False:",
     "automod-nif-scale"),
    ("rule-S-AUTOMOD-ARCHIVE-branch", _AN, 'elif module == "archive" and cmd in ARCHIVE_WRITES:', "elif False:",
     "automod-archive-merge"),
    # S-EXTERNAL-OUT (advise): a DECOMPILER's own output flag into the install
    ("rule-S-EXTERNAL-OUT-anchor", _AN, '    if e.program == "champollion":\n        return "Champollion"',
     '    if True:\n        return "Champollion"', "external-echo"),
    ("rule-S-EXTERNAL-OUT-decompilers-only", _AN, '    if e.program == "champollion":\n        return "Champollion"',
     '    if e.program in ("champollion", "caprica"):\n        return "Champollion"', "ext-tool-out"),
    ("rule-S-EXTERNAL-OUT-zone", _AN, "            if a is not None and in_install_area(a, roots):\n                return name",
     "            if a is not None:\n                return name", "external-champollion-outside"),
    ("rule-S-EXTERNAL-OUT-automod-decompile", _AN, 'if words[:2] == ["papyrus", "decompile"]:', "if False:",
     "external-automod-decompile"),
    # W-PAPYRUS / W-INSTALL (the write guard, by resolved zone)
    ("rule-W-PAPYRUS-zone", _WRR, "        papyrus = a is not None and papyrus_zone(a, roots)", "        papyrus = True",
     "papyrus-toolkit"),
    ("rule-W-PAPYRUS-mo2", _WRR, '    if z == "mo2":\n        return True\n    rel = _rel(',
     '    if False:\n        return True\n    rel = _rel(', "papyrus-mo2"),
    ("rule-W-INSTALL-zone", _WRR, "        install = a is not None and in_install_area(a, roots)",
     "        install = grep(LEGACY_INSTALL_TEXT, file_path)", "install-relative-outside"),
    ("rule-W-INSTALL-cwd", _WRR, 'a = targets.resolve(file_path, cwd, "bash", os.environ', 'a = targets.resolve(file_path, "", "bash", os.environ',
     "install-relative-inside"),
]
MUTATIONS += [pytest.param(f, find, rep, _t09(row), id=i) for i, f, find, rep, row in _T9_CLAUSES]
MUTATIONS += [
    pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in (
        # the hook must hand the write guard its roots, or the zone notes fall back to the text tests
        ("rule-W-INSTALL-wired", "tools/skyguard/run.py",
         'v = engine.evaluate_write(p["file_path"], audit, roots=roots, cwd=p.get("cwd", ""))',
         'v = engine.evaluate_write(p["file_path"], audit)', "tests/test_guard_corpus.py::test_intended_rows_reproduce"),
        ("rule-W-INI-class", _WRR, '    if grep(targets.SKYRIM_INI, file_path):', "    if False:",
         "tests/test_skyguard_write.py::test_rule4_skyrim_ini"),
        ("rule-W-SKSE-INI-class", _WRR, '    if grep(targets.SKSE_INI, file_path):', "    if False:",
         "tests/test_skyguard_write.py::test_rule5_skse_plugin_ini"),
        ("rule-W-LOADORDER-class", _WRR, '    if grep(targets.LOADORDER, file_path):', "    if False:",
         "tests/test_skyguard_write.py::test_rule6_load_order"),
        ("rule-S-AUTOMOD-ESP-dry-run", _AN, 'any(w.lower() == "--dry-run" for w in args)', "False",
         "tests/test_skyguard_writes.py::test_t08_row[t08-automod-dry]"),
        # timing: without the word-start lookbehind the sed path regex restarts inside every long word (quadratic)
        ("timing-sed-win-word-start", _WR, "(?<![A-Za-z0-9_])[A-Za-z0-9_]{2,}", "[A-Za-z0-9_]{2,}",
         "tests/test_skyguard_timing.py::test_anchored_rules_stay_fast[sed-long-word-bash]"),
    )
]

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

# ---- Plan 5 Task 7b: same-command literal assignments resolve delete operands. One mutation per clause; each names
# the t07b row (fired in-process by tests/test_skyguard_assign.py::test_t07b_row) or the single-purpose test it must
# turn red. The flow clauses are in shellparse (track_vars / lookup), the text clauses in assign.py.
_AS = "tools/skyguard/assign.py"
_TA = "tests/test_skyguard_assign.py::"


def _t07b(row):
    return _TA + "test_t07b_row[t07b-" + row + "]"


_GATE = "        if f is not top or top.ctl != 0 or top.case:"
_REC = '        if cmd is not None and not cmd.skip and cmd.stage == 0 and sep not in ("|", "|&"):'
_LOOK = '        r = self.vars.get(name if self.DIALECT == "bash" else name.lower())'
_SAME = "        if cmd.valid and lst == cmd.lst and seq < cmd.seq:"
_OPEN = '_BASH_OPEN = frozenset(("if", "while", "until", "{"))'
_CLOSE = '_BASH_CLOSE = frozenset(("fi", "done", "}"))'
_COUNT = "    for rx in (_W_PLAIN, _W_DOLLAR, _W_FD):"
_ONCE = "                if self.ctx.writes(name) == 1 and name.lower() not in assign.SPECIAL:"
_T7B_CLAUSES = [
    # (id, file, find, replace, test)
    # where an assignment may be recorded: the ROOT command, its top-level statement list
    ("t7b-root-only", _SP, '        if self.depth == 0 and self.DIALECT in ("bash", "powershell"):',
     '        if self.DIALECT in ("bash", "powershell"):', _t07b("bash-c")),
    ("t7b-top-frame", _SP, _GATE, "        if top.ctl != 0 or top.case:", _t07b("assign-in-subshell")),
    ("t7b-control-depth", _SP, _GATE, "        if f is not top or top.case:", _t07b("assign-in-if")),
    ("t7b-case-arm", _SP, _GATE, "        if f is not top or top.ctl != 0:", _t07b("assign-in-case")),
    ("t7b-open-while", _SP, _OPEN, '_BASH_OPEN = frozenset(("if", "until", "{"))',
     _TA + "test_flow_closers_reset_control_depth[while y; do :; done]"),
    ("t7b-open-brace", _SP, _OPEN, '_BASH_OPEN = frozenset(("if", "while", "until"))',
     _TA + "test_flow_closers_reset_control_depth[{ :; }]"),
    ("t7b-open-for", _SP, "            f.ctl += 1                                          # closed by `done`",
     "            pass", _TA + "test_flow_closers_reset_control_depth[for i in 1; do :; done]"),
    ("t7b-close-fi", _SP, _CLOSE, '_BASH_CLOSE = frozenset(("done", "}"))',
     _TA + "test_flow_closers_reset_control_depth[if y; then :; fi]"),
    ("t7b-close-done", _SP, _CLOSE, '_BASH_CLOSE = frozenset(("fi", "}"))',
     _TA + "test_flow_closers_reset_control_depth[while y; do :; done]"),
    ("t7b-close-brace", _SP, _CLOSE, '_BASH_CLOSE = frozenset(("fi", "done"))',
     _TA + "test_flow_closers_reset_control_depth[{ :; }]"),
    ("t7b-pipeline-separator", _SP, _REC, "        if cmd is not None and not cmd.skip and cmd.stage == 0:",
     _t07b("pipeline-head")),
    ("t7b-pipeline-stage", _SP, _REC,
     '        if cmd is not None and not cmd.skip and sep not in ("|", "|&"):', _t07b("pipeline-stage")),
    ("t7b-written-once", _SP, _ONCE, "                if self.ctx.writes(name) >= 1 and name.lower() not in "
     "assign.SPECIAL:", _t07b("reassigned-in-branch")),
    ("t7b-special-names", _SP, _ONCE, "                if self.ctx.writes(name) == 1:", _t07b("pwd-after-cd")),
    ("t7b-ps-key-folded", _SP, '                    key = name if self.DIALECT == "bash" else name.lower()',
     "                    key = name", _t07b("ps-case-insensitive")),
    ("t7b-first-in-list", _SP, "                    if cmd.seq == 0:", "                    if True:",
     _t07b("assign-after-and")),
    ("t7b-and-moves-position", _SP, '        if sep == "&&":\n            top.seq += 1',
     '        if sep == "&&":\n            pass', _t07b("assign-after-and")),
    ("t7b-or-moves-position", _SP, '        elif sep == "||":\n            top.seq += 1\n',
     '        elif sep == "||":\n', _t07b("assign-after-or")),
    ("t7b-or-breaks-chain", _SP, "            top.chain_ok = False", "            pass", _t07b("or-chain")),
    ("t7b-background", _SP, '            if top.pend and sep != "&":', "            if top.pend:",
     _t07b("background")),
    ("t7b-promotion", _SP, "                    self.vars[k][1] = top.kv", "                    pass",
     _t07b("scratch-var")),
    # which use an assignment reaches
    ("t7b-bash-case-sensitive", _SP, _LOOK, "        r = self.vars.get(name.lower())", _t07b("case-differs")),
    ("t7b-ps-case-insensitive", _SP, _LOOK, "        r = self.vars.get(name)", _t07b("ps-case-insensitive-use")),
    ("t7b-earlier-statement", _SP, "        if kv is not None and kv <= cmd.kv:", "        if kv is not None:",
     _t07b("use-before-assign")),
    ("t7b-chain-valid", _SP, _SAME, "        if lst == cmd.lst and seq < cmd.seq:", _t07b("or-chain")),
    ("t7b-chain-same-list", _SP, _SAME, "        if cmd.valid and seq < cmd.seq:",
     _TA + "test_flow_cross_list_same_position"),
    ("t7b-chain-order", _SP, _SAME, "        if cmd.valid and lst == cmd.lst:",
     _TA + "test_flow_use_before_in_same_chain"),
    # what is an assignment, and what resets every name
    ("t7b-prefix-env", _SP, "                return []                                       # `K=v cmd`: K is set for cmd only",
     "                continue", _t07b("prefix-env")),
    ("t7b-append", _SP, '            if "[" in lhs or lhs.endswith("+="):', "            if False:", _t07b("append")),
    ("t7b-subst-attached", _SP, "        saved, ctx.subst = ctx.subst, (_subst(sc, rec, ws, vals) if sc.vars else ())",
     "        saved, ctx.subst = ctx.subst, ()", _t07b("scratch-var")),
    ("t7b-two-readings", _SP, "        got[val] = s if got.get(val, s) == s else None", "        got[val] = s",
     _TA + "test_two_readings_of_one_word_value_are_not_substituted"),
    ("t7b-emit-carries", _SP, "                 body, self.ctx.subst)", "                 body)", _t07b("scratch-var")),
    ("t7b-find-delete-carries", _SP, 'self.seg, "", self.ctx.subst)', 'self.seg, "", ())', _t07b("find-delete")),
    ("t7b-deletes-wired", _DL, "        e = substituted(e)\n", "", _t07b("scratch-var")),
    # assign.py: every form of a second write counts; values and uses are literal or nothing
    ("t7b-count-plain", _AS, _COUNT, "    for rx in (_W_DOLLAR, _W_FD):",
     _TA + "test_plain_assignment_is_counted"),
    ("t7b-count-dollar", _AS, _COUNT, "    for rx in (_W_PLAIN, _W_FD):",
     _TA + "test_dollar_assignment_is_counted"),
    ("t7b-count-fd", _AS, _COUNT, "    for rx in (_W_PLAIN, _W_DOLLAR):", _t07b("fd-alloc")),
    ("t7b-count-ps-tee", _AS, "tee-object|-variable|", "", _t07b("ps-tee-variable")),
    ("t7b-count-increment", _AS, "    for m in _W_INC.finditer(text):", '    for m in _W_INC.finditer(""):',
     _TA + "test_increment_is_a_write"),
    ("t7b-count-bash-keywords", _AS, "        elif _KW_BASH.search(piece):", "        elif False:",
     _t07b("for-variable")),
    ("t7b-count-ps-keywords", _AS, "        if _KW_PS.search(piece):", "        if False:", _t07b("ps-foreach")),
    ("t7b-count-dollar-is-a-read", _AS, '_BARE_IDENT = re.compile(r"(?<![\\w$])(?<!\\$\\{)(" + _IDENT + r")")',
     '_BARE_IDENT = re.compile(r"(" + _IDENT + r")")', _TA + "test_for_list_dollar_is_a_read"),
    ("t7b-value-tilde", _AS, '        elif c in "$`' + BS * 2 + '~(){}" or c.isspace():',
     '        elif c in "$`' + BS * 2 + '(){}" or c.isspace():', _t07b("tilde-value")),
    ("t7b-unquoted-split", _AS,
     '            if v is None or v == "" or _UNSAFE_UNQUOTED.search(v) or not split_safe:',
     "            if v is None or not split_safe:", _t07b("unquoted-space")),
    ("t7b-ifs-split", _SP, '    split_safe = sc.DIALECT != "bash" or sc.ctx.writes("IFS") == 0',
     "    split_safe = True", _t07b("ifs-split")),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T7B_CLAUSES]

# ---- Task 7b fix round 1: broad poisoning (controller ruling). One mutation per poison class, each naming the
# class's unit case (tests/test_skyguard_assign.py::test_poison_class[<id>]) or the review row it must turn red.
def _pc(case):
    return _TA + "test_poison_class[" + case + "]"


_PCMD1 = '_POISON_COMMANDS = frozenset(("trap", "eval", "source", ".", "read", "readarray", "mapfile", "getopts", "declare",'
_PCMD2 = '                              "typeset", "local", "readonly", "unset", "let"))'
_T7B_R1 = [
    ("t7b-r1-continuation", _AS, '_CONT = re.compile(r"[' + BS * 2 + '`]' + BS + 'r?' + BS + 'n")',
     '_CONT = re.compile(r"(?!)")', _pc("read-continuation")),
    ("t7b-r1-dequote", _AS, '_DEQUOTE = str.maketrans("", "", ', '_DEQUOTE = {} and str.maketrans("", "", ',
     _pc("source-quoted")),
    ("t7b-r1-function", _AS, "_FUNC_DEF = re.compile(", '_FUNC_DEF = re.compile("(?!)") or re.compile(',
     _pc("function-parens")),
    ("t7b-r1-default-assign", _AS, '(_DEFAULT_SET, "default-assign"), ', "", _pc("default-assign")),
    ("t7b-r1-array", _AS, '(_ARRAY_SET, "array"), ', "", _pc("array-element")),
    ("t7b-r1-indirect", _AS, ', (_DOLLAR_NAME_SET, "indirect")', "", _pc("indirect-name")),
    ("t7b-r1-ps-variable-cmdlet", _AS, "        if _PS_VAR_CMDLET.search(t):", "        if False:", _pc("ps-sv")),
    ("t7b-r1-ps-scope", _AS, "        if _PS_WORDS.search(t):", "        if False:", _pc("ps-variable-drive")),
    ("t7b-r1-ps-quoted-call", _AS, '        if _PS_QUOTED_CALL.search(_CONT.sub("", text)):', "        if False:",
     _pc("ps-quoted-call")),
    ("t7b-r1-ps-param-prefix", _AS, "any(v.startswith(p) for v in _PS_VAR_PARAMS)", "False",
     _pc("ps-outvariable-prefix")),
    ("t7b-r1-ps-param-alias", _AS, "p in _PS_VAR_ALIASES or ", "", _pc("ps-ov-alias")),
    ("t7b-r1-arithmetic", _AS, '            if i >= 0 and "=" in piece[i:]:', "            if False:",
     _pc("arithmetic")),
    ("t7b-r1-dot", _AS, '        if w == ".":', "        if False:", _pc("dot")),
    ("t7b-r1-export", _AS, '        if w == "export" and any("$" in r for r in rest):', "        if False:",
     _pc("export-dollar")),
    ("t7b-r1-printf-v", _AS, '        if w == "printf" and any(_PRINTF_V.match(r) for r in rest):', "        if False:",
     _pc("printf-v-attached")),
    ("t7b-r1-wait-p", _AS, '        if w == "wait" and any(_WAIT_P.match(r) for r in rest):', "        if False:",
     _pc("wait-p")),
    ("t7b-r1-taker-options", _AS, '        elif after_taker and t.startswith("-"):', "        elif False:",
     _pc("dot-builtin-options")),
    ("t7b-r1-prefix-then", _AS, '_PREFIX_WORDS = frozenset(("then", ', "_PREFIX_WORDS = frozenset((",
     _pc("dot-after-then")),
    ("t7b-r1-leading-assignment", _AS, "        elif _ASSIGN_WORD.match(t):", "        elif False:",
     _pc("read-after-assignment")),
    ("t7b-r1-cmd-never", _AS, '        return "cmd"', '        return ""', _pc("cmd-never")),
    # wiring: the poison and the stray-closer flag turn the whole command's resolution off
    ("t7b-r1-gate-poison", _SP, "    if sc.unsure or sc.ctx.poisoned():", "    if sc.unsure:",
     _t07b("r1-source-quoted")),
    ("t7b-r1-gate-unsure", _SP, "    if sc.unsure or sc.ctx.poisoned():", "    if sc.ctx.poisoned():",
     _t07b("r1-stray-fi-then-if-body")),
    ("t7b-r1-stray-closer", _SP, "                    self.unsure = True              # a stray fi/done/}",
     "                    pass  # a stray fi/done/}", _t07b("r1-stray-done-then-while")),
    ("t7b-r1-count-normalized", _SP, "            self.counts = assign.write_counts(assign.normalize(self.root))",
     "            self.counts = assign.write_counts(self.root)", _t07b("r1-cont-d-nl")),
    ("t7b-r1-dialect-passed", _SP, "    ctx = _Ctx(command, dialect)", "    ctx = _Ctx(command)",
     _t07b("scratch-var")),
]
for _name in ("trap", "eval", "source", "read", "readarray", "mapfile", "getopts", "declare", "typeset", "local",
              "readonly", "unset", "let"):
    _line = _PCMD1 if ('"' + _name + '"') in _PCMD1 else _PCMD2
    _T7B_R1.append(("t7b-r1-command-" + _name, _AS, _line, _line.replace('"' + _name + '"', '"' + _name + '-x"'),
                    _pc(_name)))
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T7B_R1]


_SNAP = "tools/skyguard/snapshot.py"
_PSR = "tests/test_skyguard_side_effects.py::test_snapshot_skips_powershell_reads"
MUTATIONS += [   # Task 10 (E12): the PowerShell tool is wired; its read-only verbs take no snapshot
    pytest.param(".claude/settings.json", '"matcher": "Bash|PowerShell|Monitor"', '"matcher": "Bash|Monitor"',
                 "tests/test_repo_invariants.py::test_shell_matcher_covers_powershell", id="matcher-includes-powershell"),
    pytest.param(".claude/settings.json", '"matcher": "Edit|Write|NotebookEdit"', '"matcher": "Edit|Write|NotebookEdit|PowerShell"',
                 "tests/test_repo_invariants.py::test_shell_matcher_covers_powershell",
                 id="matcher-file-hooks-stay-off-powershell"),
    # Task 13b: Monitor and NotebookEdit were MEASURED outside every matcher (Claude Code 2.1.289); each matcher entry
    # is pinned in both the live settings and the shipped target file.
    pytest.param(".claude/settings.json", '"matcher": "Bash|PowerShell|Monitor"', '"matcher": "Bash|PowerShell"',
                 "tests/test_repo_invariants.py::test_shell_matcher_covers_powershell", id="matcher-includes-monitor"),
    pytest.param(".claude/settings.json", '"matcher": "Edit|Write|NotebookEdit"', '"matcher": "Edit|Write"',
                 "tests/test_repo_invariants.py::test_shell_matcher_covers_powershell", id="matcher-includes-notebookedit"),
    pytest.param("agent/targets/claude/settings.json", '"matcher": "Bash|PowerShell|Monitor"', '"matcher": "Bash|PowerShell"',
                 "tests/test_guard_coverage.py::test_inventory_matches_the_shipped_matchers", id="target-matcher-includes-monitor"),
    pytest.param("agent/targets/claude/settings.json", '"matcher": "Edit|Write|NotebookEdit"', '"matcher": "Edit|Write"',
                 "tests/test_guard_coverage.py::test_inventory_matches_the_shipped_matchers", id="target-matcher-includes-notebookedit"),
    pytest.param("tools/skyguard/adapters/claude.py", ' and "command" not in ti', "",
                 "tests/test_skyguard_claude_adapter.py::test_monitor_ws_twins_still_deny", id="monitor-ws-needs-no-command-key"),
    pytest.param("tools/skyguard/adapters/claude.py", 'isinstance(ws, dict)', 'ws is not None',
                 "tests/test_skyguard_claude_adapter.py::test_monitor_ws_twins_still_deny", id="monitor-ws-must-be-an-object"),
    pytest.param("tools/skyguard/adapters/claude.py", ' and ws["url"] != ""', "",
                 "tests/test_skyguard_claude_adapter.py::test_monitor_ws_twins_still_deny", id="monitor-ws-url-non-empty"),
    pytest.param("tools/skyguard/adapters/claude.py", 'isinstance(ws.get("url"), str)', 'True',
                 "tests/test_skyguard_claude_adapter.py::test_monitor_ws_twins_still_deny", id="monitor-ws-url-is-a-string"),
    pytest.param("tools/skyguard/adapters/claude.py", 'return (tool_name == "Monitor" and isinstance(ws', 'return (isinstance(ws',
                 "tests/test_skyguard_claude_adapter.py::test_ws_form_only_exempts_monitor", id="monitor-ws-only-for-monitor"),
    pytest.param("tools/skyguard/run.py", 'if p.get("ws_monitor"):', 'if False:',
                 "tests/test_skyguard_claude_adapter.py::test_websocket_monitor_is_allowed", id="run-allows-ws-monitor-branch"),
    pytest.param("tools/skyguard/adapters/claude.py", 'if key in ti:', 'if ti.get(key):',
                 "tests/test_skyguard_claude_adapter.py::test_notebook_edit_without_a_string_path_fails_closed",
                 id="notebook-bad-path-no-fallback"),
    pytest.param("tools/skyguard/adapters/claude.py", 'PATH_KEYS = {"NotebookEdit": "notebook_path"}', 'PATH_KEYS = {}',
                 "tests/test_skyguard_claude_adapter.py::test_notebook_edit_on_a_protected_path_is_denied_and_in_scratch_allowed",
                 id="adapter-reads-notebook-path"),
    pytest.param(_SNAP, " or grep(SKIP_POWERSHELL, command, icase=True)", "", _PSR + "[Get-ChildItem Data]",
                 id="snapshot-skips-pwsh-reads"),
    pytest.param(_SNAP, "get-[a-z]+|select-string|", "select-string|", _PSR + "[Get-Process]",
                 id="snapshot-skips-pwsh-get-verbs"),
    pytest.param(_SNAP, "|select-string|test-path|", "|test-path|", _PSR + "[Select-String -Path a.txt -Pattern x]",
                 id="snapshot-skips-pwsh-select-string"),
    pytest.param(_SNAP, "|test-path|resolve-path|", "|resolve-path|", _PSR + "[Test-Path Data]",
                 id="snapshot-skips-pwsh-test-path"),
    pytest.param(_SNAP, "|resolve-path|measure-object)", "|measure-object)", _PSR + "[Resolve-Path .]",
                 id="snapshot-skips-pwsh-resolve-path"),
    pytest.param(_SNAP, "|resolve-path|measure-object)", "|resolve-path)", _PSR + "[Measure-Object -Line a.txt]",
                 id="snapshot-skips-pwsh-measure-object"),
    pytest.param(_SNAP, "grep(SKIP_POWERSHELL, command, icase=True)", "grep(SKIP_POWERSHELL, command, icase=False)",
                 _PSR + "[GET-CONTENT a]", id="snapshot-pwsh-verbs-are-case-insensitive"),
    pytest.param(_SNAP, "(\\s|$)\"", "(\\s)\"", _PSR + "[Get-Date]", id="snapshot-pwsh-verb-may-end-the-line"),
    pytest.param(_SNAP, "(\\s|$)\"", "\"",
                 "tests/test_skyguard_side_effects.py::test_snapshot_does_not_skip_powershell_writes[Test-PathX]",
                 id="snapshot-pwsh-verb-must-end-at-a-boundary"),
]


# --- Task 11: the DevBench guard (mcp_rules.py) ------------------------------------------------------------------
_MC = "tools/skyguard/mcp_rules.py"
_SHR = "tools/skyguard/shell_rules.py"
_T11 = "tests/test_skyguard_mcp.py::"


def _t11row(row):
    return _T11 + "test_t11_row[" + row + "]"


def _t11curl(case):
    return _T11 + "test_curl_forms_that_send_the_denied_command[" + case + "]"


_T11_CLAUSES = [
    # (id, file, find, replace, test that must go red)
    # S-DEVBENCH-CRASH: one mutation per route, then per clause
    ("rule-S-DEVBENCH-CRASH-mcp", _MC, "    hit = judge(parts[-1], args, roots, None, env) if isinstance(args, dict) else None",
     "    hit = None", _t11row("t11-mcp-smp-reset")),
    ("rule-S-DEVBENCH-CRASH-curl", _MC, "            calls = _web_call(e, command)", "            calls = []",
     _t11row("t11-curl-smp-reset")),
    ("rule-S-DEVBENCH-CRASH-cli", _MC, "            calls = _cli_call(e, command)", "            calls = []",
     _t11row("t11-cli-exec-smp-reset")),
    ("rule-S-DEVBENCH-CRASH-case-fold", _MC, "        words = stmt.lower().split()", "        words = stmt.split()",
     _t11row("t11-mcp-smp-reset-case-space")),
    ("rule-S-DEVBENCH-CRASH-whitespace", _MC, "        words = stmt.lower().split()", "        words = stmt.lower().split(\" \")",
     _t11row("t11-mcp-smp-reset-case-space")),
    ("rule-S-DEVBENCH-CRASH-whole-command", _MC, "            if tuple(words) == denied:",
     "            if tuple(words[:len(denied)]) == denied:", _t11row("t11-mcp-smp-reset-target")),
    ("rule-S-DEVBENCH-CRASH-statements", _MC, '_SPLIT_STATEMENTS = re.compile(r"[;' + BS + "n" + BS + 'r]")',
     '_SPLIT_STATEMENTS = re.compile(r"[' + BS + "n" + BS + 'r]")', _t11row("t11-mcp-smp-reset-chained")),
    ("rule-S-DEVBENCH-CRASH-data-list", _MC, "        for denied in rules.DEVBENCH_DENY:",
     '        for denied in (("smp", "reset"),):',
     _T11 + "test_a_command_added_to_the_data_list_is_denied_with_no_code_change"),
    ("rule-S-DEVBENCH-CRASH-console-tool", _MC, '    if tool == "console":', "    if True:",
     _T11 + "test_curl_forms_that_do_not_send_it[papyrus-tool]"),
    ("rule-S-DEVBENCH-CRASH-string-command", _MC, "        if not isinstance(cmd, str):\n            return None\n        op =",
     "        op =", _T11 + "test_a_non_string_console_command_is_not_judged"),
    ("rule-S-DEVBENCH-CRASH-dict-input", _MC, "if isinstance(args, dict) else None", "if True else None",
     _T11 + "test_a_non_dict_tool_input_is_not_judged"),
    ("rule-S-DEVBENCH-CRASH-devbench-only", _MC, 'or not parts[1].startswith("devbench"):', ":",
     _T11 + "test_only_devbench_servers_are_judged[mcp__other__console]"),
    ("rule-S-DEVBENCH-CRASH-mcp-prefix", _MC, 'or parts[0] != "mcp" or', "or",
     _T11 + "test_only_devbench_servers_are_judged[xmcp__devbench-vr__console]"),
    ("rule-S-DEVBENCH-CRASH-short-name", _MC, "    if len(parts) < 3 or parts[0]", "    if parts[0]",
     _T11 + "test_only_devbench_servers_are_judged[mcp]"),
    ("rule-S-DEVBENCH-CRASH-server-name", _MC, 'not parts[1].startswith("devbench")', 'not parts[1].startswith("devbench-vr")',
     _T11 + "test_any_devbench_server_name_is_judged[devbench]"),
    # the curl route, per clause
    ("rule-S-DEVBENCH-CRASH-curl-json-flag", _MC, '"--data-urlencode", "--json")', '"--data-urlencode")', _t11curl("json-flag")),
    ("rule-S-DEVBENCH-CRASH-curl-data-flags", _MC, '_CURL_BODY_FLAGS = ("--data", "--data-raw", ',
     '_CURL_BODY_FLAGS = ("--data-raw", ', _t11curl("data")),
    ("rule-S-DEVBENCH-CRASH-curl-short-cluster", _MC, '        if w in _CURL_BODY_FLAGS or re.fullmatch(r"-[A-Za-z]*d", w):',
     '        if w in _CURL_BODY_FLAGS or w == "-d":', _t11curl("short-cluster")),
    ("rule-S-DEVBENCH-CRASH-curl-attached-body", _MC, '        if w.startswith("-d") and len(w) > 2 and not w.startswith("--"):',
     "        if False:", _t11curl("attached-body")),
    ("rule-S-DEVBENCH-CRASH-curl-every-body", _MC, "            k += 2\n            continue\n", "            break\n",
     _t11curl("two-bodies")),
    ("rule-S-DEVBENCH-CRASH-curl-endpoint", _MC, '_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)/?(?:[?#].*)?$")',
     '_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)/?$")', _t11curl("query-string")),
    ("rule-S-DEVBENCH-CRASH-curl-endpoint-slash", _MC, '_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)/?(?:[?#].*)?$")',
     '_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)(?:[?#].*)?$")', _t11curl("trailing-slash")),
    ("rule-S-DEVBENCH-CRASH-curl-any-host", _MC, '_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)/?(?:[?#].*)?$")',
     '_ENDPOINT = re.compile(r"127.0.0.1:8921/api/tool/([A-Za-z_]+)/?(?:[?#].*)?$")', _t11curl("other-host-port")),
    ("rule-S-DEVBENCH-CRASH-curl-program", _MC, '_WEB_PROGRAMS = frozenset(("curl", ', '_WEB_PROGRAMS = frozenset(("curlx", ',
     _t11curl("d")),
    ("rule-S-DEVBENCH-CRASH-curl-subst", _MC, "        e = deletes.substituted(e)", "        e = e",
     _T11 + "test_a_url_held_in_a_same_command_variable_is_resolved"),
    ("rule-S-DEVBENCH-CRASH-curl-damaged-json", _MC, '    for key in ("action", "command", "name"):', "    for key in ():",
     _T11 + "test_a_damaged_json_body_is_still_read_for_the_action_and_name"),
    ("rule-S-DEVBENCH-CRASH-curl-bad-escape", _MC, "            except ValueError:\n                out[key]", "            except KeyError:\n                out[key]",
     _T11 + "test_a_damaged_body_with_a_bad_escape_does_not_crash"),
    ("rule-S-DEVBENCH-CRASH-curl-object-body", _MC, "        return d if isinstance(d, dict) else None", "        return d",
     _T11 + "test_a_json_body_that_is_not_an_object_is_not_a_call"),
    ("rule-S-DEVBENCH-CRASH-powershell", _MC, '    if e.program in _PS_PROGRAMS:', "    if False:",
     _T11 + "test_the_powershell_cmdlets_are_read"),
    ("rule-S-DEVBENCH-CRASH-powershell-programs", _MC, '_WEB_PROGRAMS = frozenset(("curl", "invoke-restmethod", "irm", "invoke-webrequest", "iwr"))',
     '_WEB_PROGRAMS = frozenset(("curl",))', _T11 + "test_the_powershell_cmdlets_are_read"),
    # the CLI route, per clause
    ("rule-S-DEVBENCH-CRASH-cli-join", _MC, '" ".join(argv[1:])', "argv[1]", _t11row("t11-cli-exec-split-words")),
    ("rule-S-DEVBENCH-CRASH-cli-tool", _MC, '    if argv[0] == "tool" and len(argv) > 2:', "    if False:",
     _t11row("t11-cli-tool-console")),
    ("rule-S-DEVBENCH-CRASH-cli-exec", _MC, '    if argv[0] == "exec" and len(argv) > 1:', "    if False:",
     _t11row("t11-cli-exec-smp-reset")),
    ("rule-S-DEVBENCH-CRASH-cli-program", _MC, '        elif e.program == "devbench-cli":', '        elif e.program == "devbench":',
     _t11row("t11-cli-exec-smp-reset")),
    # not safe by default (R4), per clause
    ("rule-S-DEVBENCH-CRASH-unparsable", _MC, "    if (not parsed.ok or opaque) and", "    if False and",
     _t11row("t11-curl-unparsable")),
    ("rule-S-DEVBENCH-CRASH-unparsable-only", _MC, "    if (not parsed.ok or opaque) and", "    if (opaque) and",
     _t11row("t11-curl-unparsable")),
    ("rule-S-DEVBENCH-CRASH-opaque", _MC, "    if (not parsed.ok or opaque) and", "    if (not parsed.ok) and",
     _T11 + "test_an_opaque_program_next_to_the_endpoint_and_the_denied_command_denies"),
    ("rule-S-DEVBENCH-CRASH-text-needs-reset", _MC, " and _TEXT_RESET.search(command) and _TEXT_ENDPOINT.search(command):",
     " and _TEXT_ENDPOINT.search(command):",
     _T11 + "test_an_unreadable_command_that_only_names_one_of_them_does_not"),
    ("rule-S-DEVBENCH-CRASH-text-needs-endpoint", _MC, " and _TEXT_RESET.search(command) and _TEXT_ENDPOINT.search(command):",
     " and _TEXT_RESET.search(command):", _T11 + "test_an_unreadable_command_that_only_names_one_of_them_does_not"),
    ("rule-S-DEVBENCH-CRASH-prefilter", _MC, "    if not any(w in low for w in _SHELL_WORDS):\n        return None", "    pass",
     _T11 + "test_a_command_that_names_neither_is_not_scanned"),
    ("rule-S-DEVBENCH-CRASH-wired", _SHR, '    if dev and dev[0] == "S-DEVBENCH-CRASH":', "    if False:",
     _t11row("t11-curl-smp-reset")),
    ("rule-S-DEVBENCH-CRASH-outranks-ask", _MC, '            if hit[0] == "S-DEVBENCH-CRASH":\n                return hit',
     '            if False:\n                return hit', _T11 + "test_a_deny_outranks_an_ask_in_one_command"),
    # S-DEVBENCH-SAVE
    ("rule-S-DEVBENCH-SAVE-name-slash", _MC, "_BAD_NAME = re.compile(r'[/" + BS * 2, "_BAD_NAME = re.compile(r'[" + BS * 2,
     _t11row("t11-mcp-save-path-name")),
    ("rule-S-DEVBENCH-SAVE-name-backslash", _MC, "_BAD_NAME = re.compile(r'[/" + BS * 2 + ":", "_BAD_NAME = re.compile(r'[/:",
     _T11 + "test_an_unusable_save_name_asks[backslash]"),
    ("rule-S-DEVBENCH-SAVE-name-colon", _MC, "_BAD_NAME = re.compile(r'[/" + BS * 2 + ":*?", "_BAD_NAME = re.compile(r'[/" + BS * 2 + "*?",
     _T11 + "test_an_unusable_save_name_asks[colon]"),
    ("rule-S-DEVBENCH-SAVE-name-wildcards", _MC, ":*?" + '"<>|', ':"<>|', _T11 + "test_an_unusable_save_name_asks[star]"),
    ("rule-S-DEVBENCH-SAVE-name-brackets", _MC, '"<>|' + BS + "x00", BS + "x00", _T11 + "test_an_unusable_save_name_asks[angle]"),
    ("rule-S-DEVBENCH-SAVE-name-control", _MC, BS + "x00-" + BS + "x1f]", "]", _T11 + "test_an_unusable_save_name_asks[control]"),
    ("rule-S-DEVBENCH-SAVE-name-dotdot", _MC, '    if not n or n in (".", "..") or', "    if not n or",
     _T11 + "test_an_unusable_save_name_asks[dotdot]"),
    ("rule-S-DEVBENCH-SAVE-name-dot", _MC, '    if not n or n in (".", "..") or', '    if not n or n in ("..",) or',
     _T11 + "test_an_unusable_save_name_asks[dot]"),
    ("rule-S-DEVBENCH-SAVE-name-string", _MC, "    if not isinstance(name, str):\n        return None\n    n = name.strip()",
     "    n = str(name).strip()", _T11 + "test_an_unusable_save_name_asks[number]"),
    ("rule-S-DEVBENCH-SAVE-name-strip", _MC, "    n = name.strip()", "    n = name", _T11 + "test_an_unusable_save_name_asks[blank]"),
    ("rule-S-DEVBENCH-SAVE-ess-suffix", _MC, '    if n.lower().endswith(".ess"):\n        n = n[:-4]', "    if False:\n        n = n[:-4]",
     _t11row("t11-mcp-save-existing-case")),
    ("rule-S-DEVBENCH-SAVE-case-insensitive", _MC, "frozenset(n.casefold() for n in os.listdir(folder))",
     "frozenset(os.listdir(folder))", _t11row("t11-mcp-save-existing-case")),
    ("rule-S-DEVBENCH-SAVE-game-action", _MC, '        if isinstance(action, str) and action.strip().lower() == "save":',
     "        if False:", _t11row("t11-mcp-save-existing")),
    ("rule-S-DEVBENCH-SAVE-action-case", _MC, 'action.strip().lower() == "save"', 'action.strip() == "save"',
     _T11 + "test_the_save_action_is_read_leniently[args0]"),
    ("rule-S-DEVBENCH-SAVE-action-strip", _MC, 'action.strip().lower() == "save"', 'action.lower() == "save"',
     _T11 + "test_the_save_action_is_read_leniently[args1]"),
    ("rule-S-DEVBENCH-SAVE-action-string", _MC, '        if isinstance(action, str) and action.strip()', "        if action.strip()",
     _T11 + "test_other_game_actions_never_ask[7]"),
    ("rule-S-DEVBENCH-SAVE-console", _MC, "        name = _console_save_name(cmd)\n        if name is not None:",
     "        name = None\n        if name is not None:", _t11row("t11-mcp-console-save-existing")),
    ("rule-S-DEVBENCH-SAVE-console-case", _MC, '        if words and words[0].lower() == "save":', '        if words and words[0] == "save":',
     _T11 + "test_the_console_save_word_is_case_insensitive"),
    ("rule-S-DEVBENCH-SAVE-console-whole-word", _MC, '        if words and words[0].lower() == "save":',
     '        if words and words[0].lower().startswith("save"):', _T11 + "test_the_console_save_word_is_case_insensitive"),
    ("rule-S-DEVBENCH-SAVE-wired", _SHR, "    if dev:                                                  # S-DEVBENCH-SAVE",
     "    if False:                                                # S-DEVBENCH-SAVE", _t11row("t11-cli-tool-save-existing")),
    ("rule-S-DEVBENCH-SAVE-roots", "tools/skyguard/run.py", 'v = engine.evaluate_mcp(p["tool_name"], p["mcp_input"], audit, roots=roots)',
     'v = engine.evaluate_mcp(p["tool_name"], p["mcp_input"], audit)', "tests/test_guard_corpus.py::test_intended_rows_reproduce"),
    # S-INERT-NOINPUT
    ("rule-S-INERT-NOINPUT-empty", _MC, '    if name == "" or name == "unknown":', '    if name == "unknown":',
     _T11 + "test_an_empty_payload_is_refused_not_allowed"),
    ("rule-S-INERT-NOINPUT-no-tool-name", _MC, '    if name == "" or name == "unknown":', '    if name == "":',
     "tests/test_hooks.py::test_protect_mcp_refuses_when_it_cannot_see_the_tool_name"),
    # the wiring: the matcher, setup.sh and the gates that count hooks
    ("rule-S-DEVBENCH-CRASH-matcher", ".claude/settings.json", '"matcher": "mcp__devbench.*"', '"matcher": "mcp__other.*"',
     "tests/test_repo_invariants.py::test_the_devbench_mcp_hook_is_registered_under_the_devbench_matcher"),
    ("rule-S-DEVBENCH-CRASH-matcher-narrow", ".claude/settings.json", '"matcher": "mcp__devbench.*"', '"matcher": "mcp__devbench-vr__console"',
     "tests/test_repo_invariants.py::test_the_devbench_mcp_hook_is_registered_under_the_devbench_matcher"),
    ("rule-S-DEVBENCH-CRASH-setup-configures-it", "setup.sh",
     ".claude/hooks/protect-files.sh .claude/hooks/protect-mcp.sh \\\n", ".claude/hooks/protect-files.sh \\\n",
     "tests/test_repo_invariants.py::test_every_hook_is_configured_by_setup_and_carries_one_placeholder"),
    # Plan 4 moved the canary's hook list into health.py (hook-canary.sh delegates to it).
    ("rule-S-DEVBENCH-CRASH-canary-lists-it", "tools/skyguard/health.py",
     'CLAUDE_HOOKS = ("protect-files", "protect-bash", "protect-mcp", "backup-before-edit",',
     'CLAUDE_HOOKS = ("protect-files", "protect-bash", "backup-before-edit",',
     "tests/test_repo_invariants.py::test_every_gate_that_counts_hooks_agrees_with_the_shipped_hooks"),
    ("rule-S-DEVBENCH-CRASH-release-floor", ".github/workflows/release.yml", 'test "$nhooks" -ge 6', 'test "$nhooks" -ge 5',
     "tests/test_repo_invariants.py::test_every_gate_that_counts_hooks_agrees_with_the_shipped_hooks"),
    ("rule-S-DEVBENCH-CRASH-verify-floor", "tools/verify-release.sh", "*[!0-9]*) floor=6 ;;", "*[!0-9]*) floor=5 ;;",
     "tests/test_repo_invariants.py::test_every_gate_that_counts_hooks_agrees_with_the_shipped_hooks"),
    ("wrapper-protect-mcp-names-its-role", ".claude/hooks/protect-mcp.sh", 'HOOK_NAME="protect-mcp"', 'HOOK_NAME="protect-files"',
     "tests/test_hooks.py::test_a_fresh_install_gets_its_heartbeat_directory[protect-mcp.sh]"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T11_CLAUSES]


# --- Task 11 fix round 1: unreadable bodies deny when they name a bare reset; the saves-folder fallback chain -----------
def _t11u(case):
    return _T11 + "test_an_unreadable_console_body_naming_a_bare_reset_denies[" + case + "]"


_UW = '        elif tool.lower() == "console" and _unreadable_names_reset(text):'
_UC = '        if argv[1].lower() == "console" and _unreadable_names_reset(text):'
_RX = '(?:["' + "'" + '`)}'
_T11_R1 = [
    # a body with no definite command
    ("rule-S-DEVBENCH-CRASH-unreadable-body", _MC, _UW, "        elif False:", _t11row("t11-curl-form-body")),
    ("rule-S-DEVBENCH-CRASH-unreadable-console-only", _MC, _UW, "        elif _unreadable_names_reset(text):",
     _T11 + "test_an_unreadable_body_is_only_judged_for_the_console_tool"),
    ("rule-S-DEVBENCH-CRASH-unreadable-tool-case", _MC, _UW, '        elif tool == "console" and _unreadable_names_reset(text):',
     _T11 + "test_the_console_tool_name_is_case_insensitive_on_the_wire"),
    ("rule-S-DEVBENCH-CRASH-unreadable-command-type", _MC, '(tool.lower() != "console" or isinstance(args.get("command"), str))',
     "(True)", _T11 + "test_a_body_whose_command_is_not_a_string_is_unreadable_not_ignored"),
    ("rule-S-DEVBENCH-CRASH-unreadable-cli", _MC, _UC, "        if False:",
     _T11 + "test_the_cli_tool_console_with_an_unreadable_body_naming_a_reset_denies"),
    ("rule-S-DEVBENCH-CRASH-unreadable-cli-console-only", _MC, _UC, "        if _unreadable_names_reset(text):",
     _T11 + "test_an_unreadable_body_is_only_judged_for_the_console_tool"),
    ("rule-S-DEVBENCH-CRASH-unreadable-cli-tool-case", _MC, _UC, '        if argv[1] == "console" and _unreadable_names_reset(text):',
     _T11 + "test_the_cli_tool_console_with_an_unreadable_body_naming_a_reset_denies"),
    ("rule-S-DEVBENCH-CRASH-unreadable-whole-command", _MC, "calls = _web_call(e, command)", "calls = _web_call(e, e.segment)",
     _t11row("t11-ps-body-variable-built-earlier")),
    ("rule-S-DEVBENCH-CRASH-unreadable-needs-reset", _MC, "    return bool(_TEXT_BARE_RESET.search(text))", "    return True",
     _T11 + "test_an_unreadable_console_body_naming_something_else_is_allowed[smp list]"),
    ("rule-S-DEVBENCH-CRASH-unreadable-word-start", _MC, '"""' + BS + "bsmp", '"""smp',
     _T11 + "test_an_unreadable_console_body_naming_something_else_is_allowed[xsmp reset]"),
    ("rule-S-DEVBENCH-CRASH-unreadable-no-argument", _MC, "|<>]|$)" + '"""', "|<>]|$|" + BS + "s)" + '"""',
     _T11 + "test_an_unreadable_console_body_naming_something_else_is_allowed[smp reset 0x14]"),
    ("rule-S-DEVBENCH-CRASH-unreadable-whole-word", _MC, "|<>]|$)" + '"""', "|<>]|$|" + BS + "w)" + '"""',
     _T11 + "test_an_unreadable_console_body_naming_something_else_is_allowed[smp resetx]"),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-quote", _MC, _RX, "(?:['" + '`)}', _t11u("quote")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-squote", _MC, _RX, '(?:["`)}', _t11u("squote")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-backtick", _MC, _RX, '(?:["' + "'" + ")}", _t11u("backtick")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-paren", _MC, _RX, '(?:["' + "'" + "`}", _t11u("paren")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-brace", _MC, _RX, '(?:["' + "'" + "`)", _t11u("brace")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-backslash", _MC, "`)}" + BS * 3 + "]", "`)}" + BS + "]", _t11u("escaped-quote")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-bracket", _MC, BS + "];,&", ";,&", _t11u("bracket")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-semicolon", _MC, ";,&|<>]", ",&|<>]", _t11u("semicolon")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-amp", _MC, ";,&|<>]", ";,|<>]", _t11u("amp")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-pipe", _MC, ";,&|<>]", ";,&<>]", _t11u("pipe")),
    ("rule-S-DEVBENCH-CRASH-unreadable-term-redirect", _MC, ";,&|<>]", ";,&|]", _t11u("redirect")),
    ("rule-S-DEVBENCH-CRASH-unreadable-case", _MC, '|$)""", re.IGNORECASE)', '|$)""")', _t11u("case")),
    # the saves-folder fallback chain
    ("rule-S-DEVBENCH-SAVE-exists", _MC, '    if exists is False:\n        return ("", {})', '    if True:\n        return ("", {})',
     _t11row("t11-mcp-save-existing")),
    ("rule-S-DEVBENCH-SAVE-asks-every-save", _MC, '    if exists is False:\n        return ("", {})', '    if False:\n        return ("", {})',
     _t11row("t11-mcp-save-new")),
    ("rule-S-DEVBENCH-SAVE-unresolvable-asks", _MC,
     '    if exists is None:\n        return ("S-DEVBENCH-SAVE", {"op": f"Cannot tell if save {clean} exists."})',
     '    if exists is None:\n        return ("", {})', _t11row("t11-mcp-save-unresolvable")),
    ("rule-S-DEVBENCH-SAVE-overwrite-text", _MC, 'f"Overwrites save {clean}."', 'f"Cannot tell if save {clean} exists."',
     _t11row("t11-mcp-save-existing")),
    ("rule-S-DEVBENCH-SAVE-unusable-name", _MC,
     '    if clean is None:\n        return ("S-DEVBENCH-SAVE", {"op": "Cannot tell which save this overwrites."})',
     '    if clean is None:\n        return ("", {})', _t11row("t11-mcp-save-no-name")),
    ("rule-S-DEVBENCH-SAVE-case-insensitive-name", _MC, '(name + ".ess").casefold() in', '(name + ".ess") in',
     _t11row("t11-mcp-save-existing")),
    ("rule-S-DEVBENCH-SAVE-saves-folder", _MC, '[os.path.join(d, "Saves") for d', "[d for d", _t11row("t11-mcp-save-existing")),
    ("rule-S-DEVBENCH-SAVE-config-dirs", _MC, '    cands = [os.path.join(d, "Saves") for d in roots.config_dirs]', "    cands = []",
     _t11row("t11-mcp-save-existing")),
    ("rule-S-DEVBENCH-SAVE-first-wins", _MC, "    return next((c for c in cands if os.path.isdir(c)), None)",
     "    return next((c for c in reversed(cands) if os.path.isdir(c)), None)", _T11 + "test_the_first_existing_saves_folder_wins"),
    ("rule-S-DEVBENCH-SAVE-skips-missing", _MC, "    return next((c for c in cands if os.path.isdir(c)), None)",
     "    return next(iter(cands), None)", _T11 + "test_the_first_existing_saves_folder_wins"),
    ("rule-S-DEVBENCH-SAVE-config-outranks-documents", _MC, "    for docs in _documents(env):\n        cands += [",
     "    for docs in _documents(env):\n        cands = [", _T11 + "test_a_declared_config_dir_outranks_documents"),
    ("rule-S-DEVBENCH-SAVE-documents-fallback", _MC, "    for docs in _documents(env):", "    for docs in []:",
     _t11row("t11-mcp-save-docs-existing")),
    ("rule-S-DEVBENCH-SAVE-game-folders", _MC, "for g in paths.GAME_FOLDERS]", 'for g in ("Skyrim VR",)]',
     _T11 + "test_every_game_folder_name_is_searched[Skyrim Special Edition]"),
    ("rule-S-DEVBENCH-SAVE-game-folder-base", _MC, "for g in paths.GAME_FOLDERS]", 'for g in ("Skyrim VR", "Skyrim Special Edition")]',
     _T11 + "test_every_game_folder_name_is_searched[Skyrim]"),
    ("rule-S-DEVBENCH-SAVE-vr-first", _MC, "for g in paths.GAME_FOLDERS]", "for g in reversed(paths.GAME_FOLDERS)]",
     _T11 + "test_vr_outranks_the_other_game_folders"),
    ("rule-S-DEVBENCH-SAVE-userprofile", _MC, '("USERPROFILE", "HOME", ', '("HOME", ',
     _T11 + "test_documents_is_the_fallback_when_no_config_dir_has_saves"),
    ("rule-S-DEVBENCH-SAVE-home", _MC, '"USERPROFILE", "HOME", "OneDrive"', '"USERPROFILE", "OneDrive"',
     _T11 + "test_a_redirected_documents_is_found[HOME]"),
    ("rule-S-DEVBENCH-SAVE-onedrive", _MC, '"HOME", "OneDrive", "OneDriveConsumer"', '"HOME", "OneDriveConsumer"',
     _T11 + "test_a_redirected_documents_is_found[OneDrive]"),
    ("rule-S-DEVBENCH-SAVE-onedrive-consumer", _MC, '"OneDriveConsumer", "OneDriveCommercial")', '"OneDriveCommercial")',
     _T11 + "test_a_redirected_documents_is_found[OneDriveConsumer]"),
    ("rule-S-DEVBENCH-SAVE-onedrive-commercial", _MC, '"OneDriveConsumer", "OneDriveCommercial")', '"OneDriveConsumer")',
     _T11 + "test_a_redirected_documents_is_found[OneDriveCommercial]"),
    ("rule-S-DEVBENCH-SAVE-empty-documents", _MC, "        if base:  ", "        if True:  ",
     _T11 + "test_an_empty_documents_variable_is_not_read_relative_to_the_cwd"),
    ("rule-S-DEVBENCH-SAVE-no-folder", _MC, "    if folder is None:\n        return None\n    if folder not in memo:",
     "    if folder is None:\n        return False\n    if folder not in memo:", _t11row("t11-mcp-save-unresolvable")),
    ("rule-S-DEVBENCH-SAVE-listing-error", _MC, "        except OSError:\n            memo[folder] = None",
     "        except KeyError:\n            memo[folder] = None", _T11 + "test_an_unreadable_saves_folder_is_unknown_not_a_crash"),
    ("rule-S-DEVBENCH-SAVE-listing-unknown", _MC, "    if memo[folder] is None:\n        return None",
     "    if memo[folder] is None:\n        return False", _T11 + "test_an_unreadable_saves_folder_is_unknown_not_a_crash"),
    ("rule-S-DEVBENCH-SAVE-listing-memo", _MC, "    if folder not in memo:\n        try:", "    if True:\n        try:",
     _T11 + "test_a_scan_lists_each_saves_folder_once"),
    ("rule-S-DEVBENCH-SAVE-env-wired", _MC, "hit = judge(parts[-1], args, roots, None, env) if", "hit = judge(parts[-1], args, roots) if",
     _t11row("t11-mcp-save-docs-existing")),
    ("rule-S-DEVBENCH-SAVE-env-wired-shell", _MC, "hit = judge(tool, args, roots, memo, env)", "hit = judge(tool, args, roots, memo)",
     _t11row("t11-curl-save-docs-existing")),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T11_R1]


# --- Task 12: a blank or non-string command fails closed. One twin per clause; each names the test that must go red.
_T12 = "tests/test_skyguard_t12.py::"
MUTATIONS += [
    pytest.param("tools/skyguard/run.py", 'elif p["command_type"] != "string" or not p["command"]:',
                 'elif not p["command"]:',
                 _T12 + "test_run_denies_a_non_string_command", id="rule-S-INERT-NOCMD-nonstring"),
    pytest.param("tools/skyguard/shell_rules.py", '    if command.strip() == "":', '    if command == "":',
                 _T12 + "test_decide_denies_a_whitespace_only_command", id="rule-S-INERT-NOCMD-whitespace"),
]


# --- Task 13: the strict message contract and the registry rules it emptied of legacy text. One twin per clause.
_RM = "tests/test_rule_messages.py::"
_USE = "'Use xelib, Spriggit or AutoMod to change them instead.'"
_USE3 = "'Use xelib, Spriggit or AutoMod to change them instead.\\nThird line.'"
MUTATIONS += [
    pytest.param("tools/skyguard/shell_rules.py", '    if roots.game == "":', '    if roots.game == None:',
                 "tests/test_skyguard_shell.py::test_rule0b_no_root", id="rule-S-INERT-NOROOT-empty-root"),
    pytest.param("tools/skyguard/write_rules.py", '    if file_path == "":', '    if file_path == None:',
                 "tests/test_skyguard_write.py::test_rule0_empty_path", id="rule-W-INERT-NOPATH-empty-path"),
    pytest.param("tools/skyguard/write_rules.py", '    if grep(targets.PLUGIN_FILE, file_path):', '    if False:',
                 "tests/test_skyguard_write.py::test_rule1_plugin_write_denied", id="rule-W-PLUGIN-plugin-file"),
    # messages-no-echo: the command is appended to a live message; only the corpus-wide check can see that.
    pytest.param("tools/skyguard/shell_rules.py",
                 'reason = rules.message("S-RESAVER-APPLY", op=op, save=save or "the save")',
                 'reason = rules.message("S-RESAVER-APPLY", op=op, save=save or "the save") + " " + command',
                 _RM + "test_no_message_echoes_its_command", id="messages-no-echo"),
    # ... and the static twin: a template that carries a command placeholder.
    pytest.param("tools/skyguard/rules.py", _USE, "'Use xelib, Spriggit or AutoMod to change them instead of {c}.'",
                 _RM + "test_no_template_echoes_the_command", id="messages-no-echo-template"),
    # messages-two-lines: a third line, seen by the corpus-wide check and by the per-template check.
    pytest.param("tools/skyguard/rules.py", _USE, _USE3, _RM + "test_no_message_echoes_its_command",
                 id="messages-two-lines"),
    pytest.param("tools/skyguard/rules.py", _USE, _USE3, _RM + "test_message_shape", id="messages-two-lines-shape"),
    # the R13 exemption must stay narrow: only a payload-* row ...
    pytest.param("tests/test_rule_messages.py",
                 'return row_id.startswith("payload-") and bool(WRAPPER_INERT.match(text))',
                 'return bool(WRAPPER_INERT.match(text))',
                 _RM + "test_the_wrapper_exemption_does_not_widen", id="messages-exemption-any-row"),
    # ... only that exact text (the end anchor) ...
    pytest.param("tests/test_rule_messages.py", 'indistinguishable from approving\\.$")',
                 'indistinguishable from approving\\.")',
                 _RM + "test_the_wrapper_exemption_does_not_widen", id="messages-exemption-any-suffix"),
    # ... and it may skip the shape check, never the echo check.
    pytest.param("tests/test_rule_messages.py", "    run = echoed_run(text, payload)\n    if run:",
                 "    run = None if _wrapper_exempt(row_id, text) else echoed_run(text, payload)\n    if run:",
                 _RM + "test_the_wrapper_exemption_does_not_widen", id="messages-exemption-skips-echo"),
    # a legacy message coming back
    pytest.param("tools/skyguard/rules.py",
                 '"legacy": False,\n        "prefixes": [],\n        "template": (\n        \'Blocked: plugin',
                 '"legacy": True,\n        "prefixes": [],\n        "template": (\n        \'Blocked: plugin',
                 _RM + "test_no_legacy_messages", id="messages-legacy-returns"),

    pytest.param("tests/conftest.py",
                 'if os.environ.get("TOOLKIT_REQUIRE_HARNESS") == "1" and not harness_active():',
                 'if False:',
                 "tests/test_harness_required.py::test_requiring_the_harness_without_it_is_an_error",
                 id="require-harness-without-harness-is-silent"),

    pytest.param("setup.sh",
                 '    echo "  Setup does not install it. When you need xeditlib, install it yourself:"\n',
                 '    winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements 2>/dev/null || true\n',
                 "tests/test_setup_paths.py::test_setup_without_node_prints_instructions_and_never_runs_a_package_manager",
                 id="setup-no-node-autoinstall"),

    pytest.param("tools/esp-verify-wrapper.sh",
                 'if [ "$allow_empty" -eq 0 ] && { [ ! -s "$snap" ] || [ ! -s "$after" ]; }; then',
                 'if false; then',
                 "tests/test_esp_verify.py::test_two_empty_inventories_are_not_a_pass",
                 id="esp-verify-empty-inventory-fails"),

    pytest.param("tests/run_harness_tests.py",
                 '"PATHEXT", "TOOLKIT_REQUIRE_HARNESS")',
                 '"PATHEXT")',
                 "tests/test_harness_required.py::test_the_launcher_forwards_the_require_flag_to_pytest",
                 id="launcher-drops-require-harness-flag"),

    pytest.param("tools/esp-verify-wrapper.sh",
                 "    comm -13 \"$before\" \"$after\" | awk -F'\\t'",
                 "    : | awk -F'\\t'",
                 "tests/test_esp_verify.py::test_allow_empty_still_lists_the_references_that_appeared",
                 id="esp-verify-allow-empty-hides-additions"),

    pytest.param("scripts/check_could_not_check.py",
                 'if skipped and not any("short test summary info" in line for line in lines):',
                 'if False:',
                 "tests/test_could_not_check.py::test_counted_skips_without_a_short_summary_section_cannot_pass",
                 id="could-not-check-passes-without-rs"),

    pytest.param("tools/esp-verify-wrapper.sh",
                 '  if [ -z "$(find "$tmp" -name \'*.yaml\' -print -quit)" ]; then',
                 '  if false; then',
                 "tests/test_esp_verify.py::test_snapshot_dies_when_the_serializer_yields_no_yaml",
                 id="esp-verify-no-yaml-dies"),
]


# Plan 4 Task 13: the canary's Codex trust report and per-surface coverage. One entry per clause that decides
# whether an unguarded session can read as fine.
_COV = "tests/test_skyguard_coverage.py::"
_EXIT_ANCHOR = 'if counts["DEGRADED"] or counts["NEVER INVOKED"] or cov_counts["DEGRADED"] or trust_bad:'
MUTATIONS_TASK13 = [
    # Approved-but-never-run read as quiet NOT EXERCISED (the AllSigned / WSL fail-open shape).
    ("tools/skyguard/health.py", "                        and not any(_has_records(d, x) for x in hooks)):",
     "                        and False):", _COV + "test_approved_but_never_invoked_is_reported_and_exits_1",
     "canary-never-invoked-is-reported"),
    # ... and the trust gate on it: an UNAPPROVED install must read NOT RUNNING, not never-invoked.
    ("tools/skyguard/health.py", '                if (agent == "codex" and state == "NOT EXERCISED" and trust["overall"] == "trusted"\n',
     '                if (agent == "codex" and state == "NOT EXERCISED"\n',
     _COV + "test_an_untrusted_install_is_never_reported_never_invoked", "canary-never-invoked-needs-trust"),
    ("tools/skyguard/health.py", _EXIT_ANCHOR,
     'if counts["DEGRADED"] or cov_counts["DEGRADED"] or trust_bad:',
     _COV + "test_approved_but_never_invoked_is_reported_and_exits_1", "canary-never-invoked-fails-the-exit-code"),
    ("tools/skyguard/health.py", _EXIT_ANCHOR,
     'if counts["DEGRADED"] or counts["NEVER INVOKED"] or trust_bad:',
     _COV + "test_degraded_coverage_folds_into_exit_1", "canary-degraded-coverage-fails-the-exit-code"),
    ("tools/skyguard/health.py", _EXIT_ANCHOR,
     'if counts["DEGRADED"] or counts["NEVER INVOKED"] or cov_counts["DEGRADED"]:',
     _COV + "test_an_untrusted_install_says_codex_hooks_are_not_running_and_exits_1",
     "canary-untrusted-hook-fails-the-exit-code"),
    ("tools/skyguard/health.py",
     '                                 or (trust["overall"] == "unrunnable" and not trust.get("unrendered")))',
     '                                 )',
     _COV + "test_a_root_the_hooks_cannot_start_from_fails_the_canary_even_when_approved",
     "canary-unrunnable-root-fails-the-exit-code"),
    ("tools/skyguard/health.py", 'trust["overall"] == "unrunnable" and not trust.get("unrendered")',
     'trust["overall"] == "unrunnable"',
     _COV + "test_an_unrendered_install_is_not_configured_never_untrusted",
     "canary-unrendered-install-is-not-a-failure"),
    ("tools/skyguard/health.py", '    trust_unknown = bool(trust) and trust["overall"] == "unknown"',
     "    trust_unknown = False", _COV + "test_an_unreadable_codex_config_is_unknown_and_exits_2",
     "canary-unknown-trust-exits-2"),
    # Coverage rules.
    ("tools/skyguard/health.py", "        short = hooked < called\n", "        short = False\n",
     _COV + "test_coverage_is_degraded_when_one_call_has_no_line", "coverage-degraded-when-a-call-had-no-hook"),
    ("tools/skyguard/health.py", "            hooked = sum(min(hooked_by.get(t, 0), counts.get(t, 0)) for t in tools)",
     "            hooked = sum(hooked_by.get(t, 0) for t in tools)",
     _COV + "test_extra_guard_lines_for_one_tool_cannot_cover_another_tools_call", "coverage-claude-is-judged-per-tool"),
    ("tools/skyguard/health.py",
     'COUNTING_HOOKS = {"claude": ("protect-bash", "protect-files", "protect-mcp"), "codex": ("pre-tool",)}',
     'COUNTING_HOOKS = {"claude": ("protect-bash", "protect-files", "protect-mcp", "backup-before-edit"), '
     '"codex": ("pre-tool", "session-start")}',
     _COV + "test_only_the_pre_tool_guard_counts_as_seeing_a_codex_call", "coverage-counts-only-the-guard-hooks"),
    ("tools/skyguard/health.py", "            if not transcript:\n", "            if False:\n",
     _COV + "test_coverage_is_unmeasurable_without_a_start_line", "coverage-needs-a-recorded-transcript"),
    ("tools/skyguard/health.py", 'rows.append((sid, "?", 0, 0, "UNMEASURABLE", str(e)))',
     'rows.append((sid, "?", 0, 0, "NOT EXERCISED", str(e)))',
     _COV + "test_coverage_is_unmeasurable_on_an_unknown_transcript_shape", "coverage-unmeasurable-is-not-quiet"),
    # Session records.
    ("tools/skyguard/health.py", "        if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):",
     "        if not isinstance(session_id, str):",
     _COV + "test_a_session_id_that_is_not_a_plain_name_writes_nothing", "session-id-cannot-escape-the-sessions-dir"),
    ("tools/skyguard/health.py", "        for p in files[keep:]:", "        for p in files[keep + 100:]:",
     _COV + "test_only_the_twenty_newest_session_files_are_kept", "session-files-are-pruned"),
    ("tools/skyguard/run.py", "    _session_line(a, req.session_id, tool=req.tool_name)\n", "",
     _COV + "test_the_engine_records_a_codex_session_and_its_calls", "engine-records-codex-calls"),
    ("tools/skyguard/run.py",
     "        _session_line(a, req.session_id, source=req.source, transcript=req.transcript_path)\n", "",
     _COV + "test_the_engine_records_a_codex_session_and_its_calls", "engine-records-codex-session-start"),
    ("tools/skyguard/run.py", "        elif a.role in health.COUNTING_HOOKS[a.agent]:\n", "        else:\n",
     _COV + "test_the_engine_records_a_claude_guard_call_but_not_a_backup_call", "engine-records-only-guard-calls"),
    # Transcript parsers.
    ("tools/skyguard/transcripts.py",
     '        else:\n            raise Unmeasurable("unrecognised item type {!r}".format(t[:40]))',
     "        else:\n            continue",
     _COV + "test_a_renamed_item_type_is_unmeasurable_not_zero_calls", "transcript-unknown-item-is-unmeasurable"),
    ("tools/skyguard/transcripts.py", "    orig = pl.get(CODEX_SURFACE_FIELD)\n",
     '    orig = "codex-tui" if pl.get("source") == "vscode" else pl.get(CODEX_SURFACE_FIELD)\n',
     _COV + "test_the_surface_is_never_inferred_from_the_source_field", "transcript-surface-never-from-source"),
    ("tools/skyguard/transcripts.py", 'CODEX_SURFACES.get(orig, "unclaimed:" + orig)',
     'CODEX_SURFACES.get(orig, "exec")',
     _COV + "test_an_unprobed_surface_is_reported_unclaimed", "transcript-unprobed-surface-is-unclaimed"),
    ("tools/skyguard/transcripts.py", "                if isinstance(bid, str) and bid in seen:",
     "                if False:",
     _COV + "test_a_streamed_claude_tool_use_is_counted_once", "transcript-claude-dedupes-streamed-blocks"),
    # Fix round 1: false-green paths.
    ("tools/skyguard/transcripts.py", "    if len(rows) > CODEX_MIN_LINES_FOR_ITEMS and recognised == 0:",
     "    if False:", _COV + "test_a_rollout_with_content_but_no_recognised_item_is_unmeasurable",
     "transcript-no-recognised-item-is-unmeasurable"),
    ("tools/skyguard/transcripts.py", "            recognised += 1\n            continue\n",
     "            continue\n", _COV + "test_no_calls_is_not_exercised",
     "transcript-non-tool-items-count-as-recognised"),
    ("tools/skyguard/health.py", '    if codex_on and not any(r["agent"] == "codex" and r["hook"] == "pre-tool" and r["state"] == "HEALTHY"',
     '    if False and not any(r["agent"] == "codex" and r["hook"] == "pre-tool" and r["state"] == "HEALTHY"',
     _COV + "test_session_start_alone_does_not_prove_the_codex_guard_runs", "canary-ok-needs-codex-pre-tool-healthy"),
    ("tools/skyguard/health.py", '    stale = ["{} {}".format(r["agent"], r["hook"]) for r in rows if r["state"] == "STALE"]',
     "    stale = []", _COV + "test_stale_hooks_do_not_read_as_ok", "canary-stale-is-not-ok"),
    ("tools/skyguard/health.py", 'elif counts["UNMEASURABLE"] or cov_counts["UNMEASURABLE"] or unreadable or trust_unknown:',
     'elif counts["UNMEASURABLE"] or unreadable or trust_unknown:',
     _COV + "test_unmeasurable_coverage_exits_2_and_the_result_names_it", "canary-unmeasurable-coverage-exits-2"),
    ("tools/skyguard/health.py", '    if trust is not None and trust.get("unrendered"):\n        out.append(',
     '    if False:\n        out.append(',
     _COV + "test_an_unrendered_codex_keeps_exit_0_but_the_result_says_not_configured",
     "canary-unrendered-codex-is-named-in-the-result"),
    # codex_trust: an unrendered template is not configured.
    ("tools/skyguard/codex_trust.py", '    if report["unrendered"]:\n', "    if False:\n", _COV + "test_an_unrendered_install_is_not_configured_never_untrusted",
     "trust-unrendered-install-is-unrunnable"),
]


MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in MUTATIONS_TASK13]


# Plan 4 final fix pass.
_ADP = "tests/test_skyguard_codex_adapter.py::"
MUTATIONS_FINAL = [
    ("tools/skyguard/health.py", "            hooked = sum(max(0, n - refused.get(tool, 0)) for tool, n in hooked_by.items())",
     "            hooked = sum(hooked_by.values())",
     _COV + "test_coverage_excludes_refused_calls_from_hooked", "coverage-refused-calls-are-not-hooked"),
    ("tools/skyguard/run.py", '    if v.decision != "allow":', "    if False:",
     _COV + "test_the_engine_records_a_refusal_in_the_session_line_and_an_allow_does_not",
     "engine-records-codex-refusals"),
    ("tools/skyguard/adapters/codex.py", "            if isinstance(cmd, list):", "            if False:",
     _ADP + "test_an_argv_list_command_is_guarded_as_a_shell_request", "codex-bash-argv-list-unjoined"),
    ("tools/skyguard/adapters/codex.py", '    if not name.startswith("mcp__") and isinstance(cmd, list):',
     "    if False:",
     _ADP + "test_an_argv_list_command_gets_the_shell_rules", "codex-unknown-tool-argv-list-allowed"),
    ("tools/skyguard/adapters/codex.py",
     "    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):",
     "    if not isinstance(cmd, list):",
     _ADP + "test_an_argv_list_with_a_non_string_element_is_refused", "codex-argv-list-non-string-fails-open"),
    ("tools/skyguard/adapters/codex.py", """    return "'" + arg.replace("'", "''") + "'\"""",
     """    return "'" + arg + "'\"""",
     _ADP + "test_an_argv_list_is_quoted_so_one_element_stays_one_argument", "codex-argv-quote-not-escaped"),
    ("setup.sh", "        tr 'A-Z' 'a-z' < \"$CODEX_DEFS\" | grep -qF",
     "        cat \"$CODEX_DEFS\" | grep -qF",
     "tests/test_setup_paths.py::test_a_rerun_from_a_differently_cased_folder_is_not_a_moved_install",
     "setup-moved-install-check-is-case-sensitive"),
    ("setup.sh", '    echo "Start Codex in the game folder itself, never a subfolder: from a subfolder no hook runs, and Codex does not tell you."',
     "    :", "tests/test_setup_paths.py::test_setup_tells_the_user_to_start_codex_in_the_game_folder",
     "setup-omits-the-codex-subfolder-warning"),
]
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in MUTATIONS_FINAL]
# The PowerShell entry's last line. A non-zero exit after a printed verdict makes the hook command's `||`
# rerun the other shell, which prints a SECOND JSON: Codex cannot parse it and RUNS the command.
MUTATIONS.append(pytest.param(
    "agent/guards/adapters/codex/skyguard-entry.ps1", "\r\nexit 0", "\r\nexit 1",
    "tests/test_codex_entry.py::test_every_path_exits_zero[pwsh-pre-tool-hi]",
    marks=pytest.mark.skipif(sys.platform != "win32", reason="the PowerShell entry is tested on Windows"),
    id="codex-ps1-final-exit-not-zero"))


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


# Merge of Plans 4+5 (2026-10-04). One entry per guard the merge itself added; each names the test that must go red.
_MERGE = "tests/"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # codes: Plan 5 renders most verdicts from the registry; drop that source and they all code "unknown".
    ("tools/skyguard/codes.py", "    for rid, rule in sorted(rules.RULES.items()):", "    for rid, rule in []:",
     _MERGE + "test_skyguard_codes.py::test_every_registry_rule_codes_to_exactly_one_code[S-DEL-MO2]",
     "merge-codes-read-the-registry"),
    ("tools/skyguard/codes.py", '    return ".NO_" in code or "INERT" in code', '    return ".NO_" in code',
     _MERGE + "test_skyguard_codes.py::test_every_registry_rule_codes_to_exactly_one_code[S-INERT-CONFIG]",
     "merge-codes-registry-inert-is-failed"),
    ("tools/skyguard/codes.py", '_ONE_ITEM = r"(?:(?! \\| ).)*"', '_ONE_ITEM = ".*"',
     _MERGE + "test_skyguard_codes.py::test_a_later_advice_item_cannot_code_the_first",
     "merge-codes-advice-field-cannot-span-items"),
    # codex .rules: a prefix that refuses a registry delete the hook allows (the R5 subset, after Plan 5 narrowed it).
    # (A cut-down key like "HKCU" is no defect: it is an ANCESTOR of a Bethesda key, which the hook also denies.)
    ("tools/skyguard/codex_rules.py", '    "HKCU\\\\Software\\\\Bethesda Softworks",\n',
     '    "HKCU\\\\Software\\\\Other Softworks",\n',
     _MERGE + "test_codex_rules.py::test_every_prefix_rule_is_a_subset_of_its_hook_deny",
     "merge-codex-rules-prefix-broader-than-hook"),
    # Codex adapter: a scalar non-string command is refused before any rule (Plan 5 Task 12 on Codex).
    ("tools/skyguard/adapters/codex.py",
     '    if not name.startswith("mcp__") and "command" in ti and not isinstance(cmd, (str, list)):',
     "    if False:",
     _MERGE + "test_skyguard_codex_adapter.py::test_a_scalar_non_string_command_is_refused_before_the_rules[exec_command-int]",
     "merge-codex-scalar-command-allowed"),
    # engine: Plan 5's cwd and dialect must reach the rules through the Plan 4 engine.
    ("tools/skyguard/engine.py", 'cwd=req.cwd or "", shell=req.shell or ""))', 'shell=req.shell or ""))',
     _MERGE + "test_guard_corpus.py::test_intended_rows_reproduce", "merge-engine-drops-cwd"),
    ("tools/skyguard/engine.py", "    return codes.classify(write_rules.decide(path, audit, roots=roots, cwd=cwd))",
     "    return codes.classify(write_rules.decide(path, audit))",
     _MERGE + "test_guard_corpus.py::test_intended_rows_reproduce", "merge-engine-write-drops-roots"),
    # Claude adapter: the Request carries the tool's dialect, never a guessed "bash".
    ("tools/skyguard/adapters/claude.py", '                   shell=(p.get("shell") or None) if kind == "shell" else None,',
     '                   shell="bash" if kind == "shell" else None,',
     _MERGE + "test_skyguard_claude_adapter.py::test_run_judges_monitor_in_the_unknown_dialect",
     "merge-claude-request-guesses-bash"),
    # health: the canary counts Plan 5's widened matchers.
    ("tools/skyguard/health.py", 'CLAUDE_COVERED_TOOLS = ("Bash", "PowerShell", "Monitor", "Edit", "Write", "NotebookEdit")',
     'CLAUDE_COVERED_TOOLS = ("Bash", "Edit", "Write")',
     _MERGE + "test_skyguard_coverage.py::test_claude_coverage_counts_every_tool_the_installed_matchers_cover[PowerShell-protect-bash]",
     "merge-coverage-ignores-powershell"),
    ("tools/skyguard/health.py", "    return tool in CLAUDE_COVERED_TOOLS or (isinstance(tool, str) and tool.startswith(CLAUDE_COVERED_PREFIX))",
     "    return tool in CLAUDE_COVERED_TOOLS",
     _MERGE + "test_skyguard_coverage.py::test_claude_coverage_counts_every_tool_the_installed_matchers_cover[mcp__devbench-vr__console-protect-mcp]",
     "merge-coverage-ignores-devbench-mcp"),
    ("tools/skyguard/health.py", "            tools = [t for t in counts if _claude_covered(t)]",
     "            tools = [t for t in CLAUDE_COVERED_TOOLS]",
     _MERGE + "test_skyguard_coverage.py::test_claude_coverage_counts_every_tool_the_installed_matchers_cover[mcp__devbench-vr__console-protect-mcp]",
     "merge-coverage-per-tool-list-is-static"),
]]


# Plan 6 Task 7 (+ fix round 1): the lift state reader, writer and `honoured`. One mutation per clause; each names the
# test that must go red. (relpath, find, replace, test, id).
# Reader: l2-state-sum / -size / -size-if / -reparse (folder) / -reparse-file / -not-regular / -toctou-id / -toctou-reparse /
#   -version / -version-bool / -shape / -disable-shape / -num-bool / -never-raises / -safe-fresh.
# honoured: -session / -start / -future / -expiry / -clamp / -unknown-start / -honoured-finite; session_start: -last-start.
# write: -atomic / -write-sum / -write-validates (shape, version, size).
# (No twin for a non-finite `now`: a NaN or inf `now` honours nothing through the ordinary expiry comparison anyway, so the
# `not _num(now)` clause is behaviourally redundant; the test still pins it.)
_ST = "tools/skyguard/state.py"
_STT = "tests/test_skyguard_state.py::"
_V = '    if not _version_ok(data):\n'
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_ST, '    if not isinstance(got, str) or not hmac.compare_digest(got.encode("ascii", "replace"), want.encode("ascii")):',
     "    if False:", _STT + "test_s1_hand_edit_with_a_wrong_checksum_reads_as_guards_on", "l2-state-sum"),
    (_ST, "MAX_BYTES = 64 * 1024  ", "MAX_BYTES = 64 * 1024 * 1024  ",
     _STT + "test_s5_unreadable_shapes_read_as_guards_on_without_raising[oversized]", "l2-state-size"),
    (_ST, "    if len(raw) > MAX_BYTES:", "    if False:",
     _STT + "test_a_file_of_exactly_the_cap_is_read_and_one_byte_more_is_not", "l2-state-size-if"),
    (_ST, "    if _is_reparse(os.lstat(str(path.parent))):", "    if False:",
     _STT + "test_a_valid_state_reached_through_a_junction_is_still_rejected", "l2-state-reparse"),
    (_ST, "    if _is_reparse(st):", "    if False:",
     _STT + "test_a_state_file_that_is_itself_a_reparse_point_is_rejected", "l2-state-reparse-file"),
    (_ST, "    if not _stat.S_ISREG(st.st_mode):", "    if False:",
     _STT + "test_a_non_regular_state_path_is_rejected_even_when_it_opens", "l2-state-not-regular"),
    (_ST, "(fst.st_ino, fst.st_dev) != (st.st_ino, st.st_dev) or ", "",
     _STT + "test_a_file_swapped_between_lstat_and_open_is_rejected", "l2-state-toctou-id"),
    (_ST, " or _is_reparse(fst):", ":",
     _STT + "test_a_handle_that_turns_out_to_be_a_reparse_point_is_rejected", "l2-state-toctou-reparse"),
    (_ST, _V, "    if False:\n",
     _STT + "test_s5_unreadable_shapes_read_as_guards_on_without_raising[unknown_version]", "l2-state-version"),
    (_ST, '    return type(data.get("v")) is int and data["v"] == VERSION', '    return data.get("v") == VERSION',
     _STT + "test_a_bool_is_not_the_version_one", "l2-state-version-bool"),
    (_ST, "    if not isinstance(lifts, list) or not all(_valid_lift(x) for x in lifts) or not isinstance(persistent, dict):",
     "    if not isinstance(lifts, list) or not isinstance(persistent, dict):",
     _STT + "test_a_malformed_lift_rejects_the_whole_state[bad0]", "l2-state-shape"),
    (_ST, "    if dis is not None and not _valid_disable(dis):", "    if False:",
     _STT + "test_a_correctly_summed_state_with_a_bad_lock_disabled_is_rejected[by-session-int]", "l2-state-disable-shape"),
    (_ST, "    return type(v) in (int, float) and math.isfinite(v)", "    return isinstance(v, (int, float)) and math.isfinite(v)",
     _STT + "test_a_bool_is_not_a_time[created]", "l2-state-num-bool"),
    (_ST, "    except Exception as e:  # noqa: BLE001", "    except OSError as e:  # noqa: BLE001",
     _STT + "test_s5_hostile_nesting_under_the_size_cap_is_rejected_not_raised[nested-list]", "l2-state-never-raises"),
    (_ST, "State.SAFE = _Fresh()", "State.SAFE = State()",
     _STT + "test_state_safe_is_fresh_on_every_access", "l2-state-safe-fresh"),
    (_ST, '        if lift["session_id"] != session_id:', "        if False:",
     _STT + "test_s3_another_session_id_is_ignored", "l2-state-session"),
    (_ST, '        if lift["created"] < session_start:', "        if False:",
     _STT + "test_s8_a_lift_dated_before_the_session_start_is_ignored", "l2-state-start"),
    (_ST, '        if lift["created"] > now + CLOCK_SKEW_SECONDS:', "        if False:",
     _STT + "test_a_lift_dated_in_the_future_is_ignored", "l2-state-future"),
    (_ST, '        if not now < min(lift["expires_at"], lift["created"] + MAX_LIFT_SECONDS):',
     '        if not now < lift["created"] + MAX_LIFT_SECONDS:',
     _STT + "test_s4_past_expires_at_is_ignored", "l2-state-expiry"),
    (_ST, '        if not now < min(lift["expires_at"], lift["created"] + MAX_LIFT_SECONDS):',
     '        if not now < lift["expires_at"]:',
     _STT + "test_s7_expires_at_beyond_8h_is_clamped", "l2-state-clamp"),
    (_ST, "    if not _num(now) or not _num(session_start) or not isinstance(session_id, str):",
     "    if not _num(now) or not isinstance(session_id, str):",
     _STT + "test_an_unknown_session_start_honours_nothing", "l2-state-unknown-start"),
    (_ST, "    if not _num(now) or not _num(session_start) or not isinstance(session_id, str):",
     "    if not _num(now) or session_start is None or not isinstance(session_id, str):",
     _STT + "test_a_non_finite_now_or_start_honours_nothing[1800000000.0-nan]", "l2-state-honoured-finite"),
    (_ST, "        if best is None or t > best:", "        if best is None:",
     _STT + "test_the_session_start_is_the_last_start_line", "l2-state-last-start"),
    (_ST, "        os.replace(tmp, str(path))", '        open(str(path), "wb").write(payload)',
     _STT + "test_a_failed_write_leaves_the_old_state_and_no_temp_file", "l2-state-atomic"),
    (_ST, '    body["sum"] = canonical_sum(body)', '    body["sum"] = data.get("sum", "0")',
     _STT + "test_write_computes_the_sum_itself_and_ignores_a_caller_supplied_one", "l2-state-write-sum"),
    (_ST, "    problem = _shape_problem(body)\n    if problem:\n        raise ValueError", "    problem = None\n    if problem:\n        raise ValueError",
     _STT + "test_write_refuses_what_read_would_reject", "l2-state-write-validates-shape"),
    (_ST, '    if not _version_ok(body):\n        raise ValueError("refusing to write: unknown version")', "    pass",
     _STT + "test_write_refuses_what_read_would_reject", "l2-state-write-validates-version"),
    (_ST, "    if len(payload) > MAX_BYTES:\n        raise ValueError", "    if False:\n        raise ValueError",
     _STT + "test_write_refuses_what_read_would_reject", "l2-state-write-validates-size"),
]]


# Plan 6 Task 9: engine shadow mode (R8). One mutation per clause of engine.shadow plus the two wirings (Claude exit, Codex).
_EN = "tools/skyguard/engine.py"
_SH = "tests/test_skyguard_shadow.py::"
_INERT_IF = '        if v.status == "failed" or codes._inert(v.code) or v.code == "unknown":'
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_EN, _INERT_IF, '        if codes._inert(v.code) or v.code == "unknown":',
     _SH + "test_a_failed_status_stays_a_deny_even_with_an_honoured_lift", "l2-shadow-inert-still-denies"),
    (_EN, _INERT_IF, '        if v.status == "failed" or v.code == "unknown":',
     _SH + "test_an_inert_rule_code_stays_a_deny_even_when_the_status_reads_evaluated", "l2-shadow-inert-code-still-denies"),
    (_EN, _INERT_IF, '        if v.status == "failed" or codes._inert(v.code):',
     _SH + "test_an_unclassifiable_refusal_is_not_converted", "l2-shadow-unknown-converted"),
    (_EN, '        audit.log_strict("skyguard", "SHADOW", "{} ({})".format(v.code, v.decision))', "        pass",
     _SH + "test_every_converted_verdict_writes_a_shadow_audit_line_naming_the_rule", "l2-shadow-logs"),
    (_EN, "    v = side_effects(req, project, audit, v)\n    return shadow(", "    v = v\n    return shadow(",
     _SH + "test_the_backup_of_a_shadowed_delete_is_still_taken", "l2-shadow-side-effects-run"),
    (_EN, "_state.honoured(st, session_id, now, start)", "st.lifts",
     _SH + "test_another_sessions_lift_is_not_honoured", "l2-shadow-session-bound"),
    (_EN, 'if x["kind"] == "guards"]', "if True]",
     _SH + "test_a_lock_lift_does_not_switch_the_guards_off", "l2-shadow-kind"),
    (_EN, '            audit.log("skyguard", "STATE-REJECTED", st.reason)\n', "            pass\n",
     _SH + "test_an_unverifiable_state_reads_as_guards_on_with_a_log_line", "l2-shadow-rejected-logs"),
    (_EN, "    except Exception:  # noqa: BLE001 -- shadow mode", "    except OSError:  # noqa: BLE001 -- shadow mode",
     _SH + "test_a_failure_inside_shadow_mode_keeps_the_original_deny", "l2-shadow-fails-closed"),
    ("tools/skyguard/run.py", "        v = engine.shadow(v, a.project, p[\"session_id\"], a.telemetry, audit)",
     "        pass", _SH + "test_every_denied_or_asked_corpus_row_is_shadowed_except_the_inert_ones",
     "l2-shadow-claude-exit"),
    ("tools/skyguard/run.py", "audit, telemetry=a.telemetry))", "audit))",
     _SH + "test_the_codex_hook_entry_point_honours_the_lift", "l2-shadow-codex-wired"),
]]


# Plan 6 Task 9 round 2: SHADOW on every call; guards off writes where the engine reads and only when honourable.
_CL = "tools/skyguard/cli.py"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_EN, '            audit.log_strict("skyguard", "SHADOW", "allow")', "            pass",
     _SH + "test_an_allowed_call_is_returned_untouched_but_logged_while_a_lift_is_honoured", "l2-shadow-every-call"),
    (_EN, '            audit.log_strict("skyguard", "SHADOW", "{} kept ({})".format(v.code, v.decision))', "            pass",
     _SH + "test_a_kept_inert_refusal_is_still_logged_while_a_lift_is_honoured", "l2-shadow-kept-logs"),
    (_CL, "        if not seen:", "        if False:",
     _SH + "test_guards_off_without_a_visible_session_start_writes_nothing_and_says_so", "l2-guards-start-visible"),
    (_CL, "    if not os.path.isdir(root):", "    if False:",
     _SH + "test_guards_off_refuses_a_project_that_is_not_a_folder_with_two_lines", "l2-guards-project-dir"),
    (_CL, 'os.environ.get("CLAUDE_PROJECT_DIR") or ', "",
     _SH + "test_the_project_defaults_to_the_agents_project_dir_and_root_stays_a_synonym", "l2-guards-project-default"),
    (_CL, '(agent,) if agent in ("claude", "codex") else ("claude", "codex")', '("claude", "codex")',
     _SH + "test_a_start_line_of_another_agents_heartbeat_dir_counts_when_the_agent_is_unnamed_but_not_a_wrong_named_one",
     "l2-guards-agent-dir"),
]]


# Plan 6 Task 10: session start (expiry, drift, banners). One mutation per clause; each names the test that must go red.
_SS = "tools/skyguard/session.py"
_SST = "tests/test_skyguard_session_start.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SS, '        if source == "compact":\n            return [disabled] if disabled else []', '        if False:\n            return []',
     _SST + "test_compact_does_not_clear_or_report_lifts", "l2-sess-compact-shortcut"),
    (_SS, "        if not st.rejected and st.lifts:", "        if False:",
     _SST + "test_a_guards_lift_is_reported_and_cleared_at_start", "l2-sess-clear"),
    (_SS, '        failure = _relock(root) if engaged else ""', '        failure = ""',
     _SST + "test_relock_happens_before_the_report_and_names_the_lifted_files", "l2-sess-relock"),
    (_SS, "        lifted, drift = _scan(root, st.lifts) if engaged else ([], [])", "        lifted, drift = [], []",
     _SST + "test_relock_happens_before_the_report_and_names_the_lifted_files", "l2-sess-scan"),
    (_SS, "        elif p.name in manifest:", "        elif False:",
     _SST + "test_drift_is_a_file_unlocked_with_no_lift_record", "l2-sess-drift"),
    (_SS, "            banners.append(disabled)", "            pass",
     _SST + "test_a_terminal_disable_is_honoured_and_shown_every_session", "l2-sess-disabled-banner"),
    (_SS, "        engaged = _engaged(root) and not disabled", "        engaged = _engaged(root)",
     _SST + "test_a_terminal_disable_is_honoured_and_shown_every_session", "l2-sess-disabled-honoured"),
    (_SS, '.is_file()\n', ' or True\n',
     _SST + "test_the_lock_is_not_applied_when_it_was_never_engaged", "l2-sess-engaged"),
    (_SS, "        if st.rejected:\n            banners.append", "        if False:\n            banners.append",
     _SST + "test_a_rejected_state_is_reported_and_the_lock_is_enforced", "l2-sess-rejected"),
    (_SS, '    t = "".join(c if c in _SAFE else "?" for c in str(s))', "    t = str(s)",
     _SST + "test_a_hostile_reason_cannot_add_lines_or_overrun", "l2-sess-sanitize"),
    (_SS, "        if os.path.isabs(rel) or not _inside(root, target):", "        if os.path.isabs(rel):",
     _SST + "test_a_manifest_path_cannot_reach_outside_the_project", "l2-sess-guardfile-path"),
    (_SS, "not health._SESSION_ID.match(session_id)", "False",
     _SST + "test_an_unsafe_session_id_is_never_written_to_a_shell_file[$(id)]", "l2-sess-env-id"),
    (_SS, '        return _with_prefs("\\n".join(t for t in (banners, missing) if t), project, audit), "failed"',
     '        return _with_prefs("\\n".join(t for t in (missing, banners) if t), project, audit), "failed"',
     _SST + "test_banners_come_before_the_kb_guard_text_and_never_on_compact", "l2-sess-order"),
    ("tools/skyguard/run.py", '        session.export_env(p["session_id"], "claude")', "        pass",
     _SST + "test_the_claude_session_start_exports_the_id", "l2-sess-env-wired"),
]]


# Plan 6 Task 9 review fix round.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_EN, '        audit.log_strict("skyguard", "SHADOW", "{} ({})".format(v.code, v.decision))',
     '        audit.log("skyguard", "SHADOW", "{} ({})".format(v.code, v.decision))',
     _SH + "test_a_refusal_is_not_converted_when_its_shadow_line_cannot_be_written", "l2-shadow-line-must-land"),
    (_ST, "        if best is None or t > best:", "        if True:",
     _SH + "test_a_forged_early_start_line_cannot_revive_a_pre_resume_lift", "l2-state-start-max"),
    ("tools/skyguard/run.py", "    if a.role in DECISION_ROLES:", "    if True:",
     _SH + "test_a_tool_call_logs_one_shadow_line_not_one_per_hook", "l2-shadow-decision-roles-only"),
    (_EN, "        if _state_reject_is_new(project, st.reason):", "        if True:",
     _SH + "test_a_state_rejection_is_logged_once_per_reason_not_once_per_call", "l2-shadow-reject-once"),
    (_CL, "        if st0.rejected:", "        if False:",
     _SH + "test_guards_on_over_an_unverifiable_state_says_so_in_two_lines", "l2-guards-on-says-rejected"),
]]


# Plan 6 Task 10 fix round 1: reserved user-tty id, untrusted-string banners, guard-file budget, revoke notice, env dedupe,
# relock-before-banner ordering.
_RELOCK_LINE = '        failure = _relock(root) if engaged else ""\n'
_STEP3 = ('        # step 3: the banners, in the order the brief lists them\n'
          '        guards = [x for x in st.lifts if x["kind"] == "guards"]\n'
          '        if guards:\n'
          '            banners.append(\'Guards were left OFF by an earlier session (recorded reason, untrusted: "%s"); '
          'they are back ON.\'\n'
          '                           % _clean(guards[0]["reason"]))\n'
          '        if lifted:\n'
          '            banners.append("%d vanilla files were left unlocked; relocked." % len(lifted))\n')
_TO_DRIFT = ('        if drift:\n'
             '            banners.append(_clip("%d vanilla files were unlocked outside skyguard (%s); relocked." '
             '% (len(drift), _names(drift))))\n')
_SKL = "tools/skyguard/lock.py"
_RSV = "test_the_reserved_id_passed_as_an_argument_is_refused_and_changes_nothing"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SKL, "    if reserved_session(explicit):", "    if False:",
     _SST + _RSV + "[cmd0]", "l2-sess-reserved-arg"),
    (_SKL, "    return isinstance(s, str) and s.strip().lower() == USER_SESSION", "    return isinstance(s, str) and s == USER_SESSION + 'x'",
     _SST + "test_the_reserved_id_is_refused_by_guards_too", "l2-sess-reserved-match"),
    (_SKL, "    session = explicit or (USER_SESSION if _interactive() else None)", "    session = explicit or USER_SESSION",
     _SST + "test_a_piped_shell_with_no_session_never_gets_the_reserved_id", "l2-sess-mint-console-only"),
    ("tools/skyguard/cli.py", "    if lock.reserved_session(session):", "    if False:",
     _SST + "test_the_reserved_id_is_refused_by_guards_too", "l2-sess-reserved-guards"),
    (_SS, 'recorded reason, untrusted: ', 'reason: ', _SST + "test_a_recorded_reason_is_framed_restricted_and_cannot_carry_an_instruction",
     "l2-sess-frame"),
    (_SS, "def _clean(s, n=80):", "def _clean(s, n=8000):",
     _SST + "test_a_long_reason_is_capped", "l2-sess-clean-cap"),
    (_SS, "    if size > FILE_CAP or ", "    if ",
     _SST + "test_a_file_over_the_per_file_cap_is_too_large_never_differs", "l2-sess-file-cap"),
    (_SS, "or budget[\"bytes\"] + size > TOTAL_CAP", "",
     _SST + "test_the_total_byte_budget_stops_the_hashing", "l2-sess-total-cap"),
    (_SS, "            if time.monotonic() > budget[\"deadline\"]:\n                return None\n            chunk",
     "            chunk",
     _SST + "test_the_wall_time_budget_stops_the_hashing", "l2-sess-time-cap"),
    (_SS, "            missing += 1\n            continue\n        key = rel.replace(", "            differ.append('x')\n            continue\n        key = rel.replace(",
     _SST + "test_a_manifest_naming_only_a_missing_file_names_nothing", "l2-sess-missing-count"),
    (_SS, "        mine = [x for x in st.lifts if x[\"kind\"] == \"lock\" and x[\"session_id\"] == lock.USER_SESSION]",
     "        mine = []", _SST + "test_the_start_says_it_revoked_your_own_terminal_unlock", "l2-sess-revoke-notice"),
    (_SS, "        mine = [x for x in st.lifts if x[\"kind\"] == \"lock\" and x[\"session_id\"] == lock.USER_SESSION]",
     "        mine = [x for x in st.lifts if x[\"kind\"] == \"lock\"]", _SST + "test_an_agent_lift_gets_no_revoke_notice",
     "l2-sess-revoke-user-only"),
    (_SS, "        todo = [x for x in lines if x not in have]", "        todo = lines",
     _SST + "test_the_env_file_gets_the_lines_once_across_starts", "l2-sess-env-dedupe"),
    (_SS, "            or lock.reserved_session(session_id) or agent", "            or agent",
     _SST + "test_an_agent_session_cannot_be_exported_as_the_reserved_id", "l2-sess-env-reserved"),
    # Review Focus 4: move the relock to AFTER the drift banner is written. The test reads file attributes inside the
    # banner's own call, so a relock that happens later leaves the file open at that moment and it goes red.
    (_SS, _RELOCK_LINE + _STEP3 + _TO_DRIFT, _STEP3 + _TO_DRIFT + _RELOCK_LINE,
     _SST + "test_the_files_are_already_relocked_when_the_banner_is_written", "l2-sess-relock-after-banner"),
]]


# Plan 6 Task 11 (D6, R7): the lift rules, one twin per clause. Each names the ONE row or test that must go red.
_LR = "tools/skyguard/lift_rules.py"
_LT = "tests/test_skyguard_lift_rules.py::"
_SR6 = "tools/skyguard/shell_rules.py"
_NS = 'NEVER_SHADOW = frozenset(("shell.S-LIFT-PERMANENT", "shell.S-LIFT-RAW", "shell.S-LIFT-UNLOCKCMD"))'
_UNL = '("/remove", "/reset", "/grant", "/restore", "/setowner")'
_ISRO = '(?:' + BS + '$false|' + BS + '$null|0|' + BS + '$0)(?![' + BS + 'w])'
_TXT_EXECS = ('    for x in parsed.execs:\n        if x.program == "?":\n            continue\n        for w in x.argv:\n'
              '            if w.startswith("-"):\n                continue\n            t = _hit(x, w, cwd, env, roots, False)\n'
              '            if t:\n                return t\n    for w in _text_words(text):')


def _p6row(row):
    return _LT + "test_p6t11_row[p6t11-" + row + "]"


MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # ---- S-LIFT-RAW
    (_LR, '        if e.program == "attrib":', "        if False:", _p6row("raw-attrib-bsa"), "rule-S-LIFT-RAW-attrib"),
    (_LR, '    if "-r" not in words:\n        return ""\n    rec = "/s"', '    rec = "/s"', _p6row("allow-attrib-plus"),
     "rule-S-LIFT-RAW-attrib-minus-r-only"),
    (_LR, '    rec = "/s" in words', "    rec = False", _p6row("raw-attrib-tree"), "rule-S-LIFT-RAW-attrib-recursive"),
    (_LR, '        elif e.program == "icacls":', "        elif False:", _p6row("raw-icacls-remove"), "rule-S-LIFT-RAW-icacls"),
    (_LR, _UNL, '("/reset", "/grant", "/restore", "/setowner")', _p6row("raw-icacls-remove"),
     "rule-S-LIFT-RAW-icacls-remove"),
    (_LR, _UNL, '("/remove", "/grant", "/restore", "/setowner")', _p6row("raw-icacls-exe-reset"),
     "rule-S-LIFT-RAW-icacls-reset"),
    (_LR, _UNL, '("/remove", "/reset", "/restore", "/setowner")', _p6row("raw-icacls-grant-rel"),
     "rule-S-LIFT-RAW-icacls-grant"),
    (_LR, "    if not e.argv or not any(w.startswith(_ICACLS_UNLOCK) for w in words):", "    if not e.argv:",
     _p6row("allow-icacls-read"), "rule-S-LIFT-RAW-icacls-read-allowed"),
    (_LR, '    rec = "/t" in words or "/restore" in words', "    rec = False", _p6row("raw-icacls-data-tree"),
     "rule-S-LIFT-RAW-icacls-recursive"),
    (_LR, '    rec = "/t" in words or "/restore" in words', "    rec = True", _p6row("allow-icacls-data-noT"),
     "rule-S-LIFT-RAW-icacls-folder-only-allowed"),
    (_LR, '        elif e.program == "set-itemproperty":', "        elif False:", _p6row("raw-sip-readonly"),
     "rule-S-LIFT-RAW-sip"),
    (_LR, 'name[0] not in ("isreadonly", "attributes")', 'name[0] not in ("isreadonly",)', _p6row("raw-sp-attributes"),
     "rule-S-LIFT-RAW-sip-attributes"),
    (_LR, '    if not name or name[0] not in ("isreadonly", "attributes"):\n        return False\n    value', "    value",
     _p6row("allow-sip-time"), "rule-S-LIFT-RAW-sip-name-only"),
    (_LR, 'return not (name[0] == "isreadonly" and value and value[0] in _TRUE)', "return True",
     _p6row("allow-sip-readonly-true"), "rule-S-LIFT-RAW-sip-true-allowed"),
    (_LR, '        elif e.program == "set-acl":', "        elif False:", _p6row("raw-set-acl"), "rule-S-LIFT-RAW-set-acl"),
    (_LR, "    return [(e, w) for w in own] if own else _peers(parsed, e)", "    return [(e, w) for w in own]",
     _p6row("raw-set-acl-pipe"), "rule-S-LIFT-RAW-pipeline-input"),
    (_LR, 'r"' + BS + '.IsReadOnly' + BS + 's*=', 'r"' + BS + '.IsReadOnlyX' + BS + 's*=', _p6row("raw-isreadonly-assign"),
     "rule-S-LIFT-RAW-isreadonly-assign"),
    (_LR, _ISRO, BS + "S", _p6row("allow-isreadonly-true"), "rule-S-LIFT-RAW-isreadonly-false-only"),
    (_LR, 'r"|' + BS + '.Attributes' + BS + 's*', 'r"|' + BS + '.AttributesX' + BS + 's*', _p6row("raw-attributes-assign"),
     "rule-S-LIFT-RAW-attributes-assign"),
    (_LR, "(?:SetAttributes|SetAccessControl)", "(?:SetAccessControl)", _p6row("raw-io-setattributes"),
     "rule-S-LIFT-RAW-setattributes"),
    (_LR, '            text = text.replace(e.stdin_body, " ")', "            pass", _p6row("allow-heredoc-ps-text"),
     "rule-S-LIFT-RAW-heredoc-is-data"),
    (_LR, '    if (shell or "").lower() in ("", "powershell", "pwsh") or _PS_WORD.search(text) ' + BS + '\n'
          '            or any(e.dialect == "powershell" for e in parsed.execs):',
     "    if True:", _p6row("allow-bash-text-construct"), "rule-S-LIFT-RAW-powershell-only"),
    (_LR, _TXT_EXECS, "    for w in _text_words(text):", _p6row("raw-isreadonly-after-cd"), "rule-S-LIFT-RAW-text-exec-operands"),
    (_LR, "            out += [x for x in re.findall(", "            out += [] and [x for x in re.findall(",
     _p6row("raw-io-in-pwsh-c"), "rule-S-LIFT-RAW-text-nested-quote"),
    (_LR, "    if is_lock_name(name):\n        return _base(abs_path)", "    if True:\n        return _base(abs_path)",
     _p6row("allow-attrib-mod"), "rule-S-LIFT-RAW-lock-name-only"),
    (_LR, '    if set("*?[{") & set(name):', "    if False:", _p6row("raw-attrib-glob"), "rule-S-LIFT-RAW-glob"),
    # Task 11 review fixes: upstream-only peers, statement-scoped constructs, Data listings, cc globs.
    (_LR, "for x in _upstream(parsed, me) if", "for x in parsed.execs if x is not me and", _p6row("allow-scratch-sip-then-dir"),
     "rule-S-LIFT-RAW-peers-upstream-only"),
    (_LR, '        stmt = "' + BS + 'n".join(text[spans[j][0]:spans[j][1]] for j in chosen)', "        stmt = text",
     _p6row("allow-scratch-ro-then-hash"), "rule-S-LIFT-RAW-construct-statement"),
    (_LR, "        chosen = sorted([k] + _feeders(text, spans, k, assigners))", "        chosen = [k]",
     _p6row("r1-allow-scratch-var"), "rule-S-LIFT-RAW-construct-feeders"),
    # Any earlier statement counts as the feeder, assignment or not.
    (_LR, "            at = assigners.get(name, ())", "            at = list(range(j))",
     _p6row("allow-var-scratch-hash-between"), "rule-S-LIFT-RAW-construct-feeder-assigns"),
    # Task 12 (Task 11 Rulings): cacls, takeown, interpreter code, a variable-only cmdlet path.
    (_LR, '        elif e.program == "cacls":', "        elif False:", _p6row("raw-cacls-grant"), "rule-S-LIFT-RAW-cacls"),
    (_LR, '    if not any(w in _CACLS_UNLOCK for w in words):\n        return ""', "", _p6row("allow-cacls-read"),
     "rule-S-LIFT-RAW-cacls-display-allowed"),
    (_LR, '_CACLS_UNLOCK = frozenset(("/g", "/p", "/r"))', '_CACLS_UNLOCK = frozenset(("/g", "/p", "/r", "/d"))',
     _p6row("allow-cacls-deny"), "rule-S-LIFT-RAW-cacls-deny-allowed"),
    (_LR, '        elif e.program == "takeown":', "        elif False:", _p6row("raw-takeown"), "rule-S-LIFT-RAW-takeown"),
    (_LR, "            t = _interpreter_hit(e, cwd, env, roots)", '            t = ""', _p6row("raw-python-chmod"),
     "rule-S-LIFT-RAW-interpreter"),
    (_LR, "    if not code or not INTERP_UNLOCK.search(code):", "    if not code:", _p6row("allow-python-stat-master"),
     "rule-S-LIFT-RAW-interpreter-construct-only"),
    (_LR, '    r"|' + BS + 'bSet-ItemProperty' + BS + 'b[^' + BS + 'n]{0,400}' + BS + 'b(?:IsReadOnly|Attributes)' + BS + 'b"\n',
     "", _p6row("raw-python-heredoc-setattributes"), "rule-S-LIFT-RAW-interpreter-sip"),
    (_LR, ' or (_opaque_path(e, command, roots) if _sip_unlocks(e) else "")', "", _p6row("raw-sip-varpath-names-install"),
     "rule-S-LIFT-RAW-sip-varpath"),
    (_LR, "            t = _set_acl(e, parsed, cwd, env, roots) or _opaque_path(e, command, roots)",
     "            t = _set_acl(e, parsed, cwd, env, roots)", _p6row("raw-acl-varpath-names-install"),
     "rule-S-LIFT-RAW-acl-varpath"),
    (_LR, " or not _names_install(command, roots):", ":", _p6row("allow-acl-varpath-no-install"),
     "rule-S-LIFT-RAW-varpath-names-install-only"),
    (_LR, "not all(_VAR_ONLY.match(w) for w in own)", "False", _p6row("allow-acl-literal-scratch-names-install"),
     "rule-S-LIFT-RAW-varpath-variables-only"),
    (_LR, "        t = _hit(x, w, cwd, env, roots, False) or _listing_hit(x, w, cwd, env, roots)",
     "        t = _hit(x, w, cwd, env, roots, False)", _p6row("raw-gci-filter-esp"), "rule-S-LIFT-RAW-listing"),
    (_LR, "    if not pats:\n        return LOCK_NAMES[0]", "    if not pats:\n        return ''", _p6row("raw-gci-data-all"),
     "rule-S-LIFT-RAW-listing-unfiltered"),
    (_LR, "        t = pattern_target(p)", "        t = p", _p6row("allow-gci-filter-txt"), "rule-S-LIFT-RAW-listing-filter-match"),
    (_LR, "not (rec and _covers_data(a, roots))", "not _covers_data(a, roots)", _p6row("allow-gci-install-norecurse"),
     "rule-S-LIFT-RAW-listing-recurse-only"),
    (_LR, "not (rec and _covers_data(a, roots))", "True", _p6row("raw-gci-recurse-esm"), "rule-S-LIFT-RAW-listing-recurse"),
    (_LR, "    if any(targets._comp_matches(p, s) for s in _CC_SAMPLES):", "    if False:", _p6row("raw-gci-cc-glob"),
     "rule-S-LIFT-RAW-glob-creation-club"),
    (_LR, '    if not rel or len(rel) != 2 or rel[0] != "data":', "    if not rel or len(rel) != 2:",
     _p6row("allow-attrib-tools-copy"), "rule-S-LIFT-RAW-data-only"),
    (_LR, "        if len(pc) <= len(rc) and all(", "        if False and all(", _p6row("raw-attrib-tree"),
     "rule-S-LIFT-RAW-covers-data"),
    (_LR, '        return b if is_lock_name(b) else ""', '        return ""', _p6row("raw-attrib-var"),
     "rule-S-LIFT-RAW-unresolved-lock-name"),
    (_LR, '        return b if is_lock_name(b) else ""', "        return b", _p6row("allow-attrib-var-other"),
     "rule-S-LIFT-RAW-unresolved-other-allowed"),
    (_LR, "    word = dict(e.subst).get(word, word)", "    word = word", _p6row("allow-scratch-var-icacls"),
     "rule-S-LIFT-RAW-same-command-variable"),
    (_LR, "return n in _LOWER or bool(_CC.fullmatch(n))", "return n in _LOWER",
     _LT + "test_the_lock_names_are_the_lock_modules_own_list", "rule-S-LIFT-RAW-creation-club"),
    (_LR, "        if _ANY_CONSTRUCT.search(text):", "        if False:",
     _LT + "test_an_unparseable_command_naming_a_target_with_an_unlock_construct_is_denied", "rule-S-LIFT-RAW-r4-fallback"),
    (_LR, BS + '"?|([^', BS + '"|([^',
     _LT + "test_an_unparseable_command_naming_a_target_with_an_unlock_construct_is_denied", "rule-S-LIFT-RAW-open-quote"),
    (_SR6, "    raw = lift_rules.raw_unlock(command, parsed, roots, cwd, env, shell)", "    raw = None",
     _p6row("raw-icacls-remove"), "rule-S-LIFT-RAW-wired"),
    (_EN, '            audit.log_strict("skyguard", "SHADOW", "{} kept ({}; a lift rule)".format(v.code, v.decision))',
     "            pass", _LT + "test_guards_off_does_not_shadow_a_lift_rule[raw]", "l2-lift-kept-logs"),
    (_EN, _NS, _NS.replace('"shell.S-LIFT-RAW", ', ""), _LT + "test_guards_off_does_not_shadow_a_lift_rule[raw]",
     "rule-S-LIFT-RAW-never-shadowed"),
    (_EN, "        if v.code in NEVER_SHADOW:", "        if False:", _LT + "test_guards_off_does_not_shadow_a_lift_rule[permanent]",
     "l2-lift-never-shadow-clause"),
    # ---- S-LIFT-UNLOCKCMD
    (_LR, "        if _unlock_cmd(e, cwd, env, roots):", "        if False:", _p6row("cmd-unlock-cmd-c"),
     "rule-S-LIFT-UNLOCKCMD-exec"),
    (_LR, '        if a is None or targets.zone(a, roots) == "install":', "        if True:", _p6row("allow-scratch-unlockcmd"),
     "rule-S-LIFT-UNLOCKCMD-scratch-copy-allowed"),
    (_LR, '        if a is None or targets.zone(a, roots) == "install":', "        if a is None:", _p6row("cmd-unlock-abs"),
     "rule-S-LIFT-UNLOCKCMD-install-copy"),
    (_LR, '        if a is None or targets.zone(a, roots) == "install":', '        if a is not None and targets.zone(a, roots) == "install":',
     _p6row("cmd-unlock-nocwd"), "rule-S-LIFT-UNLOCKCMD-unresolved"),
    (_LR, "        if _text_unlock_cmd(text):", "        if False:",
     _LT + "test_an_unparseable_unlock_cmd_is_still_denied", "rule-S-LIFT-UNLOCKCMD-r4-fallback"),
    (_EN, _NS, _NS.replace(', "shell.S-LIFT-UNLOCKCMD"', ""), _LT + "test_guards_off_does_not_shadow_a_lift_rule[unlockcmd]",
     "rule-S-LIFT-UNLOCKCMD-never-shadowed"),
    # ---- S-LIFT-PERMANENT
    (_LR, '        if len(pos) >= 2 and (pos[0], pos[1]) == ("lock", "disable") and not _only_help(args):', "        if False:",
     _p6row("perm-python"), "rule-S-LIFT-PERMANENT-cli"),
    (_LR, "and not _only_help(args):", ":", _p6row("allow-lock-disable-help"), "rule-S-LIFT-PERMANENT-help-only"),
    (_LR, "and not _only_help(args):", "and not any(_only_help(cli_args(x) or ()) for x in parsed.execs):",
     _LT + "test_a_help_only_disable_does_not_hide_a_real_one_beside_it", "rule-S-LIFT-PERMANENT-help-per-call"),
    (_LR, "    return any(_CLI_TEXT.search(t) and _DISABLE_TEXT.search(t) for t in _opaque_texts(command, parsed))",
     "    return False", _LT + "test_an_unparseable_disable_still_asks", "rule-S-LIFT-PERMANENT-r4-fallback"),
    (_LR, '    if e.program == "skyguard":\n        return e.argv', "    if False:\n        return e.argv",
     _p6row("perm-sh-flags"), "rule-S-LIFT-PERMANENT-launcher"),
    (_LR, "argv[k + 1].lower() in _MODULES else None", "argv[k + 1].lower() in () else None", _p6row("perm-m"),
     "rule-S-LIFT-PERMANENT-module"),
    (_LR, 'n == "skyguard" or n.endswith("/skyguard")', 'n == "skyguard"', _p6row("perm-python"),
     "rule-S-LIFT-PERMANENT-package-path"),
    (_LR, 'n.endswith("skyguard/__main__.py")', "False", _p6row("perm-main-py"), "rule-S-LIFT-PERMANENT-main-py"),
    (_LR, "        if w in _PY_VALUED:\n            k += 2", "        if w in _PY_VALUED:\n            k += 1",
     _p6row("perm-python-X"), "rule-S-LIFT-PERMANENT-python-valued-option"),
    (_LR, '        if w.startswith("-") and len(w) > 1:\n            k += 1\n            continue\n        return argv',
     "        return argv", _p6row("perm-main-py"), "rule-S-LIFT-PERMANENT-python-option"),
    (_SR6, "    if lift_rules.permanent(parsed, command):", "    if False:", _p6row("perm-cmd-ps"),
     "rule-S-LIFT-PERMANENT-wired"),
    (_EN, _NS, _NS.replace('"shell.S-LIFT-PERMANENT", ', ""), _LT + "test_guards_off_does_not_shadow_a_lift_rule[permanent]",
     "rule-S-LIFT-PERMANENT-never-shadowed"),
    # ---- S-LIFT-NOTE
    (_LR, '        if cmd == "lock" and action == "unlock":', "        if False:", _p6row("note-unlock-one"),
     "rule-S-LIFT-NOTE-unlock"),
    (_LR, '        if cmd == "guards" and action == "off":', "        if False:", _p6row("note-guards-off"),
     "rule-S-LIFT-NOTE-guards-off"),
    (_LR, "                k += 2                                     # --root R", "                k += 1  # --root R",
     _p6row("note-unlock-cmd-ps"), "rule-S-LIFT-NOTE-valued-flag"),
    (_LR, '            if "=" in w or not w.startswith("--"):', '            if not w.startswith("--"):',
     _p6row("note-unlock-eq"), "rule-S-LIFT-NOTE-eq-flag"),
    (_LR, '        return "every locked file"', '        return "a file"', _p6row("note-unlock-all-sh"),
     "rule-S-LIFT-NOTE-every-file"),
    (_LR, '    return "%s, %s and %d more" % (names[0], names[1], len(names) - 2)', '    return ", ".join(names)',
     _LT + "test_many_files_are_summarised", "rule-S-LIFT-NOTE-summary"),
    (_LR, '    b = re.sub(r"[^' + BS + 'w .' + BS + '-]", "", _base(name))[:60]', "    b = name",
     _LT + "test_a_lift_rule_message_never_echoes_the_install_path", "rule-S-LIFT-NOTE-basename"),
    (_SR6, "    lifted = lift_rules.note(parsed)", '    lifted = ""', _p6row("note-guards-off-flag-first"),
     "rule-S-LIFT-NOTE-wired"),
]]


# Plan 6 Task 12 (R12): self-protection, one twin per clause. Each names the ONE row or test that must go red.
_SF = "tools/skyguard/self_rules.py"
_SFT = "tests/test_skyguard_self_rules.py::"
_WR6 = "tools/skyguard/write_rules.py"


def _p12row(row):
    return _SFT + "test_p6t12_row[p6t12-" + row + "]"


MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # ---- the protected classes (install_kind / user_kind / holders)
    (_SF, '    if rel[0] == ".skyguard":\n        return STATE', "    if False:\n        return STATE", _p12row("state-redirect"),
     "rule-S-SELF-STATE-class"),
    (_SF, '    if rel[0] == ".codex":\n        return GUARD', "    if False:\n        return GUARD",
     _p12row("guard-codex-config"), "rule-S-SELF-GUARD-codex"),
    (_SF, '([".claude", "hooks"], ["tools", "skyguard"], ["agent", "guards"])', '(["tools", "skyguard"], ["agent", "guards"])',
     _p12row("guard-hook-redirect"), "rule-S-SELF-GUARD-hooks"),
    (_SF, '([".claude", "hooks"], ["tools", "skyguard"], ["agent", "guards"])', '([".claude", "hooks"], ["agent", "guards"])',
     _p12row("guard-engine-file"), "rule-S-SELF-GUARD-engine"),
    (_SF, '([".claude", "hooks"], ["tools", "skyguard"], ["agent", "guards"])', '([".claude", "hooks"], ["tools", "skyguard"])',
     _p12row("guard-agent-guards"), "rule-S-SELF-GUARD-agent-guards"),
    (_SF, '(_settings(rel[1]) or rel[1] == "skyrim-paths.env")', '(rel[1] == "skyrim-paths.env")',
     _p12row("guard-settings-sed"), "rule-S-SELF-GUARD-settings"),
    (_SF, '(_settings(rel[1]) or rel[1] == "skyrim-paths.env")', "_settings(rel[1])", _p12row("guard-paths-env"),
     "rule-S-SELF-GUARD-paths-env"),
    (_SF, '    return fnmatch.fnmatchcase(name, "settings*.json")', '    return fnmatch.fnmatchcase(name, "settings*")',
     _p12row("allow-settings-backup-copy"), "rule-S-SELF-GUARD-settings-json-only"),
    (_SF, 'if rel[:3] == [".claude", "backups", ".hook-heartbeat"] or ', "if ", _p12row("guard-heartbeat"),
     "rule-S-SELF-GUARD-heartbeat"),
    (_SF, ' or rel == [".claude", "backups", "audit_log.txt"]', "", _p12row("guard-audit-log"), "rule-S-SELF-GUARD-audit-log"),
    (_SF, '        return GUARD if (rel == [] or rel in _HOLDERS) else ""', '        return ""', _p12row("guard-mv-claude-folder"),
     "rule-S-SELF-GUARD-holder-folder"),
    # l2-self-userlevel: the `~` expansion dropped -- the home folder is no longer known.
    (_SF, '    for n in ("HOME", "USERPROFILE"):', "    for n in ():",
     _SFT + "test_a_user_level_hook_switch_is_a_guard_file[claude-settings]", "l2-self-userlevel"),
    (_SF, '    if rel == [".codex", "config.toml"]:', "    if False:",
     _SFT + "test_a_user_level_hook_switch_is_a_guard_file[codex-config]", "rule-S-SELF-GUARD-user-codex-config"),
    (_SF, '    if len(rel) == 2 and rel[0] == ".claude" and _settings(rel[1]):', '    if rel[:1] == [".claude"]:',
     _SFT + "test_the_rest_of_the_home_folder_is_not[claude-notes]", "rule-S-SELF-GUARD-user-settings-only"),
    # ---- the shell routes
    (_SF, "            out += [(s, True) for s in srcs]", "            pass", _p12row("state-mv-away"),
     "rule-S-SELF-STATE-move-source"),
    (_SF, '    if p == "rename-item":', "    if False:", _p12row("guard-hook-rename-item"), "rule-S-SELF-GUARD-rename-item"),
    (_SF, '    elif p == "ln":', "    elif False:", _p12row("guard-hook-ln"), "rule-S-SELF-GUARD-ln"),
    (_SF, '    elif p == "truncate":', "    elif False:", _p12row("guard-hook-truncate"), "rule-S-SELF-GUARD-truncate"),
    (_SF, '    elif p in ("chmod", "chown", "chgrp"):', "    elif False:", _p12row("guard-hook-chmod"), "rule-S-SELF-GUARD-chmod"),
    (_SF, "        if any(w.lower().startswith(_ICACLS_WRITE) for w in argv[1:]):", "        if True:",
     _p12row("allow-hook-icacls-read"), "rule-S-SELF-GUARD-icacls-read-allowed"),
    (_SF, '    elif p == "icacls" and argv', '    elif False and argv', _p12row("guard-hook-icacls-deny"),
     "rule-S-SELF-GUARD-icacls"),
    (_SF, "        out += [(w, True) for w in _git_restore(argv)]", "        pass", _p12row("guard-git-checkout-hooks"),
     "rule-S-SELF-GUARD-git-checkout"),
    (_SF, '    if sub == "restore":', "    if False:", _p12row("guard-git-restore-settings"), "rule-S-SELF-GUARD-git-restore"),
    (_SF, '(STATE if text_kind(w) == STATE else "")', '""', _p12row("state-unresolved-var"),
     "rule-S-SELF-STATE-unresolved-fails-closed"),
    # ---- the wiring: shell, write, shadow, the patch Move
    (_SR6, "    if own and own[0] == self_rules.STATE:", "    if False:", _p12row("state-redirect"), "rule-S-SELF-STATE-wired"),
    (_SR6, '    if own:\n        advise("S-SELF-GUARD", file=own[1])', "    if False:\n        pass",
     _p12row("guard-hook-redirect"), "rule-S-SELF-GUARD-wired"),
    (_WR6, "    if own == self_rules.STATE:", "    if False:", _p12row("state-write-tool"), "rule-W-SELF-STATE-wired"),
    (_WR6, '    if own == self_rules.GUARD:\n        text = rules.message("W-SELF-GUARD"', '    if False:\n        text = rules.message("W-SELF-GUARD"',
     _p12row("guard-edit-hook"), "rule-W-SELF-GUARD-wired"),
    (_WR6, "    else:\n        own = self_rules.text_kind(file_path)", '    else:\n        own = ""',
     _SFT + "test_without_roots_the_path_words_decide", "rule-W-SELF-no-roots-text"),
    (_SF, '    if ".skyguard" in comps[:-1] or (comps and comps[-1] == ".skyguard"):', "    if False:",
     _SFT + "test_without_roots_the_path_words_decide", "rule-W-SELF-STATE-text-kind"),
    (_EN, 'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE", "write.W-SELF-STATE"))',
     'NEVER_SHADOW = NEVER_SHADOW | frozenset(("write.W-SELF-STATE",))',
     _SFT + "test_guards_off_does_not_open_the_state_file[shell]", "rule-S-SELF-STATE-never-shadowed"),
    (_EN, 'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE", "write.W-SELF-STATE"))',
     'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE",))',
     _SFT + "test_guards_off_does_not_open_the_state_file[write]", "rule-W-SELF-STATE-never-shadowed"),
    # l2-self-applypatch-move: the `*** Move to:` path dropped from the patch's path list.
    ("tools/skyguard/patch_paths.py", '                out.append(("move_to", path))', "                pass",
     _SFT + "test_apply_patch_move_to_into_the_state_is_denied", "l2-self-applypatch-move"),
    # ---- GUARD-FILES.sha256: the drift gate bites, setup.sh's rewritten file stays out
    ("tools/skyguard/verdict.py", "from __future__ import annotations", "from __future__ import annotations  # edited",
     _SFT + "test_the_committed_manifest_is_current", "l2-self-manifest-drift"),
    ("scripts/gen-guard-hashes.py", 'SETUP_WRITES = frozenset((".codex/hooks.json",))', "SETUP_WRITES = frozenset()",
     _SFT + "test_the_manifest_names_exactly_the_tracked_guard_files", "l2-self-manifest-setup-writes"),
    # ---- fix round 1, I1: every delete route into .skyguard/** is S-SELF-STATE (never shadowed)
    (_SF, "    words = [w for w in (deletes.operands(e) or []) if isinstance(w, str)]", "    words = []",
     _p12row("state-del-rm"), "rule-S-SELF-STATE-delete-rm"),
    ("tools/skyguard/deletes.py", '"rd", "remove-item", "rimraf"))', '"rd", "rimraf"))', _p12row("state-del-remove-item"),
     "rule-S-SELF-STATE-delete-remove-item"),
    ("tools/skyguard/deletes.py", '("rm", "rmdir", "unlink", "del", "erase", "rd",', '("rm", "rmdir", "unlink", "erase", "rd",',
     _p12row("state-del-cmd-del"), "rule-S-SELF-STATE-delete-cmd-del"),
    ("tools/skyguard/deletes.py", '("rm", "rmdir", "unlink", "del", "erase", "rd",', '("rm", "unlink", "del", "erase", "rd",',      # the parser spells cmd `rd` as rmdir
     _p12row("state-del-cmd-rd"), "rule-S-SELF-STATE-delete-cmd-rd"),
    ("tools/skyguard/deletes.py", '    if p in ("io.file.delete", "io.directory.delete"):', "    if False:",
     _p12row("state-del-io-file"), "rule-S-SELF-STATE-delete-io-file"),
    (_SF, "        hit = _recycle_delete(command, cwd, env, roots)", '        hit = ""', _p12row("state-del-recycle"),
     "rule-S-SELF-STATE-delete-recycle-bin"),
    (_SF, "    if code and (grep(deletes.DESTROYER, code) or grep(deletes.INTERP_DELETE, code)):", "    if False:",
     _p12row("state-del-python"), "rule-S-SELF-STATE-delete-interpreter"),
    (_SR6, '    if own and own[0] == self_rules.STATE:\n        return deny("S-SELF-STATE", own[1])\n    if dels.hit:',
     '    if own and own[0] == self_rules.STATE and not dels.hit:\n        return deny("S-SELF-STATE", own[1])\n    if dels.hit:',
     _SFT + "test_guards_off_does_not_let_the_lock_manifest_be_deleted", "rule-S-SELF-STATE-delete-before-del-install"),
    (_SF, '    return rel is not None and rel[:1] == [".skyguard"]', '    return rel is not None and (rel == [] or rel[:1] == [".skyguard"])',
     _SFT + "test_a_delete_of_the_whole_install_stays_s_del_install", "rule-S-SELF-STATE-delete-not-the-install"),
    # ---- fix round 1, C1: the guard hash is the REPO form of a configured file
    ("tools/skyguard/guard_hash.py", "    if not slots:\n        return data, {}", "    if True:\n        return data, {}",
     _SFT + "test_a_configured_install_banners_nothing_and_a_real_edit_is_named", "l2-self-hash-normalises-setup-slots"),
    ("tools/skyguard/guard_hash.py", "            m = rx.fullmatch(text)", "            m = rx.search(text)",
     _SFT + "test_an_edit_on_the_configured_line_still_shows[trailing-command]", "l2-self-hash-exact-line-shape"),
    ("tools/skyguard/guard_hash.py", "            if name in done:\n                continue\n", "",
     _SFT + "test_a_second_slot_line_is_not_normalised_away", "l2-self-hash-one-line-per-slot"),
    ("tools/skyguard/session.py", "    for name in guard_hash.inconsistent(configured):", "    for name in []:",
     _SFT + "test_an_edit_on_the_configured_line_still_shows[other-interpreter]", "l2-self-hash-interpreter-disagreement"),
    ("tools/skyguard/guard_hash.py", """values[name] = m.group(1).replace(esc, "'")""", "values[name] = m.group(1)",
     _SFT + "test_quote_escapes_in_the_interpreter_path_round_trip", "l2-self-hash-unescapes-values"),
    ("scripts/gen-guard-hashes.py", "    return guard_hash.digest(rel, path.read_bytes())[0]",
     '    return __import__("hashlib").sha256(path.read_bytes()).hexdigest()',
     _SFT + "test_a_configured_install_banners_nothing_and_a_real_edit_is_named", "l2-self-hash-generator-verify"),
]]


# Plan 6 Task 12 review, I2/I3 (statement scoping): one twin per clause. A feeder shape that goes unrecognised falls
# back to the whole text (fail closed) and still refuses, so those clauses are pinned by their ALLOW rows instead.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_LR, '            if last in ("|", "`") or nxt[i + 1] == "|":', '            if last in ("`",) or nxt[i + 1] == "|":',
     _p6row("r1-pipe-newline"), "l2-r1-continue-trailing-pipe"),
    (_LR, '            if last in ("|", "`") or nxt[i + 1] == "|":', '            if last in ("|",) or nxt[i + 1] == "|":',
     _p6row("r1-backtick-args"), "l2-r1-continue-backtick"),
    (_LR, '            if last in ("|", "`") or nxt[i + 1] == "|":', '            if last in ("|", "`"):',
     _p6row("r1-leading-pipe"), "l2-r1-continue-leading-pipe"),
    (_LR, r"(?:\[[^\]\n]{1,200}\]\s*)*", "", _p6row("r1-allow-cast-scratch"), "l2-r1-feeder-cast"),
    (_LR, r'_ASSIGN_SRC = (r"(?:\[[^\]\n]{1,200}\]\s*)*\$\{?(?:(?:global|script|local|private):)?',
     r'_ASSIGN_SRC = (r"(?:\[[^\]\n]{1,200}\]\s*)*\$\{?', _p6row("r1-allow-global-scratch"), "l2-r1-feeder-scope"),
    (_LR, r'r"|(?:set|new)-variable\s+', r'r"|(?:set|new)-variableX\s+', _p6row("r1-allow-setvar-scratch"),
     "l2-r1-feeder-set-variable"),
    # Re-review timing: the fail-closed whole-text reading done once per statement instead of once per command.
    (_LR, "            if whole is None:", "            if True:",
     "tests/test_skyguard_timing.py::test_lift_rules_stay_fast_on_adversarial_powershell[lift-many-constructs]",
     "l2-r1-whole-text-read-once"),
    # ... and the assignment table rebuilt for every statement.
    (_LR, "        if assigners is None:\n            assigners = _assigners(text, spans)",
     "        if True:\n            assigners = _assigners(text, spans)",
     "tests/test_skyguard_timing.py::test_lift_rules_stay_fast_on_adversarial_powershell[lift-many-fed-constructs]",
     "l2-r1-assigners-built-once"),
    (_LR, "        if _unfed(text, spans, chosen):", "        if False:", _p6row("r1-unfed"), "l2-r1-unfed-fails-closed"),
    (_LR, '_AUTOMATIC = {"_", "psitem",', '_AUTOMATIC = {"psitem",', _p6row("r1-allow-automatic"),
     "l2-r1-automatic-underscore"),
    (_LR, "for x in mine:                                          # I3", "for x in ():  # I3",
     _p6row("r1-listing-filter"), "l2-r1-construct-listing"),
]]


# Plan 6 Task 13: backups land atomically under an exclusive name and are recorded with a sha256. One twin per clause.
_BK = "tools/skyguard/backup.py"
_BKT = "tests/test_backup_manifest.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # the final name replaces whatever holds it (the L7 overwrite)
    (_BK, "            os.link(str(part), str(cand))", "            os.replace(str(part), str(cand))",
     _BKT + "test_two_backups_in_the_same_second_both_survive", "l2-backup-excl"),
    # ... and on a volume without hard links, the name is claimed without O_EXCL
    (_BK, "os.O_CREAT | os.O_EXCL | os.O_WRONLY", "os.O_CREAT | os.O_WRONLY",
     _BKT + "test_same_second_backups_survive_where_hard_links_are_unavailable", "l2-backup-excl-fallback"),
    # the copy is written straight to the final name
    (_BK, '    part = Path("{}.part-{}".format(base, os.getpid()))', "    part = base",
     _BKT + "test_a_copy_that_fails_midway_leaves_nothing_under_the_final_name", "l2-backup-part"),
    # a failed copy leaves its part behind
    (_BK, '    except OSError:\n        _discard(part)\n        audit.log(HOOK, "FAILED", "could not copy',
     '    except OSError:\n        audit.log(HOOK, "FAILED", "could not copy',
     _BKT + "test_a_copy_that_fails_midway_leaves_nothing_under_the_final_name", "l2-backup-part-cleanup"),
    (_BK, "            os.fsync(fout.fileno())", "            pass",
     _BKT + "test_the_part_is_fsynced_before_it_gets_its_final_name", "l2-backup-fsync"),
    (_BK, "    if not (on_disk == copied == want):", "    if False:",
     _BKT + "test_a_size_mismatch_is_failed_and_the_part_is_removed", "l2-backup-size"),
    # the recorded sha256 is not the hash of the bytes copied
    (_BK, "                h.update(chunk)", "                pass",
     _BKT + "test_the_manifest_line_records_the_copy", "l2-backup-manifest-sha"),
    (_BK, "        os.write(fd, line)", "        [os.write(fd, line[i:i + 1]) for i in range(len(line))]",
     _BKT + "test_the_manifest_append_is_one_write_of_one_line", "l2-backup-manifest-one-write"),
    (_BK, "(backup: {d})\\n\".format(t=ts, n=tool_name, p=file_path, d=dest.name)",
     "(backup: {d})\\n\".format(t=ts, n=tool_name, p=file_path, d=base.name)",
     _BKT + "test_the_audit_line_names_the_backup_actually_written", "l2-backup-audit-dest"),
    ("tools/skyguard/run.py", 'p["empty"], audit, "claude", p.get("session_id", ""))', 'p["empty"], audit)',
     _BKT + "test_the_claude_hook_records_its_agent_and_session", "l2-backup-claude-session"),
    ("tools/skyguard/engine.py", "False, audit, req.agent, req.session_id)", "False, audit)",
     _BKT + "test_the_codex_patch_backup_records_its_agent_and_session", "l2-backup-codex-session"),
    # ---- restore (step 3)
    # the listing guesses the path from the flattened name although the manifest recorded it (the L8 bug)
    (_BK, '        rows.append((e["ts"], dest, e["src"],', '        rows.append((e["ts"], dest, legacy_guess(dest),',
     _BKT + "test_restore_lists_the_recorded_path_verbatim", "l2-restore-manifest-path"),
    # ... and so does the restore itself
    (_BK, '    target = Path(_native(to or e["src"]))', "    target = Path(_native(to or legacy_guess(name)))",
     _BKT + "test_restore_puts_the_file_back_at_the_recorded_path", "l2-restore-manifest-target"),
    (_BK, '        if got != e["sha256"]:', "        if False:",
     _BKT + "test_restore_refuses_a_backup_whose_sha256_does_not_match", "l2-restore-sha-check"),
    (_BK, '        if e is not None and digest != e["sha256"]:', "        if False:",
     _BKT + "test_a_backup_that_changes_after_its_check_is_not_restored", "l2-restore-sha-recheck"),
    (_BK, '            note += "  (legacy: path guessed)"', "            pass",
     _BKT + "test_a_pre_manifest_backup_is_listed_as_a_guess", "l2-restore-legacy-label"),
    # a pre-manifest backup is restored to its guessed path instead of asking for --to
    (_BK, "    if e is None and not to:\n", "    if e is None and not to:\n        to = legacy_guess(name)\n    if False:\n",
     _BKT + "test_a_legacy_restore_needs_an_explicit_target", "l2-restore-legacy-needs-to"),
    (_BK, "            bad += 1\n", "            pass\n",
     _BKT + "test_an_unreadable_manifest_line_is_skipped_and_counted", "l2-restore-bad-line-counted"),
    (_BK, "    if target.is_file() and take(", "    if False and take(",
     _BKT + "test_restore_puts_the_file_back_at_the_recorded_path", "l2-restore-backs-up-current"),
    (_BK, '"(missing)" if size is None', '"" if size is None',
     _BKT + "test_a_recorded_backup_whose_file_is_gone_is_marked_missing", "l2-restore-missing-marked"),
]]
