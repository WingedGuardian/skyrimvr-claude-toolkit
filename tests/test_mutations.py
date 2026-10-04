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
        "tests/test_hooks.py::test_the_delete_rule_does_not_swallow_ordinary_work",
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
        "tools/skyguard/shell_rules.py",
        "    if has(DESTROYER):",
        '    if has(r"rm' + BS + 's+(-[a-z]*f[a-z]*' + BS + 's+)?"):',
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
        "tests/test_hooks.py::test_the_install_cannot_be_deleted_however_it_is_spelled",
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
        "tests/test_hooks.py::test_an_install_path_containing_regex_metacharacters_is_still_guarded",
        id="path-to-ere-escapes-regex-metacharacters",
    ),
    # Stop honouring the CONFIGURED config directory. MO2/Wabbajack put the INIs in the
    # instance profile, outside Documents/My Games, so for those users this arm is the
    # only thing guarding their config -- and it had no test at all.
    pytest.param(
        "tools/skyguard/paths.py",
        '    _add(config, env.get("SKYRIM_CONFIG_DIR", ""))',
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
                 '        return _with_prefs("", project, audit), "evaluated", "session"',
                 '        return "", "evaluated", "session"',
                 "tests/test_skyguard_prefs.py::test_compact_still_injects_preferences_but_takes_no_snapshot",
                 id="prefs-survive-compaction"),
    pytest.param("tools/skyguard/session.py", '    return _with_prefs("\\n".join(lines), project, audit)',
                 '    return "\\n".join(lines)',
                 "tests/test_skyguard_prefs.py::test_preferences_follow_a_clean_kb_guard_run",
                 id="session-start-injects-preferences-after-a-kb-guard-run"),
    # One twin per return site: each site shadows the others, so one mutation proves only one site.
    # (The message is bound to `missing` first so this mutation is still a valid program: cutting
    # `, project, audit)` off a one-line call would be a SyntaxError -- a kill for the wrong reason.)
    pytest.param("tools/skyguard/session.py", "        return _with_prefs(missing, project, audit)",
                 "        return missing",
                 "tests/test_skyguard_prefs.py::test_the_real_hook_delivers_preferences",
                 id="session-start-injects-preferences-when-kb-guard-is-missing"),
    # An INERT refusal must be recorded as "nothing was checked", not as a rule decision. Fold the
    # status back into the decision and the telemetry would read a blind guard as a working one.
    pytest.param(
        "tools/skyguard/codes.py",
        'status = "failed" if ".NO_" in code or code == "wrapper.INERT" else v.status',
        "status = v.status",
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
        ("tools/skyguard/codex_rules.py", '"pattern": ["reg", "delete"]', '"pattern": ["reg"]',
         "tests/test_codex_rules.py::test_every_prefix_rule_is_a_subset_of_its_hook_deny",
         "codex-rules-prefix-widened"),
        ("tools/skyguard/codex_rules.py", '"pattern": ["reg.exe", "delete"], "decision": "forbidden"',
         '"pattern": ["reg.exe", "delete"], "decision": "prompt"',
         "tests/test_codex_rules.py::test_buckets_sum", "codex-rules-decision-drifts-from-bucket"),
        ("tools/skyguard/codex_rules.py", '    "shell.DENY_REGISTRY": (\n        "forbidden",',
         '    "shell.DENY_REGISTRY": (\n        "hook_only",',
         "tests/test_codex_rules.py::test_buckets_sum", "codex-rules-forbidden-bucket-without-rule"),
        ("tools/skyguard/codex_rules.py",
         '    "shell.NO_ROOT_DENY": ("hook_only", "analysis failure, not a command"),\n', "",
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
         '    "shell.DENY_REGISTRY": (\n        "forbidden",\n        "the hook denies every `reg delete`; '
         '`reg delete` / `reg.exe delete` prefixes are a strict subset"),\n', "",
         "tests/test_codex_rules.py::test_every_shell_verdict_is_classified", "codex-unclassified-shell-verdict"),
        # The conformance harness itself: an `ask` would pass as a deny if the shape gate stopped checking.
        ("tests/test_codex_conformance.py",
         '        assert body["permissionDecision"] == "deny", (where, "only deny blocks on Codex", body)',
         "        pass",
         "tests/test_codex_conformance.py::test_the_shape_check_can_fail", "codex-conformance-shape-gate-passes-ask"),
        # The plugin rule stops covering .esm: a patch heredoc onto Data/Skyrim.esm (a Bash call carrying a
        # patch) runs. Killed by the shell-patch DERIVED variant of the captured heredoc shape.
        ("tools/skyguard/write_rules.py",
         r'''grep(r"\.(esp|esm|esl|bsa|ba2)[\s.]*$", file_path)''',
         r'''grep(r"\.(esp|esl|bsa|ba2)[\s.]*$", file_path)''',
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
     _COV + "test_claude_coverage_is_judged_per_covered_tool", "coverage-claude-is-judged-per-tool"),
    ("tools/skyguard/health.py",
     'COUNTING_HOOKS = {"claude": ("protect-bash", "protect-files"), "codex": ("pre-tool",)}',
     'COUNTING_HOOKS = {"claude": ("protect-bash", "protect-files", "backup-before-edit"), '
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
