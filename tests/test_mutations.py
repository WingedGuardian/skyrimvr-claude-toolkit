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
CMD_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="the CMD regression runs on Windows")

# Final E2E verdicts must include checks appended after the live probe returns.
E2E_VERDICT_MUTATIONS = [pytest.param(
    "scripts/e2e-codex.py", anchor, indent + "pass",
    "tests/test_e2e_codex_verdict.py::test_checks_added_after_probe_determine_the_phase_verdict[" + case + "]",
    id="e2e-verdict-" + case,
) for anchor, indent, case in [
    ('        p["pass"] = all(p["checks"].values())  # A8: include the residual check', "        ", "phase_a-A8-nogit"),
    ('    p["pass"] = all(p["checks"].values())  # B2: include file and canary checks', "    ", "phase_b-B2"),
    ('        p["pass"] = all(p["checks"].values())  # C2: include file and trust checks', "        ", "phase_c-C2-modified"),
]]

# Copies for mutation must INCLUDE tests/ (unlike conftest.copy_repo, which
# deliberately excludes it) -- we re-run the suite inside the copy.
MUTATION_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"
)

# (id, relative file, text to find, replacement, test that must then fail)
MUTATIONS = E2E_VERDICT_MUTATIONS + [
    pytest.param("tools/" + name,
                 'if "%SG_FILE_PY:~0,1%%SG_FILE_PY:~-1%"=="\'\'" set "SG_FILE_PY=%SG_FILE_PY:~1,-1%"',
                 "rem outer quote decoding removed",
                 "tests/test_wrapper_python.py::test_the_cmd_twins_use_the_recorded_interpreter[space-" + name + "]",
                 marks=CMD_ONLY, id="review-" + name + "-outer-quotes")
    for name in ("skyguard.cmd", "skydoctor.cmd")
] + [
    pytest.param("tools/" + name,
                 'if defined SG_FILE_PY set "SG_FILE_PY=%SG_FILE_PY:\'\\\'\'=\'%"',
                 "rem embedded apostrophe decoding removed",
                 "tests/test_wrapper_python.py::test_the_cmd_twins_use_the_recorded_interpreter[apostrophe-" + name + "]",
                 marks=CMD_ONLY, id="review-" + name + "-apostrophe")
    for name in ("skyguard.cmd", "skydoctor.cmd")
] + [
    pytest.param("tools/" + name, "setlocal EnableExtensions DisableDelayedExpansion",
                 "setlocal EnableExtensions",
                 "tests/test_wrapper_python.py::test_the_cmd_twins_use_the_recorded_interpreter[bang-" + name + "]",
                 marks=CMD_ONLY, id="review-" + name + "-delayed-expansion")
    for name in ("skyguard.cmd", "skydoctor.cmd")
] + [
    pytest.param("tools/" + name,
                 'if "%SG_FILE_PY:~0,1%%SG_FILE_PY:~-1%"=="\'\'"',
                 'if defined SG_FILE_PY',
                 "tests/test_wrapper_python.py::test_the_cmd_twins_keep_an_unquoted_recorded_interpreter[" + name + "]",
                 marks=CMD_ONLY, id="review-" + name + "-unquoted-path")
    for name in ("skyguard.cmd", "skydoctor.cmd")
] + [
    pytest.param("tests/test_hook_deadline_load.py",
                 "        for _ in range(n):\n            burners.append(_start_burner())",
                 "        burners = [_start_burner() for _ in range(n)]",
                 "tests/test_hook_load_cleanup.py::test_partial_burner_start_failure_stops_and_waits_for_every_started_child",
                 id="review-load-partial-start-cleanup"),
    pytest.param("scripts/e2e-codex.py", '[bash(), "setup.sh", "--agent", "codex"]',
                 '[bash(), "setup.sh"]',
                 "tests/test_e2e_codex_verdict.py::test_make_refuses_failed_setup_before_git_initialization",
                 id="review-e2e-explicit-agent"),
    pytest.param("scripts/e2e-codex.py", "    if r.returncode:\n        raise SystemExit(r.returncode)",
                 "    if False:\n        raise SystemExit(r.returncode)",
                 "tests/test_e2e_codex_verdict.py::test_make_refuses_failed_setup_before_git_initialization",
                 id="review-e2e-failed-setup-refuses"),
    pytest.param("tools/migrate-3x.py", "fence[0] == token[0] and len(fence)", "True and len(fence)",
                 "tests/test_migrate_3x.py::test_migration_closes_the_actual_open_fence_before_adding_preferences[other-kind]",
                 id="review-migration-fence-kind"),
    pytest.param("tools/migrate-3x.py", "len(fence) >= len(token)", "True",
                 "tests/test_migrate_3x.py::test_migration_closes_the_actual_open_fence_before_adding_preferences[short-close]",
                 id="review-migration-fence-length"),
    pytest.param("tools/migrate-3x.py", "and not line[m.end():].strip():", ":",
                 "tests/test_migrate_3x.py::test_migration_closes_the_actual_open_fence_before_adding_preferences[trailing-text]",
                 id="review-migration-fence-trailing-text"),
    pytest.param("scripts/build_release_zip.py", 'name = json.loads(_git("show", "{}:package.json".format(sha)).stdout)["name"]',
                 'name = json.loads((Path(__file__).resolve().parent.parent / "package.json").read_text())["name"]',
                 "tests/test_release_build.py::test_archive_name_comes_from_the_selected_commit_not_the_producer_checkout",
                 id="review-builder-committed-product-slug"),
    pytest.param("scripts/build_release_zip.py", 'raise Refused(4, "the committed package.json has no readable product name")',
                 'return "skyrim-ai-assistant-toolkit"',
                 "tests/test_release_build.py::test_a_missing_or_invalid_committed_product_slug_refuses[invalid-json]",
                 id="review-builder-unreadable-product-slug"),
    pytest.param("scripts/build_release_zip.py", 'if not isinstance(name, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:',
                 "if False:",
                 "tests/test_release_build.py::test_a_missing_or_invalid_committed_product_slug_refuses[traversal]",
                 id="review-builder-invalid-product-slug"),
    pytest.param(".github/workflows/ci.yml", 'tee "$RUNNER_TEMP/build.out"', "tee build.out",
                 "tests/test_release_build.py::test_the_real_ci_build_step_keeps_its_log_outside_the_checkout",
                 id="review-ci-build-log-outside-checkout"),
    pytest.param("tools/verify-release.sh", ' || exit 2\n\n# The workspace goes under VERIFY_WORK_ROOT',
                 '\n\n# The workspace goes under VERIFY_WORK_ROOT',
                 "tests/test_verify_release.py::test_a_failed_product_slug_read_refuses_before_artifact_work",
                 id="review-verify-product-slug-read-refuses"),
    pytest.param("scripts/build-nexus-pack.sh", ' || die "cannot read release slug from package.json"', "",
                 "tests/test_nexus_pack.py::test_a_failed_product_slug_read_refuses_before_a_download",
                 id="review-nexus-product-slug-read-refuses"),
    pytest.param("tools/skyguard/session.py", "                with state.locked(str(sf)):", "                if True:",
                 "tests/test_skyguard_session_start.py::test_session_reads_the_state_to_be_rewritten_under_the_writer_lock",
                 id="review-session-state-read-locked"),
    pytest.param("tools/skyguard/session.py", "                    st = state.read(str(sf))", "                    pass",
                 "tests/test_skyguard_session_start.py::test_session_start_preserves_a_newer_persistent_decision[True]",
                 id="review-session-state-refresh"),
    pytest.param("tools/skyguard/session.py", 'if source != "compact" and not st.rejected and st.lifts:',
                 'if not st.rejected and st.lifts:',
                 "tests/test_skyguard_session_start.py::test_compact_does_not_clear_or_report_lifts",
                 id="review-session-compact-state-read-only"),
    pytest.param("tools/skyguard/recovery.py", "    if not mcp_rules.is_devbench(tool_name):",
                 '    if len(parts) < 3 or parts[0] != "mcp" or not parts[1].startswith("devbench"):',
                 "tests/test_recovery_snapshots.py::test_each_judged_devbench_alias_keeps_the_save_and_cosave_before_the_call[my__devbench]",
                 id="review-recovery-devbench-alias"),
    pytest.param("tools/skyguard/self_rules.py",
                 "grep(deletes.INTERP_DESTROYER, code)", "grep(deletes.DESTROYER, code)",
                 "tests/test_skyguard_self_rules.py::test_recycle_examples_outside_the_install_do_not_become_state_targets[double]",
                 id="state-recycle-not-all-interpreter-strings"),
    pytest.param("tools/skyguard/deletes.py", "            text = text.replace(e.segment, code)",
                 "            pass",
                 "tests/test_skyguard_self_rules.py::test_recycle_examples_outside_the_install_do_not_become_state_targets[posix]",
                 id="state-recycle-decoded-interpreter-path"),
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
        # Plan 8 T7: re-anchored on the root-file write (the file moved to <root>/skyrim-paths.env).
        '} > "$PATHS_ENV.new" 2>/dev/null && mv -f "$PATHS_ENV.new" "$PATHS_ENV" && PATHS_OK=1 || PATHS_OK=""',
        '} > /dev/null && PATHS_OK=1 || PATHS_OK=""',
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
        marks=CMD_ONLY,
        id="wrapper-drive-root-spelling",
    ),
    # Lose the `pwd -W` fallback for a root on an MSYS mount (/tmp/...), which has no textual spelling.
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        '    msys*:/*)          ROOT_WIN=',
        '    msys*:/__never__)  ROOT_WIN=',
        "tests/test_hooks.py::test_a_root_on_an_msys_mount_still_gets_its_windows_spelling",
        marks=CMD_ONLY,
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
        # Plan 8 T10: the gate is an entry of the one payload checker both release gates run.
        "tools/check-release-payload.py",
        '    "KNOWLEDGEBASE.local.md",\n',
        "",
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
    # G1 (RT-A): interpreter code that launches a process, naming a protected root, is judged like a delete.
    pytest.param(
        "tools/skyguard/deletes.py",
        "                has(INTERP_LAUNCH, code) and has(LAUNCH_DELETE_WORD, code))):\n",
        "                False)):\n",
        # py-subprocess: its command list carries no delete WORD, so only the process-launch clause can catch it
        # (py-os-system's text also holds `del`, which the legacy delete words catch on their own).
        "tests/test_skyguard_deletes.py::test_g1_interpreter_code_naming_a_root_denies[py-subprocess]",
        id="g1-process-launch-api-not-judged",
    ),
    # G1: python -Bc CODE (a flag cluster ending in c) carries code.
    pytest.param(
        "tools/skyguard/deletes.py",
        "        elif _PYTHON.fullmatch(p) and _PY_CODE_CLUSTER.fullmatch(w):\n",
        "        elif False:\n",
        "tests/test_skyguard_deletes.py::test_g1_interpreter_code_naming_a_root_denies[py-Bc-rmtree]",
        id="g1-python-code-cluster-unread",
    ),
    # G1: node -pe CODE carries code.
    pytest.param(
        "tools/skyguard/deletes.py",
        'CODE_FLAGS = frozenset(("-c", "-e", "-E", "--eval", "-p", "--print", "-r", "-pe"))',
        'CODE_FLAGS = frozenset(("-c", "-e", "-E", "--eval", "-p", "--print", "-r"))',
        "tests/test_skyguard_deletes.py::test_g1_interpreter_code_naming_a_root_denies[node-pe-rmSync]",
        id="g1-node-pe-unread",
    ),
    # G2 (RT-A): a program that runs a command line it is handed is opaque.
    pytest.param(
        "tools/skyguard/deletes.py",
        '    if e.program == "?" or os.path.splitext(e.program)[0] in _WRAPPER_PROGRAMS:\n',
        '    if e.program == "?":\n',
        "tests/test_skyguard_deletes.py::test_g2_wrapper_with_delete_and_root_denies[schtasks-rd]",
        id="g2-command-wrappers-not-opaque",
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
    # Final review D3: since Task 6/7 a RESOLVED delete is placed by Roots.config_dirs
    # (`_add_dir(cfg, v)`), so that is the clause this test guards. The old anchor
    # (`_add(config, v)` -> a mis-indented `pass`) was killed only by an IndentationError;
    # reverted with the right indent it survived. The mention regex arm has its own twin.
    pytest.param(
        "tools/skyguard/paths.py",
        "        _add_dir(cfg, v)",
        "        pass",
        "tests/test_hooks.py::test_a_configured_config_directory_outside_documents_is_guarded",
        id="configured-config-directory-is-guarded",
    ),
    pytest.param(
        "tools/skyguard/paths.py",
        "        _add(config, v)",
        "        pass",
        "tests/test_skyguard_deletes.py::"
        "test_an_unresolved_delete_in_a_command_naming_the_configured_config_dir_is_unresolved",
        id="configured-config-directory-is-named",
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
        "paths.read_install(a.root_win or a.root, ",      # Plan 8 T8: re-anchored (the read moved into read_install)
        "paths.read_install(a.root, ",
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
    pytest.param("tools/skyguard/prefs.py", "if not fenced[i] and _HEADING.match(line)), None)",
                 "if _HEADING.match(line)), None)",
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
        "tests/test_patch_paths.py::test_every_way_to_run_it_from_a_file_is_refused",
        id="patch-env-assignment-prefix-is-a-run",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", '_NAME = r"(?:[^\\s\'\\"`;&|()<>]*[/\\\\])?', '_NAME = r"',
        "tests/test_patch_paths.py::test_every_way_to_run_it_from_a_file_is_refused",
        id="patch-path-prefix-is-a-run",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", "(?:\\.exe)?(?=[\\s;", "(?=[\\s;",
        "tests/test_patch_paths.py::test_every_way_to_run_it_from_a_file_is_refused",
        id="patch-exe-suffix-is-a-run",
    ),
    pytest.param(
        "tools/skyguard/patch_paths.py", "or (_QUOTED_POSITION.search(cmd) and _WRAPPER.search(cmd))", "",
        "tests/test_patch_paths.py::test_every_way_to_run_it_from_a_file_is_refused",
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
        'if [ "$MS" -ge "$end" ]; then',
        'if false; then',
        "tests/test_python_fallback.py::test_a_hanging_fallback_engine_is_cut_off_by_timeout",
        id="fallback-engine-call-has-no-time-limit",
    ),
    pytest.param(
        "tools/skyguard/run.py",
        "faulthandler.dump_traceback_later(_engine_budget(), exit=True, file=sys.stdout)",
        "pass",
        "tests/test_python_fallback.py::test_a_catastrophic_regex_inside_the_engine_is_killed_at_the_budget",
        id="engine-watchdog-disarmed",
    ),
] + [
    # load-deadline fix (2026-10-05): one mutation per new clause of the bash entries' time limits and of
    # the remembered fallback. Each names the ONE test that isolates that clause.
    pytest.param(m[0], m[1], m[2], m[3], id=m[4])
    for m in [
        # 1: the kill is by WINDOWS pid (a hung engine must actually be gone afterwards)
        ("agent/guards/adapters/claude/protect-files.sh",
         '"$tk" //F //T //PID "$w" >/dev/null 2>&1 </dev/null &', ":",
         "tests/test_hook_deadline.py::test_a_hung_engine_on_the_healthy_path_is_killed_by_its_pid",
         "deadline-kill-by-windows-pid"),
        # 2: the OUTER deadline comes from the definition timeout ...
        ("agent/guards/adapters/claude/protect-files.sh",
         "DL=$(( T0_MS + (H_S - 15) * 1000 ))", "DL=$(( T0_MS + 600000 ))",
         "tests/test_hook_deadline.py::test_the_outer_deadline_answers_while_the_child_is_still_dying[claude]",
         "deadline-outer-from-definition-timeout"),
        # ... and the answer does not wait for a kill that is still landing
        ("agent/guards/adapters/claude/protect-files.sh",
         '"$tk" //F //T //PID "$w" >/dev/null 2>&1 </dev/null &', '"$tk" //F //T //PID "$w" >/dev/null 2>&1 </dev/null',
         "tests/test_hook_deadline.py::test_the_outer_deadline_answers_while_the_child_is_still_dying[claude]",
         "deadline-kill-is-not-waited-for"),
        ("agent/guards/adapters/codex/skyguard-entry.sh",
         "DL=$(( T0_MS + (H_S - 15) * 1000 ))", "DL=$(( T0_MS + 600000 ))",
         "tests/test_hook_deadline.py::test_the_outer_deadline_answers_while_the_child_is_still_dying[codex]",
         "codex-sh-deadline-outer-from-definition-timeout"),
        # ... while time is left, it does let the kill land (no engine still running at the answer)
        ("agent/guards/adapters/claude/protect-files.sh",
         '            while kill -0 "$cpid" 2>/dev/null; do', "            while false; do",
         "tests/test_hook_deadline.py::test_with_time_left_the_wrapper_answers_only_once_the_killed_child_is_gone",
         "deadline-kill-allowed-to-land-while-time-is-left"),
        ("agent/guards/adapters/claude/protect-files.sh",
         "HOOK_TIMEOUT_S=60 ", "HOOK_TIMEOUT_S=90 ",
         "tests/test_hook_deadline.py::test_every_claude_wrapper_carries_its_definition_timeout",
         "deadline-definition-timeout-drift"),
        # 3: one process per candidate
        ("agent/guards/adapters/claude/protect-files.sh",
         '    n="$RANDOM$RANDOM"\n    _bounded "$b" none',
         '    "$@" -c "import sys" >/dev/null 2>&1\n    n="$RANDOM$RANDOM"\n    _bounded "$b" none',
         "tests/test_hook_deadline.py::test_a_candidate_costs_one_probe_process",
         "python-fallback-second-probe-process"),
        # 4: the remembered fallback: used, and every one of its guards
        ("agent/guards/adapters/claude/protect-files.sh",
         "    py_cached && return 0", "    false && return 0",
         "tests/test_hook_deadline.py::test_a_verified_fallback_is_remembered_and_not_probed_again",
         "python-fallback-not-remembered"),
        ("agent/guards/adapters/claude/protect-files.sh",
         '[ -x "${c[0]}" ] && [ ! "${c[0]}" -nt "$f" ] && ', '[ -x "${c[0]}" ] && ',
         "tests/test_hook_deadline.py::test_a_remembered_fallback_is_dropped_when_it_or_the_engine_changed[interpreter]",
         "python-fallback-cache-ignores-a-changed-interpreter"),
        ("agent/guards/adapters/claude/protect-files.sh",
         '[ ! "$ROOT/tools/skyguard/run.py" -nt "$f" ] || return 1', "true",
         "tests/test_hook_deadline.py::test_a_remembered_fallback_is_dropped_when_it_or_the_engine_changed[engine]",
         "python-fallback-cache-ignores-a-changed-engine"),
        ("agent/guards/adapters/claude/protect-files.sh",
         '*) RC=1; _fail python "fallback ${PYC[*]} did not echo the engine nonce -- not used" ;;', "*) ;;",
         "tests/test_hook_deadline.py::test_a_cache_naming_a_stub_cannot_substitute_the_interpreter",
         "python-fallback-cache-substitutes-an-interpreter"),
        ("agent/guards/adapters/claude/protect-files.sh",
         '*) RC=1; _fail python "fallback ${PYC[*]} did not echo the engine nonce -- not used" ;;', "*) ;;",
         "tests/test_hook_deadline.py::test_a_fresh_fallback_that_answers_without_the_nonce_is_refused",
         "python-fallback-engine-run-without-nonce"),
        ("agent/guards/adapters/claude/protect-files.sh",
         '[ "${c[0]:-}" = "skyguard-pyfb-v1" ] && ', "",
         "tests/test_hook_deadline.py::test_a_malformed_cache_is_not_used[tag]",
         "python-fallback-cache-version-unchecked"),
        ("agent/guards/adapters/claude/protect-files.sh",
         'case "${c[0]##*/}" in python|python3|py|python.exe|python3.exe|py.exe) ;; *) return 1 ;; esac', ":",
         "tests/test_hook_deadline.py::test_a_malformed_cache_is_not_used[name]",
         "python-fallback-cache-any-program"),
        ("agent/guards/adapters/claude/protect-files.sh",
         'case "${c[0]}" in /*|[A-Za-z]:/*) ;; *) return 1 ;; esac', ":",
         "tests/test_hook_deadline.py::test_a_malformed_cache_is_not_used[relative]",
         "python-fallback-cache-relative-path"),
        ("agent/guards/adapters/claude/protect-files.sh",
         '[ "${#c[@]}" -eq 1 ] || [ "${c[1]}" = "-3" ] || return 1', ":",
         "tests/test_hook_deadline.py::test_a_malformed_cache_is_not_used[args]",
         "python-fallback-cache-any-arguments"),
        ("agent/guards/adapters/claude/protect-files.sh",
         'NOCACHE=1; : > "$PYFB_CACHE" 2>/dev/null; FB=""', 'FB=""',
         "tests/test_hook_deadline.py::test_a_remembered_fallback_that_fails_is_forgotten",
         "python-fallback-failed-cache-kept"),
        # 5: probe budgets from the time left, sub-second
        ("agent/guards/adapters/claude/protect-files.sh",
         '[ "$b" -le "$PROBE_MS" ] || b=$PROBE_MS', "b=$PROBE_MS",
         "tests/test_hook_deadline.py::test_a_probe_gets_only_the_time_left_before_the_deadline",
         "python-fallback-probe-ignores-time-left"),
        ("agent/guards/adapters/claude/protect-files.sh",
         'f="${f}000"; f="${f:0:3}"', 'f="000"',
         "tests/test_hook_deadline.py::test_a_sub_second_probe_budget_is_honoured",
         "python-fallback-probe-whole-seconds-only"),
        # 6: no re-probe after run.py's own watchdog
        ("agent/guards/adapters/claude/protect-files.sh",
         '[ "$RC" -ne 124 ] && [ -z "$WD" ] && ', '[ "$RC" -ne 124 ] && ',
         "tests/test_hook_deadline.py::test_no_reprobe_after_the_engine_watchdog_fired",
         "python-fallback-reprobes-after-the-watchdog"),
        ("tools/skyguard/run.py",
         "exit=True, file=sys.stdout)", "exit=True)",
         "tests/test_hook_deadline.py::test_no_reprobe_after_the_engine_watchdog_fired",
         "engine-watchdog-report-not-on-stdout"),
    ]
] + [
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
        '        sp = patch_paths.shell_patch(cmd)',
        '        sp = None',
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
        # rt-fix-b: the command keys are read for every non-MCP tool; reading them for Bash only reverts it.
        '        keys = [k for k in _COMMAND_KEYS if k in ti]\n',
        '        keys = [k for k in _COMMAND_KEYS if k in ti] if name == "Bash" else []\n',
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
    # Plan 7 Task 1: the agent-neutral engine entry (run.py neutral pre-tool).
    pytest.param(
        'tools/skyguard/neutral.py',
        'if type(d.get("v")) is not int or d.get("v") != 1:',
        'if d.get("v") != 1:',
        'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[bool-version]',
        id='neutral-bool-version-accepted',
    ),
    pytest.param(
        'tools/skyguard/neutral.py',
        '        v = Verdict("deny", BAD.format(e), status="failed", code="neutral.BAD_REQUEST")',
        '        return ("", None)',
        'tests/test_skyguard_neutral.py::test_handle_turns_a_bad_request_into_a_failed_deny_never_a_raise',
        id='neutral-bad-request-not-a-deny',
    ),
    pytest.param(
        'tools/skyguard/neutral.py',
        'raise BadRequest("unknown field {}".format(_names(extra)))',
        'raise BadRequest("unknown field {}".format(", ".join(extra)))',
        'tests/test_skyguard_neutral.py::test_an_unknown_key_is_named_on_one_short_line',
        id='neutral-unknown-key-echoed-raw',
    ),
    pytest.param(
        'tools/skyguard/run.py',
        'raise engine.RootsUnavailable(neutral.ENV_UNREADABLE)',
        'raise engine.RootsUnavailable(ENV_UNREADABLE)',
        'tests/test_skyguard_neutral.py::test_cli_unreadable_paths_file_is_a_neutral_failed_deny',
        id='neutral-env-refusal-names-the-claude-hook',
    ),
    pytest.param(
        'tools/skyguard/run.py',
        '    from skyguard import neutral\n    if a.role != "pre-tool":',
        '    from skyguard import neutral\n    if False:',
        'tests/test_skyguard_neutral.py::test_cli_rejects_a_role_other_than_pre_tool_with_exit_1_and_no_stdout',
        id='neutral-any-role-accepted',
    ),
    # Plan 7 Task 1, fix round 1.
    pytest.param('tools/skyguard/neutral.py', '    if extra:', '    if False:',
                 'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[unknown-key]',
                 id='neutral-unknown-field-accepted'),
    pytest.param('tools/skyguard/neutral.py', '        if k in seen:', '        if False:',
                 'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[duplicate-kind]',
                 id='neutral-duplicate-key-last-wins'),
    pytest.param('tools/skyguard/neutral.py', '    if v.decision not in DECISIONS:', '    if False:',
                 'tests/test_skyguard_neutral.py::test_render_is_ascii_one_line_and_total',
                 id='neutral-render-passes-an-unknown-decision'),
    pytest.param('tools/skyguard/neutral.py', 'if type(d.get("v")) is not int or d.get("v") != 1:',
                 'if isinstance(d.get("v"), bool) or d.get("v") != 1:',
                 'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[float-version]',
                 id='neutral-float-version-accepted'),
    pytest.param('tools/skyguard/neutral.py', '    except RecursionError:', '    except ZeroDivisionError:',
                 'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[deep-request]',
                 id='neutral-deep-nesting-crashes'),
    pytest.param('tools/skyguard/neutral.py', 'raw = raw.decode("utf-8")', 'raw = raw.decode("utf-8", "replace")',
                 'tests/test_skyguard_neutral.py::test_invalid_utf8_is_a_bad_request_not_a_replacement_character',
                 id='neutral-bad-utf8-replaced'),
    # Merge of Plan 5 (2026-10-05): engine.pre_tool now routes kind mcp to the DevBench judge itself (rt-fix-b /
    # Task 17), so dropping this branch still denies `smp reset`; what the branch alone adds is the NOT JUDGED
    # label for a server the rules do not judge. The engine route has its own mutation (codex-mcp-devbench-judged).
    pytest.param('tools/skyguard/neutral.py', '    if req.kind == "mcp":', '    if False:',
                 'tests/test_skyguard_neutral.py::test_an_mcp_call_the_rules_do_not_judge_is_labelled_unjudged_not_evaluated[mcp__other__thing]',
                 id='neutral-mcp-not-judged'),
    pytest.param('tools/skyguard/neutral.py', '    if req.kind == "other":', '    if False:',
                 'tests/test_skyguard_neutral.py::test_kind_other_is_allowed_but_never_reads_as_a_verdict',
                 id='neutral-other-reads-as-a-verdict'),
    pytest.param('tools/skyguard/neutral.py', '    if not isinstance(obj, dict):', '    if False:',
                 'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[mcp-args-not-object]',
                 id='neutral-mcp-args-not-object-accepted'),
    pytest.param('tools/skyguard/neutral.py', '    if agent.strip().lower() in OWN_ENTRY_AGENTS:', '    if False:',
                 'tests/test_skyguard_neutral.py::test_malformed_requests_are_refused_with_the_reason[agent-claims-codex]',
                 id='neutral-agent-may-claim-codex'),
    pytest.param('tools/skyguard/neutral.py',
                 '    return v._replace(reason=text) if text and v.decision == "deny" else v', '    return v',
                 'tests/test_skyguard_neutral.py::test_inert_shell_refusals_name_no_other_agents_hook[no-command]',
                 id='neutral-inert-text-names-protect-bash'),
    pytest.param('tools/skyguard/engine.py', '    if req.agent != "codex":', '    if False:',
                 'tests/test_skyguard_neutral.py::test_a_judged_neutral_shell_call_is_not_logged_as_unhooked',
                 id='neutral-shell-call-logged-as-unhooked'),
    pytest.param('tools/skyguard/engine.py', "the agent's hook could not read this call",
                 'the Codex hook could not read this call',
                 'tests/test_skyguard_neutral.py::test_the_engine_translate_refusal_names_no_agent',
                 id='engine-translate-refusal-names-codex'),
    pytest.param('tools/skyguard/run.py', '    a.role = neutral.TELEMETRY_ROLE', '    pass',
                 'tests/test_skyguard_neutral.py::test_cli_writes_telemetry_when_asked',
                 id='neutral-telemetry-collides-with-codex'),
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
        (r'''if [ "$MS" -ge "$end" ]; then''',
         r'''if false; then''',
         "tests/test_codex_entry.py::test_a_hung_engine_is_a_deny[bash]", "codex-sh-no-deadline"),
        (r'''"$tk" //F //T //PID "$w" >/dev/null 2>&1 </dev/null &''',
         r''':''',
         "tests/test_codex_entry.py::test_a_hung_engine_is_killed_with_its_children[bash]", "codex-sh-kill-by-windows-pid"),
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
        # reading to EOF waits for a grandchild that inherited the pipe; the read must stop at the EXIT
        (r'''        if ! kill -0 "$cpid" 2>/dev/null; then''',
         r'''        if false; then''',
         "tests/test_codex_entry.py::test_a_grandchild_holding_stdout_does_not_outlast_the_deadline[bash]",
         "codex-sh-engine-output-read-to-eof"),
        (r'''_utf8_ok "$OUT" || deny emission "engine output is not valid UTF-8"''',
         r'''true''',
         "tests/test_codex_entry.py::test_invalid_utf8_from_the_engine_never_reaches_codex[pre-tool-bash]",
         "codex-sh-invalid-utf8-passes"),
        (r'''[ -z "$NUL" ] || deny emission "engine output has a NUL byte"''',
         r'''true''',
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
        # (Merge 2026-10-05: named test_rules_file_passes_codex_execpolicy, which SKIPS without `codex` on PATH, so the
        # mutation "survived" by a skip; test_render_shape counts the rendered decisions with no codex needed.)
        ("tools/skyguard/codex_rules.py", 'format(_s(r["decision"]))', 'format(_s("prompt"))',
         "tests/test_codex_rules.py::test_render_shape", "codex-rules-render-wrong-decision"),
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
         r'''PLUGIN_FILE = r"\.(esp|esm|esl|bsa|ba2)[\s.]*" + STREAM + "$"''',
         r'''PLUGIN_FILE = r"\.(esp|esl|bsa|ba2)[\s.]*" + STREAM + "$"''',
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
] + [
    # Plan 7 fix round 2: an occurrence of the authorization marker that is not a clean AUTHORIZED list
    # must void everything. Treated as ignorable, a stray "NOT AUTHORIZED, ever." beside a later AUTHORIZED
    # sentence stops denying, and an unattended install becomes possible.
    pytest.param(
        "tools/inventory-runtimes.py",
        '    return "void", {}, "unrecognised wording: " + body  # unclassified-is-void',
        '    return "template", {}, None',
        "tests/test_inventory_runtimes.py::test_any_unclassifiable_occurrence_voids_a_later_authorization",
        id="inventory-unclassified-authorization-is-not-void"),
    # Round 3: a loose spelling of the marker that is not the exact marker must void everything; skipped, a
    # damaged "Host - software installs: NOT AUTHORIZED." is invisible and a later AUTHORIZED sentence wins.
    pytest.param(
        "tools/inventory-runtimes.py",
        "        if text[m.start():m.start() + len(AUTH_MARKER)].lower() != AUTH_MARKER.lower():",
        "        if False:",
        "tests/test_inventory_runtimes.py::test_a_loose_marker_that_is_not_the_exact_marker_voids_everything",
        id="inventory-loose-marker-is-not-void"),
    # Round 3: format characters inside the marker must be stripped before searching.
    pytest.param(
        "tools/inventory-runtimes.py",
        '    text = "".join(c for c in text if unicodedata.category(c) != "Cf")\n',
        "",
        "tests/test_inventory_runtimes.py::test_normalisation_does_not_break_the_clean_cases",
        id="inventory-format-characters-not-stripped"),
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
    # (Merge 2026-10-05: rt-fix-a's parser resolves the t04-find-exec / t06-resaver-variable-* shapes itself, so these
    # clauses are pinned by the shapes that still reach them; a full test name is used as-is below.)
    ("save-from-segment", 'or (lambda m: m.group(0) if m else "")(RESAVER_TEXT_SAVE.search(e.segment))', 'or ""',
     "test_resaver_save_named_by_the_find_segment"),
    ("opaque-is-unresolved", "    if not parsed.ok or opaque:", "    if not parsed.ok:",
     "test_resaver_text_rule_on_an_opaque_program[opaque-apply]"),
    ("parse-fail-falls-back", "    if not parsed.ok or opaque:", "    if opaque:", "t06-resaver-parse-fail"),
    ("fallback-needs-apply", 'if "resaver" not in low or RESAVER_APPLY not in low:', 'if "resaver" not in low:',
     "test_resaver_text_rule_on_an_opaque_program[opaque-dry]"),
    ("fallback-op-lookbehind", "re.compile(r\"(?<![^\\s\\\"'])(\" + \"|\".join(RESAVER_OPS)",
     "re.compile(r\"(\" + \"|\".join(RESAVER_OPS)", "test_resaver_text_rule_on_an_opaque_program[opaque-op-suffix]"),
    ("fallback-op-lookahead", "+ r\")(?![^\\s\\\"'])\", re.ASCII", "+ r\")\", re.ASCII",
     "test_resaver_text_rule_on_an_opaque_program[opaque-op-in-filename]"),
    # Task 7: decide() parses once (with the dialect) and hands the parse to the ReSaver rule.
    # (Anchor follows Task 17, which made decide() accept the caller's parse; the fallback parse keeps the dialect.)
    ("dialect", "    parsed = shellparse.parse(command, shell) if parsed is None else parsed\n    dels",
     "    parsed = shellparse.parse(command) if parsed is None else parsed\n    dels",
     "t04-heredoc-py"),
]
MUTATIONS += [
    pytest.param(_SR, find, rep, "tests/test_skyguard_shell.py::"
                 + (row if row.startswith("test_") else "test_resaver_rows[" + row + "]"),
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
    # (Task 17 moved the dialect into evaluate_shell's own parse; revert it at the one place it is read.)
    pytest.param("tools/skyguard/engine.py", '    command, shell = req.command, req.shell or ""',
                 '    command, shell = req.command, ""',
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
    ("targets-loopback-share", _TG,
     "if _LOOPBACK.fullmatch(host) or _ipv4_loopback(host) or (me and host.lower() == me):", "if False:",
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
    ("pwsh-command", 'self.reparse(_join_words(rest, "powershell"), "powershell")', "pass",
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
    ("cmd-c", '                    self.reparse(text, "cmd")', "                    pass", "test_bash_shell_strings_reparsed_in_their_dialect"),
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
    ("cd-not-from-a-pipeline", 'if self.SESSION_CD or (sep not in ("|", "|&") and cmd.stage == 0):', "if True:", "test_bash_cd_before"),
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
     'e.program in ALL_SHELLS or (consumer_kind(e.program) == "interpreter" and _reads_code(e.argv)) or' + "\n",
     "e.program in ALL_SHELLS or" + "\n", "test_r3_unreconstructable_stdin_into_a_shell_or_interpreter_is_opaque"),
    ("r3-literal-code-to-body", r'ctx.out[j] = reader._replace(stdin_body="\n".join(texts + strings))', "pass",
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
    # (Merge 2026-10-05: rt-fix-a also expands an alias at its use, which alone passes test_r1_more_wrappers' alias
    # case; each half is pinned by its own test.)
    ("r1-alias", 'self.reparse(body, "bash")              # an alias body runs where the alias is used', "pass",
     "test_r1_an_alias_body_is_judged_where_it_is_defined"),
    ("ra-alias-use", "self._sub_run(body.split(), k)          # RT-A: an alias defined in the same command", "pass",
     "test_ra_an_alias_use_runs_its_body_with_the_uses_arguments"),
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
     "        if code and resolved is None and (has(DESTROYER, code) or has(INTERP_DELETE, code) or (",
     "        if code and resolved is None and False and (has(DESTROYER, code) or has(INTERP_DELETE, code) or (",
     "python-rmtree"),
    ("rule-S-DEL-INSTALL-interpreter-api", _DL,
     "(has(DESTROYER, code) or has(INTERP_DELETE, code) or (", "(has(DESTROYER, code) or (", "node-rmsync"),
    # clause (b): an UNRESOLVED operand
    ("rule-S-DEL-UNRESOLVED-needs-root-mention", _DL, "    if unresolved_any and names_root(command, roots, has):",
     "    if unresolved_any:", "tmp-var"),
    ("rule-S-DEL-UNRESOLVED-needs-unresolved", _DL, "    if unresolved_any and names_root(command, roots, has):",
     "    if names_root(command, roots, has):", "grep-control"),
    ("rule-S-DEL-UNRESOLVED-raw-glob", _DL,
     "            if raw_reaches(w, lit_base, e.dialect, roots) and unresolved_reach is None:",
     "            if False:", "glob-middle"),
    ("rule-S-DEL-UNRESOLVED-short-name", _DL,
     '    return bool(m) and re.sub(r"[\\s.]", "", comp).startswith(m.group(1))', "    return False", "short-name"),
    ("rule-S-DEL-UNRESOLVED-pipe-input", _DL,
     '    if not words and e.stdin_from == "pipe":' + "\n" + '        return [UNRESOLVED]',
     "    if False:" + "\n" + "        return [UNRESOLVED]", "pipe-remove-item"),
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

# ---- RT-A: S-DEL-UNKNOWN-DIR and S-SHELL-UNREAD; each named twin must turn the intended rows red.
_IR = "tests/test_guard_corpus.py::test_intended_rows_reproduce"
MUTATIONS += [
    pytest.param(_DL, "            if cd_lost and unknown_dir is None and _everything(w):", "            if False:", _IR,
                 id="rule-S-DEL-UNKNOWN-DIR-whole-folder"),
    pytest.param("tools/skyguard/shell_rules.py", "    if parsed.unread:", "    if False:", _IR,
                 id="rule-S-SHELL-UNREAD-denies"),
    pytest.param("tools/skyguard/shellparse.py",
                 "            if (reader.program in ALL_SHELLS or _ps_reads_commands(reader)) and",
                 "            if False and", _IR, id="rule-S-SHELL-UNREAD-pipe-into-shell"),
]

# ---- Plan 5 Task 8: shell writes (E21). One twin per primitive class and per clause; each names the t08 row it must
# turn red, fired in-process by tests/test_skyguard_writes.py::test_t08_row.
_WR = "tools/skyguard/writes.py"
_TGT = "tools/skyguard/targets.py"


def _t08(row):
    return "tests/test_skyguard_writes.py::test_t08_row[t08-" + row + "]"


_WRITE_CLAUSES = [
    # (id, file, find, replace, t08 row that must go red)
    ("rule-S-WRITE-PLUGIN-redirect", _WR, "    out = _redirect_targets(e) + list(tool_write_targets(e))\n",
     "    out = list(tool_write_targets(e))\n", "redirect-esp"),
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
    ("rule-S-DEVBENCH-CRASH-mcp", _MC, "    hit = judge(parts[-1], args, roots, None, env)",
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
    # rt-fix-b: a deny-listed line anywhere in a DevBench call denies (the papyrus-tool ruling moved to SENDS); the
    # console tool's own clause now only decides which strings are console SAVE lines.
    ("rule-S-DEVBENCH-CRASH-console-tool", _MC, '    if tool == "console":', "    if True:",
     "tests/test_skyguard_rtb.py::test_rtb_row[rtb-mcp-papyrus-save-line]"),
    ("rule-S-DEVBENCH-CRASH-string-command", _MC, "    elif isinstance(obj, (list, tuple)):", "    elif False:",
     "tests/test_skyguard_rtb.py::test_rtb_row[rtb-mcp-command-list]"),
    ("rule-S-DEVBENCH-CRASH-dict-input", _MC, "    for s in _strings(args):",
     "    for s in (_strings(args) if isinstance(args, dict) else ()):",
     _T11 + "test_a_non_dict_tool_input_is_judged_on_its_strings"),
    ("rule-S-DEVBENCH-CRASH-devbench-only", _MC, ' and is_devbench_server("__".join(parts[1:]))', "",
     _T11 + "test_only_devbench_servers_are_judged[mcp__other__console]"),
    # (Merge 2026-10-05: back on decide()'s own clauses -- Task 17 had moved them into is_devbench(), which decide()
    # no longer calls since rt-fix-b's is_devbench_server; mutating is_devbench() there survived.)
    ("rule-S-DEVBENCH-CRASH-mcp-prefix", _MC, ' and parts[0] == "mcp" and is_devbench_server("__', ' and is_devbench_server("__',
     _T11 + "test_only_devbench_servers_are_judged[xmcp__devbench-vr__console]"),
    ("rule-S-DEVBENCH-CRASH-short-name", _MC, "    return len(parts) >= 3 and parts[0]", "    return parts[0]",
     _T11 + "test_only_devbench_servers_are_judged[mcp__devbenchless]"),
    ("rule-S-DEVBENCH-CRASH-server-name", _MC, 'is_devbench_server("__".join(parts[1:]))',
     '"__".join(parts[1:]).startswith("devbench-vr")',
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
    ("rule-S-DEVBENCH-CRASH-powershell-programs", _MC, '_WEB_PROGRAMS = frozenset(("curl", "wget", "invoke-restmethod", "irm", "invoke-webrequest", "iwr"))',
     '_WEB_PROGRAMS = frozenset(("curl", "wget"))', _T11 + "test_the_powershell_cmdlets_are_read"),
    # the CLI route, per clause
    ("rule-S-DEVBENCH-CRASH-cli-join", _MC, '" ".join(argv[1:])', "argv[1]", _t11row("t11-cli-exec-split-words")),
    ("rule-S-DEVBENCH-CRASH-cli-tool", _MC, '    if argv[0] == "tool" and len(argv) > 2:', "    if False:",
     _t11row("t11-cli-tool-console")),
    ("rule-S-DEVBENCH-CRASH-cli-exec", _MC, '    if argv[0] == "exec" and len(argv) > 1:', "    if False:",
     _t11row("t11-cli-exec-smp-reset")),
    ("rule-S-DEVBENCH-CRASH-cli-program", _MC, '        elif e.program == "devbench-cli":', '        elif e.program == "devbench":',
     _t11row("t11-cli-exec-smp-reset")),
    # not safe by default (R4), per clause
    ("rule-S-DEVBENCH-CRASH-unparsable", _MC, "    textual = not parsed.ok or opaque or bool(", "    textual = False and bool(",
     _t11row("t11-curl-unparsable")),
    ("rule-S-DEVBENCH-CRASH-unparsable-only", _MC, "    textual = not parsed.ok or opaque or bool(", "    textual = opaque or bool(",
     _t11row("t11-curl-unparsable")),
    ("rule-S-DEVBENCH-CRASH-opaque", _MC, "    textual = not parsed.ok or opaque or bool(", "    textual = not parsed.ok or bool(",
     _T11 + "test_an_opaque_program_next_to_the_endpoint_and_the_denied_command_denies"),
    ("rule-S-DEVBENCH-CRASH-text-needs-reset", _MC, " and _TEXT_RESET.search(command) and _TEXT_ENDPOINT.search(_norm_text(command)):",
     " and _TEXT_ENDPOINT.search(_norm_text(command)):",
     _T11 + "test_an_unreadable_command_that_only_names_one_of_them_does_not"),
    ("rule-S-DEVBENCH-CRASH-text-needs-endpoint", _MC, " and _TEXT_RESET.search(command) and _TEXT_ENDPOINT.search(_norm_text(command)):",
     " and _TEXT_RESET.search(command):", _T11 + "test_an_unreadable_command_that_only_names_one_of_them_does_not"),
    ("rule-S-DEVBENCH-CRASH-prefilter", _MC,
     '    if not any(w in low for w in _SHELL_WORDS) and not ("api" in low and "tool" in low):' + "\n        return None",
     "    pass",
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
    # rt-fix-b: `.` and `..` are now emptied by the trailing-dot strip (Windows drops trailing dots), so their own
    # clause was dead and is gone; the strip is what the [dot] / [dotdot] cases now guard.
    ("rule-S-DEVBENCH-SAVE-name-dotdot", _MC, '    n = name.strip().rstrip(" .")', '    n = name.strip().rstrip(" ")',
     _T11 + "test_an_unusable_save_name_asks[dotdot]"),
    ("rule-S-DEVBENCH-SAVE-name-dot", _MC, '    n = name.strip().rstrip(" .")', '    n = name.strip()',
     _T11 + "test_an_unusable_save_name_asks[dot]"),
    ("rule-S-DEVBENCH-SAVE-name-string", _MC, "    if not isinstance(name, str):\n        return None\n    n = name.strip()",
     "    n = str(name).strip()", _T11 + "test_an_unusable_save_name_asks[number]"),
    ("rule-S-DEVBENCH-SAVE-name-strip", _MC, "    n = name.strip()", "    n = name", _T11 + "test_a_save_name_is_read_trimmed"),
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
    ("rule-S-DEVBENCH-SAVE-console", _MC, "        name = _console_save_name(line)",
     "        name = None", _t11row("t11-mcp-console-save-existing")),
    ("rule-S-DEVBENCH-SAVE-console-case", _MC, '        if words and words[0].lower() in SAVE_COMMANDS:',
     '        if words and words[0] in SAVE_COMMANDS:', _T11 + "test_the_console_save_word_is_case_insensitive"),
    ("rule-S-DEVBENCH-SAVE-console-whole-word", _MC, '        if words and words[0].lower() in SAVE_COMMANDS:',
     '        if words and words[0].lower().startswith(SAVE_COMMANDS):', _T11 + "test_the_console_save_word_is_case_insensitive"),
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
    ("rule-S-DEVBENCH-CRASH-matcher", ".claude/settings.json", '"matcher": "mcp__.*[Dd][Ee][Vv][-_. ]?[Bb][Ee][Nn][Cc][Hh].*"', '"matcher": "mcp__other.*"',
     "tests/test_repo_invariants.py::test_the_devbench_mcp_hook_is_registered_under_the_devbench_matcher"),
    ("rule-S-DEVBENCH-CRASH-matcher-narrow", ".claude/settings.json", '"matcher": "mcp__.*[Dd][Ee][Vv][-_. ]?[Bb][Ee][Nn][Cc][Hh].*"',
     '"matcher": "mcp__devbench-vr__console"',
     "tests/test_repo_invariants.py::test_the_devbench_mcp_hook_is_registered_under_the_devbench_matcher"),
    ("rule-S-DEVBENCH-CRASH-setup-configures-it", "setup.sh",
     ".claude/hooks/protect-files.sh .claude/hooks/protect-mcp.sh \\\n", ".claude/hooks/protect-files.sh \\\n",
     "tests/test_repo_invariants.py::test_every_hook_is_configured_by_setup_and_carries_one_placeholder"),
    # Plan 4 moved the canary's hook list into health.py (hook-canary.sh delegates to it).
    ("rule-S-DEVBENCH-CRASH-canary-lists-it", "tools/skyguard/health.py",
     'CLAUDE_HOOKS = ("protect-files", "protect-bash", "protect-mcp", "backup-before-edit",',
     'CLAUDE_HOOKS = ("protect-files", "protect-bash", "backup-before-edit",',
     "tests/test_repo_invariants.py::test_every_gate_that_counts_hooks_agrees_with_the_shipped_hooks"),
    # Plan 8 T10: both release gates read the floor from tools/check-release-payload.py.
    ("rule-S-DEVBENCH-CRASH-release-floor", "tools/check-release-payload.py", "CLAUDE_HOOK_FLOOR = 6", "CLAUDE_HOOK_FLOOR = 5",
     "tests/test_repo_invariants.py::test_every_gate_that_counts_hooks_agrees_with_the_shipped_hooks"),
    ("release-codex-entry-floor", "tools/check-release-payload.py", "CODEX_ENTRY_FLOOR = 2", "CODEX_ENTRY_FLOOR = 1",
     "tests/test_release_payload.py::test_the_hook_floor_is_the_tree_recounted"),
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
    ("rule-S-DEVBENCH-SAVE-env-wired", _MC, "hit = judge(parts[-1], args, roots, None, env)", "hit = judge(parts[-1], args, roots)",
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
                 '"PATHEXT", "TOOLKIT_REQUIRE_HARNESS",',
                 '"PATHEXT",',
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
    # rt-fix-b: Bash and every other command-carrying tool now share one branch; the argv join is that branch's.
    ("tools/skyguard/adapters/codex.py", "        if isinstance(cmd, list):", "        if False:",
     _ADP + "test_an_argv_list_command_is_guarded_as_a_shell_request", "codex-bash-argv-list-unjoined"),
    ("tools/skyguard/adapters/codex.py", "        if isinstance(cmd, list):", '        if isinstance(cmd, list) and name == "Bash":',
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
    mutated = text.replace(find, replace)
    # A mutant that does not COMPILE is killed by the SyntaxError, whatever the named test
    # asserts -- so it proves nothing about the clause. Final review D3 found one
    # (a mis-indented `pass`), killed only that way while a correct revert survived.
    if relpath.endswith(".py"):
        try:
            compile(mutated, relpath, "exec")
        except SyntaxError as exc:
            raise AssertionError(f"mutation of {relpath} does not compile ({exc}); it would be killed by "
                                 f"the syntax error, not by the test it names.\n  {replace!r}")
    io.open(target, "w", encoding="utf-8", newline="").write(mutated)
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
     "        if not isinstance(cmd, str):\n            # Merge of Plans 4+5",
     "        if False:\n            # Merge of Plans 4+5",
     _MERGE + "test_skyguard_codex_adapter.py::test_a_scalar_non_string_command_is_refused_before_the_rules[exec_command-int]",
     "merge-codex-scalar-command-allowed"),
    # engine: Plan 5's cwd and dialect must reach the rules through the Plan 4 engine.
    ("tools/skyguard/engine.py", 'cwd=req.cwd or "", shell=shell, parsed=parsed))', 'shell=shell, parsed=parsed))',
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
    ("tools/skyguard/health.py", "    return tool in CLAUDE_COVERED_TOOLS or (isinstance(tool, str) and CLAUDE_COVERED_MCP.fullmatch(tool) is not None)",
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
    (_ST, "        _replace_retrying(tmp, str(path))", '        open(str(path), "wb").write(payload)',
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
    (_EN, "    v = side_effects(req, project, audit, v, roots_fn, deadline)\n    return shadow(", "    v = v\n    return shadow(",
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
    ("tools/skyguard/run.py", "audit, telemetry=a.telemetry,", "audit,",
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
    (_SS, "                    if not st.rejected and st.lifts:", "                    if False:",
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
    # The ONLY clause holding a direct lift rule in shadow mode: engine.shadow's carried-refusal step reads Verdict.never
    # alone (_carried), so it no longer silently backstops this one (it did after A-C1, and this twin survived).
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
    # Merge of master into plan6: Rename-Item and truncate are read by writes.py / the parser now (self_rules' own
    # branches were dropped as duplicates), so these two twins break THAT route and name the same Plan 6 rows.
    ("tools/skyguard/shellparse.py", "                self._rename_as_move(e)", "                pass",
     _p12row("guard-hook-rename-item"), "rule-S-SELF-GUARD-rename-item"),
    (_SF, '    elif p == "ln":', "    elif False:", _p12row("guard-hook-ln"), "rule-S-SELF-GUARD-ln"),
    ("tools/skyguard/writes.py", '    if p == "truncate":', "    if False:", _p12row("guard-hook-truncate"),
     "rule-S-SELF-GUARD-truncate"),
    (_SF, '    elif p in ("chmod", "chown", "chgrp"):', "    elif False:", _p12row("guard-hook-chmod"), "rule-S-SELF-GUARD-chmod"),
    (_SF, "        if any(w.lower().startswith(_ICACLS_WRITE) for w in argv[1:]):", "        if True:",
     _p12row("allow-hook-icacls-read"), "rule-S-SELF-GUARD-icacls-read-allowed"),
    (_SF, '    elif p == "icacls" and argv', '    elif False and argv', _p12row("guard-hook-icacls-deny"),
     "rule-S-SELF-GUARD-icacls"),
    # Merge of master into plan6: inside the install S-GIT-DISCARD now asks first, so the git route is shown from the
    # home folder (the rows p6t12-guard-git-* moved to the ask).
    (_SF, "        out += [(w, True) for w in _git_restore(argv)]", "        pass",
     _SFT + "test_a_git_restore_of_a_guard_file_outside_the_install_is_the_guard_rule[checkout]",
     "rule-S-SELF-GUARD-git-checkout"),
    (_SF, '    if sub == "restore":', "    if False:",
     _SFT + "test_a_git_restore_of_a_guard_file_outside_the_install_is_the_guard_rule[restore]",
     "rule-S-SELF-GUARD-git-restore"),
    (_SF, '(STATE if text_kind(w) == STATE else "")', '""', _p12row("state-unresolved-var"),
     "rule-S-SELF-STATE-unresolved-fails-closed"),
    # ---- the wiring: shell, write, shadow, the patch Move
    (_SR6, "    if own and own[0] == self_rules.STATE:", "    if False:", _p12row("state-redirect"), "rule-S-SELF-STATE-wired"),
    (_SR6, '    if own:\n        advise("S-SELF-GUARD", file=own[1], op=own[2])', "    if False:\n        pass",
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
    (_SF, "        hit = _recycle_delete(command, parsed, cwd, env, roots)",'        hit = ""', _p12row("state-del-recycle"),
     "rule-S-SELF-STATE-delete-recycle-bin"),
    (_SF, "    if code and (grep(deletes.INTERP_DESTROYER, code) or delete_api):", "    if False:",
     _p12row("state-del-python"), "rule-S-SELF-STATE-delete-interpreter"),
    # Re-anchored in the final fix pass (lane A C1 put the raw-unlock check between this and the deletes).
    (_SR6, '    if own and own[0] == self_rules.STATE:\n        return deny("S-SELF-STATE", own[1])\n    # Plan 6 final fix',
     '    if own and own[0] == self_rules.STATE and not dels.hit:\n        return deny("S-SELF-STATE", own[1])\n'
     '    # Plan 6 final fix',
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
    (_BK, '_shown_stamp(dest, e["ts"]), dest, e["src"],', '_shown_stamp(dest, e["ts"]), dest, legacy_guess(dest),',
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
    # ---- fix round 1
    # I1: the manifest append without its lock (seek-to-end + write is two steps on the Windows CRT)
    (_BK, '    with _exclusive(Path(str(path) + ".lock")):', "    with contextlib.nullcontext():",
     _BKT + "test_concurrent_manifest_appends_lose_and_tear_nothing", "l2-backup-manifest-lock"),
    # ... and a lock that waits forever instead of failing the manifest line
    (_BK, "        deadline = time.monotonic() + LOCK_WAIT", "        deadline = time.monotonic() + 5",
     _BKT + "test_a_manifest_lock_that_cannot_be_had_in_time_keeps_the_backup", "l2-backup-manifest-lock-bounded"),
    # I2: the O_EXCL placeholder stays under the final name when os.replace fails
    (_BK, "                _drop_placeholder(cand, mine)\n", "                pass\n",
     _BKT + "test_a_failed_replace_in_the_fallback_leaves_no_placeholder", "l2-backup-excl-fallback-cleanup"),
    (_BK, "        if st.st_size == 0 and (st.st_ino", "        if (st.st_ino",
     _BKT + "test_a_placeholder_someone_else_filled_is_not_removed", "l2-backup-placeholder-still-empty"),
    (_BK, " and (st.st_ino, st.st_dev) == (mine.st_ino, mine.st_dev):", ":",
     _BKT + "test_a_placeholder_someone_else_replaced_is_not_removed", "l2-backup-placeholder-same-file"),
    # M1-M6
    (_BK, "    return (m.group(1), int(m.group(2) or 0)) if m else (ts, 0)", "    return (m.group(0), 0) if m else (ts, 0)",
     _BKT + "test_a_burst_lists_newest_first", "l2-restore-burst-order"),
    (_BK, r'_NOT_IN_NAME = "/\\:"', r'_NOT_IN_NAME = "/\\"',
     _BKT + "test_a_manifest_dest_that_is_not_a_plain_name_is_counted_bad[C:x.esp]", "l2-restore-dest-plain-name"),
    (_BK, "            parts += 1\n", "            pass\n",
     _BKT + "test_orphaned_part_files_are_counted", "l2-restore-orphan-parts"),
    (_BK, '    if e is not None and not to and not os.path.isabs(_native(e["src"])):', "    if False:",
     _BKT + "test_a_relative_recorded_path_needs_an_explicit_target", "l2-restore-relative-src"),
    (_BK, '        kind = "lost" if (first', '        kind = "legacy" if (first',
     _BKT + "test_a_manifest_era_backup_without_its_line_is_labelled_so", "l2-restore-line-missing-label"),
]]


# Plan 6 Task 14: nested PSC/PEX snapshots without the 7-day window, content-addressed. One twin per clause.
_SN = "tools/skyguard/snapshot.py"
_SNT = "tests/test_snapshot_nested.py::"
_SNR = "tests/test_snapshot_nested.py::test_an_unfinished_baseline_is_partial_and_resumes_on_the_next_call"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # ---- what is walked (the three step-1 REDs, put back one at a time)
    (_SN, 'TREES = ("Data/Scripts", "Data/Source/Scripts")', 'TREES = ("Data/Scripts",)',
     _SNT + "test_the_sse_source_layout_is_snapshotted", "l2-snap-sse-layout"),
    (_SN, '                            stack.append((e.path, rel + e.name + "/"))', "                            pass",
     _SNT + "test_a_nested_pex_is_snapshotted", "l2-snap-nested"),
    (_SN, "elif e.name[-4:].lower() in EXTS and e.is_file():",
     "elif e.name[-4:].lower() in EXTS and e.is_file() and time.time() - e.stat().st_mtime < 7 * DAY:",
     _SNT + "test_a_psc_older_than_seven_days_is_snapshotted", "l2-snap-no-age-window"),
    (_SN, "e.name[-4:].lower() in EXTS", "e.name[-4:] in EXTS",
     "tests/test_skyguard_side_effects.py::test_snapshot_stores_every_script_whatever_its_age_or_case",
     "l2-snap-any-case"),
    # ---- the content-addressed store and the index
    (_SN, "    dest = blobs / digest\n", '    dest = blobs / "{}-{}".format(digest, time.time_ns())\n',
     _SNT + "test_identical_bytes_are_stored_once", "l2-snap-content-addressed"),
    (_SN, "    todo = [r for r, st in seen.items() if (files.get(r) or [None, None])[:2] != list(st)]",
     "    todo = list(seen)", _SNT + "test_only_files_whose_entry_changed_are_read_again", "l2-snap-index"),
    (_SN, "    if not (on_disk == copied == want):", "    if False:",
     _SNT + "test_a_file_that_changes_size_while_it_is_copied_is_not_stored", "l2-snap-blob-size"),
    (_SN, "    if failed and name is None:", "    if False:",
     _SNT + "test_a_file_that_changes_size_while_it_is_copied_is_not_stored", "l2-snap-unread-file-logged"),
    (_SN, "    backup._discard(part)\n    return digest", "    return digest",
     _SNT + "test_no_part_file_is_left_in_the_store", "l2-snap-blob-part-cleanup"),
    (_SN, "        if not _blob_size_is(dest, copied) and not _replace(part, dest):   # no hard links",
     "        if False:   # no hard links",
     _SNT + "test_without_hard_links_the_blob_is_still_stored", "l2-snap-blob-no-hard-links"),
    # ---- manifests: full or a delta on one full map
    (_SN, "        out.pop(rel, None)", "        pass",
     _SNT + "test_a_removed_script_is_gone_from_the_next_snapshot", "l2-snap-delta-gone"),
    (_SN, "            if len(changed) + len(gone) <= max(REBASE_MIN, len(bset) // REBASE_SHARE):", "            if False:",
     _SNT + "test_a_small_change_is_a_delta_on_a_full_base", "l2-snap-delta"),
    (_SN, "            if len(changed) + len(gone) <= max(REBASE_MIN, len(bset) // REBASE_SHARE):", "            if True:",
     _SNT + "test_a_large_change_is_written_as_a_new_full_map", "l2-snap-rebase"),
    (_SN, "len(gone) <= max(", "len(gone) < max(",
     _SNT + "test_a_delta_at_the_threshold_stays_a_delta", "l2-snap-rebase-boundary"),
    # ---- when a manifest is written (one twin per clause of the condition)
    (_SN, "(changed and not was_partial)", "changed", _SNR, "l2-snap-resume-writes-no-manifest"),
    (_SN, " or altered or gone", " or gone",
     _SNT + "test_a_real_change_during_a_baseline_is_snapshotted_at_once", "l2-snap-change-during-baseline"),
    (_SN, ' or gone or idx.get("dirty")', ' or idx.get("dirty")',
     _SNT + "test_a_deletion_during_a_baseline_is_snapshotted_at_once", "l2-snap-deletion-during-baseline"),
    (_SN, ' or idx.get("dirty") or (complete', " or (complete",
     _SNT + "test_a_failed_manifest_is_written_by_the_next_call", "l2-snap-owed-manifest"),
    (_SN, '            idx["dirty"] = True', "            pass",
     _SNT + "test_a_failed_manifest_is_written_by_the_next_call", "l2-snap-owed-manifest-recorded"),
    (_SN, " or (complete and was_partial):", " or False:", _SNR, "l2-snap-completed-baseline-manifest"),
    # ---- the time-boxed baseline
    (_SN, "        if i and time.monotonic() >= deadline:", "        if False:", _SNR, "l2-snap-time-box"),
    (_SN, "if i and time.monotonic() >= deadline:", "if time.monotonic() >= deadline:",
     _SNT + "test_every_call_makes_progress_however_small_its_box", "l2-snap-progress-per-call"),
    (_SN, "    if rate and rl.is_file() and not partial_marker.exists():", "    if rate and rl.is_file():", _SNR,
     "l2-snap-resume-ignores-rate-limit"),
    (_SN, '    head = "AUTO-SNAPSHOT PARTIAL" if (unread or truncated) else "AUTO-SNAPSHOT"', '    head = "AUTO-SNAPSHOT"',
     _SNR,
     "l2-snap-partial-in-the-log"),
    (_SN, "snap_files, not complete,", "snap_files, False,", _SNR, "l2-snap-partial-in-the-manifest"),
    # ---- the rate limit
    (_SN, "int(now) - last < rate", "int(now) - last < 0",
     "tests/test_skyguard_side_effects.py::test_snapshot_rate_limit_window_is_60_seconds", "l2-snap-rate-window"),
    (_SN, " and not any(m > rl_ns for _s, m in seen.values())", "",
     "tests/test_skyguard_side_effects.py::test_snapshot_rate_limit_and_newer_psc", "l2-snap-newer-file-looks"),
    (_SN, "        os.utime(str(rl), ns=(walk_ns, walk_ns))", "        pass",
     _SNT + "test_a_file_changed_after_the_walk_started_is_newer_than_the_last_walk", "l2-snap-walk-start-time"),
    (_SN, '    lock = backup._exclusive(store / ".lock", wait=lock_wait)',
     '    lock = backup._exclusive(store / ".lock-mutant", wait=lock_wait)',
     _SNT + "test_the_store_lock_makes_a_concurrent_call_skip", "l2-snap-store-lock"),
    # ---- pruning
    (_SN, "    if complete and (prune_due or", "    if (prune_due or",
     _SNT + "test_an_unfinished_baseline_does_not_prune", "l2-snap-prune-complete-only"),
    (_SN, 'or now - idx.get("pruned", 0) >= PRUNE_EVERY)', "or True)",
     _SNT + "test_pruning_runs_at_most_once_a_day_per_call", "l2-snap-prune-daily"),
    (_SN, "        keep.add(good[-1])", "        pass",
     _SNT + "test_pruning_keeps_the_newest_snapshot_whatever_its_age", "l2-snap-prune-keeps-newest"),
    (_SN, '    keep.update(n for n in good if now - loaded[n].get("t", 0) < KEEP_DAYS * DAY)', "    pass",
     _SNT + "test_pruning_keeps_every_snapshot_younger_than_14_days", "l2-snap-prune-keeps-young"),
    (_SN, "        if new:\n            keep.add(n)", "        if new:\n            pass",
     _SNT + "test_pruning_drops_old_snapshots_but_never_a_paths_only_latest_content", "l2-snap-prune-keeps-latest-copy"),
    (_SN, '            keep.add(m["base"])', "            pass",
     _SNT + "test_pruning_keeps_the_base_of_a_kept_delta", "l2-snap-prune-keeps-base"),
    (_SN, '    if unreadable:\n        audit.log(HOOK, "PRUNE-SKIPPED", "{} unreadable',
     '    if False:\n        audit.log(HOOK, "PRUNE-SKIPPED", "{} unreadable',
     _SNT + "test_an_unreadable_snapshot_stops_blob_pruning", "l2-snap-prune-unreadable-stops"),
    (_SN, '        live.update(v[2] for v in idx["files"].values())', "        pass",
     _SNT + "test_pruning_keeps_the_blob_of_live_content_no_snapshot_holds_yet", "l2-snap-prune-keeps-indexed"),
    (_SN, "and e.name not in live)", "and False)",
     _SNT + "test_pruning_drops_old_snapshots_but_never_a_paths_only_latest_content", "l2-snap-prune-prunes"),
    (_SN, '".part-" in entry.name and now - entry.stat().st_mtime > DAY', '".part-" in entry.name', _SNT + "test_pruning_removes_only_stale_part_files",
     "l2-snap-prune-part-age"),
    (_SN, "e.name not in live) or _stale_part(e, now):", "e.name not in live):",
     _SNT + "test_pruning_removes_only_stale_part_files", "l2-snap-prune-stale-parts"),
    (_SN, "if d.is_dir() and _LEGACY_DIR.match(d.name) and ", "if d.is_dir() and ",
     "tests/test_skyguard_side_effects.py::test_snapshot_prunes_old_legacy_dirs_and_keeps_new", "l2-snap-legacy-dirs-only"),
    # ---- SessionStart
    (_SN, '    if source == "compact":\n        return None', "    if False:\n        return None",
     _SNT + "test_session_start_takes_no_snapshot_on_compact", "l2-snap-session-not-on-compact"),
    (_SN, "box=box, rate=0,", "box=box,", _SNT + "test_session_start_ignores_the_rate_limit",
     "l2-snap-session-ignores-rate-limit"),
    (_SN, "    if box <= 0:", "    if False:",
     _SNT + "test_the_session_start_baseline_never_runs_past_the_engine_budget", "l2-snap-session-no-time-left"),
    ("tools/skyguard/run.py",
     "    box = min(snapshot.BASELINE_BOX, _T0 + _engine_budget() - SNAPSHOT_MARGIN - time.monotonic())",
     "    box = snapshot.BASELINE_BOX",
     _SNT + "test_the_session_start_baseline_never_runs_past_the_engine_budget", "l2-snap-session-budget-cap"),
    ("tools/skyguard/run.py", '        _session_snapshot(a.project, "" if p["empty"] else p["source"], audit)\n', "",
     _SNT + "test_the_session_start_hook_takes_the_baseline[claude]", "l2-snap-session-claude-wired"),
    ("tools/skyguard/run.py", "        _session_snapshot(a.project, req.source, audit)\n", "",
     _SNT + "test_the_session_start_hook_takes_the_baseline[codex]", "l2-snap-session-codex-wired"),
    # ---- fix round 1 (review I1: links and the walk's box)
    (_SN, '    return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE)',
     "    return False", _SNT + "test_a_junction_out_of_the_tree_is_not_followed", "l2-snap-r1-junction-out"),
    (_SN, '    return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE)',
     "    return False", _SNT + "test_a_junction_loop_is_not_followed", "l2-snap-r1-junction-loop"),
    (_SN, '        if linked:\n            audit.log(HOOK, "LINKS"', '        if False:\n            audit.log(HOOK, "LINKS"',
     _SNT + "test_a_junction_out_of_the_tree_is_not_followed", "l2-snap-r1-links-logged"),
    (_SN, '    tail += ", {} linked folder(s) not followed".format(linked) if linked else ""', '    tail += ""',
     _SNT + "test_a_junction_out_of_the_tree_is_not_followed", "l2-snap-r1-links-in-snapshot-line"),
    (_SN, "            if deadline is not None and _walk_clock() >= deadline:\n                return out, linked, True",
     "            if False:\n                return out, linked, True",
     _SNT + "test_the_walk_stops_at_its_time_box_and_deletes_nothing", "l2-snap-r1-walk-box"),
    (_SN, "    gone = [] if truncated else [r for r in files if r not in seen]",
     "    gone = [r for r in files if r not in seen]",
     _SNT + "test_the_walk_stops_at_its_time_box_and_deletes_nothing", "l2-snap-r1-cut-walk-deletes-nothing"),
    (_SN, "if truncated or r in seen}", "if r in seen}",
     _SNT + "test_the_walk_stops_at_its_time_box_and_deletes_nothing", "l2-snap-r1-cut-walk-keeps-unwalked"),
    (_SN, "(unread or truncated)", "unread",
     _SNT + "test_the_walk_stops_at_its_time_box_and_deletes_nothing", "l2-snap-r1-cut-walk-is-partial"),
    # ---- I2: hard links
    (_SN, "    _restat_multilink(root, store, files, seen, audit)\n", "",
     _SNT + "test_a_hard_link_edited_through_its_other_path_is_seen", "l2-snap-r1-restat-multilink"),
    (_SN, "        files[rel] = [seen[rel][0], seen[rel][1], sha, st.st_nlink]",
     "        files[rel] = [seen[rel][0], seen[rel][1], sha, 1]",
     _SNT + "test_a_hard_link_edited_through_its_other_path_is_seen", "l2-snap-r1-record-nlink"),
    # ---- I3: newest first
    (_SN, "    todo.sort(key=lambda r: (-seen[r][1], r))", "    todo.sort()",
     _SNT + "test_a_baseline_reads_the_newest_scripts_first", "l2-snap-r1-newest-first"),
    # ---- the fsync ruling and its two compensations
    (_SN, "backup._copy_hashed(src, part, fsync=False, deadline=deadline)", "backup._copy_hashed(src, part, deadline=deadline)",
     _SNT + "test_blobs_are_not_fsynced_but_the_index_and_manifests_are", "l2-snap-r1-blobs-not-fsynced"),
    (_SN, "        if not _blob_size_is(dest, copied) and not _replace(part, dest):\n            return None\n    except OSError:",
     "        if False:\n            return None\n    except OSError:",
     _SNT + "test_a_torn_blob_is_replaced_not_trusted", "l2-snap-r1-torn-blob-replaced"),
    (_SN, "        if backup._sha256(blob) != sha:", "        if False:",
     _SNT + "test_materialise_refuses_a_damaged_blob", "l2-snap-r1-materialise-verifies-sha"),
    # ---- M4: materialise trusts nothing in a manifest
    (_SN, 'any(p in ("", ".", "..") for p in parts)', "False",
     _SNT + "test_materialise_refuses_a_path_that_leaves_its_target[dotdot]", "l2-snap-r1-materialise-dotdot"),
    (_SN, 'or ":" in rel or', "or",
     _SNT + "test_materialise_refuses_a_path_that_leaves_its_target[drive]", "l2-snap-r1-materialise-drive"),
    (_SN, 'or "\\\\" in rel or', "or",
     _SNT + "test_materialise_refuses_a_path_that_leaves_its_target[backslash]", "l2-snap-r1-materialise-backslash"),
    (_SN, "        if not isinstance(sha, str) or not _BLOB.match(sha) or", "        if",
     _SNT + "test_materialise_refuses_a_malformed_hash", "l2-snap-r1-materialise-hash-shape"),
    (_SN, 'shutil.copyfile(str(store / "blobs" / sha), str(part))\n            os.replace(str(part), str(target))',
     'shutil.copyfile(str(store / "blobs" / sha), str(target))',
     _SNT + "test_materialise_replaces_a_hard_linked_target_instead_of_writing_through",
     "l2-snap-r1-materialise-no-write-through"),
    # ---- M1, M2, M3, M5, M6
    (_SN, "lock_wait=CALL_LOCK_WAIT):", "lock_wait=None):",
     _SNT + "test_a_tool_call_waits_only_briefly_for_the_store_lock", "l2-snap-r1-call-lock-wait"),
    (_SN, "            if late():\n                return _pruned(store, now, audit, 0, stopped=True)",
     "            if False:\n                return _pruned(store, now, audit, 0, stopped=True)",
     _SNT + "test_pruning_stops_at_its_deadline", "l2-snap-r1-prune-deadline"),
    (_SN, 'key=lambda n: (loaded[n].get("t", 0), _order(n)))', "key=_order)",
     _SNT + "test_pruning_orders_snapshots_by_their_recorded_time_not_their_name", "l2-snap-r1-prune-order-by-t"),
    (_SN, '    for d in (store / "manifests", store):', "    for d in ():",
     _SNT + "test_pruning_removes_stale_part_files_beside_manifests_and_index", "l2-snap-r1-prune-other-parts"),
    (_SN, '    elif not (isinstance(idx, dict) and isinstance(idx.get("files"), dict)):', "    elif False:",
     _SNT + "test_an_unreadable_index_stops_blob_pruning", "l2-snap-r1-prune-index-unreadable"),
    (_SN, "                keep.add(n)                           # its base is gone",
     "                pass  # its base is gone",
     _SNT + "test_a_delta_whose_base_is_unreadable_is_kept", "l2-snap-r1-prune-baseless-delta-kept"),
    # ---- gate ruling, condition 3: the hard-link re-check is capped and resumes
    (_SN, "        if done < len(order) and _walk_clock() >= stop:", "        if False:",
     _SNT + "test_the_hard_link_recheck_is_capped_and_resumes_where_it_stopped", "l2-snap-r2-restat-cap"),
    (_SN, "    start = bisect.bisect_right(multi, after) if after else 0", "    start = 0",
     _SNT + "test_the_hard_link_recheck_is_capped_and_resumes_where_it_stopped", "l2-snap-r2-restat-resumes"),
    (_SN, '            audit.log(HOOK, "RESTAT-PARTIAL",', '            (lambda *a: None)(HOOK, "RESTAT-PARTIAL",',
     _SNT + "test_the_hard_link_recheck_is_capped_and_resumes_where_it_stopped", "l2-snap-r2-restat-cap-logged"),
]]

# Plan 6 Task 16: skydoctor. One twin per state and clause, each killed through the named test.
# No twin (behaviourally redundant, said so rather than padded): the expiry clause in _lift_for (collect() has already
# removed every expired lift from a state that verifies, and a rejected state's lifts are never read), and the
# `st.rejected` early return in clear_expired (a rejected State carries no lifts, so nothing would be rewritten anyway).
_DR = "tools/skyguard/doctor.py"
_DRT = "tests/test_skydoctor.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # ---- the line and the exit code
    (_DR, "        if not str(self.reason).strip():", "        if False:",
     _DRT + "test_the_row_constructor_refuses_a_blank_reason", "l2-doctor-row-reason-never-blank"),
    (_DR, "        if self.status not in STATUSES:", "        if False:",
     _DRT + "test_the_row_constructor_refuses_a_blank_reason", "l2-doctor-row-status-is-one-of-three"),
    (_DR, "    if NOT_LIVE in counted:", "    if False:",
     _DRT + "test_exit_codes_not_live_outranks_unknown", "l2-doctor-exit-1-not-live"),
    (_DR, "    if UNKNOWN in counted:", "    if False:",
     _DRT + "test_layer0_a_toolchain_check_that_cannot_run_is_unknown", "l2-doctor-exit-2-unknown"),
    (_DR, "    counted = [r.status for r in rows if r.counted]", "    counted = [r.status for r in rows]",
     _DRT + "test_layer2_the_honest_gap_list_is_printed_and_not_counted", "l2-doctor-exit-gaps-not-counted"),
    # ---- Layer 0 / Layer 1
    (_DR, "    if rc == 0:", "    if rc == 99:",
     _DRT + "test_a_fully_live_install_exits_0_and_every_line_is_live", "l2-doctor-l0-live"),
    (_DR, "    if rc == 1:", "    if rc == 99:",
     _DRT + "test_layer0_a_degraded_toolchain_is_not_live", "l2-doctor-l0-not-live"),
    (_DR, '        elif name == "AGENTS.md" and size > AGENTS_MD_MAX:', "        elif False:",
     _DRT + "test_layer1_agents_md_over_the_budget_is_not_live_and_the_cap_itself_is_live", "l2-doctor-l1-budget"),
    (_DR, '        elif name == "AGENTS.md" and size > AGENTS_MD_MAX:', '        elif name == "AGENTS.md" and size >= AGENTS_MD_MAX:',
     _DRT + "test_layer1_agents_md_over_the_budget_is_not_live_and_the_cap_itself_is_live", "l2-doctor-l1-budget-boundary"),
    (_DR, 'rows.append(Row("Layer 1", name, NOT_LIVE, "missing:', 'rows.append(Row("Layer 1", name, LIVE, "missing:',
     _DRT + "test_layer1_a_missing_instruction_file_is_not_live", "l2-doctor-l1-missing"),
    # ---- Layer 2: one per target state, the mechanism, the disable
    (_DR, "        if fs.ours is not None and fs.readonly:", "        if fs.ours is not None and False:",
     _DRT + "test_a_fully_live_install_exits_0_and_every_line_is_live", "l2-doctor-l2-locked"),
    (_DR, "        lift = None if st.rejected else _lift_for(st.lifts, p.name, now)", "        lift = None",
     _DRT + "test_layer2_a_lifted_file_reads_unlocked_with_its_lift_time", "l2-doctor-l2-lift-until"),
    (_DR, '            and (x.get("files") is None or name.lower() in (f.lower() for f in x["files"]))]',
     "            and True]",
     _DRT + "test_layer2_a_lift_for_another_file_does_not_explain_this_one", "l2-doctor-l2-lift-names-the-file"),
    (_DR, 'label="unlocked (outside skyguard)"))', 'label="unlocked"))',
     _DRT + "test_layer2_a_file_opened_outside_skyguard_says_so", "l2-doctor-l2-outside-skyguard"),
    (_DR, '        elif reason.startswith("multi-link"):', "        elif False:",
     _DRT + "test_layer2_a_multilink_target_is_skipped", "l2-doctor-l2-skipped-multilink"),
    (_DR, "            if name in manifest:", "            if False:",
     _DRT + "test_layer2_a_locked_file_that_vanished_is_missing", "l2-doctor-l2-missing"),
    (_DR, "    elif st.lock_disabled:", "    elif False:",
     _DRT + "test_layer2_a_persistent_disable_is_named_with_its_date", "l2-doctor-l2-disabled-since"),
    (_DR, '    engaged = mstatus != "absent" or marked', "    engaged = True",
     _DRT + "test_layer2_mechanism_and_a_never_applied_lock", "l2-doctor-l2-never-applied"),
    # ---- R9: the one write, and nothing else
    (_DR, "    due = [x for x in state.read(str(sf)).lifts if _expired(x, now)]", "    due = []",
     _DRT + "test_an_expired_lift_is_cleared_and_logged", "l2-doctor-r9-clears-expired"),
    (_DR, '    return not now < min(lift["expires_at"], lift["created"] + state.MAX_LIFT_SECONDS)',
     '    return not now < lift["expires_at"]',
     _DRT + "test_an_expired_lift_is_cleared_and_logged", "l2-doctor-r9-eight-hour-cap"),
    (_DR, '    Audit(str(root)).log("skydoctor", "INFO",', '    (lambda *a: None)("skydoctor", "INFO",',
     _DRT + "test_an_expired_lift_is_cleared_and_logged", "l2-doctor-r9-logs"),
    (_DR, "        if not gone:\n            return []", "        if False:\n            return []",
     _DRT + "test_the_report_writes_nothing_when_nothing_expired", "l2-doctor-writes-nothing"),
    # ---- Guards / Integrity
    (_DR, "    if not off:", "    if True:",
     _DRT + "test_guards_off_names_who_why_and_until", "l2-doctor-guards-off"),
    (_DR, '    if st.rejected:\n        rows.append(Row("Guards"', '    if False:\n        rows.append(Row("Guards"',
     _DRT + "test_a_rejected_state_reads_state_rejected", "l2-doctor-state-rejected"),
    (_DR, "    if differ or missing:", "    if False:",
     _DRT + "test_integrity_an_edited_guard_file_is_named", "l2-doctor-integrity-differs"),
    (_DR, 'return [Row("Integrity", "guard files", UNKNOWN, "no readable',
     'return [Row("Integrity", "guard files", LIVE, "no readable',
     _DRT + "test_integrity_without_a_manifest_is_unknown", "l2-doctor-integrity-no-manifest"),
    # ---- Layer 3
    (_DR, "        if not any(wire.search(c) for c in cmds):", "        if False:",
     _DRT + "test_claude_an_unwired_hook_is_not_live", "l2-doctor-claude-wiring"),
    (_DR, 'HOOK_STATUS = {"HEALTHY": LIVE, "DEGRADED": NOT_LIVE,', 'HOOK_STATUS = {"HEALTHY": LIVE, "DEGRADED": LIVE,',
     _DRT + "test_claude_a_degraded_hook_is_not_live_and_an_unexercised_one_unknown", "l2-doctor-claude-hook-state"),
    (_DR, '        elif data is not None and data.get("disableAllHooks") is True:', "        elif False:",
     _DRT + "test_claude_disable_all_hooks_is_not_live", "l2-doctor-claude-hook-switch"),
    (_DR, '    if overall == "trusted":', '    if overall in ("trusted", "untrusted"):',
     _DRT + "test_codex_untrusted_hooks_are_not_live", "l2-doctor-codex-trust"),
    (_DR, '    elif overall == "unknown":', "    elif False:",
     _DRT + "test_codex_trust_it_cannot_read_is_unknown", "l2-doctor-codex-trust-unknown"),
    (_DR, '    if (Path(root) / ".codex" / "rules" / "skyrim.rules").is_file():', "    if True:",
     _DRT + "test_codex_a_missing_rules_file_is_not_live", "l2-doctor-codex-rules"),
    (_DR, 'COVERAGE_STATUS = {"HEALTHY": LIVE, "DEGRADED": NOT_LIVE,', 'COVERAGE_STATUS = {"HEALTHY": LIVE, "DEGRADED": LIVE,',
     _DRT + "test_codex_coverage_short_is_not_live_and_none_recorded_is_unknown", "l2-doctor-codex-coverage"),
    (_DR, "    if not cov:", "    if False:",
     _DRT + "test_codex_coverage_short_is_not_live_and_none_recorded_is_unknown", "l2-doctor-codex-no-sessions"),
    # ---- the launchers
    ("tools/skydoctor", 'src="${BASH_SOURCE[0]//' + BS * 2 + '//}"', 'src="${BASH_SOURCE[0]}"',
     _DRT + "test_the_bash_launcher_reports_json_and_exits_by_the_worst_line", "l2-doctor-launcher-windows-path"),
    ("tools/skydoctor.cmd", '"%SKYGUARD_PYTHON%" -B "%HERE%skyguard' + BS + 'doctor.py" %*',
     '"%SKYGUARD_PYTHON%" -B "%HERE%skyguard' + BS + 'cli.py" %*',
     _DRT + "test_the_cmd_twin_runs", "l2-doctor-launcher-cmd"),
    # ---- fix round 1 (task-16-review.md): one twin per fix
    (_DR, "    if compared <= 0 and not (differ or missing):", "    if False:",
     _DRT + "test_i1_integrity_is_never_live_on_an_empty_set[empty]", "l2-doctor-fix-i1-empty-manifest"),
    (_DR, "        if not os.path.isabs(rel) and session._inside(base, os.path.realpath(os.path.join(base, rel))):",
     "        if True:",
     _DRT + "test_i1_integrity_is_never_live_on_an_empty_set[outside]", "l2-doctor-fix-i1-out-of-tree-line-not-counted"),
    (_DR, "    if not per:", "    if False:",
     _DRT + "test_i2_a_lock_with_nothing_present_is_not_live", "l2-doctor-fix-i2-zero-of-zero"),
    (_DR, "    elif st.rejected:", "    elif False:",
     _DRT + "test_m1_the_lock_is_not_live_while_a_disable_cannot_be_read", "l2-doctor-fix-m1-unreadable-disable"),
    (_DR, "    except Exception as e:  # noqa: BLE001 -- the one write failing",
     "    except ZeroDivisionError as e:  # noqa: BLE001 -- the one write failing",
     _DRT + "test_i3_a_state_file_that_cannot_be_rewritten_reads_unknown_not_a_crash", "l2-doctor-fix-i3-clear-fails"),
    (_DR, '    except Exception as e:  # noqa: BLE001\n        return [Row(section, "check"',
     '    except ZeroDivisionError as e:  # noqa: BLE001\n        return [Row(section, "check"',
     _DRT + "test_i3_one_failing_layer_reads_unknown_and_hides_no_other", "l2-doctor-fix-i3-per-layer"),
    (_DR, "    except Exception as e:  # noqa: BLE001 -- unknown (2)",
     "    except ZeroDivisionError as e:  # noqa: BLE001 -- unknown (2)",
     _DRT + "test_i3_main_turns_an_unexpected_exception_into_exit_2", "l2-doctor-fix-i3-main-exit-2"),
    (_DR, 'r"(?:^|[/' + BS + '"\'' + BS + 's])' + BS + '.claude/hooks/"', 'r"/' + BS + '.claude/hooks/"',
     _DRT + "test_m2_a_relative_hook_command_is_wired[bare]", "l2-doctor-fix-m2-relative-wiring"),
    (_DR, "        _kill_tree(p)", "        p.kill()",
     _DRT + "test_m3_a_toolchain_timeout_kills_the_whole_tree_by_pid", "l2-doctor-fix-m3-kill-tree-by-pid"),
]]


# The skyguard.sh launcher splits its own path on either slash (found by Task 16's outside run).
MUTATIONS += [
    pytest.param("tools/skyguard.sh", 'src="${BASH_SOURCE[0]//' + BS + BS + '//}"', 'src="${BASH_SOURCE[0]}"',
                 "tests/test_skyguard_lock.py::"
                 "test_the_bash_wrapper_called_by_a_backslash_path_from_elsewhere_runs_its_own_package",
                 id="l2-skyguard-sh-backslash-path"),
    # skydoctor: an empty instruction file is not live (Task 16 re-review).
    pytest.param("tools/skyguard/doctor.py", "        if size == 0:\n", "        if False:\n",
                 "tests/test_skydoctor.py::test_layer1_an_empty_instruction_file_is_not_live",
                 id="l2-doctor-empty-instructions"),
]


# Plan 6 Task 15, part 1: the Task 14 re-review minors that live in snapshot.py (task-14-rereview.md N1-N3, N6).
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SN, "        if late():\n            return _pruned(store, now, audit, 0, stopped=True)\n        loaded[n]",
     "        if False:\n            return _pruned(store, now, audit, 0, stopped=True)\n        loaded[n]",
     _SNT + "test_pruning_loads_no_manifest_once_its_deadline_has_passed", "l2-snap-r3-prune-load-in-box"),
    (_SN, "not isinstance(sha, str) or not _BLOB", "not _BLOB",
     _SNT + "test_materialise_refuses_a_non_string_hash_with_a_valueerror", "l2-snap-r3-materialise-hash-type"),
    (_SN, "    relinked = _sweep_links(root, store, files, seen)\n", "    relinked = 0\n",
     _SNT + "test_a_file_hard_linked_after_it_was_indexed_is_rechecked", "l2-snap-r3-sweep"),
    (_SN, "            seen[rel] = (st.st_size, st.st_mtime_ns)\n            relinked += 1",
     "            relinked += 1",
     _SNT + "test_a_file_hard_linked_after_it_was_indexed_is_rechecked", "l2-snap-r3-sweep-real-stat"),
    (_SN, " or links_changed or relinked:", " or links_changed:",
     _SNT + "test_a_link_count_found_by_the_sweep_is_kept_in_the_index", "l2-snap-r3-sweep-persisted"),
    (_SN, "    except (OSError, UnicodeDecodeError):\n        after = \"\"", "    except OSError:\n        after = \"\"",
     _SNT + "test_a_garbled_restat_cursor_restarts_the_pass", "l2-snap-r3-cursor-garbage"),
    (_SN, "        elif cursor_file.exists():\n            cursor_file.unlink()",
     "        elif False:\n            cursor_file.unlink()",
     _SNT + "test_a_completed_restat_pass_removes_its_cursor", "l2-snap-r3-cursor-unlink"),
]]


# Plan 6 Task 15, part 2: recovery snapshots of the load order, the INIs and the named save. One twin per clause.
# No twin (said so rather than padded): the configured-roots-only rule (nothing probes outside roots, so there is no
# probe to put back) and the "roots unreadable" branch (removing it crashes on None, which the hook's own try logs).
_RC = "tools/skyguard/recovery.py"
_RCT = "tests/test_recovery_snapshots.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # ---- what is snapshotted
    (_RC, '_INI = re.compile(r"^skyrim[^/' + BS * 2 + ']*' + BS + '.ini$", re.IGNORECASE)',
     '_INI = re.compile(r"^skyrim[^/' + BS * 2 + ']*' + BS + '.ini$")',
     _RCT + "test_a_command_snapshots_the_load_order_and_the_game_inis_from_the_configured_roots", "l2-rec-ini-any-case"),
    (_RC, 'LOADORDER_FILES = ("plugins.txt", "loadorder.txt")', 'LOADORDER_FILES = ("plugins.txt",)',
     _RCT + "test_a_command_snapshots_the_load_order_and_the_game_inis_from_the_configured_roots", "l2-rec-loadorder"),
    (_RC, "not snapshot.read_only(command), now,", "True, now,",
     _RCT + "test_a_read_only_command_takes_no_recovery_snapshot[ls -la]", "l2-rec-read-only"),
    # ---- dedup and the rate limit
    (_RC, "        if isinstance(rec, list) and len(rec) > 3 and rec[2] == sha:", "        if False:",
     _RCT + "test_unchanged_content_is_recorded_once", "l2-rec-dedup"),
    (_RC, "        if not full and isinstance(rec, list) and rec[:2] == [st.st_size, st.st_mtime_ns]:", "        if False:",
     _RCT + "test_inside_the_rate_window_an_unchanged_listing_is_not_read", "l2-rec-stat-gate"),
    (_RC, '    full = not (0 <= now - idx.get("checked", 0) < CONFIG_RATE)', "    full = False",
     _RCT + "test_after_the_rate_window_a_rewrite_that_keeps_size_and_mtime_is_seen", "l2-rec-rate-reread"),
    (_RC, '    if full:\n        idx["checked"] = int(now)', '    if False:\n        idx["checked"] = int(now)',
     _RCT + "test_inside_the_rate_window_an_unchanged_listing_is_not_read", "l2-rec-rate-window"),
    (_RC, '    if idx.get("note") != msg:', "    if True:",
     _RCT + "test_an_install_that_declares_no_roots_says_so_once", "l2-rec-note-once"),
    (_RC, '                MANIFEST, lines[0]["snap"]))\n            continue', '                MANIFEST, lines[0]["snap"]))',
     _RCT + "test_a_failed_manifest_append_is_retried_by_the_next_call", "l2-rec-index-after-append"),
    (_RC, "    if last == base:\n        n = 2", "    if False:\n        n = 2",
     _RCT + "test_two_snapshots_in_one_second_get_different_ids", "l2-rec-unique-id"),
    # ---- the named save
    (_RC, '        w = shell_rules.resaver_writes(command, shell or "", parsed)', "        w = None",
     _RCT + "test_resaver_apply_snapshots_the_named_save_and_its_cosave_only", "l2-rec-resaver"),
    (_RC, "    members = [ess] + ([co] if co else [])", "    members = [ess]",
     _RCT + "test_resaver_apply_snapshots_the_named_save_and_its_cosave_only", "l2-rec-cosave"),
    (_RC, "        if base:\n            j = paths.norm_abs(", "        if False:\n            j = paths.norm_abs(",
     _RCT + "test_resaver_by_a_path_relative_to_the_cwd", "l2-rec-resaver-cwd"),
    (_RC, "        if folder:\n            cands.append(", "        if False:\n            cands.append(",
     _RCT + "test_resaver_by_bare_name_finds_it_in_the_saves_folder", "l2-rec-resaver-saves-folder"),
    (_RC, "    name = mcp_rules.save_name(parts[-1], args)", "    name = None",
     _RCT + "test_devbench_game_save_over_an_existing_save_snapshots_it", "l2-rec-devbench-mcp"),
    (_RC, "        for name in mcp_rules.shell_save_names(command, parsed):", "        for name in []:",
     _RCT + "test_devbench_console_save_through_curl_snapshots_it", "l2-rec-devbench-shell"),
    (_RC, "            if n.casefold() == name.casefold():", "            if n == name:",
     _RCT + "test_the_save_snapshot_reads_a_call_exactly_as_the_rule_does[game-case]", "l2-rec-save-name-case"),
    ("tools/skyguard/mcp_rules.py", '        if isinstance(act, str) and act.strip().lower() == "save":',
     '        if act == "save":',
     _RCT + "test_the_save_snapshot_reads_a_call_exactly_as_the_rule_does[game-case]", "l2-rec-save-name-as-judge"),
    ("tools/skyguard/mcp_rules.py", "        return _console_save_name(c) if isinstance(c, str) else None",
     "        return None", _RCT + "test_the_save_snapshot_reads_a_call_exactly_as_the_rule_does[console]",
     "l2-rec-save-name-console"),
    (_RC, "    if all(isinstance(r, list) and r[:2] == [s.st_size, s.st_mtime_ns] for r, s in zip(recs, stats)):",
     "    if False:", _RCT + "test_an_unchanged_save_is_not_copied_twice", "l2-rec-save-unchanged"),
    (_RC, "    if last is not None and 0 <= now - last < SAVE_RATE:", "    if False:",
     _RCT + "test_a_burst_of_runs_on_one_save_keeps_the_state_before_the_first", "l2-rec-save-rate"),
    (_RC, "    if total > SAVE_MAX_BYTES:", "    if False:",
     _RCT + "test_a_save_over_the_size_bound_is_skipped_and_says_why", "l2-rec-save-bound"),
    # ---- the time box: never past the hook's budget, and never silent
    (_RC, "            sha = snapshot._store_blob(blobs, m, deadline=box_end)",
     "            sha = snapshot._store_blob(blobs, m)",
     _RCT + "test_a_save_copy_that_runs_out_of_time_is_partial_never_silent", "l2-rec-save-deadline"),
    (_RC, "    box_end = start + SAVE_BOX if deadline is None else min(deadline, start + SAVE_BOX)",
     "    box_end = start + SAVE_BOX",
     _RCT + "test_a_save_copy_that_runs_out_of_time_is_partial_never_silent", "l2-rec-save-caller-deadline"),
    ("tools/skyguard/backup.py", "                if deadline is not None and _clock() >= deadline:", "                if False:",
     _RCT + "test_a_save_copy_that_runs_out_of_time_is_partial_never_silent", "l2-rec-copy-deadline"),
    (_SN, "    except backup.CopyTimeout:\n        backup._discard(part)\n        raise\n", "",
     _RCT + "test_a_save_copy_that_runs_out_of_time_is_partial_never_silent", "l2-rec-timeout-is-partial"),
    # ---- the state a snapshot stands for, and pruning
    (_RC, "    for e in entries[:mine[-1] + 1]:", "    for e in entries:",
     _RCT + "test_resolve_gives_the_full_state_at_a_snapshot", "l2-rec-resolve-at-id"),
    (_RC, '    kept = {parsed[i]["snap"] for i in newest.values()}', "    kept = set()",
     _RCT + "test_pruning_drops_old_lines_but_keeps_each_paths_newest", "l2-rec-prune-keeps-newest"),
    (_RC, '        days = SAVE_KEEP_DAYS if parsed[i]["kind"] == "save" else KEEP_DAYS', "        days = KEEP_DAYS",
     _RCT + "test_old_save_snapshots_age_out_sooner", "l2-rec-prune-save-days"),
    (_RC, "    if any(e is None for _r, e in keep):", "    if False:",
     _RCT + "test_an_unreadable_manifest_line_stops_blob_pruning_and_is_kept", "l2-rec-prune-unreadable"),
    (_RC, "        if (_BLOB.match(ent.name) and ent.name not in live) or snapshot._stale_part(ent, now):", "        if False:",
     _RCT + "test_pruning_drops_old_lines_but_keeps_each_paths_newest", "l2-rec-prune-blobs"),
    # ---- the hooks: the copy is made by the PreToolUse hook, before the command
    ("tools/skyguard/run.py", "        _recovery(a, p, audit, None if env is None else paths.roots(a.root, a.root_win, env))",
     "        pass", _RCT + "test_the_save_is_copied_by_the_hook_before_the_command_can_change_it[claude-snapshot-before-tool]",
     "l2-rec-claude-wired"),
    ("tools/skyguard/run.py", "        _recovery(a, p, audit, roots)          # Task 15", "        pass  # Task 15",
     _RCT + "test_the_mcp_hook_copies_the_save_before_devbench_overwrites_it", "l2-rec-claude-mcp-wired"),
    ("tools/skyguard/engine.py", "            recovery.run(project, req.command, False, audit,",
     "            (lambda *a, **k: None)(project, req.command, False, audit,",
     _RCT + "test_the_save_is_copied_by_the_hook_before_the_command_can_change_it[codex-pre-tool]", "l2-rec-codex-wired"),
]]


# Plan 6 Task 15, part 3: `skyguard snapshot list | restore` (controller ruling) and per-file restore (re-review N4).
_SC = "tools/skyguard/snapcli.py"
_SCT = "tests/test_snapshot_restore_cli.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SC, "        base = Path(to) if to else _new_folder(project, sid)", "        base = Path(to) if to else Path(project)",
     _SCT + "test_a_config_restore_goes_to_a_new_folder_byte_identical_and_the_live_files_are_untouched",
     "l2-restore-cli-new-folder"),
    (_SC, "        if clash:", "        if False:",
     _SCT + "test_restore_refuses_to_overwrite_a_file_in_the_destination_and_writes_nothing", "l2-restore-cli-no-clobber"),
    (_SC, "            if target.is_file() and backup.take(", "            if False and backup.take(",
     _SCT + "test_in_place_backs_up_what_it_replaces_first", "l2-restore-cli-in-place-backup"),
    (_SC, "        if odd:", "        if False:",
     _SCT + "test_in_place_refuses_a_save_outside_the_saves_folder[outside]", "l2-restore-cli-in-place-names"),
    (_SC, '    if bad:\n        print("REFUSED: {} of the', '    if False:\n        print("REFUSED: {} of the',
     _SCT + "test_one_damaged_blob_refuses_the_whole_restore_and_names_it", "l2-restore-cli-damaged-refuses"),
    (_SC, "    if not wanted:\n        return keys, []", "    if True:\n        return keys, []",
     _SCT + "test_per_file_restore_of_the_good_files_works_beside_a_damaged_one", "l2-restore-cli-per-file"),
    (_SC, "        if not hit:\n            missing.append(w)", "        if False:\n            missing.append(w)",
     _SCT + "test_a_path_that_matches_nothing_is_refused", "l2-restore-cli-path-must-match"),
    (_SC, ' or s.rsplit("/", 1)[-1] == w', "",
     _SCT + "test_in_place_backs_up_what_it_replaces_first", "l2-restore-cli-path-by-name"),
    (_SN, "        if not blob.is_file():\n            bad.append(", "        if False:\n            bad.append(",
     _SCT + "test_per_file_restore_of_the_damaged_file_is_refused", "l2-restore-cli-missing-blob"),
    (_SN, "    if only is not None:\n        files = {", "    if False:\n        files = {",
     _SCT + "test_materialise_restores_only_the_files_asked_for", "l2-restore-materialise-only"),
    ("tools/skyguard/cli.py", '    if cmd == "snapshot":', "    if False:",
     _SCT + "test_the_bash_launcher_lists_and_restores", "l2-restore-cli-wired"),
    ("tools/skyguard/backup.py", '    print("  bash tools/skyguard.sh snapshot list")', "    pass",
     _SCT + "test_the_backup_listing_points_at_the_snapshot_listing", "l2-restore-backup-list-points-at-snapshots"),
]]

# Task 15: the sweep is capped by a path count (time.monotonic ticks every ~15.6 ms on Windows, MEASURED overrun).
MUTATIONS.append(pytest.param(_SN, "        if k >= SWEEP_MAX or _sweep_clock() >= stop:", "        if _sweep_clock() >= stop:",
                              _SNT + "test_the_sweep_is_capped_per_comparison_and_resumes", id="l2-snap-r3-sweep-cap"))


# Plan 6 Task 15 fix round 1 (task-15-review.md): one twin per finding clause.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # I1: the save follows the rule, not the read-only first word
    (_RC, '    if payload_empty or not (command or "").strip():' + "\n        return",
     '    if payload_empty or not (command or "").strip() or snapshot.read_only(command):' + "\n        return",
     _RCT + "test_a_read_only_looking_prefix_does_not_hide_the_save[env-wrapper]", "l2-rec-fr1-i1-prefix"),
    # I2: the save ReSaver WRITES (its out), not the input
    ("tools/skyguard/shell_rules.py", "RESAVER_TEXT_SAVE.findall(e.segment)\n            return t[0], ws[1:] or ws",
     "RESAVER_TEXT_SAVE.findall(e.segment)\n            return t[0], ws[:1]",
     _RCT + "test_resaver_keeps_the_save_it_writes_not_the_one_it_reads", "l2-rec-fr1-i2-out"),
    (_RC, '    return None, ("NEW", "ReSaver writes {}, which does not exist yet -- nothing to keep".format(word))',
     "    return None, None",
     _RCT + "test_resaver_writing_a_new_out_keeps_nothing_and_says_so", "l2-rec-fr1-i2-new-out-logged"),
    (_RC, '    if "*" in word or "?" in word:', "    if False:",
     _RCT + "test_resaver_given_a_pattern_says_it_cannot_keep_one_save", "l2-rec-fr1-m10-pattern"),
]]

_SCF = _SCT + "test_in_place_refuses_"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # I3: --in-place writes only inside the configured roots, only the kind's own files, never the guard's files
    (_SC, "    if recovery._INI.match(name) and parent in {_real(d) for d in roots.config_dirs}:",
     "    if recovery._INI.match(name):", _SCF + "an_ini_outside_the_configured_config_dir[outside-ini]",
     "l2-restore-fr1-i3-config-in-root"),
    (_SC, "    if recovery._INI.match(name) and parent in {_real(d) for d in roots.config_dirs}:",
     "    if parent in {_real(d) for d in roots.config_dirs}:",
     _SCF + "an_ini_outside_the_configured_config_dir[other-file-in-config-dir]", "l2-restore-fr1-i3-config-kind"),
    (_SC, '        ok = name.endswith((".ess", ".skse")) and folder is not None and parent == _real(folder)',
     '        ok = name.endswith((".ess", ".skse"))', _SCF + "a_save_outside_the_saves_folder[outside]",
     "l2-restore-fr1-i3-save-in-root"),
    (_SC, '        ok = name.endswith((".ess", ".skse")) and folder is not None and parent == _real(folder)',
     "        ok = folder is not None and parent == _real(folder)", _SCF + "a_save_outside_the_saves_folder[not-a-save]",
     "l2-restore-fr1-i3-save-kind"),
    (_SC, "        ok = name.endswith(snapshot.EXTS) and inside and", "        ok = name.endswith(snapshot.EXTS) and",
     _SCF + "a_script_snapshot_key_outside_the_script_files[Data/x.pex]", "l2-restore-fr1-i3-script-in-trees"),
    (_SC, "        ok = name.endswith(snapshot.EXTS) and inside and", "        ok = inside and",
     _SCF + "a_script_snapshot_key_outside_the_script_files[Data/Scripts/x.ini]", "l2-restore-fr1-i3-script-kind"),
    (_SC, '_NEVER_DIRS = (".claude", ".codex", ".skyguard")', "_NEVER_DIRS = ()",
     _SCT + "test_in_place_never_writes_inside_the_guards_own_folders_even_when_configured_there",
     "l2-restore-fr1-i3-never-guard-dirs"),
    ("tools/skyguard/shell_rules.py", "    if lift_rules.restore_in_place(parsed):", "    if False:",
     _SCT + "test_the_guard_asks_before_an_in_place_restore[bash tools/skyguard.sh snapshot restore "
     "20261005_120000-config --in-place]", "rule-S-RESTORE-INPLACE-fires"),
    ("tools/skyguard/lift_rules.py", "        if verb and any(_in_place_flag(a) for a in args):",
     "        if verb:", _SCT + "test_the_guard_lets_a_restore_to_a_new_folder_through",
     "rule-S-RESTORE-INPLACE-only-in-place"),
    # M8: an unreadable blob is a refusal
    (_SC, '    except OSError as e:' + "\n" + '        print("REFUSED: a stored copy',
     '    except ZeroDivisionError as e:' + "\n" + '        print("REFUSED: a stored copy',
     _SCT + "test_an_unreadable_stored_copy_is_a_refusal_not_a_traceback", "l2-restore-fr1-m8-unreadable-blob"),
    # I4: whole snapshots or a refusal
    (_RC, "            if e[\"snap\"] in kept and last[e[\"snap\"]] == i:" + "\n" +
     "                need.update(latest.values())",
     "            if e[\"snap\"] in kept and last[e[\"snap\"]] == i:" + "\n" + "                pass",
     _RCT + "test_pruning_never_cuts_into_the_state_a_kept_snapshot_needs", "l2-rec-fr1-i4-prune-keeps-needed"),
    (_RC, '    if len(mine) != first["lines"]:', "    if False:",
     _RCT + "test_a_snapshot_missing_one_of_its_own_lines_is_refused", "l2-rec-fr1-i4-lines-count"),
    (_RC, '    missing = sorted(set(first["state"]) - set(state))', "    missing = []",
     _RCT + "test_a_snapshot_whose_earlier_state_line_is_gone_is_refused", "l2-rec-fr1-i4-state-check"),
]]

# Review M2: a save copy keeps SAVE_RESERVE_BYTES free on its drive.
MUTATIONS.append(pytest.param(_RC, "    if free is not None and free - total < SAVE_RESERVE_BYTES:", "    if False:",
                              _RCT + "test_a_save_that_would_fill_the_drive_is_skipped_and_says_why",
                              id="l2-rec-fr1-m2-reserve"))


# Plan 6 Task 15 fix round 2 (task-15-rereview.md N1-N3).
_SCR = _SCT + "test_the_guard_asks_for_every_spelling_of_an_in_place_restore"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/lift_rules.py",
     '        verb = "snapshot" in args and "restore" in args[args.index("snapshot") + 1:]',
     '        verb = list(args[:2]) == ["snapshot", "restore"]', _SCR + "[option-first]",
     "rule-S-RESTORE-INPLACE-verb-anywhere"),
    ("tools/skyguard/lift_rules.py", '    return len(head) >= 4 and "--in-place".startswith(head)',
     '    return word == "--in-place"', _SCR + "[abbrev-in]", "rule-S-RESTORE-INPLACE-flag-spellings"),
    (_SC, 'prog="skyguard snapshot", add_help=False, allow_abbrev=False)', 'prog="skyguard snapshot", add_help=False)',
     _SCT + "test_the_cli_refuses_an_abbreviated_in_place[--in]", "l2-restore-fr2-no-abbrev-snapshot"),
    ("tools/skyguard/cli.py", 'prog="skyguard guards", add_help=False, allow_abbrev=False)',
     'prog="skyguard guards", add_help=False)',
     _SCT + "test_no_skyguard_subcommand_takes_an_abbreviated_option[guards]", "l2-restore-fr2-no-abbrev-guards"),
    ("tools/skyguard/lock.py", '    sp = sub.add_parser("status", parents=[common], allow_abbrev=False)',
     '    sp = sub.add_parser("status", parents=[common])',
     _SCT + "test_no_skyguard_subcommand_takes_an_abbreviated_option[lock-json]", "l2-restore-fr2-no-abbrev-lock-status"),
    # No twin for the common parent parser's allow_abbrev: a parent only lends its arguments; the subparser's own
    # setting decides (the status subparser's twin above covers it).
    (_RC, '(?:-' + BS + 'd+)?' + BS + 'Z")', '(?:-' + BS + 'd+)?$")',
     _SCT + "test_a_recovery_id_with_a_trailing_newline_is_not_an_id", "l2-rec-fr2-id-anchor"),
]]

# Plan 7 final review (2026-10-05): one mutation per gating or validation clause the fix pass added. Each reverts
# that clause alone, and the test named for it must go red.
_AC, _INV, _T = "tools/adapter_conformance.py", "tools/inventory-runtimes.py", "tests/"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # I1: the agent's hook timeout must clear the adapter's deadline plus the margin, unless a failed hook blocks.
    (_AC, "lambda v: _is_seconds(v) and v >= ADAPTER_TIMEOUT_S + HOOK_TIMEOUT_MARGIN_S)", "lambda v: True)",
     _T + "test_capability_report.py::test_a_hook_timeout_without_margin_over_the_adapter_deadline_demotes_when_a_failed_hook_allows[5]",
     "p7final-report-ignores-hook-timeout"),
    (_AC, "v >= ADAPTER_TIMEOUT_S + HOOK_TIMEOUT_MARGIN_S)", "v >= ADAPTER_TIMEOUT_S)",
     _T + "test_capability_report.py::test_a_hook_timeout_without_margin_over_the_adapter_deadline_demotes_when_a_failed_hook_allows[25]",
     "p7final-report-timeout-has-no-margin"),
    (_AC, '            if leaf == TIMEOUT_GATE and not _is_seconds(value.get("value")):', "            if False:",
     _T + "test_capability_report.py::test_a_hook_timeout_that_is_not_a_positive_number_is_a_problem_and_demotes[banana]",
     "p7final-report-accepts-a-non-numeric-timeout"),
    # I2: the every-tool-mapped leaf gates; every listed tool needs its own decoy; a decoy names a listed tool.
    (_AC, '                           ("pre_tool_hook.every_write_and_shell_tool_mapped", lambda v: v is True)):',
     "                           ):",
     _T + "test_capability_report.py::test_the_every_tool_mapped_leaf_gates_the_status[leaf0]",
     "p7final-report-ignores-tool-mapping"),
    (_AC, "            if tool not in covered:", "            if False:",
     _T + "test_adapter_template.py::test_every_listed_write_tool_needs_its_own_decoy_case",
     "p7final-conformance-one-decoy-covers-every-tool"),
    (_AC, "            elif tool not in profile[key]:", "            elif False:",
     _T + "test_adapter_template.py::test_a_decoy_case_must_name_a_listed_tool_and_expect_block",
     "p7final-conformance-decoy-tool-not-checked"),
    (_AC, '        if not p["write_tools"]:', "        if False:",
     _T + "test_adapter_template.py::test_the_profile_tool_lists_are_validated[bad0-write_tools]",
     "p7final-profile-empty-write-tools"),
    (_AC, '        if set(p["write_tools"]) & set(p["shell_tools"]):', "        if False:",
     _T + "test_adapter_template.py::test_the_profile_tool_lists_are_validated[bad4-both]",
     "p7final-profile-tool-in-both-lists"),
    # I3: the live shell canary is part of the proof.
    (_AC, 'all(proof.get(p.split(".")[1]) is True for p in PROOF_BOOLS)',
     'all(proof.get(p.split(".")[1]) is True for p in PROOF_BOOLS[:2])',
     _T + "test_capability_report.py::test_the_live_shell_canary_gates_the_status[False]",
     "p7final-report-ignores-shell-canary"),
    ("ADAPTING.md", "run `echo x > Data/Canary.esp` through your shell tool", "use your shell tool",
     _T + "test_adapting_claims.py::test_the_live_proof_covers_the_shell_tool_and_a_broken_hook_the_live_session_runs",
     "p7final-doc-drops-shell-canary"),
    ("ADAPTING.md", "**copy's**", "**real install's**",
     _T + "test_adapting_claims.py::test_the_live_proof_covers_the_shell_tool_and_a_broken_hook_the_live_session_runs",
     "p7final-doc-broken-hook-not-live"),
    # I4: the adapter argv is a list; a command must be an argv list.
    (_AC, '        return [sys.executable, str(Path(adapter_dir) / "adapter.py")]',
     '        return (sys.executable + " " + str(Path(adapter_dir) / "adapter.py")).split()',
     _T + "test_adapter_template.py::test_a_temp_or_interpreter_path_with_a_space_or_apostrophe_still_conforms[has space]",
     "p7final-conformance-splits-the-argv"),
    (_AC, "    if command is not None and not (isinstance(command, list) and command",
     "    if False and not (isinstance(command, list) and command",
     _T + "test_adapter_template.py::test_a_command_must_be_an_argv_list_never_a_string_to_split[command1]",
     "p7final-conformance-accepts-a-non-list-command"),
    # M8: duplicate case ids.
    (_AC, "            for i in sorted({i for i in ids if ids.count(i) > 1}, key=str)]", "            for i in []]",
     _T + "test_adapter_template.py::test_duplicate_case_ids_are_a_failed_row_not_a_silent_shadow",
     "p7final-conformance-duplicate-ids-shadow"),
    # I5: the winget allowlist, in the parser and where the command is built; the whole text only.
    (_INV, "            problem = id_problem(im.group(1), im.group(2), im.group(3))", "            problem = None",
     _T + "test_inventory_runtimes.py::test_an_id_outside_the_runtimes_allowlist_authorizes_nothing[node=Contoso.Backdoor==1.0]",
     "p7final-inventory-any-id-authorizes"),
    (_INV, " and not id_problem(runtime, *authorization[runtime]):", ":",
     _T + "test_inventory_runtimes.py::test_the_plan_refuses_an_unlisted_id_even_if_handed_one_directly",
     "p7final-inventory-plan-builds-any-id"),
    (_INV, "        if not m or int(m.group(1)) < PYTHON_FLOOR[1]:", "        if not m:",
     _T + "test_inventory_runtimes.py::test_an_id_outside_the_runtimes_allowlist_authorizes_nothing[python=Python.Python.3.7==3.7.9]",
     "p7final-inventory-python-below-floor"),
    (_INV, '        if version.split(".")[:2] != ["3", m.group(1)]:', "        if False:",
     _T + "test_inventory_runtimes.py::test_an_id_outside_the_runtimes_allowlist_authorizes_nothing[python=Python.Python.3.12==3.11.4]",
     "p7final-inventory-version-not-bound-to-id"),
    (_INV, '    pl.add_argument("--auth-file", help=', '    pl.add_argument("--auth-text")\n    pl.add_argument("--auth-file", help=',
     _T + "test_inventory_runtimes.py::test_the_cli_takes_the_whole_task_text_never_a_chosen_sentence",
     "p7final-inventory-takes-a-chosen-sentence"),
    # M6: a non-DevBench MCP call is labelled unjudged, not evaluated.
    ("tools/skyguard/neutral.py",
     '        if req.mcp_tool not in ("", "unknown") and not mcp_rules.judged_server(req.mcp_tool):', "        if False:",
     _T + "test_skyguard_neutral.py::test_an_mcp_call_the_rules_do_not_judge_is_labelled_unjudged_not_evaluated[mcp__other__thing]",
     "p7final-neutral-unjudged-mcp-reads-evaluated"),
    # M1: the release gate's line continuation.
    (".github/workflows/release.yml", "refs/remotes/origin/master >/dev/null " + BS + "\n            || { echo",
     "refs/remotes/origin/master >/dev/null             || { echo",
     _T + "test_adapter_payload.py::test_the_release_gates_parse_and_lost_no_line_continuation",
     "p7final-release-loses-line-continuation"),
    # M2: the payload check lists what ADAPTING.md names.
    ("tools/check-release-payload.py", '    "tools/adapting/examples/json_deny.slots.py",\n', "",
     _T + "test_adapter_payload.py::test_every_file_adapting_md_names_or_cites_is_checked",
     "p7final-payload-drops-a-worked-example"),
    # M3: the prose under the prompt.
    ("README.md", "It does not install Python, Git, Node or any other system software",
     "It will install dependencies: Python, Git, Node or any other system software",
     _T + "test_setup_prompt.py::test_the_prose_after_each_embedded_prompt_does_not_contradict_it",
     "p7final-readme-promises-installs"),
    # M4: no promised lock, no clashing layer numbers.
    ("docs/safety-philosophy.md", "### Defense 1: Knowledge", "### Layer 1: Knowledge",
     _T + "test_adapting_claims.py::test_the_layer_2_lock_is_not_promised", "p7final-layer-numbers-clash"),
    # M7: the tripwire scans the texts an agent reads.
    ("tests/test_no_host_installs_in_new_files.py", '    "ADAPTING.md",\n    "SETUP_PROMPT.txt",\n', "",
     _T + "test_no_host_installs_in_new_files.py::test_the_scan_covers_every_text_an_agent_reads_as_instructions",
     "p7final-tripwire-skips-the-instructions"),
    # M9: what `python` means off Windows.
    ("ADAPTING.md", "On Linux and macOS that is usually `python3`; on", "On",
     _T + "test_adapting_claims.py::test_the_doc_says_what_python_means_off_windows", "p7final-doc-python-only"),
]]

# Task 17 (gap X4): the DevBench judge on Codex. One mutation per clause, each naming the test that must go red.
# Merge of rt-fix-b (2026-10-05): the route is rt-fix-b's (`if req.kind == "mcp":` in engine._rules), which keeps an ask
# an ask (the Codex render refuses it) and fails closed on unreadable arguments; Task 17's ask-to-advice downgrade is
# gone, so its three downgrade mutations became one that puts the downgrade back.
_T17 = "tests/test_skyguard_codex_adapter.py::"
_T17_ROUTE = '    if req.kind == "mcp" and mcp_rules.is_devbench(req.mcp_tool):\n        return '
_T17_JUDGE = '        return evaluate_mcp(req.mcp_tool, args, audit, roots=_soft_roots(roots_fn) or _paths.Roots("", ""))'
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # Codex stops routing DevBench MCP calls to the judge: smp reset is allowed again.
    ("tools/skyguard/engine.py", '    if req.kind == "mcp":\n        # rt-fix-b', '    if False:\n        # rt-fix-b',
     _T17 + "test_a_devbench_smp_reset_is_denied_on_codex[mcp__devbench-vr__console]", "t17-codex-devbench-not-routed"),
    # An ask outcome is quietly downgraded to an allow on Codex: the overwrite of an existing save runs unconfirmed.
    ("tools/skyguard/engine.py", _T17_JUDGE,
     _T17_JUDGE.replace("        return evaluate_mcp(", "        v = evaluate_mcp(")
     + '\n        return v._replace(decision="allow", reason="", context=v.reason) if v.decision == "ask" else v',
     _T17 + "test_a_devbench_ask_is_refused_with_the_confirmation_on_codex", "t17-codex-devbench-ask-downgraded"),
    # Unreadable arguments are judged as no arguments (an allow) instead of the INERT refusal.
    ("tools/skyguard/engine.py", '            return _failed(INERT_TRANSLATE.format("unreadable MCP arguments"), "codex.TRANSLATE")',
     "            args = {}",
     _T17 + "test_unreadable_devbench_arguments_are_refused_as_inert_on_codex", "t17-codex-devbench-bad-args-allowed"),
    # The judged call is logged as unhooked again (the log would claim nothing guarded it).
    ("tools/skyguard/engine.py", _T17_ROUTE + "False", "    if False:\n        return False",
     _T17 + "test_a_judged_devbench_call_is_not_logged_as_unhooked", "t17-codex-devbench-logged-as-unhooked"),
    # Every MCP server counts as DevBench for the unhooked log, not only DevBench's.
    ("tools/skyguard/mcp_rules.py", 'parts[0] == "mcp" and is_devbench_server("__".join(parts[1:]))', 'parts[0] == "mcp"',
     _T17 + "test_only_devbench_servers_are_judged_on_codex", "t17-is-devbench-matches-every-server"),
    # The underscore spelling of the server name is no longer recognised.
    ("tools/skyguard/mcp_rules.py", '    return "devbench" in re.sub(r"[-_.\\s]", "", server.lower())',
     '    return server.lower().startswith("devbench-")',
     _T17 + "test_a_devbench_smp_reset_is_denied_on_codex[mcp__devbench_vr__console]", "t17-is-devbench-needs-a-hyphen"),
]]


# Task 17 step 2 (D-P4-2): status "partial" on a command the parser could not fully read. One mutation per clause.
_T17E = "tests/test_skyguard_engine.py::"
_T17_PARTIAL = '    if v.status == "evaluated" and (parsed is None or not parsed.ok or any(e.program == "?" for e in parsed.execs)):'
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/engine.py", _T17_PARTIAL,
     '    if v.status == "evaluated" and (parsed is None or any(e.program == "?" for e in parsed.execs)):',
     _T17E + "test_a_parse_failure_makes_the_verdict_partial", "t17-partial-ignores-parse-failure"),
    ("tools/skyguard/engine.py", _T17_PARTIAL,
     '    if v.status == "evaluated" and (parsed is None or not parsed.ok):',
     _T17E + "test_an_opaque_program_makes_the_verdict_partial", "t17-partial-ignores-opaque-program"),
    ("tools/skyguard/engine.py", _T17_PARTIAL,
     '    if (parsed is None or not parsed.ok or any(e.program == "?" for e in parsed.execs)):',
     _T17E + "test_a_failed_status_is_never_lowered_to_partial", "t17-partial-lowers-a-failed-status"),
    ("tools/skyguard/engine.py", _T17_PARTIAL, "    if False:",
     _T17E + "test_a_deny_on_an_unparseable_command_stays_a_deny_and_says_partial", "t17-partial-never-set"),
    # decide() must reuse the parse it is handed: dropping the argument parses twice and can disagree.
    ("tools/skyguard/engine.py", "shell=shell, parsed=parsed))", "shell=shell))",
     _T17E + "test_decide_reuses_the_parse_it_is_handed", "t17-decide-reparses"),
]]


# rt-fix-b (routes red team, 2026-10-04). One mutation per guard clause the branch added; each names the row or test
# that must go red. RTB rows fire in-process from tests/test_skyguard_rtb.py; Codex rows from test_skyguard_codex_routes.
def _rtb(row):
    return "tests/test_skyguard_rtb.py::test_rtb_row[rtb-" + row + "]"


def _cxr(row):
    return "tests/test_skyguard_codex_routes.py::test_codex_route_rows[rt-codex-" + row + "]"


_CXT = "tests/test_skyguard_codex_routes.py::"
_RTT = "tests/test_skyguard_routes.py::"
_CX = "tools/skyguard/adapters/codex.py"
_EN = "tools/skyguard/engine.py"
_PP = "tools/skyguard/patch_paths.py"
_RTB_CLAUSES = [
    # (id, file, find, replace, test that must go red)
    # --- S-WRITE-PLUGIN: non-text writers onto a plugin or archive name (writes.tool_write_targets)
    ("rule-S-WRITE-PLUGIN-tool-writers-wired", _WR, "    out = _redirect_targets(e) + list(tool_write_targets(e))\n",
     "    out = _redirect_targets(e)\n", _rtb("curl-o")),
    ("rule-S-WRITE-PLUGIN-curl-output", _WR, '        out = _opt_values(argv, ("--output",), ("-o",))', "        out = []",
     _rtb("curl-output")),
    ("rule-S-WRITE-PLUGIN-short-cluster-value", _WR, "                if w.endswith(c) and k + 1 < len(argv):",
     "                if False:", _rtb("curl-cluster")),
    ("rule-S-WRITE-PLUGIN-curl-remote-name", _WR,
     '        if _has_flag(argv, ("--remote-name", "--remote-name-all"), "O", _CURL_VALUED):', "        if False:",
     _rtb("curl-remote-name")),
    ("rule-S-WRITE-PLUGIN-curl-valued-letters", _WR, '_CURL_VALUED = "XHdubceAFTwxmrEKoCYyzQPtUD"', '_CURL_VALUED = ""',
     _rtb("curl-xpost")),
    ("rule-S-WRITE-PLUGIN-wget-output", _WR, '        out = _opt_values(argv, ("--output-document",), ("-O",))',
     "        out = []", _rtb("wget-O")),
    ("rule-S-WRITE-PLUGIN-wget-output-wins", _WR, '        if out:\n            return [w for w in out if w != "-"]',
     "        if False:\n            return []", _rtb("wget-stdout")),
    ("rule-S-WRITE-PLUGIN-wget-url-name", _WR, '        return _download_names(argv, ("--directory-prefix",), ("-P",))',
     "        return []", _rtb("wget-url-name")),
    ("rule-S-WRITE-PLUGIN-web-cmdlets", _WR, "    if p in WEB_GET or (", "    if p in () or (", _rtb("iwr-outfile")),
    ("rule-S-WRITE-PLUGIN-bits-transfer", _WR, '    if p == "start-bitstransfer":', "    if False:", _rtb("bits-transfer")),
    ("rule-S-WRITE-PLUGIN-certutil", _WR, '    if p == "certutil" and', "    if False and", _rtb("certutil-urlcache")),
    ("rule-S-WRITE-PLUGIN-bitsadmin", _WR, '    if p == "bitsadmin" and', "    if False and", _rtb("bitsadmin")),
    ("rule-S-WRITE-PLUGIN-truncate", _WR, '    if p == "truncate":', "    if False:", _rtb("truncate")),
    ("rule-S-WRITE-PLUGIN-perl-inplace", _WR, '    if p == "perl" and', "    if False and", _rtb("perl-inplace")),
    ("rule-S-WRITE-PLUGIN-perl-cluster", _WR, 're.fullmatch(r"-[a-hj-zA-Z]*i', 're.fullmatch(r"-i', _rtb("perl-pi")),
    ("rule-S-WRITE-PLUGIN-fsutil", _WR, '    if p == "fsutil" and', "    if False and", _rtb("fsutil-zero")),
    ("rule-S-WRITE-PLUGIN-openssl", _WR, '    if p == "openssl":', "    if False:", _rtb("openssl-out")),
    ("rule-S-WRITE-PLUGIN-7z", _WR, '    if p in ("7z", "7za", "7zr", "7z.exe") and', "    if False and", _rtb("7z-add")),
    ("rule-S-WRITE-PLUGIN-zip", _WR, '    if p == "zip":', "    if False:", _rtb("zip")),
    ("rule-S-WRITE-PLUGIN-tar", _WR, '    if p == "tar" and _tar_creates(argv):', "    if False:", _rtb("tar-create")),
    ("rule-S-WRITE-PLUGIN-tar-creates-only", _WR, '    if p == "tar" and _tar_creates(argv):', '    if p == "tar":',
     _rtb("tar-extract")),
    ("rule-S-WRITE-PLUGIN-tar-bundled", _WR, '        return bool(set(first) & set("cruA"))', "        return False",
     _rtb("tar-bundled")),
    ("rule-S-WRITE-PLUGIN-export-cmdlets", _WR, '    if p in ("export-csv", "export-clixml"):', "    if False:",
     _rtb("export-csv")),
    ("rule-S-WRITE-PLUGIN-ps-method-calls", _WR, "    lit = text_write_targets_ps(command)", "    lit = []",
     _rtb("ps-downloadfile")),
    ("rule-S-WRITE-PLUGIN-interpreter-literals", _WR, "            lit += text_write_targets(code)", "            pass",
     _rtb("python-open-write")),
    ("rule-S-WRITE-PLUGIN-open-write-mode", _WR, "[rbt]*[wax+]\"\"\", re.IGNORECASE)", "?\"\"\", re.IGNORECASE)",
     _rtb("python-open-read")),
    ("rule-S-WRITE-PLUGIN-pathlib", _WR, 're.compile(r"' + BS + "bPath" + BS + '(', 're.compile(r"' + BS + "bNoPath" + BS + '(',
     _rtb("python-pathlib")),
    ("rule-S-WRITE-PLUGIN-node-writefile", _WR, "(?:writeFile|appendFile|", "(?:appendFile|", _rtb("node-writefile")),
    # --- S-WRITE-SAVE: a write, copy or move onto a save in the settings folder
    ("rule-S-WRITE-SAVE-wired", _SHR, "    if wr.save:", "    if False:", _rtb("save-redirect")),
    ("rule-S-WRITE-SAVE-write", _WR, "            if is_save(a, roots):", "            if False:", _rtb("save-redirect")),
    ("rule-S-WRITE-SAVE-copy-onto", _WR, "            if is_save(a_dst, roots):", "            if False:", _rtb("save-cp")),
    ("rule-S-WRITE-SAVE-copy-into-folder", _WR, "                if hit is not None and is_save(",
     "                if False and is_save(", _rtb("save-cp-into-folder")),
    ("rule-S-WRITE-SAVE-interpreter-literal", _WR, '        elif is_save(targets.resolve(word, cwd, "cmd", env), roots):',
     "        elif False:", _rtb("save-python")),
    ("rule-S-WRITE-SAVE-cosave", _WR, 'SAVE_EXTS = (".ess", ".skse")', 'SAVE_EXTS = (".ess",)', _rtb("save-cosave")),
    ("rule-S-WRITE-SAVE-settings-folder-only", _WR, '.endswith(SAVE_EXTS) and targets.zone(abs_path, roots) == "config"',
     ".endswith(SAVE_EXTS)", _rtb("write-save-outside")),
    # --- S-MOVE-ROOT: moving the install or its folders away
    # Merge (2026-10-05): a root moved OUT of its zone now reaches S-MOVE-ROOT through the delete branch too, so this
    # clause is pinned by a move that stays in its zone (no delete hit): Saves renamed beside itself.
    ("rule-S-MOVE-ROOT-wired", _SHR, "    if wr.move_root:                                         # rt-fix-b",
     "    if False:                                         # rt-fix-b", _rtb("move-saves")),
    ("rule-S-MOVE-ROOT-protected-roots", _WR, "    if targets.covers_protected(abs_path, roots):", "    if False:",
     _rtb("move-install")),
    ("rule-S-MOVE-ROOT-data-folder", _WR, '    if _rel(abs_path, roots.install_dirs) == ["data"] and', "    if False and",
     _rtb("move-data-relative")),
    ("rule-S-MOVE-ROOT-saves-folder", _WR, '    return _rel(abs_path, roots.config_dirs) == ["saves"] or', "    return False or",
     _rtb("move-saves")),
    ("rule-S-MOVE-ROOT-mv", _WR, 'MOVE_PROGRAMS = frozenset(("mv", "move-item"))', 'MOVE_PROGRAMS = frozenset(("move-item",))',
     _rtb("move-install")),
    ("rule-S-MOVE-ROOT-move-item", _WR, 'MOVE_PROGRAMS = frozenset(("mv", "move-item"))', 'MOVE_PROGRAMS = frozenset(("mv",))',
     _rtb("move-item")),
    # Merge (2026-10-05): writes.move_sources' own rename-item branch duplicated rt-fix-a's parser (every Rename-Item
    # with a -NewName is also a move-item exec) and was removed; the clause that carries it is the parser's.
    ("rule-S-MOVE-ROOT-rename-item", "tools/skyguard/shellparse.py",
     '            if p == "rename-item" and self.d == "powershell" or p == "rename" and self.d == "cmd":',
     '            if p == "rename" and self.d == "cmd":', _rtb("rename-item")),
    # --- S-GIT-DISCARD: git overwriting a protected working tree
    ("rule-S-GIT-DISCARD-wired", _SHR, "    git = anchored.git_discard(parsed, roots, cwd, env)", '    git = ""',
     _rtb("git-reset-hard")),
    ("rule-S-GIT-DISCARD-checkout-pathspec", _AN,
     '        if "--" in args or any(a in ("-f", "--force") for a in opts):',
     '        if any(a in ("-f", "--force") for a in opts):', _rtb("git-checkout-stash-dir")),
    ("rule-S-GIT-DISCARD-checkout-force", _AN,
     '        if "--" in args or any(a in ("-f", "--force") for a in opts):', '        if "--" in args:',
     _rtb("git-checkout-force")),
    ("rule-S-GIT-DISCARD-new-branch-is-no-path", _AN, '        if any(a in ("-b", "-B", "--orphan") for a in opts):',
     "        if False:", _rtb("git-checkout-new-branch-dotted")),
    ("rule-S-GIT-DISCARD-checkout-pathy", _AN,
     '        return any(_PATHY.search(a) for a in args if not a.startswith("-"))', "        return False",
     _rtb("git-checkout-dot")),
    ("rule-S-GIT-DISCARD-restore-staged-only", _AN, "        return worktree or not staged", "        return True",
     _rtb("git-restore-staged")),
    ("rule-S-GIT-DISCARD-restore-worktree", _AN, "        return worktree or not staged", "        return not staged",
     _rtb("git-restore-both")),
    ("rule-S-GIT-DISCARD-restore-cluster", _AN, 'a.startswith("-") and not a.startswith("--") and "W" in a[1:] for a in opts)',
     "False for a in opts)", _rtb("git-restore-cluster")),
    ("rule-S-GIT-DISCARD-reset-hard", _AN, '        return "--hard" in opts', "        return False", _rtb("git-reset-hard")),
    ("rule-S-GIT-DISCARD-reset-hard-only", _AN, '        return "--hard" in opts', "        return True", _rtb("git-reset-soft")),
    ("rule-S-GIT-DISCARD-switch", _AN, '        return any(a in ("-f", "--force", "--discard-changes") for a in opts)',
     "        return False", _rtb("git-switch-discard")),
    ("rule-S-GIT-DISCARD-checkout-index-cluster", _AN,
     '(a.startswith("-") and not a.startswith("--") and "f" in a[1:])', "False", _rtb("git-checkout-index")),
    ("rule-S-GIT-DISCARD-read-tree", _AN, '        return "-u" in opts and "--reset" in opts', "        return False",
     _rtb("git-read-tree")),
    ("rule-S-GIT-DISCARD-dash-C", _AN, '        if w in ("-C", "--work-tree") and k + 1 < len(argv):', "        if False:",
     _rtb("git-dash-C")),
    ("rule-S-GIT-DISCARD-work-tree-equals", _AN, '        if w.startswith("--work-tree="):', "        if False:",
     _rtb("git-work-tree")),
    ("rule-S-GIT-DISCARD-cd", _AN, "        where = targets.exec_cwd(e, cwd, env)", "        where = targets.normalise_cwd(cwd)",
     _rtb("git-cd")),
    ("rule-S-GIT-DISCARD-config-zone", _AN, 'targets.zone(where, roots) in ("install", "config", "mo2"):',
     'targets.zone(where, roots) in ("install", "mo2"):', _rtb("git-config-dir")),
    ("rule-S-GIT-DISCARD-protected-zones-only", _AN, 'targets.zone(where, roots) in ("install", "config", "mo2"):',
     "True:", _rtb("git-reset-hard-elsewhere")),
    # --- W-SAVE: Edit/Write onto a save
    ("rule-W-SAVE-wired", _WRR, "        if is_save(a, roots):", "        if False:", _rtb("write-save")),
    ("rule-W-SAVE-cosave", _WR, 'SAVE_EXTS = (".ess", ".skse")', 'SAVE_EXTS = (".ess",)', _rtb("write-cosave")),
    ("rule-W-SAVE-settings-folder-only", _WR, '.endswith(SAVE_EXTS) and targets.zone(abs_path, roots) == "config"',
     ".endswith(SAVE_EXTS)", _rtb("write-save-outside")),
    # --- Claude Write with a non-string file_path is no path (W-INERT-NOPATH)
    ("rule-W-INERT-NOPATH-non-string-file-path", "tools/skyguard/adapters/claude.py",
     '        return _sub(fp) if isinstance(fp, str) else ""', "        return _sub(fp)", _rtb("write-path-list")),
    # --- DevBench: what the console actually runs, and where a console line can sit
    ("rule-S-DEVBENCH-CRASH-nul-ends-the-line", _MC, 'command.split("' + BS + 'x00", 1)[0])', "command)", _rtb("mcp-nul")),
    ("rule-S-DEVBENCH-CRASH-invisible-characters", _MC, '    return _INVISIBLE.sub("", command.split(',
     '    return (lambda _p, s: s)("", command.split(', _rtb("mcp-zero-width")),
    ("rule-S-DEVBENCH-CRASH-quoted-statement", _MC, "            s = s[1:-1]", "            pass", _rtb("mcp-quoted")),
    ("rule-S-DEVBENCH-CRASH-any-key", _MC, "    for s in _strings(args):",
     '    for s in ([args.get("command")] if isinstance(args, dict) and isinstance(args.get("command"), str) else []):',
     _rtb("mcp-unknown-key")),
    ("rule-S-DEVBENCH-CRASH-nested-object", _MC, "    elif isinstance(obj, dict):", "    elif False:", _rtb("mcp-nested")),
    ("rule-S-DEVBENCH-SAVE-papyrus-console-functions", _MC, "        if fn in _CONSOLE_FUNCTIONS:", "        if False:",
     _rtb("mcp-papyrus-executecommand-save")),
    ("rule-S-DEVBENCH-SAVE-papyrus-savegame", _MC, '        elif fn == "savegame":', "        elif False:",
     _rtb("mcp-papyrus-savegame")),
    ("rule-S-DEVBENCH-SAVE-console-savegame", _MC, 'SAVE_COMMANDS = ("save", "savegame")', 'SAVE_COMMANDS = ("save",)',
     _rtb("mcp-console-savegame")),
    ("rule-S-DEVBENCH-SAVE-scenario-steps", _MC, "        for stool, sargs in _steps(args):", "        for stool, sargs in ():",
     _rtb("mcp-scenario-step-save")),
    ("rule-S-DEVBENCH-SAVE-scenario-console-step", _MC, '        if isinstance(step.get("console"), str):', "        if False:",
     _rtb("mcp-scenario-console-save")),
    ("rule-S-DEVBENCH-SAVE-trailing-dot", _MC, '    n = name.strip().rstrip(" .")', "    n = name.strip()",
     _rtb("mcp-save-trailing-dot")),
    ("rule-S-DEVBENCH-SAVE-short-name", _MC, ' or re.search(r"~[0-9]", n):', ":", _rtb("mcp-save-short-name")),
    ("rule-S-DEVBENCH-CRASH-server-separators", _MC, 're.sub(r"[-_.' + BS + 's]", "", server.lower())', "server.lower()",
     _T11 + "test_any_devbench_server_name_is_judged[dev-bench]"),
    ("rule-S-DEVBENCH-CRASH-server-case", _MC, '"", server.lower())', '"", server)',
     _T11 + "test_any_devbench_server_name_is_judged[DevBench-vr]"),
    ("rule-S-DEVBENCH-CRASH-percent-encoded-url", _MC, "    u = unquote(word)", "    u = word", _rtb("curl-percent-encoded")),
    ("rule-S-DEVBENCH-CRASH-query-string", _MC, "    out = [(tool, query)] if query else []", "    out = []",
     _rtb("curl-query-string")),
    ("rule-S-DEVBENCH-CRASH-mcp-endpoint", _MC, "        if _MCP_ENDPOINT.search(u):", "        if False:",
     _rtb("curl-mcp-jsonrpc")),
    ("rule-S-DEVBENCH-SAVE-mcp-jsonrpc-params", _MC, "        if rpc and isinstance(args, dict):", "        if False:",
     _rtb("curl-mcp-jsonrpc-save")),
    ("rule-S-DEVBENCH-CRASH-mcp-prefilter", _MC, '_SHELL_WORDS = ("api/tool", "devbench", "/mcp")',
     '_SHELL_WORDS = ("api/tool", "devbench")', _rtb("curl-mcp-body")),
    ("rule-S-DEVBENCH-CRASH-wget-program", _MC, '_WEB_PROGRAMS = frozenset(("curl", "wget", ', '_WEB_PROGRAMS = frozenset(("curl", ',
     _rtb("wget-post-data")),
    ("rule-S-DEVBENCH-CRASH-wget-body", _MC, '    elif e.program == "wget":', "    elif False:", _rtb("wget-post-data")),
    ("rule-S-DEVBENCH-CRASH-dotnet-web-call", _MC, "    textual = not parsed.ok or opaque or bool(_DOTNET_WEB.search(command))",
     "    textual = not parsed.ok or opaque", _rtb("ps-uploadstring")),
    ("rule-S-DEVBENCH-CRASH-cli-call", _MC, '    if argv[0] == "call" and len(argv) > 2:', "    if False:",
     _rtb("cli-call-executecommand")),
    # --- Codex: apply_patch Delete / Move judged as deletes (engine)
    ("codex-patch-delete-judged", _EN, "        if op in DELETE_OPS and roots is not None:", "        if False:",
     _cxr("patch-delete-install")),
    ("codex-patch-move-out-is-a-delete", _EN, 'DELETE_OPS = ("delete", "move_from")', 'DELETE_OPS = ("delete",)',
     _cxr("patch-move-out")),
    ("codex-patch-move-inside-is-a-rename", _EN,
     "            if b is not None and targets.zone(b, roots) == targets.zone(a, roots):", "            if False:",
     _CXT + "test_a_move_inside_the_same_folder_is_a_rename_not_a_delete"),
    ("codex-patch-delete-unresolved", _EN,
     '        if deletes.raw_reaches(path, targets.normalise_cwd(cwd), "cmd", roots):', "        if False:",
     _cxr("patch-delete-unresolved")),
    ("codex-patch-delete-strict-roots", _EN, "    if not any(op in DELETE_OPS for op in req.ops):", "    if True:",
     _CXT + "test_a_patch_delete_refuses_when_the_paths_file_is_unreadable"),
    ("codex-patch-delete-malformed-roots", _EN, "    if roots.malformed:", "    if False:",
     _CXT + "test_a_patch_delete_refuses_when_the_paths_file_has_an_unreadable_value"),
    ("codex-patch-delete-code", "tools/skyguard/codes.py", '    return found[0] if found else "unknown"',
     '    return "unknown"', "tests/test_codex_conformance.py::test_conformance"),
    # --- Codex: MCP routed to the DevBench judge
    ("codex-mcp-devbench-judged", _EN, '    if req.kind == "mcp":', "    if False:", _cxr("mcp-crash")),
    ("codex-mcp-arguments-as-sent", _CX, "mcp_args=json.dumps({} if raw_ti is None else raw_ti, sort_keys=True)",
     "mcp_args=json.dumps(ti, sort_keys=True)", _cxr("mcp-list-input")),
    # --- Codex adapter shapes
    ("codex-deep-nesting-named", _CX, "    except RecursionError:", "    except ZeroDivisionError:",
     _CXT + "test_deep_nesting_is_refused_by_name_not_by_a_crash"),
    ("codex-no-cwd-refused", _CX, '    if not isinstance(d.get("cwd"), str) or not d["cwd"].strip():', "    if False:",
     _CXT + "test_a_malformed_codex_payload_is_refused_with_its_reason"),
    ("codex-non-object-tool-input-refused", _CX,
     "    if raw_ti is not None and not isinstance(raw_ti, dict) and not is_mcp:", "    if False:",
     _CXT + "test_a_malformed_codex_payload_is_refused_with_its_reason"),
    ("codex-workdir-read", _CX, '    if not is_mcp and isinstance(ti.get("workdir"), str) and ti["workdir"].strip():',
     "    if False:", _cxr("workdir")),
    ("codex-patch-tool-case", _CX, 'if name.lower() in ("apply_patch", "applypatch"):', 'if name in ("apply_patch", "applypatch"):',
     _cxr("patch-case")),
    ("codex-unified-exec-cmd-key", _CX, '_COMMAND_KEYS = ("command", "cmd", "chars", "script")',
     '_COMMAND_KEYS = ("command", "chars", "script")',
     _cxr("unknown-cmd-key")),
    ("codex-write-stdin-chars-key", _CX, '_COMMAND_KEYS = ("command", "cmd", "chars", "script")',
     '_COMMAND_KEYS = ("command", "cmd", "script")',
     _cxr("write-stdin")),
    ("codex-patch-under-unknown-key", _CX, "            if body is not None:", "            if False:",
     _cxr("unknown-patch-key")),
    ("codex-write-stdin-poll-is-no-command", _CX, '        if key == "chars" and not cmd.strip():', "        if False:",
     _cxr("write-stdin-poll")),
    # --- shell spellings of apply_patch (patch_paths)
    ("codex-shell-patch-case-insensitive", _PP, '(?=' + BS + 's|$|<)""", re.I)', '(?=' + BS + 's|$|<)""")',
     _cxr("shell-patch-upper")),
    ("codex-shell-patch-quoted-program", _PP, "(?P<q>['\"]?)", "(?P<q>)", _cxr("shell-patch-quoted")),
    ("codex-shell-patch-body-beside-program", _PP, "        if _BODY_LINE.search(cmd) and _PROGRAM_WORD.search(cmd):",
     "        if False:", _cxr("shell-patch-variable")),
    # --- linked-worktree install protection (paths, run)
    ("worktree-main-tree-is-a-game-root", _PA, "    _add(game, main)\n", "",
     _RTT + "test_hooks_run_from_a_worktree_name_the_main_install_as_a_root"),
    ("worktree-main-tree-is-an-install-dir", _PA, "    for v in [root_posix, root_win, main] +", "    for v in [root_posix, root_win] +",
     _RTT + "test_hooks_run_from_a_worktree_still_guard_the_main_install"),
    ("worktree-only-a-linked-worktree", _PA,
     '    if os.path.basename(worktrees).lower() != "worktrees" or os.path.basename(git_dir).lower() != ".git":',
     "    if False:", _RTT + "test_a_dot_git_file_that_is_not_a_linked_worktree_adds_nothing"),
    ("worktree-main-paths-file-read", "tools/skyguard/run.py", "        if main:", "        if False:",
     _RTT + "test_hooks_run_from_a_worktree_read_the_main_trees_paths_file"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _RTB_CLAUSES]


# Merge of rt-fix-a + rt-fix-b into plan5 (2026-10-05). One mutation per clause the merge itself decided.
_MDEL = "tests/test_skyguard_deletes.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # rt-fix-a's move-as-delete: a file moved out of the install is no longer refused.
    ("tools/skyguard/deletes.py", "        if e.program in MOVE_PROGRAMS and resolved is None:", "        if False:",
     _MDEL + "test_moving_a_file_out_of_the_install_is_a_delete[mv]", "merge-rt-move-out-is-a-delete"),
    # The merge's precedence: a root moved away reads as a delete again (S-DEL-INSTALL's "deletes X inside the install").
    ("tools/skyguard/shell_rules.py", "        if wr.move_root:\n            return deny(\"S-MOVE-ROOT\", wr.move_root)\n        rid, file",
     "        rid, file",
     _MDEL + "test_moving_a_root_folder_away_names_the_move_not_a_delete[mv-install]", "merge-rt-move-root-wins-over-delete"),
]]


# 3rd-party drift fix B1: toolchain-check must compare the INSTALLED spriggit with the pin, and derive the SDK it needs
# from the installed version. One twin per clause (each shadows the other if only one is mutated).
MUTATIONS += [
    pytest.param("tools/toolchain-check.sh", 'elif [ "$INSTALLED" = "$PIN" ]; then', 'elif [ -n "$INSTALLED" ]; then',
                 "tests/test_toolchain_check.py::test_older_spriggit_against_the_pin_is_a_mismatch",
                 id="toolchain-check-spriggit-version-must-equal-pin"),
    pytest.param("tools/toolchain-check.sh", '|| [ "${SP_MINOR:-0}" -ge 41 ] 2>/dev/null; then NEED=10; fi', '|| false; then NEED=10; fi',
                 "tests/test_toolchain_check.py::test_pinned_version_on_sdk_9_cannot_work",
                 id="toolchain-check-spriggit-version-sets-needed-sdk"),
]


# Plan 8 T6b: toolchain-check's wrapper rows must prove the wrapper reached its dependency (identity, not
# startability), keep an absent optional dependency optional, and run under the bash running the check (a bare
# `bash` can be WSL's launcher). And automod-cli.sh's .NET 10 build: SDK pin, retarget, refusal, Cli-only build,
# the net10.0 DLL, and the stale-net8 refusal. One twin per clause.
_TC = "tests/test_toolchain_check.py::"
_AM = "tests/test_automod_cli.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/toolchain-check.sh", '''wrapper automod-cli "$ROOT/tools/automod" "Spooky's AutoMod Toolkit"''',
     'wrapper automod-cli "$ROOT/tools/automod" ""', _TC + "test_automod_cloned_but_not_built_is_not_ok",
     "t6b-toolchain-automod-identity"),
    ("tools/toolchain-check.sh", 'wrapper resaver-cli "$ROOT/tools/resaver-cli/ReSaver.jar" "need <op>"',
     'wrapper resaver-cli "$ROOT/tools/resaver-cli/ReSaver.jar" ""',
     _TC + "test_resaver_whose_driver_cannot_compile_is_not_ok", "t6b-toolchain-resaver-identity"),
    ("tools/toolchain-check.sh", '  elif [ ! -e "$dep" ]; then', "  elif false; then",
     _TC + "test_resaver_without_its_jar_is_optional_not_ok", "t6b-toolchain-absent-dependency-is-optional"),
    ("tools/toolchain-check.sh", '  check "$w" "$expect" "$BASH" "$ROOT/tools/$w.sh" --help',
     '  check "$w" "$expect" bash "$ROOT/tools/$w.sh" --help',
     _TC + "test_wrappers_run_under_this_bash_not_a_bare_bash_on_path", "t6b-toolchain-wrappers-run-under-this-bash"),
    ("tools/automod-cli.sh", '"version": "10.0.100", "rollForward"', '"version": "8.0.100", "rollForward"',
     _AM + "test_rebuild_pins_the_net10_sdk_not_8", "t6b-automod-sdk-10-pin"),
    ("tools/automod-cli.sh", '    if grep -qF "$TFM_OLD" "$PROPS"; then', "    if false; then",
     _AM + "test_rebuild_retargets_the_clone_to_net10", "t6b-automod-retarget"),
    ("tools/automod-cli.sh", '    if ! grep -qF "$TFM_OLD" "$PROPS" && ! grep -qF "$TFM_NEW" "$PROPS"; then',
     "    if false; then",
     _AM + "test_rebuild_refuses_a_target_it_does_not_recognise_and_writes_nothing", "t6b-automod-refuses-unknown-target"),
    ("tools/automod-cli.sh", '    "$DOTNET" build "$CLI_PROJ" -c Release', '    "$DOTNET" build "$AUTOMOD" -c Release',
     _AM + "test_rebuild_builds_the_cli_project_only", "t6b-automod-builds-cli-only"),
    ("tools/automod-cli.sh", 'OUT="$CLI_PROJ/bin/Release/net10.0"',
     'OUT="$CLI_PROJ/bin/Release/net8.0"', _AM + "test_a_run_uses_the_net10_build",
     "t6b-automod-runs-the-net10-dll"),
    ("tools/automod-cli.sh", '    if [ -f "$OLD_DLL" ]; then', "    if false; then",
     _AM + "test_an_old_net8_build_is_reported_stale_not_run", "t6b-automod-stale-net8-named"),
]]


# Plan 8 T6b fix round 1 (review I1-I3, M1, M7). AutoMod's pinned commit (move a clean clone, never touch a dirty
# one, undo only our own patch, record and compare the built commit); toolchain-check's AutoMod version row, ReSaver
# lib/ and repo-local Spriggit; esp-verify's repo-local Spriggit and pinned package version; the setup prompt's
# pinned clone. One twin per clause.
_EV = "tests/test_esp_verify.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/automod-cli.sh", '    g checkout -q --detach "$PIN_COMMIT" || return 1', "    true",
     _AM + "test_upgrade_a_clean_clone_at_an_older_commit_is_moved_to_the_pin", "t6b-r1-automod-moves-to-pin"),
    ("tools/automod-cli.sh", '    if [ -n "$dirty" ]; then', "    if false; then",
     _AM + "test_local_changes_stop_the_move_even_when_git_could_carry_them", "t6b-r1-automod-dirty-clone-untouched"),
    ("tools/automod-cli.sh", "    if only_our_patch; then g checkout -q -- Directory.Build.props || return 1; fi", "",
     _AM + "test_upgrade_undoes_only_its_own_patch_before_moving", "t6b-r1-automod-undoes-own-patch"),
    ("tools/automod-cli.sh", '''    [ "$(g show HEAD:Directory.Build.props | sed "s#$TFM_OLD#$TFM_NEW#" | tr -d '\\r')" = "$(tr -d '\\r' < "$PROPS")" ]''',
     "    true", _AM + "test_a_user_edit_to_the_props_file_is_not_mistaken_for_our_patch",
     "t6b-r1-automod-own-patch-is-exact"),
    ("tools/automod-cli.sh", '''    { printf '%s' "$(g rev-parse HEAD)"; [ -n "$(local_changes)" ] && printf ' dirty'; echo; } > "$MARKER" || exit 1''',
     "    true",
     _AM + "test_built_reports_the_pinned_commit_after_a_rebuild", "t6b-r1-automod-records-built-commit"),
    ("tools/automod-cli.sh", '    if [ "$built" != "$PIN_COMMIT" ]; then', '    if false; then',
     _AM + "test_built_from_another_commit_is_a_mismatch", "t6b-r1-automod-built-must-equal-pin"),
    ("tools/automod-cli.sh", '    if [ "$rc" -ne 0 ]; then', "    if false; then",
     _AM + "test_a_failed_build_says_the_first_build_needs_internet", "t6b-r1-automod-failed-build-reported"),
    ("tools/toolchain-check.sh", '  if AM_BUILT=$("$BASH" "$ROOT/tools/automod-cli.sh" --built 2>&1); then',
     "  if AM_BUILT=unchecked; then", _TC + "test_automod_built_from_another_commit_is_degraded",
     "t6b-r1-toolchain-automod-version"),
    ("tools/toolchain-check.sh", '  elif [ -n "$also" ] && ! compgen -G "$also" >/dev/null; then', "  elif false; then",
     _TC + "test_resaver_whose_lib_was_removed_is_not_ok", "t6b-r1-toolchain-resaver-lib"),
    ("tools/toolchain-check.sh", '''elif SP_OUT=$(spriggit_local --version 2>&1); printf '%s' "$SP_OUT" | grep -qE 'Spriggit[^0-9]*[0-9]+\\.[0-9]+'; then''', "elif false; then",
     _TC + "test_a_spriggit_restored_from_the_manifest_is_found", "t6b-r1-toolchain-local-spriggit"),
    # The real `spriggit --version` exits 1: judging the probe by its exit status never finds the local tool.
    ("tools/toolchain-check.sh", '''elif SP_OUT=$(spriggit_local --version 2>&1); printf '%s' "$SP_OUT" | grep -qE 'Spriggit[^0-9]*[0-9]+\\.[0-9]+'; then''',
     "elif spriggit_local --version >/dev/null 2>&1; then",
     _TC + "test_a_spriggit_restored_from_the_manifest_is_found", "t6b-r1-toolchain-local-spriggit-by-output"),
    ("tools/toolchain-check.sh", 'spriggit_local() { (cd "$ROOT" && "$DOTNET" tool run spriggit -- "$@"); }',
     'spriggit_local() { ("$DOTNET" tool run spriggit -- "$@"); }',
     _TC + "test_a_spriggit_restored_from_the_manifest_is_found", "t6b-r1-toolchain-local-spriggit-from-root"),
    ("tools/esp-verify-wrapper.sh", '''elif SP_OUT=$(spriggit_local --version 2>&1); printf '%s' "$SP_OUT" | grep -qE 'Spriggit[^0-9]*[0-9]+\\.[0-9]+'; then''', "elif false; then",
     _EV + "test_a_manifest_installed_spriggit_drives_the_guard", "t6b-r1-espverify-local-spriggit"),
    ("tools/esp-verify-wrapper.sh", '''elif SP_OUT=$(spriggit_local --version 2>&1); printf '%s' "$SP_OUT" | grep -qE 'Spriggit[^0-9]*[0-9]+\\.[0-9]+'; then''',
     "elif spriggit_local --version >/dev/null 2>&1; then",
     _EV + "test_a_manifest_installed_spriggit_drives_the_guard", "t6b-r1-espverify-local-spriggit-by-output"),
    ("tools/esp-verify-wrapper.sh", 'spriggit_local() { (cd "$TOOLKIT_ROOT" && "$DOTNET" tool run spriggit -- "$@"); }',
     'spriggit_local() { ("$DOTNET" tool run spriggit -- "$@"); }',
     _EV + "test_a_manifest_installed_spriggit_drives_the_guard", "t6b-r1-espverify-local-spriggit-from-root"),
    ("tools/esp-verify-wrapper.sh",
     '  plugin="$(cd "$(dirname "$plugin")" && pwd)/$(basename "$plugin")"   # the local tool runs from the toolkit root',
     "", _EV + "test_a_manifest_installed_spriggit_drives_the_guard", "t6b-r1-espverify-absolute-plugin-path"),
    ("tools/esp-verify-wrapper.sh", 'PKGVER="${ESP_VERIFY_PKGVER:-${PIN:-0.41.0}}"', 'PKGVER="${ESP_VERIFY_PKGVER:-0.40.0}"',
     _EV + "test_the_package_version_defaults_to_the_pin", "t6b-r1-espverify-pkgver-is-the-pin"),
    ("SETUP_PROMPT.txt", "at tag v1.11.2 (commit e1c34ea", "(commit e1c34ea",
     "tests/test_automod_docs_pinned.py::test_no_user_facing_copy_clones_automod_unpinned_or_builds_it_bare",
     "t6b-r1-setup-prompt-pinned-clone"),
]]


# Plan 8 T6b fix round 2 (re-review N1-N3): the shipped pin is a constant unless the tests' own flag is set; a
# detached commit nothing else holds is never moved; a build records dirty state and the record's age.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/automod-cli.sh", '    if [ "${TOOLKIT_TEST_MODE:-}" = 1 ]; then', "    if true; then",
     _AM + "test_the_pin_override_is_ignored_without_the_test_flag", "t6b-r2-pin-override-needs-test-mode"),
    ("tools/automod-cli.sh", '    if ! g symbolic-ref -q HEAD >/dev/null && ! held_elsewhere "$head"; then', "    if false; then",
     _AM + "test_a_detached_commit_of_the_users_own_is_never_moved", "t6b-r2-detached-own-commit-kept"),
    ("tools/automod-cli.sh", '''    [ -n "$(g branch -a --contains "$1" 2>/dev/null | grep -v 'HEAD detached')" ]''', "    false",
     _AM + "test_a_detached_commit_kept_on_a_branch_may_be_moved", "t6b-r2-branch-holds-the-commit"),
    ("tools/automod-cli.sh", '''[ -n "$(local_changes)" ] && printf ' dirty';''', '''false && printf ' dirty';''',
     _AM + "test_a_dirty_build_marks_itself_and_is_not_the_pin", "t6b-r2-dirty-build-recorded"),
    ("tools/automod-cli.sh", '    if [ "$files" = "Directory.Build.props" ] && only_our_patch; then files=""; fi', "",
     _AM + "test_our_own_patch_does_not_count_as_dirty", "t6b-r2-own-patch-is-not-dirt"),
    ("tools/automod-cli.sh", '    if [ -n "$state" ]; then', "    if false; then",
     _TC + "test_automod_built_from_a_dirty_tree_is_degraded", "t6b-r2-dirty-build-is-degraded"),
    ("tools/automod-cli.sh", '    if [ "$DLL" -nt "$MARKER" ]; then', "    if false; then",
     _AM + "test_a_dll_newer_than_its_build_record_is_of_unknown_origin", "t6b-r2-dll-newer-than-record"),
    ("tools/automod-cli.sh", '''    built="${built%$'\\r'}"; state="${state%$'\\r'}"   # a record written with CRLF''', "    true",
     _TC + "test_automod_built_from_the_pinned_commit_says_which", "t6b-r2-crlf-record"),
]]


# Plan 5 final review, lane A (parser and path layer) fix pass. One twin per new clause; each names the test that
# must go red. P5A rows fire in-process from tests/test_skyguard_p5_final_a.py.
def _p5a(row):
    return "tests/test_skyguard_p5_final_a.py::test_p5a_row[p5a-" + row + "]"


_P5S = "tests/test_shellparse.py::"
_P5T = "tests/test_targets.py::"
MUTATIONS += [pytest.param(_SP, find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # C1: option values in a cluster
    ("c1-o-takes-a-value", '                    takes = opts.count("o") + opts.count("O")', "                    takes = 0",
     _p5a("c1-eo-pipefail-c")),
    ("c1-O-takes-a-value", '                    takes = opts.count("o") + opts.count("O")',
     '                    takes = opts.count("o")', _P5S + "test_c1_a_shell_option_value_is_not_the_command"),
    ("c1-zsh-attached-value", "                    takes = 0 if o < 0 else 1 if o == len(opts) - 1 else 0",
     "                    takes = 0 if o < 0 else 1", _P5S + "test_c1_a_shell_option_value_is_not_the_command"),
    ("c1-zsh-value-is-not-options", "                    opts = opts if o < 0 else opts[:o]", "                    opts = opts",
     _P5S + "test_c1_a_script_after_option_values_is_the_script"),
    ("c1-plus-c", '                if "c" in opts:', '                if w[0] == "-" and "c" in opts:',
     _P5S + "test_c1_a_shell_option_value_is_not_the_command"),
    # M4: a script that is stdin
    ("m4-shell-stdin-script", "        if not cflag and j < n and v[j] in _STDIN_SCRIPTS:", "        if False:",
     _p5a("m4-bash-dev-stdin")),
    ("m4-source-stdin-script", '                if self.d == "bash" and v[k] in _STDIN_SCRIPTS:', "                if False:",
     _P5S + "test_m4_a_script_that_is_stdin_reads_the_heredoc_or_pipe"),
]]
MUTATIONS += [pytest.param(_SP, find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # I1: PowerShell's location is session-wide
    ("i1-pipeline-stage-cd-carries", "    SESSION_CD = True\n", "    SESSION_CD = False\n",
     _p5a("i1-setlocation-pipe-outnull")),
    ("i1-block-cd-makes-caller-unknown", "        if f.cd != p.cd:", "        if False:", _p5a("i1-setlocation-in-if")),
    ("i1-piped-set-location-is-unknown", '            new = "?" if cmd.stage > 0 else "~"', '            new = "~"',
     _P5S + "test_i1_powershell_location_is_session_wide"),
    # I4: a backtick does not make a command name an expression
    ("i4-resolver-string-quoted", "                strq = self.q[k] if self.strq is None else self.strq[k]",
     "                strq = self.q[k]", _p5a("i4-tick-in-name")),
    ("i4-set-location-string-quoted", "        if not cmd.words or _ps_string(cmd.words[0]) or cmd.words[0].dyn:",
     "        if not cmd.words or cmd.words[0].quoted or cmd.words[0].dyn:",
     _P5S + "test_i4_a_string_in_command_position_is_still_an_expression"),
    ("i4-here-string-is-a-string", '    return r[:1] in _SQ + _DQ or (r[:1] == "@" and r[1:2] in _SQ + _DQ)',
     "    return r[:1] in _SQ + _DQ", _P5S + "test_pwsh_expressions_and_here_strings_are_not_commands"),
]]
MUTATIONS += [pytest.param(_SP, find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # I2: cmd cd takes the rest of the line
    ("i2-cd-joins-the-rest", '                new = " ".join(s for s, _ in parts)', "                new = parts[0][0]",
     _p5a("i2-cmd-cd-unquoted-space")),
    ("i2-cd-glued-switch", "        glued = None if w0.quoted else _CMD_CD_GLUED.fullmatch(first)", "        glued = None",
     _p5a("i2-cmd-cd-glued-switch")),
    ("i2-pushd-not-joined", '            elif p == "pushd" or any(w.dyn for _, w in parts) or any(',
     "            elif False or any(w.dyn for _, w in parts) or any(", _P5S + "test_i2_cmd_cd_takes_the_rest_of_the_line"),
    ("i2-variable-not-joined", '            elif p == "pushd" or any(w.dyn for _, w in parts) or any(',
     '            elif p == "pushd" or any(False for _, w in parts) or any(',
     _P5S + "test_i2_cmd_cd_takes_the_rest_of_the_line"),
    ("i2-gap-not-one-space", '                    self.t[a[1].start + len(a[1].raw):b[1].start] != " " for a, b in',
     "                    False for a, b in", _P5S + "test_i2_cmd_cd_takes_the_rest_of_the_line"),
    ("i2-unknown-glued-switch", '                if lead.lower() != "/d":', "                if False:",
     _P5S + "test_i2_cmd_cd_takes_the_rest_of_the_line"),
]]
MUTATIONS += [pytest.param("tools/skyguard/targets.py", find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    ("i3-glob-in-cd-target-is-unknown", "    if r is not None and any(_has_glob(c, e.dialect)",
     "    if False and any(_has_glob(c, e.dialect)", _p5a("i3-bash-cd-glob")),
    ("i5-splat-is-unresolvable", '    if dialect == "powershell" and _PS_SPLAT.match(word):', "    if False:",
     _p5a("i5-hashtable-splat")),
]]
MUTATIONS += [pytest.param(_SP, find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # I6: a string stage piped into a shell reader or iex
    ("i6-shell-reader-parses-strings", "                for text in texts + strings:", "                for text in texts:",
     _p5a("i6-string-into-pwsh-stdin")),
    ("i6-iex-is-a-reader", '    return e.program == "invoke-expression" and e.dialect == "powershell" and not e.argv',
     "    return False", _p5a("i6-string-into-iex")),
    ("i6-iex-reader-is-parsed", "            elif reader.program in ALL_SHELLS or _ps_reads_commands(reader):",
     "            elif reader.program in ALL_SHELLS:", _p5a("i6-string-into-iex")),
    ("i6-variable-string-is-opaque", "                    if urec.words[0].dyn:", "                    if False:",
     _P5S + "test_i6_a_string_holding_a_variable_piped_into_iex_is_opaque"),
    ("i6-decoded-into-iex-is-unread", "            if (reader.program in ALL_SHELLS or _ps_reads_commands(reader)) and \\",
     "            if reader.program in ALL_SHELLS and \\", _P5S + "test_i6_decoded_text_piped_into_iex_is_unread"),
    # I7: an opaque method call keeps its receiver and arguments
    ("i7-call-arguments-kept", '            if f.kind == "call" and f.io == "?":', "            if False:",
     _P5S + "test_i7_an_opaque_method_keeps_its_receiver_and_arguments"),
    ("i7-receiver-text-kept", '                "?", (self.t[w.start:i],), (False,), (), "powershell", f.cd, "", self.t[w.start:i])))',
     '                "?", (tail.group(1),), (False,), (), "powershell", f.cd, "", self.t[w.start:i])))',
     _P5S + "test_i7_an_opaque_method_keeps_its_receiver_and_arguments"),
    ("i7-delete-deletes-its-receiver", '            if tail.group(1).lower() == "delete":', "            if False:",
     _p5a("i7-delete-method")),
]]
MUTATIONS += [pytest.param(_SP, find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # M1, M2
    ("m1-encodedcommand-any-prefix", '                if name == "ec" or (name and "encodedcommand".startswith(name)):',
     '                if name in ("e", "ec") or (len(name) >= 3 and "encodedcommand".startswith(name)):',
     _p5a("m1-en-prefix")),
    ("m2-nice-attached-n", 'r"-[0-9]{1,3}|-n.+"', 'r"-[0-9]{1,3}"', _p5a("m2-nice-attached-n")),
    ("m2-nice-adjustment-takes-a-value", '                if k < n and v[k] in ("-n", "--adjustment"):',
     '                if k < n and v[k] == "-n":', _P5S + "test_m2_nice_value_spellings"),
    ("m2-nice-double-dash", "                    k += 1                                      # M2: `-n10` carries its value\n"
     '                if k < n and v[k] == "--":\n                    k += 1\n',
     "                    k += 1                                      # M2: `-n10` carries its value\n",
     _P5S + "test_m2_nice_value_spellings"),
]]
MUTATIONS += [pytest.param(_SP, find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # coordinator ruling: cmd's /c quote rule, and nested quotes split by the caller fail closed
    ("cmdq-split-nested-quotes-opaque", "                if len(rest) > 1 and line.lstrip().startswith('\"'):",
     "                if False:", _p5a("cmdq-s-nested-quotes")),
    ("cmdq-readings-reparsed", "                for text in _cmd_c_lines(line, s_flag):", "                for text in [line]:",
     _p5a("cmdq-s-quoted-line")),
    ("cmdq-s-switch-detected", '            if head == "/s":', "            if False:",
     _P5S + "test_cmd_c_s_switch_always_strips"),
    ("cmdq-s-switch-strips", "    if s_flag:\n        return [stripped]\n", "", _P5S + "test_cmd_c_quote_rule"),
    ("cmdq-ambiguous-both-readings", "        return [stripped, s]", "        return [stripped]",
     _P5S + "test_cmd_c_quote_rule"),
    ("cmdq-two-quotes-only", "    if s.count('\"') == 2 and inner and", "    if inner and", _P5S + "test_cmd_c_quote_rule"),
    ("cmdq-special-char-strips", " and not (set(inner) & _CMD_QUOTE_SPECIAL) and", " and", _P5S + "test_cmd_c_quote_rule"),
    ("cmdq-no-whitespace-strips", 'and re.search(r"\\s", inner):', "and True:", _P5S + "test_cmd_c_quote_rule"),
]]
MUTATIONS += [pytest.param("tools/skyguard/targets.py", find, rep, test, id="p5a-" + i) for i, find, rep, test in [
    # M5 (coordinator ruling): a drive-relative word resolves only when it provably can
    ("m5-drive-relative-modelled", "            return _drive_relative(s, base, dialect)",
     "            return paths.norm_abs(s)", _p5a("m5-pwsh-drive-relative-operand")),
    ("m5-bash-bare-drive-is-root", '        return None if rest else drive + "/"', "        return None",
     _p5a("m5-bash-cd-drive")),
    ("m5-other-drive-unknown", "    if not base or base[:2].lower() != drive:", "    if not base:",
     _P5T + "test_m5_drive_relative_words"),
    ("m5-name-joined-on-cwd", "    return paths.norm_abs(paths._join(base, rest)) if rest else base",
     "    return None if rest else base", _p5a("m5-pwsh-drive-relative-operand")),
    ("m5-bare-drive-is-cwd", "    return paths.norm_abs(paths._join(base, rest)) if rest else base",
     "    return paths.norm_abs(paths._join(base, rest)) if rest else None", _p5a("m5-pwsh-set-location-drive")),
]]

# Plan 5 final review, fix pass (rules lane). One mutation per clause the fix pass added; each names the row or test
# that must go red. P5F rows fire in-process from tests/test_skyguard_p5fix.py.
def _p5f(row):
    return "tests/test_skyguard_p5fix.py::test_p5f_row[p5f-" + row + "]"


_DOCS = "tests/test_guard_docs_agree.py::test_the_documented_ask_count_matches_the_registry"
_P5F_CLAUSES = [
    # (id, file, find, replace, test that must go red)
    # D1: the Recycle-Bin delete (VB FileSystem::DeleteFile/DeleteDirectory, Shell.Application Namespace(10).MoveHere).
    ("p5f-recycle-bin-delete-not-judged", _DL,
     "    for w, how in (recycle_targets(recycle_text(command, parsed)) if resolved is None else ()):",
     "    for w, how in ():",
     _p5f("vb-deletedirectory-ps")),
    ("p5f-recycle-bin-shell-application-not-matched", _DL,
     '    r"|' + BS + '.' + BS + 's*Namespace' + BS + 's*' + BS + '(' + BS + 's*(?:10|0x0*a)' + BS + 's*' + BS + ')'
     + BS + 's*' + BS + '.' + BS + 's*MoveHere)"', '    r")"',
     _p5f("shell-app-recycle")),
    ("p5f-recycle-bin-relative-path-unresolved", _DL, "    base = targets.normalise_cwd(cwd)\n", "    base = None\n",
     _p5f("vb-relative-in-install")),
    ("p5f-recycle-bin-double-quoted-not-resolved", _DL, '        elif how == "double":', "        elif False:",
     "tests/test_skyguard_deletes.py::test_recycle_bin_delete_with_a_double_quoted_path_resolves"),
    ("p5f-recycle-bin-unresolved-not-raw-matched", _DL,
     '        if unresolved_reach is None and raw_reaches(w, base, "powershell", roots):', "        if False:",
     "tests/test_skyguard_deletes.py::test_recycle_bin_delete_of_an_unresolved_root_variable_is_unresolved"),
    ("p5f-recycle-bin-unresolved-not-counted", _DL,
     "            continue\n        unresolved_any = True\n        if unresolved_reach is None and raw_reaches(w, base,",
     "            continue\n        if unresolved_reach is None and raw_reaches(w, base,",
     "tests/test_skyguard_deletes.py::test_recycle_bin_delete_of_a_variable_in_a_command_naming_the_install_is_unresolved"),
    ("p5f-recycle-bin-not-in-fallback-destroyer", _DL, 'DESTROYER += "|" + _RECYCLE_WORD', "",
     "tests/test_skyguard_deletes.py::test_recycle_bin_words_reach_the_parse_failure_fallback"),
    ("p5f-recycle-bin-not-in-opaque-destroyer", _DL, 'DESTROYER_LOOSE += "|" + _RECYCLE_WORD', "",
     "tests/test_skyguard_deletes.py::test_recycle_bin_words_reach_the_parse_failure_fallback"),
    # B-I1: moves with pipeline / unresolved sources and variable destinations.
    ("p5f-move-item-pipe-source-dropped", _DL, '        if not src and e.stdin_from == "pipe":', "        if False:",
     _p5f("pipe-gci-move")),
    ("p5f-mv-target-pipe-source-dropped", _DL, '            if not words and e.stdin_from == "pipe":',
     "            if False:", _MDEL + "test_xargs_mv_with_a_target_reads_its_sources_from_the_pipe"),
    ("p5f-move-unresolved-source-ignores-named-root", _DL,
     "                    unresolved_any = True                     # with a protected root named: S-DEL-UNRESOLVED",
     "                    pass", _p5f("pipe-get-item-move")),
    ("p5f-move-unresolved-source-no-upstream-check", _DL,
     '                    if (src is UNRESOLVED or e.stdin_from == "pipe") and piped_reach(i):', "                    if False:",
     _MDEL + "test_pipeline_move_of_an_ancestor_of_the_install_is_unresolved"),
    ("p5f-move-unresolved-source-no-raw-match", _DL,
     "                    elif src is not UNRESOLVED and raw_reaches(src, mcwd, e.dialect, roots):",
     "                    elif False:", _p5f("mv-root-var-source")),
    ("p5f-move-into-protected-area-is-a-delete", _DL,
     "                    if dz in RULE_OF:\n                        continue\n",
     "                    if False:\n                        continue\n",
     _MDEL + "test_an_unresolved_move_into_a_protected_folder_is_not_a_delete"),
    ("p5f-move-variable-destination-proves-nothing", _DL,
     "                    leaves = _expands(dst) and not raw_reaches(dst, mcwd, e.dialect, roots)",
     "                    leaves = False", _p5f("mv-to-env-temp")),
    ("p5f-move-variable-destination-reaching-a-root-leaves", _DL,
     "                    leaves = _expands(dst) and not raw_reaches(dst, mcwd, e.dialect, roots)",
     "                    leaves = _expands(dst)",
     _MDEL + "test_a_protected_file_moved_to_a_variable_destination_that_reaches_a_root_may_stay"),
    ("p5f-expands-misses-cmd-variable", _DL, ' or bool(re.search(r"%[A-Za-z_]' + BS + 'w*%", word))', "",
     _MDEL + "test_expands[%TEMP%/x-True]"),
    ("p5f-expands-misses-backtick", _DL, ' or "`" in word or bool(', " or bool(", _MDEL + "test_expands[`pwd`/x-True]"),
    ("p5f-copy-item-pipe-source-dropped", "tools/skyguard/writes.py",
     '        return [(dst[0] if dst else ".", src)] if src or e.stdin_from == "pipe" else []',
     '        return [(dst[0] if dst else ".", src)] if src else []', _p5f("pipe-copy-onto-save")),
    # D2: the documented ask count drifts from the registry again (core.md, README, safety-philosophy).
    ("p5f-core-md-ask-count-stale", "agent/instructions/core.md", "### ASKS THE USER -- seven rules",
     "### ASKS THE USER -- two rules", _DOCS + "[core]"),
    ("p5f-readme-ask-count-stale", "README.md", "Only seven rules ask *you*", "Only two rules ask *you*",
     _DOCS + "[readme]"),
    ("p5f-philosophy-ask-count-stale", "docs/safety-philosophy.md", "Seven rules in the toolkit ask you",
     "Two rules in the toolkit ask you", _DOCS + "[philosophy]"),
    # Privacy: a fixture carries the home of whoever runs the gate again (built at collection time, so this file
    # never names anyone).
    ("p5f-fixture-carries-home", "tests/fixtures/routes-harness/cases9.py", '"file_path":"<HOME>/.codex/config.toml"',
     '"file_path":"' + str(Path.home()).replace(BS, "/") + '/.codex/config.toml"',
     "tests/test_payload_fixtures.py::test_no_file_under_fixtures_carries_the_capturing_users_home"),
    # B-M4: the DevBench URL is read as curl sends it.
    ("p5f-devbench-url-not-normalised", "tools/skyguard/mcp_rules.py",
     "        u = _norm_url(w)                                      # rt-fix-b %63onsole; B-M4 dot / empty segments",
     "        u = unquote(w)", _p5f("devbench-dot-segment")),
    ("p5f-devbench-prefilter-needs-api-slash-tool", "tools/skyguard/mcp_rules.py",
     ' and not ("api" in low and "tool" in low):', ":", _p5f("devbench-dotdot-segment")),
    ("p5f-devbench-text-fallback-not-normalised", "tools/skyguard/mcp_rules.py",
     "_TEXT_ENDPOINT.search(_norm_text(command)):", "_TEXT_ENDPOINT.search(unquote(command)):",
     "tests/test_skyguard_p5fix.py::test_the_text_fallback_reads_a_dot_segment_url"),
    ("p5f-devbench-url-keeps-leading-double-slash", "tools/skyguard/mcp_rules.py",
     "        path = path[1:]                                    # normpath keeps a POSIX leading `//`",
     "        pass", "tests/test_skyguard_p5fix.py::test_norm_url[double-slash]"),
    ("p5f-devbench-url-drops-trailing-slash", "tools/skyguard/mcp_rules.py",
     '    if parts.path.endswith("/") and not path.endswith("/"):', "    if False:",
     "tests/test_skyguard_p5fix.py::test_norm_url[double-slash]"),
    # B-I1 pricing refinements: moves read through the command's own assignments; the Recycle-Bin text scan skips
    # data heredocs and mentions.
    ("p5f-recycle-text-reads-data-heredocs", _DL, '        if e.stdin_body and e.stdin_from == "heredoc:data":',
     "        if False:", _MDEL + "test_a_heredoc_that_quotes_the_recycle_bin_call_is_not_a_delete"),
    ("p5f-recycle-text-reads-mentions", _DL, "        if e.program in _MENTION_PROGRAMS:", "        if False:",
     _MDEL + "test_echoing_the_recycle_bin_call_is_not_a_delete"),
    ("p5f-move-destination-not-landed", _DL, "                    d = _landing(e, dst, command, cwd, env)",
     "                    pass", _MDEL + "test_a_move_into_the_installs_backups_through_an_assigned_variable_stays"),
    ("p5f-landing-no-literal-folder", _DL, '        if pre.strip("/' + BS * 2 + '"):', "        if False:",
     _MDEL + "test_a_move_into_a_literal_data_folder_with_a_loop_variable_stays"),
    ("p5f-expand-assigned-ambiguous", _DL, '    return vals.pop() if len(vals) == 1 and "" not in vals else None',
     "    return vals.pop() if vals else None", _MDEL + "test_expand_assigned[$D/x-$D/x]"),
    ("p5f-move-source-not-expanded", _DL,
     "                if a is None and src is not UNRESOLVED and _expand_assigned(src, command) != src:",
     "                if False:", _MDEL + "test_a_move_out_through_an_assigned_variable_still_leaves"),
    # C-M7: the nonce refusal is observable only as "the stub exited 0 yet the wrapper recorded exit 1". Twin 1: the
    # refusal is kept but with another code (still denied, so only the exact record can see it). Twin 2: the engine
    # run no longer carries a nonce (still refused, since the stub never echoes one; only the run log can see it).
    ("p5f-nonce-refusal-code-changed", "agent/guards/adapters/claude/protect-files.sh",
     '*) RC=1; _fail python "fallback ${PYC[*]} did not echo the engine nonce -- not used" ;;',
     '*) RC=3; _fail python "fallback ${PYC[*]} did not echo the engine nonce -- not used" ;;',
     "tests/test_hook_deadline.py::test_a_fresh_fallback_that_answers_without_the_nonce_is_refused"),
    ("p5f-nonce-not-passed-to-the-engine-run", "agent/guards/adapters/claude/protect-files.sh",
     '        _bounded "$1" in "${PYC[@]}" "$RUNPY" "${ENGINE_ARGS[@]}" --nonce "$NONCE"',
     '        _bounded "$1" in "${PYC[@]}" "$RUNPY" "${ENGINE_ARGS[@]}"',
     "tests/test_hook_deadline.py::test_a_fresh_fallback_that_answers_without_the_nonce_is_refused"),
    # B-M1: message fields from command text keep the two-line, 200-character contract.
    ("p5f-message-keeps-control-characters", "tools/skyguard/rules.py",
     '    clean = {k: _CONTROL.sub(" ", str(v)) for k, v in fields.items()}', "    clean = {k: str(v) for k, v in fields.items()}",
     "tests/test_rule_messages.py::test_every_template_keeps_its_shape_whatever_the_fields[newline-in-file]"),
    ("p5f-message-field-not-cut", "tools/skyguard/rules.py", ' else v[:cap - 3] + "..." for k, v in clean.items()})',
     " else v for k, v in clean.items()})",
     "tests/test_rule_messages.py::test_every_template_keeps_its_shape_whatever_the_fields[long-fields]"),
    ("p5f-message-cap-not-shrunk-for-a-long-line", "tools/skyguard/rules.py",
     '        if cap <= 16 or all(len(ln) <= LINE_MAX for ln in out.split("' + BS + 'n")):', "        if True:",
     "tests/test_rule_messages.py::test_every_template_keeps_its_shape_whatever_the_fields[long-fields]"),
    # C-M1: intent_diff writes UTF-8 whatever the console code page.
    ("p5f-intent-diff-console-encoding", "scripts/guard-corpus/intent_diff.py", '    if hasattr(sys.stdout, "buffer"):',
     "    if False:", "tests/test_intent_diff.py::test_main_writes_utf8_whatever_the_console_encoding"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _P5F_CLAUSES]


# Plan 8 T10 (release machinery): one mutation per gating clause; each names the test that must go red.
_BZ, _CRP, _VR, _CL = ("scripts/build_release_zip.py", "tools/check-release-payload.py", "tools/verify-release.sh",
                       "scripts/check_ci_leg.py")
_RB, _RP, _VT, _CT, _WT = ("tests/test_release_build.py::", "tests/test_release_payload.py::",
                           "tests/test_verify_release.py::", "tests/test_ci_leg.py::", "tests/test_workflows.py::")
_T10 = [
    # T10.1 the builder
    ("t10-build-dirty-tree-allowed", _BZ, "    if dirty and not allow_dirty:", "    if False:",
     _RB + "test_an_untracked_file_refuses_the_build_and_never_reaches_the_zip"),
    ("t10-build-foreign-rev-allowed", _BZ,
     '    if _git("merge-base", "--is-ancestor", sha, "HEAD", check=False).returncode != 0:', "    if False:",
     _RB + "test_a_rev_that_is_not_an_ancestor_of_head_is_refused"),
    ("t10-build-committed-build-info-allowed", _BZ,
     '    if _git("cat-file", "-e", "{}:{}".format(sha, BUILD_INFO), check=False).returncode == 0:', "    if False:",
     _RB + "test_the_build_info_is_a_file_the_commit_cannot_supply"),
    ("t10-build-ships-tests", _BZ, 'EXCLUDE = ("tests", ".github")', 'EXCLUDE = (".github",)',
     _RB + "test_tests_and_github_are_not_shipped"),
    ("t10-build-info-wrong-commit", _BZ, 'built-from=git archive (committed objects only)\\n".format(sha, version)',
     'built-from=git archive (committed objects only)\\n".format("0" * 40, version)',
     _RB + "test_a_clean_build_has_one_versioned_root_and_build_info"),
    # T10.2 the one payload checker
    ("t10-payload-routes-harness-allowed", _CRP, '    "tests/fixtures/routes-harness",\n', "",
     _RP + "test_every_check_can_fail_on_its_own_line[routes-harness-shipped]"),
    ("t10-payload-github-allowed", _CRP, '    ".github",\n', "",
     _RP + "test_every_check_can_fail_on_its_own_line[github-shipped]"),
    ("t10-payload-paths-env-anywhere", _CRP, 'ABSENT_NAMES = ("skyrim-paths.env", ".nexus_api_key")',
     'ABSENT_NAMES = (".nexus_api_key",)', _RP + "test_every_check_can_fail_on_its_own_line[paths-env-shipped-deep]"),
    ("t10-payload-stdin-any-cat", _CRP, 'None if "INPUT=$(cat)" in text.splitlines() else', 'None if "cat" in text else',
     _RP + "test_every_check_can_fail_on_its_own_line[claude-hook-inert-stdin]"),
    ("t10-payload-heartbeat-in-comment", _CRP, 'HEARTBEAT_RE = re.compile(r"^[^#]*hook-heartbeat", re.M)',
     'HEARTBEAT_RE = re.compile(r"hook-heartbeat")',
     _RP + "test_every_check_can_fail_on_its_own_line[claude-hook-heartbeat-in-comment]"),
    ("t10-payload-ps1-stdin-unchecked", _CRP, "None if PS_STDIN_RE.search(text) else", "None if True else",
     _RP + "test_every_check_can_fail_on_its_own_line[codex-ps1-no-stdin]"),
    ("t10-payload-expected-commit-ignored", _CRP, "None if commit == expect_commit else", "None if True else",
     _RP + "test_every_check_can_fail_on_its_own_line[build-info-other-commit]"),
    ("t10-payload-short-sha-accepted", _CRP, "None if SHA_RE.fullmatch(commit) else", "None if commit else",
     _RP + "test_every_check_can_fail_on_its_own_line[build-info-short-sha]"),
    # T10.3 the leak grep
    ("t10-leaks-backslash-spelling", _CRP, '("home path, backslash", "Users" + BS + n),', '',
     _RP + "test_every_home_spelling_is_found_without_the_bare_name"),
    ("t10-leaks-bare-name", _CRP, "        if n in persons or (n.lower() not in GENERIC_ACCOUNTS and len(n) >= 4):",
     "        if n in persons:",
     _RP + "test_every_spelling_of_the_home_is_found"),
    ("t10-leaks-utf16-unread", _CRP, '            for enc in ("utf-8", "utf-16-le", "utf-16-be"):',
     '            for enc in ("utf-8", "utf-16-be"):', _RP + "test_utf16_is_read_too"),
    ("t10-leaks-allow-prefix-ignored", _CRP, '                    text = text.replace(a, "<allowed>")',
     "                    pass", _RP + "test_an_allowed_prefix_is_blanked_but_anything_else_under_the_home_is_not"),
    ("t10-leaks-baseline-skips-changed", _CRP, "                if twin.is_file() and twin.read_bytes() == data:",
     "                if twin.is_file():", _RP + "test_changed_from_skips_only_byte_identical_files"),
    ("t10-leaks-names-not-from-env", _CRP, '    names = {env.get(k, "") for k in ("USERNAME", "USER", "LOGNAME")}',
     "    names = set()", _RP + "test_the_names_come_from_the_environment"),
    ("t10-leaks-runner-is-a-person", _CRP, 'GENERIC_ACCOUNTS = frozenset(("runner", ', "GENERIC_ACCOUNTS = frozenset((",
     _RP + "test_a_generic_account_is_searched_by_path_only"),
    ("t10-leaks-nothing-to-search-passes", _CRP, "    if not names and not extra and not persons:", "    if False:",
     _RP + "test_leaks_mode_with_nothing_to_search_for_is_could_not_check"),
    # T10.3 the verifier
    ("t10-verify-scrub-keeps-the-environment", _VR, '  scrub() { env -i "${SCRUB_ENV[@]}" "$@"; }',
     '  scrub() { env "${SCRUB_ENV[@]}" "$@"; }', _VT + "test_the_scrubbed_install_runs_and_its_canary_is_invisible"),
    ("t10-verify-payload-leaks-unchecked", _VR, '"$PY" "$HERE/check-release-payload.py" --leaks "$(winpath "$ROOT")" "${REQ[@]}"; rc=$?',
     "rc=0", _VT + "test_a_home_path_in_the_payload_is_red"),
    ("t10-verify-expected-commit-dropped", _VR, '"$(winpath "$ROOT")" ${EXPECT:+--expect-commit "$EXPECT"}; then',
     '"$(winpath "$ROOT")"; then', _VT + "test_the_expected_commit_is_enforced"),
    ("t10-verify-two-roots-accepted", _VR, """if [ "$(printf '%s\\n' "$tops" | grep -c .)" -ne 1 ]; then""",
     "if false; then", _VT + "test_two_roots_are_refused"),
    # T10.4 the per-leg tripwires
    ("t10-ci-skip-ceiling-off", _CL, "    if skipped > ceiling:", "    if False:", _CT + "test_each_tripwire_goes_red"),
    ("t10-ci-skip-ceiling-not-per-leg", _CL, '    ceiling = spec["skip_ceiling"][leg]',
     '    ceiling = max(spec["skip_ceiling"].values())', _CT + "test_each_tripwire_goes_red"),
    ("t10-ci-xfail-ceiling-off", _CL, '    if xfailed > spec["xfail_ceiling"]:', "    if False:",
     _CT + "test_each_tripwire_goes_red"),
    ("t10-ci-required-modules-off", _CL, "    for mod in required:", "    for mod in []:", _CT + "test_each_tripwire_goes_red"),
    ("t10-ci-load-test-allowed", _CL, '    for mod in spec["forbidden_passing"]:', "    for mod in []:",
     _CT + "test_each_tripwire_goes_red"),
    ("t10-ci-log-collected-floor-off", _CL, '    elif collected < spec["collected_floor"]:', "    elif False:",
     _CT + "test_each_tripwire_goes_red"),
    ("t10-ci-collect-floor-off", _CL, "    if n < floor:", "    if False:", _CT + "test_collect_mode"),
    # wave-1 merge: a node id carrying summary-shaped text must not be read as the collect summary
    ("w1-ci-collect-reads-node-ids", _CL, '    lines = [l for l in lines if "::" not in l]', "    pass",
     _CT + "test_collect_mode"),
    # T10.5 the freeze-gate baseline
    ("t10-freeze-baseline-is-the-tag-being-cut", "tests/test_codex_definitions.py",
     '        if _git("rev-parse", tag + "^{commit}", cwd=repo).stdout.decode().strip() == head:', "        if False:",
     "tests/test_codex_definitions.py::test_the_release_being_cut_is_not_its_own_baseline"),
    # T10.4/T10.6 the workflows
    ("t10-wf-action-unpinned", ".github/workflows/release.yml",
     "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97  # v7.0.0",
     "uses: actions/setup-python@v7  # v7.0.0", _WT + "test_every_action_is_pinned_by_commit_sha[release.yml]"),
    ("t10-wf-no-timeout", ".github/workflows/release.yml", "    timeout-minutes: 45\n", "",
     _WT + "test_every_job_has_a_timeout[release.yml]"),
    ("t10-wf-input-spliced", ".github/workflows/release.yml", '          TAG="${INPUT_TAG:-$REF_NAME}"',
     '          TAG="${{ github.event.inputs.tag }}"', _WT + "test_no_user_input_is_spliced_into_a_script[release.yml]"),
    ("t10-wf-not-on-master", ".github/workflows/release.yml",
     '          git merge-base --is-ancestor "$SHA" refs/remotes/origin/master ', "          true ",
     _WT + "test_release_gates_are_in_order"),
    ("t10-wf-release-verify-not-scrubbed", ".github/workflows/release.yml",
     'run: bash tools/verify-release.sh --local "$ZIP" --scrubbed --expect-commit "$SHA" --tag "$TAG"',
     'run: bash tools/verify-release.sh --local "$ZIP" --expect-commit "$SHA" --tag "$TAG"',
     _WT + "test_release_gates_are_in_order"),
    ("t10-wf-release-keeps-token", ".github/workflows/release.yml",
     "          persist-credentials: false\n          # Without this", "          # Without this",
     _WT + "test_least_privilege_and_no_dangerous_triggers[release.yml]"),
    ("t10-wf-load-test-in-ci", ".github/workflows/ci.yml",
     "        run: python -B tests/run_harness_tests.py tests/test_skyguard_timing.py",
     "        run: SKYGUARD_LOAD_TEST=1 python -B tests/run_harness_tests.py tests/test_skyguard_timing.py",
     _WT + "test_the_load_test_never_runs_in_ci_and_the_deadline_tests_run_on_both_legs"),
    ("t10-wf-python-range-short", ".github/workflows/ci.yml", '        python: ["3.10", "3.11", "3.12", "3.13"]',
     '        python: ["3.11"]', _WT + "test_the_behavior_matrix_is_both_oses_times_the_tested_pythons"),
    ("t10-wf-collect-tripwire-dropped", ".github/workflows/ci.yml",
     '          python scripts/check_ci_leg.py collect collect.log --leg "$RUNNER_OS"', "          true",
     _WT + "test_the_tripwires_bracket_the_suite_on_every_leg"),
    # Review M7: dropping the pipefail TEXT was an equivalent mutant (the job's bash default already adds it);
    # the real hazard is losing that default, so the twin removes it.
    ("t10-wf-behavior-loses-bash-default", ".github/workflows/ci.yml",
     "    defaults:\n      run:\n        shell: bash\n    steps:\n      # Full history WITH tags",
     "    steps:\n      # Full history WITH tags", _WT + "test_the_tripwires_bracket_the_suite_on_every_leg"),
    ("t10-wf-behavior-shallow", ".github/workflows/ci.yml",
     "tagless shallow clone.\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1\n"
     "        with:\n          fetch-depth: 0",
     "tagless shallow clone.\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1\n"
     "        with:\n          fetch-depth: 1",
     _WT + "test_every_job_that_runs_the_freeze_gate_has_full_history"),
    ("t10-wf-dotnet-channel", ".github/workflows/ci.yml", '          dotnet-version: "10.0.x"', '          dotnet-version: "9.0.x"',
     _WT + "test_node_comes_from_node_version_and_dotnet_is_channel_10"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T10]

# Plan 8 T10 fix round 1 (review I1-I3, M1-M5, M8, M9): one twin per new clause.
_T10FR = [
    # I1 the identity tokens, failing closed
    ("t10fr-require-tokens-off", _CRP, "    if require and not (persons or token_paths):", "    if False:",
     _RP + "test_require_tokens_fails_closed_without_them"),
    ("t10fr-verify-tokens-not-required", _VR, 'REQ=(); [ "${VERIFY_REQUIRE_LEAK_TOKENS:-0}" = 1 ] && REQ=(--require-tokens)',
     "REQ=()", _VT + "test_the_release_mode_fails_closed_without_leak_tokens"),
    ("t10fr-wf-tokens-not-required", ".github/workflows/release.yml", '          VERIFY_REQUIRE_LEAK_TOKENS: "1"\n', "",
     _WT + "test_release_gates_are_in_order"),
    ("t10fr-token-paths-ignored", _CRP, '    extra = [p for p in os.environ.get("RELEASE_LEAK_PATHS", "").split("|") if p.strip()] + token_paths',
     '    extra = [p for p in os.environ.get("RELEASE_LEAK_PATHS", "").split("|") if p.strip()]',
     _RP + "test_a_token_name_is_found_even_when_the_runner_is_generic"),
    # I2 the config folder allow-list
    ("t10fr-config-shape-unchecked", _CRP, "    return bool(CONFIG_SHAPE.match(c[len(h) + 1:]))", "    return True",
     _RP + "test_the_config_folder_is_allow_listed_only_in_its_expected_shape"),
    ("t10fr-config-not-below-home", _CRP, ' or not c.lower().startswith(h.lower() + "/"):', ":",
     _RP + "test_the_config_folder_is_allow_listed_only_in_its_expected_shape"),
    ("t10fr-config-allowed-unvalidated", _CRP, "        if config_dir_ok(c, home):", "        if True:",
     _RP + "test_a_home_level_config_hides_nothing"),
    # I3 secrets and key shapes
    ("t10fr-secret-value-unread", _CRP, "                        i = raw.find(value)", "                        i = -1",
     _RP + "test_a_secret_value_is_found_and_never_printed"),
    ("t10fr-secret-file-unread", _CRP, "        if p is not None and p.is_file():", "        if False:",
     _RP + "test_the_nexus_key_file_is_read"),
    ("t10fr-shape-nexus", _CRP, '     re.compile(r"[A-Za-z0-9+/]{16,}={0,2}--', '     re.compile(r"(?!)[A-Za-z0-9+/]{16,}={0,2}--',
     _RP + "test_every_key_shape_is_found"),
    ("t10fr-shape-github", _CRP, 're.compile(r"\\b(?:gh[pousr]_', 're.compile(r"(?!)(?:gh[pousr]_',
     _RP + "test_every_key_shape_is_found"),
    ("t10fr-shape-openai", _CRP, 're.compile(r"\\bsk-(?:proj-', 're.compile(r"(?!)sk-(?:proj-',
     _RP + "test_every_key_shape_is_found"),
    ("t10fr-shape-aws", _CRP, 're.compile(r"\\bAKIA[0-9A-Z]{16}\\b")', 're.compile(r"(?!)AKIA[0-9A-Z]{16}\\b")',
     _RP + "test_every_key_shape_is_found"),
    # M3 spellings
    ("t10fr-leaks-utf16be-unread", _CRP, '            for enc in ("utf-8", "utf-16-le", "utf-16-be"):',
     '            for enc in ("utf-8", "utf-16-le"):', _RP + "test_utf16_big_endian_is_read_too"),
    ("t10fr-leaks-url-encoded-path", _CRP,
     '    out += [(k + ", url-encoded", v.replace(" ", "%20")) for k, v in out if " " in v]', "    out += []",
     _RP + "test_url_and_json_slash_spellings_of_a_path"),
    ("t10fr-leaks-json-slash-path", _CRP, '           ("json-slash", fwd.replace("/", BS + "/"))]', "           ]",
     _RP + "test_url_and_json_slash_spellings_of_a_path"),
    # M5 named hooks, M8 dotenv
    ("t10fr-registered-hook-unchecked", _CRP, "        missing = [p for p in named if not (root / p).is_file()]",
     "        missing = []", _RP + "test_a_registered_hook_missing_from_the_payload_is_named"),
    ("t10fr-dotenv-allowed", _CRP, 'DOTENV_GLOBS = (".env", "*.env", ".env.*", "*.env.local")', "DOTENV_GLOBS = ()",
     _RP + "test_a_dotenv_file_is_refused"),
    # M1/M2 the builder
    ("t10fr-build-info-attributes-ignored", _BZ, "    if info.exists():", "    if False:",
     _RB + "test_machine_local_info_attributes_refuse_the_build"),
    ("t10fr-build-attributes-file-ignored", _BZ,
     '"-c", "core.attributesFile=" + _empty_attributes_file().replace("' + BS * 2 + '", "/"), *args],', "*args],",
     _RB + "test_the_default_global_attributes_file_is_neutralised"),
    ("t10fr-build-altering-attrs-allowed", _BZ, 'ALTERING_ATTRS = ("filter", "export-subst", "export-ignore", "ident")',
     "ALTERING_ATTRS = ()", _RB + "test_a_committed_attribute_that_alters_the_archive_refuses"),
    ("t10fr-build-submodule-allowed", _BZ, "    if links:", "    if False:", _RB + "test_a_submodule_refuses_the_build"),
    # M4 published/tagged verify
    ("t10fr-verify-root-not-the-tag", _VR, '  [ "$tops" = "$RELEASE_SLUG-${TAG#v}" ] && ok', "  true && ok",
     _VT + "test_a_tag_must_match_the_root_folder_and_build_info"),
    ("t10fr-verify-build-version-not-the-tag", _VR, '  [ "$BUILT_VERSION" = "${TAG#v}" ] && ok', "  true && ok",
     _VT + "test_a_tag_must_match_the_root_folder_and_build_info"),
    # M9 release tags only
    ("t10fr-freeze-any-tag-is-a-release", "tests/test_codex_definitions.py", '"--list", "v[0-9]*", ', "",
     "tests/test_codex_definitions.py::test_the_release_being_cut_is_not_its_own_baseline"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T10FR]

# Plan 8 T10 fix round 2 (re-review N1-N3, N5): one twin per new clause.
_T10FR2 = [
    # N1 a hit never prints identity text; the workflow masks every spelling first
    ("t10fr2-label-carries-home-spelling", _CRP, '            needles.append(("identity token #{} ({})".format(k, kind), s.lower()))',
     '            needles.append(("{} ({})".format(s, kind), s.lower()))',
     _RP + "test_a_hit_never_prints_the_identity_in_any_spelling"),
    ("t10fr2-label-carries-path-spelling", _CRP,
     '            needles.append(("identity token #{} (path, {})".format(k, kind), s.lower()))',
     '            needles.append(("path {} ({})".format(s, kind), s.lower()))',
     _RP + "test_a_hit_never_prints_the_identity_in_any_spelling"),
    ("t10fr2-file-name-not-withheld", _CRP, "            if any(needle in name.lower() for _, needle in needles):",
     "            if False:", _RP + "test_a_file_name_holding_the_identity_is_withheld"),
    ("t10fr2-config-folder-printed", _CRP,
     '            print("  allow-listed the config folder (validated <home>/.../Documents/My Games/Skyrim*)")',
     '            print("  allow-listed " + c)', _RP + "test_the_config_folder_is_never_printed"),
    ("t10fr2-mask-only-raw-tokens", _CRP, '    return ["::add-mask::" + v for v in sorted(vals) if v]',
     '    return ["::add-mask::" + v for v in sorted(set(persons) | set(paths)) if v]',
     _RP + "test_mask_lines_cover_every_token_and_spelling"),
    ("t10fr2-wf-no-mask-step", ".github/workflows/release.yml",
     "        run: python -B tools/check-release-payload.py --mask-tokens", "        run: true",
     _WT + "test_release_masks_every_identity_spelling_before_anything_prints"),
    # N3 the token floor
    ("t10fr2-token-name-floor-off", _CRP, '        elif len(t) < 4 or not re.search(r"[A-Za-z0-9]", t):', "        elif False:",
     _RP + "test_a_vague_token_is_rejected_by_number_never_text"),
    ("t10fr2-token-path-floor-off", _CRP,
     '            if len(t) < 4 or len(segs) < 2 or not any(re.search(r"[A-Za-z0-9]", x) for x in segs):',
     "            if False:", _RP + "test_a_vague_token_is_rejected_by_number_never_text"),
    ("t10fr2-token-problems-ignored", _CRP, "    problems = token_problems()\n    if problems:",
     "    problems = token_problems()\n    if False:", _RP + "test_a_vague_token_is_rejected_by_number_never_text"),
    # N2 the post-build proof
    ("t10fr2-proof-misses-dropped-files", _BZ,
     '    problems = ["missing from the zip: " + p for p in sorted(set(want) - set(got))]', "    problems = []",
     _RB + "test_the_post_build_proof_catches_a_missing_or_altered_member"),
    ("t10fr2-proof-misses-extra-files", _BZ,
     '    problems += ["not in the commit: " + p for p in sorted(set(got) - set(want))]', "    problems += []",
     _RB + "test_the_post_build_proof_catches_a_missing_or_altered_member"),
    ("t10fr2-proof-misses-altered-bytes", _BZ,
     '        if got[p].replace(b"' + BS + 'r' + BS + 'n", b"' + BS + 'n") != blobs[want[p]].replace(b"' + BS + 'r' + BS
     + 'n", b"' + BS + 'n"):', "        if False:", _RB + "test_the_post_build_proof_catches_a_missing_or_altered_member"),
    ("t10fr2-proof-not-wired", _BZ, "    if problems:\n        raise Refused(5,", "    if False:\n        raise Refused(5,",
     _RB + "test_a_build_whose_archive_differs_is_refused_and_writes_nothing"),
    # N5 attributes parsed, not substring-matched
    ("t10fr2-attrs-pattern-read-as-attribute", _BZ, "        for attr in parts[1:]:", "        for attr in parts:",
     _RB + "test_gitattributes_are_parsed_not_substring_matched"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T10FR2]

# Plan 8 T6c: the CI ruff hard gate skips template slot fragments, by a naming rule, and nothing else.
_RG = "tests/test_ruff_gate.py::"
MUTATIONS += [pytest.param("ruff.toml", find, rep, test, id=i) for i, find, rep, test in [
    ("t6c-ruff-force-exclude-dropped", "force-exclude = true", "force-exclude = false",
     _RG + "test_the_shipped_slot_fragments_pass_the_gate_as_ci_passes_them"),
    ("t6c-ruff-slot-fragments-not-excluded", 'extend-exclude = ["*.slots.py"]', "extend-exclude = []",
     _RG + "test_the_shipped_slot_fragments_pass_the_gate_as_ci_passes_them"),
    ("t6c-ruff-exclude-swallows-real-files", 'extend-exclude = ["*.slots.py"]', 'extend-exclude = ["*.py"]',
     _RG + "test_the_gate_skips_a_slot_fragment_but_still_catches_a_real_undefined_name"),
]]

# Plan 8 T6c: the lock generator's completeness guard covers every pytest version, not only 8.0.0.
MUTATIONS += [pytest.param("scripts/lock-python-dependencies.py", '    if "pytest" in packages:',
                           '    if packages.get("pytest", {}).get("metadata", {}).get("version") == "8.0.0":',
                           "tests/test_lock_generator.py::test_an_incomplete_pytest_closure_is_refused_for_any_pytest_version[tomli-9.0.3]",
                           id="t6c-lock-guard-keyed-on-one-pytest-version")]

# Plan 8 T6c: the lock never carries a pygments with the open ReDoS advisory (a stale pin would re-introduce it).
MUTATIONS += [pytest.param("requirements.lock", "Pygments==2.21.0 \\", "Pygments==2.19.2 \\",
                           "tests/test_dependency_locks.py::test_the_lock_never_pins_a_pygments_with_the_known_advisory",
                           id="t6c-lock-pygments-with-advisory")]

# Plan 8 T6c: package.json pins exact versions (a range lets a plain `npm install` float away from the lock).
MUTATIONS += [pytest.param("package.json", '"zod": "3.25.76"', '"zod": "^3.25.76"',
                           "tests/test_dependency_locks.py::test_package_json_pins_exact_versions_that_the_lock_resolves_to",
                           id="t6c-package-json-range-not-exact")]

# Plan 8 T6c: the FOMOD smoke wrapper reports a missing install as a failure when TOOLKIT_REQUIRE_NODE_DEPS=1, never as a pass.
# (Its control, "must go red on fast-xml-parser 4.3.6", needs a package manager and lives in the ci.yml node job.)
_NS = "tests/test_node_fomod_smoke.py::"
MUTATIONS += [pytest.param("tests/test_node_fomod_smoke.py", "    if os.environ.get(\"TOOLKIT_REQUIRE_NODE_DEPS\") == \"1\":" + chr(10) + "        pytest.fail(",
                           "    if False:" + chr(10) + "        pytest.fail(",
                           _NS + "test_a_missing_install_fails_when_required_and_skips_otherwise",
                           id="t6c-fomod-smoke-missing-install-must-fail-when-required")]
MUTATIONS += [pytest.param("tests/run_harness_tests.py", '"TOOLKIT_REQUIRE_NODE_DEPS")', ")",
                           "tests/test_harness_required.py::test_the_launcher_forwards_the_node_deps_flag_to_pytest",
                           id="t6c-launcher-drops-require-node-deps-flag")]

# --- Plan 8 T9: the 3.x -> 4.0 migration (tools/migrate-3x.py, tools/pre-update-snapshot.sh) ----------------
# One row per clause that decides "shipped" vs "yours", what is read, what is kept, and what is never copied.
# Not mutated (said so, not hidden): the exclusive-create `open(target, "xb")` behind the suffixed settings
# backup -- shadowed by the suffix choice, it only matters in a race no test can stage -- and the snapshot's
# `cmp -s` read-back, which no test can make a working `cp` fail.
_M3 = "tools/migrate-3x.py"
_M3T = "tests/test_migrate_3x.py::"
_SN = "tools/pre-update-snapshot.sh"
_M3_CLAUSES = [
    # A placeholder value may not hold a backtick: a note the user wrote after a path is theirs, not setup's.
    ("m3x-placeholder-value-excludes-backticks", _M3, '        parts.append("([^`' + BS + 'n]*)")',
     '        parts.append("(.*)")',
     _M3T + "test_an_edit_anywhere_is_not_mistaken_for_the_shipped_file[a note in backticks on a path line]"),
    # The same placeholder must hold the same value on every line it appears.
    ("m3x-same-placeholder-same-value", _M3, "if values.setdefault(name, value) != value:", "if False:",
     _M3T + "test_an_edit_anywhere_is_not_mistaken_for_the_shipped_file[the mods path changed in one of its two places]"),
    # The recorded paths (3.9 skyrim-paths.env) must agree with the file.
    ("m3x-recorded-paths-cross-check", _M3, "if name in values and values[name] != recorded:", "if False:",
     _M3T + "test_a_hand_corrected_path_is_an_edit_when_the_install_recorded_another"),
    # The spliced mod-manager block must be one setup.sh could write, line for line.
    ("m3x-block-must-be-a-shipped-block", _M3,
     'and not any(_block_matches(lines[start:end], v, values) for v in block["variants"]):', "and False:",
     _M3T + "test_an_edit_anywhere_is_not_mistaken_for_the_shipped_file[inside the MO2 block]"),
    # CRLF is not an edit.
    ("m3x-crlf-folded", _M3,
     '    return data.decode("utf-8", "surrogateescape").replace("' + BS + "r" + BS + 'n", "' + BS + 'n")',
     '    return data.decode("utf-8", "surrogateescape")',
     _M3T + "test_a_real_391_rendering_is_recognised_as_shipped[crlf-stock]"),
    # Each 4.x sign alone marks the live install as not-3.x (one twin per clause: each shadows the others).
    ("m3x-era-4x-instruction-file", _M3, "if p and GENERATED_4X.match(norm(p.read_bytes())):", "if False:",
     _M3T + "test_one_4x_file_is_enough_to_skip_the_live_files[CLAUDE.md]"),
    ("m3x-era-4x-settings", _M3, "if p and SETTINGS_4X in norm(p.read_bytes()):", "if False:",
     _M3T + "test_one_4x_file_is_enough_to_skip_the_live_files[settings]"),
    ("m3x-era-4x-codex", _M3, "        if self.codex:", "        if False:",
     _M3T + "test_one_4x_file_is_enough_to_skip_the_live_files[codex]"),
    # The kb-guard store is a source at all (the no-snapshot path) ...
    ("m3x-kb-store-is-a-source", _M3, "        cands.append(kb)", "        pass",
     _M3T + "test_without_a_snapshot_the_kb_guard_copy_is_used"),
    # ... its NEWEST 3.x copy, not an older one ...
    ("m3x-kb-store-newest-copy", _M3, "            files[name] = ok[-1] / name", "            files[name] = ok[0] / name",
     _M3T + "test_without_a_snapshot_the_kb_guard_copy_is_used"),
    # ... skipping 4.x copies stored by a 4.0 session that started before setup (review I2) ...
    ("m3x-kb-store-skips-4x-copies", _M3,
     "        ok = [d for d in dirs if (d / name).is_file() and not _is_4x_doc(d / name)]",
     "        ok = [d for d in dirs if (d / name).is_file()]",
     _M3T + "test_a_4x_session_before_setup_does_not_hide_the_3x_copy"),
    # ... and the knowledgebase copy from BEFORE the first 4.x copy (it has no header to tell it by).
    ("m3x-kb-store-knowledgebase-before-4x", _M3, "(cutoff is None or _stamp_key(d.name) < cutoff)", "True",
     _M3T + "test_a_4x_session_before_setup_does_not_hide_the_3x_copy"),
    # Idempotence: the state record (a deleted block stays deleted) and the marker (a lost state file).
    ("m3x-state-remembers-carried", _M3, "if key in carried or (name, digest) in present:",
     "if (name, digest) in present:", _M3T + "test_a_block_the_user_deleted_does_not_come_back"),
    ("m3x-marker-remembers-carried", _M3, "if key in carried or (name, digest) in present:",
     "if key in carried:", _M3T + "test_a_lost_state_file_does_not_duplicate_a_block"),
    # Blocks are read structurally: a marker inside carried text is content, not a block.
    ("m3x-blocks-read-structurally", _M3, 'if open_key is None and kind == "BEGIN":', 'if kind == "BEGIN":',
     _M3T + "test_hostile_content_is_copied_verbatim_and_never_becomes_preferences[absent]"),
    # A `## My preferences` heading ahead of the blocks, so carried text never becomes the injected preferences.
    ("m3x-prefs-heading-ahead-of-blocks", _M3,
     '                    prefix += "' + BS + "n## My preferences" + BS + "n" + BS + 'n"', '                    pass',
     _M3T + "test_hostile_content_is_copied_verbatim_and_never_becomes_preferences[no-prefs-section]"),
    # The fence outgrows any backtick run in the carried text.
    ("m3x-fence-outgrows-content", _M3, '    return "`" * max(4, longest + 1)', '    return "````"',
     _M3T + "test_hostile_content_is_copied_verbatim_and_never_becomes_preferences[absent]"),
    # The old KNOWLEDGEBASE.local.md is backed up BEFORE it is rewritten.
    ("m3x-local-kb-backed-up-first", _M3,
     '                _write_checked(run_dir / (LOCAL_KB + ".before"), local_bytes)', "                pass",
     _M3T + "test_upgrade_from_391_with_edits_keeps_every_edit"),
    # A different existing .3x-backup is never overwritten.
    ("m3x-settings-backup-not-overwritten", _M3,
     '                    target = root / ".claude" / "settings.json.3x-backup.{}".format(digest[:12])',
     "                    pass", _M3T + "test_an_existing_different_settings_backup_is_never_overwritten"),
    # Notes appended to a shipped KNOWLEDGEBASE.md are carried as text.
    ("m3x-kb-appended-notes-carried", _M3,
     '        if len(raw) > n and hashlib.sha256(raw[:n]).hexdigest() == spec["sha256"]:', "        if False:",
     _M3T + "test_upgrade_from_391_with_edits_keeps_every_edit"),
    # pre-update-snapshot.sh: copies what the new toolkit would overwrite ...
    ("snap-copies-what-the-update-overwrites", _SN,
     "rel=${rel#./}\n        [ -f \"$TARGET/$rel\" ] && printf", "rel=${rel#./}\n        false && printf",
     _M3T + "test_snapshot_copies_what_the_update_replaces_and_changes_nothing"),
    # ... but never enumerates the install itself, another game folder, or a backups folder.
    ("snap-not-from-the-install-itself", _SN, '[ "$SRC" != "$TARGET" ] && ', "",
     _M3T + "test_snapshot_never_copies_beyond_what_the_update_replaces[the install itself]"),
    ("snap-not-from-a-game-folder", _SN, '[ ! -f "$SRC/SkyrimVR.exe" ] && ', "",
     _M3T + "test_snapshot_never_copies_beyond_what_the_update_replaces[another game folder SkyrimVR.exe]"),
    ("snap-not-from-a-game-folder-se", _SN, ' && [ ! -f "$SRC/SkyrimSE.exe" ]', "",
     _M3T + "test_snapshot_never_copies_beyond_what_the_update_replaces[another game folder SkyrimSE.exe]"),
    ("snap-not-from-a-game-folder-le", _SN, '   && [ ! -f "$SRC/TESV.exe" ]', "",
     _M3T + "test_snapshot_never_copies_beyond_what_the_update_replaces[another game folder TESV.exe]"),
    ("snap-free-space-checked", _SN, '    *) if [ "$need" -gt $(( free_kb * 1024 / 10 * 8 )) ]; then', "    *) if false; then",
     _M3T + "test_snapshot_refuses_when_the_drive_is_nearly_full"),
    ("snap-backups-pruned", _SN, " -o -path ./.claude/backups", "",
     _M3T + "test_snapshot_never_copies_beyond_what_the_update_replaces[unpacked toolkit]"),
    ("snap-unique-stamp", _SN, 'while [ -e "$BACKUPS/pre-update-$STAMP" ]; do', "while false; do",
     _M3T + "test_two_snapshots_in_one_second_do_not_share_a_folder"),
    # --- Fix round 1 (T9 review) ---
    # I1: a shipped 3.x file is copied too -- "shipped" never means "no copy" ...
    ("m3x-pristine-files-copied-too", _M3, "        if ckey not in copied:",
     '        if ckey not in copied and verdict == "edited":',
     _M3T + "test_a_corrected_path_is_kept_and_carried_whatever_the_verdict[renamed profile]"),
    # ... and every filled-in path is carried as data: this run's copy, and the stable one setup.sh reads ...
    ("m3x-carried-paths-run-copy", _M3,
     '            _write_checked(run_dir / "carried-paths.env", paths_text.encode("utf-8", "surrogateescape"))', "            pass",
     _M3T + "test_a_corrected_path_is_kept_and_carried_whatever_the_verdict[moved instance]"),
    ("m3x-carried-paths-stable-copy", _M3,
     '                _write_atomic(root / STORE / "carried-paths.env", paths_text.encode("utf-8", "surrogateescape"))', "                pass",
     _M3T + "test_a_corrected_path_is_kept_and_carried_whatever_the_verdict[corrected overwrite path]"),
    # ... found line by line in an EDITED file as well.
    ("m3x-paths-searched-in-edited-files", _M3, "        if len(found) > len(best):", "        if False:",
     _M3T + "test_paths_are_still_found_when_no_template_is_complete"),
    ("m3x-paths-newest-complete-template", _M3,
     '            return found, "searched"          # the newest template whose every path line is still there',
     "            pass", _M3T + "test_paths_in_an_edited_file_come_from_the_newest_complete_template"),
    # Minor 1: the collision suffix sorts inside its own second.
    ("m3x-stamp-suffix-after-the-time", _M3,
     '    m = re.match(r"(.*_' + BS + 'd{6})(?:_(' + BS + 'd+))?' + BS + 'Z", name)',
     '    m = re.match(r"(.*?)(?:_(' + BS + 'd+))?' + BS + 'Z", name)',
     _M3T + "test_a_collision_suffix_sorts_inside_its_own_second"),
    # Minor 2: a source with no settings file says so.
    ("m3x-settings-not-checked-said", _M3, "    if SETTINGS not in src.files:", "    if False:",
     _M3T + "test_a_4x_session_before_setup_does_not_hide_the_3x_copy"),
    # Minor 3: removed shipped hooks and changed commands are reported.
    ("m3x-settings-removed-hooks", _M3, "        if pair in shipped and pair not in mine:", "        if False:",
     _M3T + "test_the_settings_report_names_removed_and_changed_hooks"),
    ("m3x-settings-changed-command", _M3, "        if command not in ref_cmds:", "        if False:",
     _M3T + "test_the_settings_report_names_removed_and_changed_hooks"),
    # Minor 4: an unclosed fence in the user's file is closed before the blocks.
    ("m3x-unclosed-fence-closed", _M3, '                    prefix += token + "' + BS + 'n"', "                    pass",
     _M3T + "test_an_unclosed_fence_in_the_users_file_does_not_leak_into_preferences"),
    # Minor 5: the .before copy is read back; the undo record is written even on failure.
    ("m3x-before-copy-read-back", _M3,
     '                _write_checked(run_dir / (LOCAL_KB + ".before"), local_bytes)',
     '                (run_dir / (LOCAL_KB + ".before")).write_bytes(local_bytes)',
     _M3T + "test_a_backup_that_does_not_read_back_stops_the_run[KNOWLEDGEBASE.local.md.before]"),
    ("m3x-undo-record-on-failure", _M3, "            if run_dir.is_dir():", "            if rc == 0 and run_dir.is_dir():",
     _M3T + "test_a_failure_still_leaves_the_undo_record"),
    # Minor 7: the three guards that had no twin.
    ("m3x-source-copy-read-back", _M3, "    if path.read_bytes() != data:", "    if False:",
     _M3T + "test_a_backup_that_does_not_read_back_stops_the_run[CLAUDE.md]"),
    ("m3x-local-kb-read-back", _M3, "            if not local_path.read_bytes().startswith(old):", "            if False:",
     _M3T + "test_a_local_knowledgebase_that_does_not_read_back_is_reported"),
    ("m3x-new-local-kb-prefix", _M3,
     '                prefix = "# Local knowledgebase' + BS + 'n' + BS + 'n## My preferences' + BS + 'n' + BS + 'n"',
     '                prefix = ""', _M3T + "test_a_new_local_knowledgebase_starts_with_an_empty_preferences_section"),
    # Minor 8: a CRLF local knowledgebase stays CRLF.
    ("m3x-crlf-local-kb-kept", _M3,
     '            nl = "' + BS + 'r' + BS + 'n" if old.count(b"' + BS + 'r' + BS + 'n") * 2 > old.count(b"' + BS + 'n") else "' + BS + 'n"',
     '            nl = "' + BS + 'n"', _M3T + "test_a_crlf_local_knowledgebase_stays_crlf"),
    # --- Fix round 2 (T9 re-review) ---
    # N5: the carried text is inserted untouched; only the block's own lines follow the file's style.
    ("m3x-carried-text-not-rewritten", _M3,
     '    return nl.join(head + ["", f + "text"]) + nl + content + f + nl + end',
     '    return nl.join(head + ["", f + "text"]) + nl + content.replace("' + BS + 'n", nl) + f + nl + end',
     _M3T + "test_a_crlf_local_knowledgebase_stays_crlf"),
    # N1: a value that is not UTF-8 is written with its exact bytes (both copies) ...
    ("m3x-carried-paths-run-copy-lossless", _M3,
     '            _write_checked(run_dir / "carried-paths.env", paths_text.encode("utf-8", "surrogateescape"))',
     '            _write_checked(run_dir / "carried-paths.env", paths_text.encode("utf-8"))',
     _M3T + "test_a_cp1252_accented_path_is_carried_byte_exact_through_the_launcher[only the path changed]"),
    ("m3x-carried-paths-stable-copy-lossless", _M3,
     '                _write_atomic(root / STORE / "carried-paths.env", paths_text.encode("utf-8", "surrogateescape"))',
     '                _write_atomic(root / STORE / "carried-paths.env", paths_text.encode("utf-8"))',
     _M3T + "test_a_cp1252_accented_path_is_carried_byte_exact_through_the_launcher[only the path changed]"),
    # ... and printed without dying (the launcher's printer) ...
    ("m3x-launcher-prints-any-value", _M3, "a.dry_run, out=_say)", "a.dry_run, out=print)",
     _M3T + "test_a_cp1252_accented_path_is_carried_byte_exact_through_the_launcher[only the path changed]"),
    # ... ANY exception is recorded as FAILED, and a retry reuses the failed run's folder.
    ("m3x-any-failure-recorded", _M3,
     "    except BaseException as e:                # noqa: BLE001 -- ANY end but success is recorded, then re-raised",
     "    except (_Failed, OSError) as e:",
     _M3T + "test_an_unforeseen_error_is_recorded_and_every_run_gets_a_new_folder"),
    # Minor: a newline in the source label cannot break out of the env file's comment.
    ("m3x-label-newline-stays-in-comment", _M3,
     '    label = re.sub(r"[' + BS + 'r' + BS + 'n]+", " ", src_label)', "    label = src_label",
     _M3T + "test_a_newline_in_the_source_label_stays_in_its_comment"),
    # Minor: the fence closer is as long as the opener.
    ("m3x-fence-closer-matches-opener", _M3,
     "                token = fence",
     '                token = "```"',
     _M3T + "test_an_unclosed_four_backtick_fence_is_closed_with_four"),
    # N3: the snapshot counts each file once against free space.
    ("snap-free-space-counts-each-file-once", _SN, 'sort -zu "$LIST" > "$UNIQ"', 'cat "$LIST" > "$UNIQ"',
     _M3T + "test_snapshot_counts_each_file_once_against_free_space"),
    # --- Fix round 3 (T9 re-review 2) ---
    # R1: a copy is created EXCLUSIVELY -- no code path can replace an earlier run's copy ...
    ("m3x-copy-created-exclusively", _M3, '        with open(path, "xb") as fh:', '        with open(path, "wb") as fh:',
     _M3T + "test_a_copy_never_replaces_an_existing_file"),
    # ... and a later run's record names the failed run it left alone.
    ("m3x-new-record-names-the-failed-run", _M3,
     '        log += ["An earlier run FAILED: {} (its copies and its record are untouched).".format(failed_before), ""]',
     "        pass", _M3T + "test_an_unforeseen_error_is_recorded_and_every_run_gets_a_new_folder"),
    # R2: an interrupt is recorded as INTERRUPTED and is not success.
    ("m3x-interrupt-recorded", _M3,
     "    except BaseException as e:                # noqa: BLE001 -- ANY end but success is recorded, then re-raised",
     "    except Exception as e:", _M3T + "test_an_interrupt_is_recorded_as_interrupted_not_success"),
    ("m3x-interrupt-named-interrupted", _M3,
     '        word = "FAILED" if isinstance(e, Exception) else "INTERRUPTED"     # Ctrl+C, SystemExit, ...',
     '        word = "FAILED"', _M3T + "test_an_interrupt_is_recorded_as_interrupted_not_success"),
    # R3: a lone surrogate in a label is shown, never fatal (label, and the printer's fallback).
    ("m3x-label-escapes-lone-surrogates", _M3,
     '    return text.encode("utf-8", "backslashreplace").decode("utf-8")', "    return text",
     _M3T + "test_a_lone_surrogate_in_a_label_is_shown_not_fatal"),
    ("m3x-printer-escapes-lone-surrogates", _M3,
     '        data = line.encode("utf-8", "backslashreplace").decode("utf-8")', "        raise",
     _M3T + "test_a_lone_surrogate_in_a_label_is_shown_not_fatal"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _M3_CLAUSES]

# Plan 8 T15b: scripts/thirdparty-audit.py, the third-party version and integration-drift gate. One twin per gate
# clause; each names the offline test (synthetic upstream responses) that must go red when the clause is reverted.
_TPA = "scripts/thirdparty-audit.py"
_TPT = "tests/test_thirdparty_audit.py::"
_T15B_CLAUSES = [
    ("t15b-drift-does-not-exit-1", _TPA, "    if DRIFT in statuses:\n        return 1", "    if False:\n        return 1",
     _TPT + "test_a_readme_install_version_that_differs_from_the_pin_is_drift"),
    ("t15b-unchecked-does-not-exit-2", _TPA, "    if UNCHECKED in statuses or not rows:", "    if not rows:",
     _TPT + "test_an_unreachable_source_is_could_not_check_with_exit_2"),
    ("t15b-no-rows-is-ok", _TPA, "    if UNCHECKED in statuses or not rows:", "    if UNCHECKED in statuses:",
     _TPT + "test_nothing_audited_is_not_ok"),
    ("t15b-newer-upstream-fails", _TPA, '        return NEWER, "upstream moved on (reported, not a failure)"',
     '        return DRIFT, "upstream moved on"', _TPT + "test_a_newer_upstream_is_reported_not_failed"),
    ("t15b-pin-ahead-of-upstream-passes", _TPA, '    return DRIFT, "pin is ahead of upstream latest -- not published?"',
     '    return NEWER, "ahead"', _TPT + "test_a_pin_ahead_of_upstream_is_drift"),
    ("t15b-ref-version-not-compared", _TPA, "        elif not same(v, target, compare):", "        elif False:",
     _TPT + "test_a_readme_install_version_that_differs_from_the_pin_is_drift"),
    ("t15b-unpinned-install-passes", _TPA,
     '            rows.append(Row(name, "install refs", target, None, DRIFT, "%s:%d %s" % (rel, line, ref.get("unpinned", "no version"))))',
     "            agree += 1", _TPT + "test_an_unpinned_install_of_a_pinned_tool_is_drift"),
    ("t15b-pin-variable-read-as-a-version", _TPA, '        elif re.search(r"[$%{]", v):', "        elif False:",
     _TPT + "test_an_install_line_filled_from_the_pin_variable_is_ok"),
    ("t15b-history-refs-not-skipped", _TPA,
     '                if HISTORY.search(history_context(lines, i, m.start())):\n                    yield rel, i + 1, "history", m.group(0)',
     '                if False:\n                    yield rel, i + 1, "history", m.group(0)',
     _TPT + "test_a_historical_install_mention_is_skipped_and_counted"),
    ("t15b-history-paths-not-skipped", _TPA, "                if not HISTORY.search(history_context(lines, i, m.start())):",
     "                if True:", _TPT + "test_a_historical_path_mention_is_not_an_instruction"),
    ("t15b-history-window-too-wide", _TPA, "HISTORY_WINDOW = 30\n", "HISTORY_WINDOW = 200\n",
     _TPT + "test_a_history_word_far_from_the_reference_does_not_hide_it"),
    ("t15b-history-ignores-new-clause", _TPA, '    before = lines[idx][:start][-HISTORY_WINDOW:].rsplit(";", 1)[-1]',
     "    before = lines[idx][:start][-HISTORY_WINDOW:]", _TPT + "test_a_new_clause_after_a_semicolon_is_not_history"),
    ("t15b-history-ignores-introducing-line", _TPA, '    if idx > 0 and lines[idx - 1].rstrip().endswith(":"):',
     "    if False:", _TPT + "test_a_historical_path_mention_is_not_an_instruction"),
    ("t15b-zip-path-not-compared", _TPA, "    missing = sorted({p for p, _ in paths if p not in have})", "    missing = []",
     _TPT + "test_a_documented_path_the_release_no_longer_ships_is_drift"),
    ("t15b-missing-asset-passes", _TPA, '    if args["asset"] not in assets:', "    if False:",
     _TPT + "test_a_release_without_the_documented_asset_is_drift"),
    ("t15b-install-latest-never-drifts", _TPA, "    elif same(pinned, latest):", "    elif True:",
     _TPT + "test_install_from_latest_when_latest_is_not_the_verified_version_is_drift"),
    ("t15b-offline-miss-answers", _TPA, '            raise CouldNotCheck("offline and not cached: " + url)',
     "            return Response(200, b'{\"version\": \"0.160.0\"}')",
     _TPT + "test_offline_without_a_cached_answer_is_could_not_check"),
    ("t15b-could-not-check-swallowed", _TPA, "        rows.append(Row(tool, check, pinned, None, UNCHECKED, str(exc)))",
     "        pass", _TPT + "test_an_unreachable_source_is_could_not_check_with_exit_2"),
    ("t15b-pin-sites-disagree-pass", _TPA,
     '            bad = {k: v for k, v in values.items() if not same(v, pinned, compare) or "disagrees" in k}',
     "            bad = {}", _TPT + "test_pin_sites_that_disagree_are_drift"),
    ("t15b-unreadable-pin-site-ignored", _TPA, "        if unread:\n", "        if False:\n",
     _TPT + "test_an_unreadable_declared_pin_site_is_could_not_check"),
    ("t15b-rate-limit-not-remembered", _TPA, "                self.exhausted[host] = reset", "                pass",
     _TPT + "test_rate_limit_exhaustion_stops_calling_the_host"),
    ("t15b-nexus-called-without-key", _TPA, '        if host == "api.nexusmods.com" and not self.nexus_key:',
     "        if False:", _TPT + "test_nexus_without_a_key_is_could_not_check_and_makes_no_call"),
    ("t15b-key-not-redacted", _TPA, '            data = data.replace(s.encode("utf-8"), b"<redacted>")', "            pass",
     _TPT + "test_a_key_echoed_by_an_upstream_is_never_cached_or_printed"),
    ("t15b-token-sent-to-every-host", _TPA, '        if host == "api.github.com" and self.github_token:',
     "        if self.github_token:", _TPT + "test_github_token_goes_only_to_the_api_host"),
    ("t15b-moved-tag-passes", _TPA, '    elif same(sha, pinned, "prefix"):', "    elif True:",
     _TPT + "test_a_moved_tag_is_drift"),
    ("t15b-missing-commit-path-passes", _TPA, '        if not gh_path_exists(f, args["repo"], pinned, path):',
     "        if False:", _TPT + "test_a_path_missing_at_the_pinned_commit_is_drift"),
    ("t15b-cli-flag-not-compared", _TPA, "    missing = sorted(documented - options)", "    missing = []",
     _TPT + "test_a_documented_cli_flag_missing_upstream_is_drift"),
    ("t15b-nupkg-tfm-not-compared", _TPA, "    if want in tfms:", "    if True:",
     _TPT + "test_a_package_without_the_pinned_sdk_framework_is_drift"),
    ("t15b-action-sha-not-compared", _TPA, "                elif sha != ref:", "                elif False:",
     _TPT + "test_an_action_sha_that_is_not_its_tag_is_drift"),
    ("t15b-action-by-tag-passes", _TPA, '        if not re.fullmatch(r"[0-9a-f]{40}", ref):', "        if False:",
     _TPT + "test_an_action_by_tag_not_sha_is_drift"),
    ("t15b-npm-range-passes", _TPA,
     '            rows.append(Row(name, "exact pin", spec, None, DRIFT, "package.json range, not an exact pin"))',
     "            pass", _TPT + "test_an_npm_range_is_drift"),
    ("t15b-npm-lock-not-compared", _TPA, "            if locked != spec:", "            if False:",
     _TPT + "test_an_npm_lock_that_disagrees_is_drift"),
    ("t15b-pip-lock-not-compared", _TPA, "                if spec not in locked:", "                if False:",
     _TPT + "test_a_python_lock_that_disagrees_is_drift"),
    ("t15b-unknown-only-name-silent", _TPA,
     '            rows.append(Row(o, "selection", None, None, UNCHECKED, "--only names no known integration"))',
     "            pass", _TPT + "test_nothing_audited_is_not_ok"),
    ("t15b-major-compare-is-exact", _TPA, "        return vkey(a)[:1] == vkey(b)[:1]", "        return a == b",
     _TPT + "test_a_major_version_reference_is_held_to_the_runtime_pin"),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T15B_CLAUSES]

# Merge of master into plan6 (2026-10-05): one twin per guard the merge itself added; each names the test that must go red.
_MM_SR = "tests/test_skyguard_self_rules.py::"
_MM_RP = "tests/test_release_payload.py::test_every_check_can_fail_on_its_own_line"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # master's apply_patch delete judge must step aside for the state, or guards off shadows a patch delete of it.
    ("tools/skyguard/engine.py",
     "    if (self_rules.kind_of(a, roots, os.environ) if a is not None else self_rules.text_kind(path)) == self_rules.STATE:",
     "    if False:",
     _MM_SR + "test_apply_patch_into_the_state_is_denied[delete]", "p6mm-patch-delete-of-state-is-s-del"),
    # S-SELF-STATE reads master's Recycle-Bin definition, MoveHere included.
    ("tools/skyguard/self_rules.py",
     "    for w, how in deletes.recycle_targets(deletes.recycle_text(command, parsed)):",
     '    for w, how in [t for t in deletes.recycle_targets(deletes.recycle_text(command, parsed)) if "MoveHere" not in command]:',
     _MM_SR + "test_a_recycle_bin_delete_of_the_state_is_s_self_state_in_every_spelling[shell-application-movehere]",
     "p6mm-self-state-recycle-misses-movehere"),
    # the payload checker's guard-manifest section, and a Plan 6 launcher in its required list.
    ("tools/check-release-payload.py", '            "adapter": check_adapter, "guards": check_guards}',
     '            "adapter": check_adapter}', _MM_RP + "[guard-file-edited]", "p6mm-payload-guards-section-dropped"),
    ("tools/check-release-payload.py", '    "tools/skydoctor",\n', "", _MM_RP + "[skydoctor-missing]",
     "p6mm-payload-skydoctor-not-required"),
]]

# Plan 8 T6d: every delivery path exact and verified (the delivery-path scan in scripts/thirdparty-audit.py, the
# NOT DELIVERED status, the pinned-hash check) -- one twin per clause, each killed by the named offline test; the
# twins on README/Dockerfile/docs/package.json/inventory-runtimes strip a pin from a REAL shipped file.
_T6D_CLAUSES = [
    ('t6d-npm-bare-install-passes', 'scripts/thirdparty-audit.py', '            out.append("bare `npm install` resolves ranges at install time: use `npm ci` (the lockfile)")', '            pass', 'tests/test_thirdparty_audit.py::test_an_npm_install_must_name_an_exact_version_or_commit'),
    ('t6d-npm-unversioned-package-passes', 'scripts/thirdparty-audit.py', '            if not (EXACT_NPM.fullmatch(s) or COMMIT_NPM.fullmatch(s)):', '            if False:', 'tests/test_thirdparty_audit.py::test_an_npm_install_must_name_an_exact_version_or_commit'),
    ('t6d-npm-tag-counts-as-a-commit', 'scripts/thirdparty-audit.py', 'COMMIT_NPM = re.compile(r"(?:github:)?[\\w.-]+/[\\w.-]+#[0-9a-f]{40}")', 'COMMIT_NPM = re.compile(r"(?:github:)?[\\w.-]+/[\\w.-]+#\\S+")', 'tests/test_thirdparty_audit.py::test_an_npm_install_must_name_an_exact_version_or_commit'),
    ('t6d-dotnet-tool-without-version-passes', 'scripts/thirdparty-audit.py', '        if not re.search(r"--version[ =]+[^\\s`\'\\"]", args)]),', '        if False]),', 'tests/test_thirdparty_audit.py::test_a_dotnet_tool_install_must_name_a_version'),
    ('t6d-command-args-span-the-whole-line', 'scripts/thirdparty-audit.py', '    return [m.group("args") for m in re.finditer(head + r"(?P<args>[^`\\"\'\\n|;&)]*)", line)]', '    return [line for m in re.finditer(head, line)]', 'tests/test_thirdparty_audit.py::test_a_dotnet_tool_install_must_name_a_version'),
    ('t6d-clone-without-tag-passes', 'scripts/thirdparty-audit.py', '        if not re.search(r"--branch[ =]+\\S+", args):', '        if False:', 'tests/test_thirdparty_audit.py::test_a_git_clone_must_name_a_tag_and_be_commit_verified'),
    ('t6d-clone-without-commit-proof-passes', 'scripts/thirdparty-audit.py', '        if not proof or not re.search(proof[1], repo.read(proof[0]) or ""):', '        if False:', 'tests/test_thirdparty_audit.py::test_a_git_clone_must_name_a_tag_and_be_commit_verified'),
    ('t6d-clone-prose-is-a-command', 'scripts/thirdparty-audit.py', '    for m in re.finditer(r"\\bgit clone\\s+(?P<args>(?:-|https?://|git@|\\$)[^`\\"\'\\n|;&]*)", line):', '    for m in re.finditer(r"\\bgit clone\\b(?P<args>[^`\\"\'\\n|;&]*)", line):', 'tests/test_thirdparty_audit.py::test_a_git_clone_must_name_a_tag_and_be_commit_verified'),
    ('t6d-winget-without-exact-passes', 'scripts/thirdparty-audit.py', '        if not (re.search(r"--exact\\b", args) and re.search(r"--version[ =]+[^\\s`\'\\"-]", args))]),', '        if not re.search(r"--version[ =]+[^\\s`\'\\"-]", args)]),', 'tests/test_thirdparty_audit.py::test_a_winget_install_must_be_exact_with_a_version'),
    ('t6d-winget-without-version-passes', 'scripts/thirdparty-audit.py', '        if not (re.search(r"--exact\\b", args) and re.search(r"--version[ =]+[^\\s`\'\\"-]", args))]),', '        if not re.search(r"--exact\\b", args)]),', 'tests/test_thirdparty_audit.py::test_a_winget_install_must_be_exact_with_a_version'),
    ('t6d-base-image-by-tag-passes', 'scripts/thirdparty-audit.py', '        re.match(r"\\s*FROM\\s+(?!scratch\\b)\\S+", line) and "@sha256:" not in line) else []),', '        False) else []),', 'tests/test_thirdparty_audit.py::test_a_base_image_must_be_pinned_by_digest'),
    ('t6d-container-download-needs-no-checksum', 'scripts/thirdparty-audit.py', '                              re.search(r"\\b(?:curl|wget)\\b", line)\n                              and not re.search(r"\\bsha(?:256|512)sum\\b", line)) else []),', '                              False) else []),', 'tests/test_thirdparty_audit.py::test_a_container_download_must_be_checksummed_in_the_same_step'),
    ('t6d-container-download-rule-never-applies', 'scripts/thirdparty-audit.py', '    "download-checksum": (lambda rel: rel.endswith("Dockerfile") or "/.devcontainer/" in "/" + rel,', '    "download-checksum": (lambda rel: False,', 'tests/test_thirdparty_audit.py::test_a_container_download_must_be_checksummed_in_the_same_step'),
    ('t6d-continuation-lines-not-joined', 'scripts/thirdparty-audit.py', '        if stripped.endswith("\\\\") and not stripped.endswith("\\\\\\\\"):', '        if False:', 'tests/test_thirdparty_audit.py::test_a_container_download_must_be_checksummed_in_the_same_step'),
    ('t6d-latest-statement-is-an-install', 'scripts/thirdparty-audit.py', '        "install follows whatever is newest"] if re.search(r"(?i)\\b(?:download|install|acquire|click|grab)", line)\n        and re.search(', '        "install follows whatever is newest"] if re.search(', 'tests/test_thirdparty_audit.py::test_an_install_that_follows_the_newest_release_is_unpinned'),
    ('t6d-download-without-hash-passes', 'scripts/thirdparty-audit.py', '        re.search(r"(?i)\\bdownload", line) and re.search(DOWNLOAD_HOSTS, line)', '        False', 'tests/test_thirdparty_audit.py::test_a_download_needs_a_hash_or_the_pinned_downloads_table'),
    ('t6d-exemption-ignored', 'scripts/thirdparty-audit.py', '                    hit = next((k for k, e in enumerate(exempt) if fnmatch.fnmatch(rel, e[0]) and e[1] == rule\n                                and re.search(e[2], line)), None)', '                    hit = None', 'tests/test_thirdparty_audit.py::test_an_exempt_line_is_reported_with_its_reason_and_an_unused_exemption_is_stale'),
    ('t6d-exemption-ignores-its-rule', 'scripts/thirdparty-audit.py', '                    hit = next((k for k, e in enumerate(exempt) if fnmatch.fnmatch(rel, e[0]) and e[1] == rule', '                    hit = next((k for k, e in enumerate(exempt) if fnmatch.fnmatch(rel, e[0])', 'tests/test_thirdparty_audit.py::test_an_exempt_line_is_reported_with_its_reason_and_an_unused_exemption_is_stale'),
    ('t6d-stale-exemption-not-reported', 'scripts/thirdparty-audit.py', '    stale = [e for k, e in enumerate(exempt) if k not in used]', '    stale = []', 'tests/test_thirdparty_audit.py::test_an_exempt_line_is_reported_with_its_reason_and_an_unused_exemption_is_stale'),
    ('t6d-real-tree-stale-exemption-survives', 'scripts/thirdparty-audit.py', 'DELIVERY_EXEMPT = [\n', 'DELIVERY_EXEMPT = [\n    ("README.md", "pip", r"no such line anywhere", "dead entry"),\n', 'tests/test_thirdparty_audit.py::test_every_delivery_exemption_still_matches_the_real_tree'),
    ('t6d-finding-is-not-drift', 'scripts/thirdparty-audit.py', '        rows.append(Row("delivery paths", rule, None, None, DRIFT, "%s:%d %s" % (rel, line, problem)))', '        rows.append(Row("delivery paths", rule, None, None, OK, "%s:%d %s" % (rel, line, problem)))', 'tests/test_thirdparty_audit.py::test_an_unpinned_delivery_path_fails_the_audit'),
    ('t6d-scan-not-wired-into-run', 'scripts/thirdparty-audit.py', '    if wanted is None or "delivery paths" in wanted:\n        audit_delivery(repo, rows)', '    if False:\n        audit_delivery(repo, rows)', 'tests/test_thirdparty_audit.py::test_an_unpinned_delivery_path_fails_the_audit'),
    ('t6d-not-delivered-reads-as-unpinned', 'scripts/thirdparty-audit.py', '    if spec.get("not_delivered"):', '    if False:', 'tests/test_thirdparty_audit.py::test_an_integration_the_toolkit_never_delivers_is_reported_with_its_reason'),
    ('t6d-registry-entry-without-pin-or-reason', 'scripts/thirdparty-audit.py', '     "not_delivered": "the user\'s own tool; no shipped instruction installs it"},', '     },', 'tests/test_thirdparty_audit.py::test_every_registry_entry_reads_a_pin_or_says_why_it_is_not_delivered'),
    ('t6d-published-hash-not-compared', 'scripts/thirdparty-audit.py', '            ok = want in f.get_ok(url).text().lower()', '            ok = True', 'tests/test_thirdparty_audit.py::test_a_pinned_hash_upstream_publishes_is_ok_and_a_different_one_is_drift'),
    ('t6d-downloaded-hash-not-compared', 'scripts/thirdparty-audit.py', '            ok = hashlib.sha256(f.get_ok(url, accept=it.get("accept")).body).hexdigest() == want', '            ok = True', 'tests/test_thirdparty_audit.py::test_a_pinned_hash_is_checked_by_downloading_when_upstream_publishes_none'),
    ('t6d-nexus-non-main-file-counts', 'scripts/thirdparty-audit.py', '    main = [str(x.get("version")) for x in files if x.get("category_name") == "MAIN" and x.get("version")]', '    main = [str(x.get("version")) for x in files if x.get("version")]', 'tests/test_thirdparty_audit.py::test_the_newest_main_nexus_file_is_the_upstream_version'),
    ('t6d-dockerfile-base-image-loses-its-digest', '.devcontainer/Dockerfile', '3.0.6-3.11-bookworm@sha256:aea59f6834231486ee510ed90fc89659762e8ac246bb5e5fc49809ab736a0b94', '3.0.6-3.11-bookworm', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-dockerfile-node-loses-its-checksum', '.devcontainer/Dockerfile', '    && echo "$sum  /tmp/node.tar.gz" | sha256sum -c - \\\n', '', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-pyffi-loses-its-lock', 'docs/installing-tools.md', 'with the Python you will run it from, from the toolkit root: `python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`', 'with the Python you will run it from, from the toolkit root: `python -m pip install pyffi setuptools`', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-skill-lz4-loses-its-lock', 'agent/skills/skyrim-save/SKILL.md', '`python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`', '`pip install lz4`', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-pip-lock-without-hashes-passes', 'scripts/thirdparty-audit.py', '        if not (re.search(r"--require-hashes\\b", args) and re.search(r"-r\\s+\\S+\\.lock\\b", args)):', '        if not re.search(r"-r\\s+\\S+\\.lock\\b", args):', 'tests/test_thirdparty_audit.py::test_a_pip_install_must_be_hash_locked'),
    ('t6d-pip-hashes-without-lock-passes', 'scripts/thirdparty-audit.py', '        if not (re.search(r"--require-hashes\\b", args) and re.search(r"-r\\s+\\S+\\.lock\\b", args)):', '        if not re.search(r"--require-hashes\\b", args):', 'tests/test_thirdparty_audit.py::test_a_pip_install_must_be_hash_locked'),
    ('t6d-pip-dry-run-is-an-install', 'scripts/thirdparty-audit.py', '        if "--dry-run" in args.split():\n            continue', '        if False:\n            continue', 'tests/test_thirdparty_audit.py::test_a_pip_install_must_be_hash_locked'),
    ('t6d-pip-dry-run-anywhere-on-the-line', 'scripts/thirdparty-audit.py', '        if "--dry-run" in args.split():', '        if "--dry-run" in line:', 'tests/test_thirdparty_audit.py::test_pip_is_judged_per_command'),
    ('t6d-pip-judged-per-line', 'scripts/thirdparty-audit.py', '    for args in _command_args(line, r"\\bpip3?(?:\\.exe)?\\s+install\\b"):', '    for args in [line] if re.search(r"\\bpip3?(?:\\.exe)?\\s+install\\b", line) else []:', 'tests/test_thirdparty_audit.py::test_pip_is_judged_per_command'),
    ('t6d-runner-without-version-passes', 'scripts/thirdparty-audit.py', '        if specs and not (EXACT_NPM.fullmatch(specs[0]) or EXACT_PY.fullmatch(specs[0])):', '        if False:', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-runner-family-missing', 'scripts/thirdparty-audit.py', '    "package-runner": (lambda rel: True, lambda line, repo, window: _runner_problems(line)),', '    "package-runner": (lambda rel: True, lambda line, repo, window: []),', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-pipx-without-pin-passes', 'scripts/thirdparty-audit.py', '            out.append("pipx / uv tool without an exact `pkg==x.y.z`")', '            pass', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-scoop-without-version-passes', 'scripts/thirdparty-audit.py', '        out += ["scoop app `%s` without @version" % s for s in _specs(args) if not re.search(r"@\\d", s)]', '        pass', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-cargo-without-locked-passes', 'scripts/thirdparty-audit.py', '        if not (re.search(r"--version[ =]+\\d", args) and "--locked" in args.split()):', '        if not re.search(r"--version[ =]+\\d", args):', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-go-latest-passes', 'scripts/thirdparty-audit.py', '        out += ["go install `%s` without @vX.Y.Z" % s for s in _specs(args) if not re.search(r"@v\\d", s)]', '        pass', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-gh-without-tag-passes', 'scripts/thirdparty-audit.py', '            out.append("gh release download without a tag: you get the newest release")', '            pass', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-gh-without-checksum-passes', 'scripts/thirdparty-audit.py', '        elif not re.search(r"\\bsha(?:256|512)sum\\b|Get-FileHash|\\b[0-9a-f]{64}\\b", window):', '        elif False:', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-pipe-to-sh-passes', 'scripts/thirdparty-audit.py', '    sh = re.search(r"\\b(?:curl|wget)\\b[^|\\n]*\\|\\s*(?:sudo\\s+)?(?:ba|z|da)?sh\\b", line)', '    sh = None', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-pipe-to-iex-passes', 'scripts/thirdparty-audit.py', '          and re.search(r"(?i)\\|\\s*(?:iex|Invoke-Expression)\\b|\\b(?:iex|Invoke-Expression)\\s*\\(", line))', '          and False)', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-system-package-without-version-passes', 'scripts/thirdparty-audit.py', '        out += ["system package `%s` without an exact version (pkg=version)" % p for p in pkgs', '        out += ["system package `%s` without an exact version (pkg=version)" % p for p in []', 'tests/test_thirdparty_audit.py::test_a_system_package_must_name_an_exact_version'),
    ('t6d-choco-version-not-accepted', 'scripts/thirdparty-audit.py', '        if re.search(r"--version[ =]+\\d", args):   # choco install x --version 1.2.3', '        if False:', 'tests/test_thirdparty_audit.py::test_every_installer_family_needs_an_exact_version'),
    ('t6d-latest-release-passes', 'scripts/thirdparty-audit.py', '        r"(?i)\\b(?:latest|newest|current)\\s+(?:LTS\\s+)?(?:release|version|build)\\b|releases/latest|big green LTS|"', '        r"(?!)"', 'tests/test_thirdparty_audit.py::test_an_install_that_follows_the_newest_release_is_unpinned'),
    ('t6d-download-hash-not-accepted', 'scripts/thirdparty-audit.py', '        and not SHA256_HEX.search(window) and "Pinned downloads" not in window) else []),', '        and "Pinned downloads" not in window) else []),', 'tests/test_thirdparty_audit.py::test_a_download_needs_a_hash_or_the_pinned_downloads_table'),
    ('t6d-download-table-pointer-not-accepted', 'scripts/thirdparty-audit.py', '        and not SHA256_HEX.search(window) and "Pinned downloads" not in window) else []),', '        and not SHA256_HEX.search(window)) else []),', 'tests/test_thirdparty_audit.py::test_a_download_needs_a_hash_or_the_pinned_downloads_table'),
    ('t6d-download-rule-only-in-markdown', 'scripts/thirdparty-audit.py', '    "download-unverified": (lambda rel: True, lambda line, repo, window: [', '    "download-unverified": (lambda rel: rel.endswith((".md", ".txt")), lambda line, repo, window: [', 'tests/test_thirdparty_audit.py::test_an_unverified_download_is_found_in_every_shipped_text_type'),
    ('t6d-download-hash-window-too-wide', 'scripts/thirdparty-audit.py', 'HASH_WINDOW = 3\n', 'HASH_WINDOW = 8\n', 'tests/test_thirdparty_audit.py::test_a_download_hash_may_wrap_onto_the_next_two_lines'),
    ('t6d-git-scm-not-a-download-host', 'scripts/thirdparty-audit.py', 'python\\.org|git-scm\\.com|"', 'python\\.org|"', 'tests/test_thirdparty_audit.py::test_the_pre_fix_manual_steps_are_seen'),
    ('t6d-history-files-not-skipped', 'scripts/thirdparty-audit.py', '            if not any(fnmatch.fnmatch(rel, x) for x in DELIVERY_HISTORY_FILES):', '            if True:', 'tests/test_thirdparty_audit.py::test_only_history_files_are_skipped'),
    ('t6d-only-markdown-files-scanned', 'scripts/thirdparty-audit.py', 'DELIVERY_SUFFIXES = (".md", ".txt", ".sh", ".ps1", ".cmd", ".bat", ".py", ".json", ".yml", ".yaml", ".js", ".example")', 'DELIVERY_SUFFIXES = (".md", ".txt")', 'tests/test_thirdparty_audit.py::test_an_unverified_download_is_found_in_every_shipped_text_type'),
    ('t6d-wording-history-skip-returns', 'scripts/thirdparty-audit.py', '            for rule, (applies, finder) in DELIVERY_RULES.items():\n                if not applies(rel):\n                    continue\n                for problem in finder(line, repo, window):', '            for rule, (applies, finder) in DELIVERY_RULES.items():\n                if not applies(rel) or HISTORY.search(line):\n                    continue\n                for problem in finder(line, repo, window):', 'tests/test_thirdparty_audit.py::test_no_wording_silences_an_install_line'),
    ('t6d-doc-jdk-major-not-held', 'scripts/thirdparty-audit.py', '              {"pattern": r"\\b(?:Open)?JDK (?P<v>\\d{2})\\b(?![+.\\d])", "compare": "major",', '              {"pattern": r"(?!)(?P<v>x)", "compare": "major",', 'tests/test_thirdparty_audit.py::test_a_doc_stating_another_jdk_than_the_image_is_drift'),
    ('t6d-package-json-xeditlib-loses-its-commit', 'package.json', '"xeditlib": "https://codeload.github.com/WingedGuardian/xeditlib/tar.gz/19bef0976738a9db0760d45975263e2d7eb76062"', '"xeditlib": "github:WingedGuardian/xeditlib"', 'tests/test_dependency_locks.py::test_xeditlib_and_koffi_are_locked_so_npm_ci_installs_them'),
    ('t6d-readme-xeditlib-back-to-unpinned', 'README.md', 'toolkit root: `npm ci --ignore-scripts --no-audit --no-fund`)', 'toolkit root: `npm install github:WingedGuardian/xeditlib`)', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-manual-steps-lose-their-hash', 'tools/inventory-runtimes.py', '+ "ce45e23275049f4b36edd90d5fd986a1e230efb6c511e9260a90176ce8e825df -- and run it. Then open a new terminal.",', '+ "-- and run it. Then open a new terminal.",', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-container-doc-states-jdk-17-again', 'docs/container-vs-windows.md', 'ReSaver CLI (JDK 21.0.12 is in the image)', 'ReSaver CLI (JDK 17 is in the image)', 'tests/test_thirdparty_audit.py::test_the_real_tree_install_refs_agree_with_their_pins'),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T6D_CLAUSES]
# Fix round 1, review P1/M6: tools/resaver-cli.sh's platform classpath separator and JDK-change recompile.
_T6D_CLAUSES_R1 = [
    ('t6d-resaver-linux-gets-windows-separator', 'tools/resaver-cli.sh', "  *) S=':' ;;", "  *) S=';' ;;", 'tests/test_resaver_cli_classpath.py::test_the_classpath_separator_is_the_platforms'),
    ('t6d-resaver-windows-gets-linux-separator', 'tools/resaver-cli.sh', "  MINGW*|MSYS*|CYGWIN*|Windows*) S=';' ;;", "  MINGW*|MSYS*|CYGWIN*|Windows*) S=':' ;;", 'tests/test_resaver_cli_classpath.py::test_the_classpath_separator_is_the_platforms'),
    ('t6d-resaver-seam-outside-test-mode', 'tools/resaver-cli.sh', '[ "${TOOLKIT_TEST_MODE:-}" = 1 ] && [ -n "${RESAVER_CLI_UNAME:-}" ] && UNAME="$RESAVER_CLI_UNAME"', '[ -n "${RESAVER_CLI_UNAME:-}" ] && UNAME="$RESAVER_CLI_UNAME"', 'tests/test_resaver_cli_classpath.py::test_the_platform_seam_is_ignored_outside_test_mode'),
    ('t6d-resaver-jdk-change-ignored', 'tools/resaver-cli.sh', '[ "$(cat "$STAMP" 2>/dev/null)" = "${JV:-?}" ] || JDK_CHANGED=1', 'true', 'tests/test_resaver_cli_classpath.py::test_a_jdk_change_recompiles_the_driver'),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T6D_CLAUSES_R1]


# check_ci_leg reads real pytest -v output (`collecting ... collected N items`, MEASURED 2026-10-06) and only
# non-node-id lines.
MUTATIONS += [
    pytest.param("scripts/check_ci_leg.py", r'r"^(?:collecting \.\.\. )?collected (\d+) items?\b"',
                 r'r"^collected (\d+) items?\b"',
                 "tests/test_ci_leg.py::test_the_real_pytest_collect_line_is_read", id="ci-leg-real-collect-line"),
    # Anchoring is what keeps a node id from counting as the collect line.
    pytest.param("scripts/check_ci_leg.py", r're.match(r"^(?:collecting', r're.search(r"(?:collecting',
                 "tests/test_ci_leg.py::test_a_node_id_mentioning_collected_is_not_the_collect_line",
                 id="ci-leg-collect-line-anchored"),
    # Linux-read files stay LF in an autocrlf checkout.
    pytest.param(".gitattributes", ".devcontainer/**   text eol=lf", "",
                 "tests/test_gen_agent_trees.py::test_files_linux_tools_read_are_pinned_lf", id="eol-devcontainer-lf"),
]

# Final gate followups: launcher fallback, isolated barrier evidence, and accurate setup output.
MUTATIONS += [
    pytest.param("tests/harness_pytest.py", "    log = Path(name)\n", "    log = Path(previous)\n    log.touch(exist_ok=True)\n", "tests/test_harness_tripwire_scope.py::test_overlapping_control_attempt_does_not_accuse_the_innocent_test[native]", id="review-tripwire-shared-log"),
    pytest.param("tests/harness_pytest.py", "    os.environ[\"HARNESS_TRIPWIRE_LOG\"] = str(log)\n", "    pass\n", "tests/test_harness_tripwire_scope.py::test_overlapping_control_attempt_does_not_accuse_the_innocent_test[shell]", id="review-tripwire-scope-env"),
    pytest.param("tests/harness_pytest.py", "            assert not attempts, \"PACKAGE-MANAGER TRIPWIRE: \" + repr(attempts)\n", "            assert True\n", "tests/test_harness_tripwire_scope.py::test_an_unmarked_tests_own_attempt_still_fails_and_keeps_its_evidence[native]", id="review-tripwire-own-attempt-refuses"),
    pytest.param("tests/harness_pytest.py", "        os.environ[\"HARNESS_TRIPWIRE_LOG\"] = previous\n", "        pass\n", "tests/test_harness_tripwire_scope.py::test_aborted_scope_restores_the_parent_log", id="review-tripwire-parent-log-restored"),
    pytest.param("tests/test_automod_docs_pinned.py", "SKIP_DIRS = {\".git\", \"node_modules\", \".superpowers\", \".pytest_cache\", \".venv\", \"__pycache__\"}", "SKIP_DIRS = {\".git\", \"node_modules\", \".superpowers\", \".venv\", \"__pycache__\"}", "tests/test_automod_docs_pinned.py::test_generated_caches_are_not_instruction_copies[.pytest_cache]", id="review-instruction-scan-ignores-pytestcache"),
    pytest.param("tests/test_automod_docs_pinned.py", "SKIP_DIRS = {\".git\", \"node_modules\", \".superpowers\", \".pytest_cache\", \".venv\", \"__pycache__\"}", "SKIP_DIRS = {\".git\", \"node_modules\", \".superpowers\", \".pytest_cache\", \"__pycache__\"}", "tests/test_automod_docs_pinned.py::test_generated_caches_are_not_instruction_copies[.venv]", id="review-instruction-scan-ignores-venv"),
    pytest.param("tests/test_automod_docs_pinned.py", "SKIP_DIRS = {\".git\", \"node_modules\", \".superpowers\", \".pytest_cache\", \".venv\", \"__pycache__\"}", "SKIP_DIRS = {\".git\", \"node_modules\", \".superpowers\", \".pytest_cache\", \".venv\"}", "tests/test_automod_docs_pinned.py::test_generated_caches_are_not_instruction_copies[__pycache__]", id="review-instruction-scan-ignores-pycache"),
    pytest.param("setup.sh", "Configured safety hooks, once your agent loads them, can:",
                 "The safety hooks are now active. They will:",
                 "tests/test_setup_coverage_summary.py::test_setup_summary_does_not_promise_active_hooks_or_universal_permission_prompts[claude]",
                 id="review-setup-summary-activation-prerequisite"),
    pytest.param("setup.sh", "Advise on ordinary game-file edits",
                 "Ask permission before editing any game file",
                 "tests/test_setup_coverage_summary.py::test_setup_summary_does_not_promise_active_hooks_or_universal_permission_prompts[all]",
                 id="review-setup-summary-advice-not-universal-ask"),
    pytest.param("setup.sh", "Auto-backups (Edit/Write/NotebookEdit)",
                 "Auto-backups (Edit/Write)",
                 "tests/test_setup_coverage_summary.py::test_setup_summary_scopes_its_backups_and_script_snapshots",
                 id="review-setup-summary-notebook-backup-surface"),
    pytest.param("setup.sh", "shell/script writes are not backed up",
                 "all writes are backed up",
                 "tests/test_setup_coverage_summary.py::test_setup_summary_scopes_its_backups_and_script_snapshots",
                 id="review-setup-summary-script-backup-limit"),
    pytest.param("setup.sh", "Snapshot Papyrus files before non-read-only shell commands",
                 "Snapshot your active scripts before running external tools",
                 "tests/test_setup_coverage_summary.py::test_setup_summary_scopes_its_backups_and_script_snapshots",
                 id="review-setup-summary-papyrus-snapshot-scope"),
    pytest.param("setup.sh", "recognized raw writes to ESP/ESM/ESL/BSA/BA2",
                 "direct writes to ESP/ESM/BSA files",
                 "tests/test_setup_coverage_summary.py::test_setup_summary_scopes_its_backups_and_script_snapshots",
                 id="review-setup-summary-recognized-raw-write-scope"),
] + [
    pytest.param("tools/" + name, "if not defined SG_FILE_PY goto probe",
                 "rem absent-recorded-interpreter bypass removed",
                 "tests/test_wrapper_python.py::test_the_cmd_twins_probe_python_when_no_interpreter_is_recorded[absent-file-" + name + "]",
                 marks=CMD_ONLY, id="review-" + name + "-absent-interpreter-fallback")
    for name in ("skyguard.cmd", "skydoctor.cmd")
]



# Plan 6 final fix pass, lane A (p6-fixA). One mutation per clause; each names the test that must go red.
_FXA = "tests/test_skyguard_never_shadow_mix.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # C1: the raw unlock is read before every shadowable rule (a decoy delete first no longer hides it).
    ("tools/skyguard/shell_rules.py",
     "    raw = lift_rules.raw_unlock(command, parsed, roots, cwd, env, shell)\n    if raw:\n        return deny(raw[0], raw[1])\n",
     "",
     _FXA + "test_a_never_shadow_refusal_stands_under_guards_off_beside_a_decoy[attrib-after-decoy-delete]",
     "fixA-c1-raw-unlock-first"),
    # C1: `lock disable` (an ask) is carried beside the refusal that outranks it.
    ("tools/skyguard/shell_rules.py", '        v = v._replace(never=Verdict("ask", perm))', "        pass",
     _FXA + "test_a_never_shadow_refusal_stands_under_guards_off_beside_a_decoy[disable-after-decoy-delete]",
     "fixA-c1-permanent-carried"),
    # C1: ...but never in place of the decoy's deny when nothing is shadowed (the twin of the one above).
    ("tools/skyguard/shell_rules.py", '        v = v._replace(never=Verdict("ask", perm))',
     '        v = Verdict("ask", perm)',
     _FXA + "test_without_a_lift_the_decoy_call_is_refused_no_less_strictly[disable-after-decoy-delete]",
     "fixA-c1-permanent-not-instead-of-the-deny"),
    # C1: shadow mode returns the carried NEVER_SHADOW refusal.
    ("tools/skyguard/engine.py", "        kept = _carried(v)\n        if kept is not None:",
     "        kept = None\n        if kept is not None:",
     _FXA + "test_a_never_shadow_refusal_stands_under_guards_off_beside_a_decoy[disable-after-decoy-ask]",
     "fixA-c1-shadow-returns-the-kept"),
    # C1: combine ranks a NEVER_SHADOW deny above an earlier shadowable one...
    ("tools/skyguard/engine.py", "            top = next((v for v in hits if _is_never(v)), hits[0])",
     "            top = hits[0]",
     _FXA + "test_combine_puts_a_never_shadow_deny_above_an_earlier_shadowable_deny", "fixA-c1-combine-ranks"),
    # C1: ...and carries a NEVER_SHADOW ask beside a deny that outranks it.
    ("tools/skyguard/engine.py", "                top = top._replace(never=kept[0])", "                pass",
     _FXA + "test_combine_keeps_a_deny_and_carries_the_never_shadow_ask_beside_it", "fixA-c1-combine-carries"),
    # C1 end to end on Codex: drop both combine clauses and the decoy delete hides the state write again.
    ("tools/skyguard/engine.py",
     "    kept = sorted((n for n in (_never_of(v) for v in vs) if n is not None), key=lambda n: n.decision != \"deny\")",
     "    kept = []\n    _is_never = lambda v: False  # noqa: E731",
     _FXA + "test_a_codex_patch_deleting_a_file_then_adding_the_state_is_refused_under_guards_off",
     "fixA-c1-codex-patch"),
]]
_FXI1 = "tests/test_skyguard_state_text_fallback.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # A-I1: S-SELF-STATE reads an unreadable command's text...
    ("tools/skyguard/self_rules.py", "        hit = _state_text(command, parsed)", '        hit = ""',
     _FXI1 + "test_an_unparseable_write_into_the_state_is_s_self_state[unclosed-quote-redirect]", "fixA-i1-fallback"),
    # ...needing a write construct (a read of the state is not a write)...
    ("tools/skyguard/self_rules.py", "            if m and _STATE_TEXT_WRITE.search(stmt):", "            if m:",
     _FXI1 + "test_an_unparseable_command_that_writes_no_state_is_not_s_self_state[read-only-unclosed]",
     "fixA-i1-needs-a-write"),
    # ...in the SAME statement as the `.skyguard` path.
    ("tools/skyguard/self_rules.py", "            if m and _STATE_TEXT_WRITE.search(stmt):",
     "            if m and _STATE_TEXT_WRITE.search(text):",
     _FXI1 + "test_an_unparseable_command_that_writes_no_state_is_not_s_self_state[write-and-mention-in-another-line]",
     "fixA-i1-same-statement"),
]]
_FXI2 = "tests/test_skyguard_lock.py::"
_PROT = "0x4 | (0x80000000 if protected else 0x20000000)"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # A-I2: the DACL is written with the file's protection, never always-unprotected...
    ("tools/skyguard/lock.py", _PROT, "0x4 | 0x20000000", _FXI2 + "test_lock_and_unlock_keep_a_protected_dacl_exactly",
     "fixA-i2-keep-protected"),
    # ...nor always-protected (the inheriting twin).
    ("tools/skyguard/lock.py", _PROT, "0x4 | 0x80000000", _FXI2 + "test_lock_and_unlock_keep_an_inheriting_dacl_exactly",
     "fixA-i2-keep-inheriting"),
    # ...and unlock writes back the protection the manifest recorded at lock time.
    ("tools/skyguard/lock.py", "            _remove_file(p, keep, prot if isinstance(prot, bool) else None)",
     "            _remove_file(p, keep, None)", _FXI2 + "test_unlock_restores_the_protection_the_manifest_recorded",
     "fixA-i2-restore-recorded"),
]]
_FXI3 = "tests/test_skyguard_lock.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # A-I3 / C-I1 ruling: the guarded hook path relocks an expired lock lift...
    ("tools/skyguard/engine.py", "        _relock_due(project, st, now, audit)", "        pass",
     _FXI3 + "test_the_first_hook_call_after_a_lock_lift_expires_relocks_the_file_and_logs_it", "fixA-i3-wired"),
    # ...only once it IS expired (the one expiry test both the gate and the relock read)...
    ("tools/skyguard/state.py", '    return not now < min(lift["expires_at"], lift["created"] + MAX_LIFT_SECONDS)',
     "    return True", _FXI3 + "test_a_hook_call_before_the_expiry_relocks_nothing", "fixA-i3-only-when-due"),
    # ...never a file a live lift still covers...
    ("tools/skyguard/lock.py", "        return name.lower() not in covered and (want is None or name.lower() in want)",
     "        return want is None or name.lower() in want",
     _FXI3 + "test_an_expired_lift_does_not_relock_a_file_a_live_lift_still_covers", "fixA-i3-live-lift-covers"),
    # ...only the files the expired lift named...
    ("tools/skyguard/lock.py",
     '    want = None if any(x.get("files") is None for x in due) else {f.lower() for x in due for f in x["files"]}',
     "    want = None", _FXI3 + "test_an_expired_lift_relocks_only_the_files_it_named", "fixA-i3-only-its-files"),
    # ...and never over a persistent disable (said so in the log, not as a failed relock).
    ("tools/skyguard/lock.py", "    if st.lock_disabled or not supported() or not (Path(root)",
     "    if not supported() or not (Path(root)",
     _FXI3 + "test_an_expired_unlock_all_relocks_every_target_and_nothing_while_disabled", "fixA-i3-not-while-disabled"),
    # A-M1: a compaction is not a new session start (guards status and the engine agree).
    ("tools/skyguard/state.py", '        if f[2] == "compact":\n            continue', "        pass",
     "tests/test_skyguard_shadow.py::test_a_compact_does_not_end_guards_off_and_status_agrees", "fixA-m1-compact"),
]]
_FXB1 = "tests/test_restore_backup_containment.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # B-I1: the backup restore refuses the guard's files, a plugin or archive...
    ("tools/skyguard/backup.py", "    why = _restore_refusal(target)\n    if why:", '    why = ""\n    if why:',
     _FXB1 + "test_the_reviewers_state_json_probe_is_refused_at_the_restore", "fixA-b1-containment"),
    # ...reading the target through links too...
    ("tools/skyguard/backup.py", "    for p in (str(target), os.path.realpath(str(target))):", "    for p in (str(target),):",
     _FXB1 + "test_a_restore_through_a_junction_into_the_state_is_refused", "fixA-b1-realpath"),
    # ...backup-before-edit no longer copies the state...
    ("tools/skyguard/backup.py", r', r"[/\\]\.skyguard[/\\]")', ")",
     _FXB1 + "test_the_state_folder_is_not_backed_up_at_all", "fixA-b1-skip-state"),
    # ...and the guard asks before `restore-from-backup --restore` (S-RESTORE-INPLACE)...
    ("tools/skyguard/lift_rules.py", "    return restore_backup(parsed)", "    return False",
     _FXB1 + "test_the_guard_asks_before_a_backup_restore[sh]", "fixA-b1-guard-wired"),
    ("tools/skyguard/lift_rules.py", "        if shellparse._PYTHON.fullmatch(e.program or \"\") and any(\"backup\" in a",
     "        if False and any(\"backup\" in a", _FXB1 + "test_the_guard_asks_before_a_backup_restore[python-entry]",
     "fixA-b1-guard-python-entry"),
    # ...but not before the listing (the twin).
    ("tools/skyguard/lift_rules.py", '        if e.program == "restore-from-backup" and "--restore" in e.argv:',
     '        if e.program == "restore-from-backup":', _FXB1 + "test_listing_the_backups_is_not_asked[list]",
     "fixA-b1-listing-allowed"),
]]
_FXSL = "tests/test_skyguard_lock.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # The state lock (coordination item 3): every write takes it...
    # ...and the hook's relock waits only briefly for it, skipping (logged) when busy.
    ("tools/skyguard/lock.py", "    with _state.locked(sf, wait):", "    if True:",
     _FXSL + "test_a_busy_state_file_skips_the_relock_without_blocking_the_hook", "fixA-state-lock-hook-short-wait"),
    # A-M3: skyguard.sh runs a SKYGUARD_PYTHON path with spaces as one word...
    ("tools/skyguard.sh", '    if [ -e "$cand" ]; then run=("$cand"); else', '    if false; then run=("$cand"); else',
     _FXSL + "test_skyguard_sh_runs_a_skyguard_python_whose_path_has_spaces", "fixA-m3-path-one-word"),
    # ...and still splits a launcher with arguments (the twin).
    ("tools/skyguard.sh", """else IFS=' ' read -r -a run <<< "$cand"; fi""", 'else run=("$cand"); fi',
     _FXSL + "test_skyguard_sh_still_splits_a_launcher_with_arguments", "fixA-m3-launcher-split"),
    # Item 6: the rule texts name the runnable command (the corpus rows quote it).
    ("tools/skyguard/rules.py", "        'Use: bash tools/skyguard.sh lock unlock {file}'",
     "        'Use: tools/skyguard lock unlock {file}'",
     "tests/test_skyguard_lift_rules.py::test_p6t11_row[p6t11-raw-attrib-bsa]", "fixA-cmd-text-runnable"),
]]



# Plan 6 final review, fix lane B: one twin per clause, each killed by the named test.
_FB = [
    # B-M1: a live file already identical to the snapshot is not rewritten (a mod manager's hard link survives)
    (_SC, "        same = [k for k in keys if _unchanged(live[k], chosen[k])]", "        same = []",
     _SCT + "test_in_place_restore_of_an_unchanged_snapshot_leaves_a_hard_link_intact", "p6fb-m1-identical-not-rewritten"),
    (_SC, "        return os.path.isfile(str(target)) and not os.path.islink(str(target)) and backup._sha256(target) == sha",
     "        return os.path.isfile(str(target))",
     _SCT + "test_in_place_restore_still_replaces_a_changed_file_beside_an_unchanged_one", "p6fb-m1-only-identical-skipped"),
    # B-M2: an unreadable paths file never silently skips the save a command rewrites
    (_RC, "    if not (resaver or dev):\n        return [], []", "    if not (resaver or dev) or roots is None:\n        return [], []",
     _RCT + "test_an_absolute_save_is_kept_even_when_the_paths_file_is_unreadable", "p6fb-m2-absolute-save-needs-no-roots"),
    (_RC, "    if roots is None and not n:", "    if False:",
     _RCT + "test_a_bare_save_name_with_an_unreadable_paths_file_is_logged_not_kept", "p6fb-m2-bare-name-not-new"),
    (_RC, '                notes.append(("SKIPPED", UNREADABLE_SAVE.format(name)))', "                pass",
     _RCT + "test_a_devbench_save_with_an_unreadable_paths_file_is_logged", "p6fb-m2-devbench-shell-logged"),
    (_RC, '             [("SKIPPED", UNREADABLE_SAVE.format(name))], False, now', "             [], False, now",
     _RCT + "test_a_devbench_save_with_an_unreadable_paths_file_is_logged", "p6fb-m2-devbench-mcp-logged"),
    # B-M4: a full config restore skips (and names) a no-longer-configured folder; by name it still refuses
    (_SC, "        moved = [k for k, why in odd if not wanted and _no_longer_configured(live[k], why)]", "        moved = []",
     _SCT + "test_a_full_in_place_config_restore_skips_a_no_longer_configured_folder_and_restores_the_rest",
     "p6fb-m4-full-restore-skips-moved-folder"),
    (_SC, "if not wanted and _no_longer_configured(", "if _no_longer_configured(",
     _SCT + "test_a_per_file_in_place_restore_of_a_no_longer_configured_ini_still_refuses", "p6fb-m4-by-name-still-refuses"),
    (_SC, "    return why == _OUT_OF_ROOTS and (name in _LOADORDER", "    return (name in _LOADORDER",
     _SCT + "test_in_place_never_writes_inside_the_guards_own_folders_even_when_configured_there",
     "p6fb-m4-only-the-out-of-roots-reason"),
    # B-M6: backing up a save before replacing it keeps the save reserve free on the backups drive
    ("tools/skyguard/backup.py", "    if free - size < SAVE_RESERVE_BYTES:", "    if False:",
     _SCT + "test_in_place_save_restore_refuses_when_its_backup_would_fill_the_drive", "p6fb-m6-save-reserve"),
    ("tools/skyguard/backup.py", '    if not str(file_path).lower().endswith((".ess", ".skse")):\n        return ""',
     '    if False:\n        return ""', _SCT + "test_the_save_reserve_applies_to_saves_only", "p6fb-m6-saves-only"),
    ("tools/skyguard/backup.py", "        short = _save_room(backups, file_path)", '        short = ""',
     _SCT + "test_in_place_save_restore_refuses_when_its_backup_would_fill_the_drive", "p6fb-m6-wired-into-take"),
    # B-M3: skydoctor's R9 clear is a read-modify-write under the one state lock that every write takes
    (_DR, "    with state.locked(str(sf)):", "    if True:",
     _DRT + "test_r9_clear_does_not_undo_a_lock_enable_that_lands_after_its_first_read", "p6fb-m3-doctor-rmw-locked"),
    (_ST, "    with locked(path, wait):\n        _replace(path, payload)", "    _replace(path, payload)",
     _STT + "test_a_write_waits_while_another_process_holds_the_state_lock", "p6fb-m3-write-takes-the-lock"),
    (_ST, "    if key in _HELD:\n        yield\n        return", "    if False:\n        yield\n        return",
     _STT + "test_write_inside_locked_does_not_wait_on_its_own_lock", "p6fb-m3-lock-is-reentrant"),
]
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in _FB]


# Final fix merge: skydoctor relocks an expired lock lift before clearing it (fix A concern 2).
MUTATIONS += [
    pytest.param("tools/skyguard/doctor.py", "    lock.relock_expired(str(root), now=now, wait=state.LOCK_WAIT)\n", "",
                 "tests/test_skydoctor.py::test_an_expired_lock_lift_is_relocked_not_stranded",
                 id="p6final-doctor-relocks-expired-lift"),
]


# ---- Plan 8 T6 (lane p8-deferred): deferred items fixed in Plan 8. One twin per clause, each killed by the named test.
# Rows 18-20, prefs: an unclosed fence is plain text, a fence closes only on its own character, the `(...)` variant
# heading is the section, a near-miss heading is said and audited, the read is bounded, a section beyond the bound
# says why it is missing, and a section cut by the bound is marked as cut.
_P8P = "tools/skyguard/prefs.py"
_P8PT = "tests/test_skyguard_prefs.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_P8P, "            end = _next_closer(closers, i, m.group(1))\n",
     "            end = _next_closer(closers, i, m.group(1)) or len(lines) - 1\n",
     _P8PT + "test_an_unclosed_fence_does_not_swallow_the_sections_after_it", "p8t6-prefs-unclosed-fence-is-text"),
    (_P8P, "            key = fence[0]\n", "            key = \"`\"\n",
     _P8PT + "test_a_closed_fence_still_hides_headings_inside_it", "p8t6-prefs-fence-closer-same-character"),
    (_P8P, r'_HEADING = re.compile(r"^##[ \t]+my[ \t]+preferences[ \t]*(?:\([^)\n]*\)[ \t]*)?:?[ \t]*#*[ \t]*$", re.I)',
     r'_HEADING = re.compile(r"^##[ \t]+my[ \t]+preferences[ \t]*:?[ \t]*#*[ \t]*$", re.I)',
     _P8PT + "test_the_variant_heading_with_a_parenthetical_is_the_section", "p8t6-prefs-variant-heading"),
    (_P8P, "        if near:\n", "        if False:\n",
     _P8PT + "test_a_near_miss_heading_says_so_and_is_audited", "p8t6-prefs-near-miss-said"),
    (_P8P, "            data = fh.read(READ_CAP)\n", "            data = fh.read()\n",
     _P8PT + "test_the_read_is_bounded", "p8t6-prefs-read-bounded"),
    (_P8P, "        if cut:\n            return _not_injected(", "        if False:\n            return _not_injected(",
     _P8PT + "test_the_read_is_bounded", "p8t6-prefs-cut-says-why"),
    (_P8P, "    elif cut and not ended:", "    elif False:",
     _P8PT + "test_a_section_cut_by_the_read_cap_says_it_was_cut", "p8t6-prefs-cut-section-marked"),
]]
# Row 24: the launcher's scrub keeps SystemDrive, and a minimal env elsewhere cannot drop it unnoticed.
# Row 26: the catastrophic-regex test arms the reaper, and the reaper really ends a spinning process (per platform: the
# branch that runs there).
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tests/run_harness_tests.py", 'FORWARDED_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", ', 'FORWARDED_ENV = ("SYSTEMROOT", ',
     "tests/test_env_scrub.py::test_the_launcher_scrub_keeps_systemroot_and_systemdrive", "p8t6-launcher-keeps-systemdrive"),
    ("tests/test_skyguard_lock.py",
     '            "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"), "COMSPEC": str(SYS32 / "cmd.exe"), "SKYGUARD_NO_PAUSE": "1",',
     '            "COMSPEC": str(SYS32 / "cmd.exe"), "SKYGUARD_NO_PAUSE": "1",',
     "tests/test_env_scrub.py::test_every_minimal_env_that_keeps_systemroot_keeps_systemdrive",
     "p8t6-every-minimal-env-keeps-systemdrive"),
    ("tests/test_python_fallback.py", '    run_py.write_text(src.replace(needle, reaper + "    re.match(',
     '    run_py.write_text(src.replace(needle, "    import re\\n" + "    re.match(',
     "tests/test_python_fallback.py::test_a_catastrophic_regex_inside_the_engine_is_killed_at_the_budget",
     "p8t6-catastrophic-test-arms-the-reaper"),
]]
MUTATIONS += [
    pytest.param("tests/spin_reaper.py", "            k32.TerminateProcess(handle, 9)\n", "            pass\n",
                 "tests/test_spin_reaper.py::test_a_spinning_process_is_ended_at_the_deadline",
                 marks=pytest.mark.skipif(sys.platform != "win32", reason="the Windows branch runs on Windows"),
                 id="p8t6-reaper-terminates-windows"),
    pytest.param("tests/spin_reaper.py", "        signal.pidfd_send_signal(fd, signal.SIGKILL)\n", "        pass\n",
                 "tests/test_spin_reaper.py::test_a_spinning_process_is_ended_at_the_deadline",
                 marks=pytest.mark.skipif(sys.platform == "win32", reason="the POSIX branch runs on POSIX"),
                 id="p8t6-reaper-terminates-posix"),
]
# Rows 22/51: the plan docs' links resolve under CI's own check.
MUTATIONS.append(pytest.param(
    ".github/workflows/ci.yml",
    "              if 'node_modules' in f.split(os.sep):",
    "              if False:",
    "tests/test_markdown_links.py::test_dependency_website_links_are_not_toolkit_links",
    id="p8-link-check-excludes-dependency-websites"))
MUTATIONS.append(pytest.param(
    "docs/superpowers/plans/2026-10-02-plan3-closeout.md",
    "plan3's own example of a relative link to skse-cross-compile.md",
    "plan3's own [skse-cross-compile.md](skse-cross-compile.md) example",
    "tests/test_markdown_links.py::test_every_relative_markdown_link_resolves", id="p8t6-plan-doc-link-broken"))
# Row 52: any DevBench server name, in the installed matcher and in health's count of what it covers.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (".claude/settings.json", '"matcher": "mcp__.*[Dd][Ee][Vv][-_. ]?[Bb][Ee][Nn][Cc][Hh].*",',
     '"matcher": "mcp__devbench.*",',
     "tests/test_repo_invariants.py::test_the_devbench_mcp_hook_is_registered_under_the_devbench_matcher",
     "p8t6-devbench-matcher-prefix-only"),
    ("tools/skyguard/health.py", 'CLAUDE_COVERED_MCP = re.compile(r"mcp__.*[Dd][Ee][Vv][-_. ]?[Bb][Ee][Nn][Cc][Hh].*")',
     'CLAUDE_COVERED_MCP = re.compile(r"mcp__devbench.*")',
     "tests/test_skyguard_coverage.py::test_every_devbench_spelling_is_covered_and_judged[mcp__DevBench-vr__console]",
     "p8t6-health-counts-prefix-only"),
]]
# Row 54: an archive writer in the install gets its own note; its redirect keeps the redirect note.
# Row 55: Codex `script` is command text; two command fields are refused. Row 57: an apply_patch Delete is audited
# under its rule's own hook.
_P8WT = "tests/test_skyguard_writes.py::"
_P8CR = "tests/test_skyguard_codex_routes.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/writes.py", '                notes["S-WRITE-ARCHIVE" if w in archives else "S-WRITE-INSTALL"].append(_name(w))',
     '                notes["S-WRITE-INSTALL"].append(_name(w))',
     _P8WT + "test_an_archive_writer_in_the_install_gets_the_archive_note[compress-archive]",
     "rule-S-WRITE-ARCHIVE-note-routed"),
    ("tools/skyguard/writes.py", "        archives = set(tool_write_targets(e)) if e.program in ARCHIVE_PROGRAMS else set()",
     "        archives = set(write_targets(e)) if e.program in ARCHIVE_PROGRAMS else set()",
     _P8WT + "test_a_redirect_on_an_archive_writer_keeps_the_redirect_note", "rule-S-WRITE-ARCHIVE-only-the-archive"),
    ("tools/skyguard/writes.py", '"7z.exe", "zip", "tar"))', '"7z.exe", "zip"))',
     _P8WT + "test_an_archive_writer_in_the_install_gets_the_archive_note[tar]", "rule-S-WRITE-ARCHIVE-tar"),
    ("tools/skyguard/adapters/codex.py", '_COMMAND_KEYS = ("command", "cmd", "chars", "script")',
     '_COMMAND_KEYS = ("command", "cmd", "chars")',
     _P8CR + "test_codex_route_rows[rt-codex-script-key]", "p8t6-codex-script-key-read"),
    ("tools/skyguard/adapters/codex.py", "        if len(keys) > 1:\n", "        if False:\n",
     _P8CR + "test_codex_route_rows[rt-codex-two-command-keys]", "p8t6-codex-two-command-fields-refused"),
    ("tools/skyguard/engine.py", '    audit.rule(rules.RULES[rid]["hook"], rid, "deny", reason)',
     '    audit.rule("protect-files", rid, "deny", reason)',
     _P8CR + "test_a_patch_delete_is_audited_under_its_rules_own_hook", "p8t6-patch-delete-audit-label"),
]]
# Row 49: the prompt's authorization form lists exactly the parser's allowlist, whichever side changes alone.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/inventory-runtimes.py", '               "node": ("OpenJS.NodeJS", "OpenJS.NodeJS.LTS")}',
     '               "node": ("OpenJS.NodeJS", "OpenJS.NodeJS.LTS", "OpenJS.NodeJS.Nightly")}',
     "tests/test_setup_prompt.py::test_the_authorization_form_lists_exactly_the_parsers_allowlist",
     "p8t6-allowlist-grows-without-the-prompt"),
    ("SETUP_PROMPT.txt", "for python, Git.Git for git, and OpenJS.NodeJS", "for python, and OpenJS.NodeJS",
     "tests/test_setup_prompt.py::test_the_authorization_form_lists_exactly_the_parsers_allowlist",
     "p8t6-prompt-drops-an-allowed-id"),
]]
# T4 claims audit K6: `find` is judged as GNU find on every dialect, and its -exec program by argv.
# K1: a shadowed ask says it was let through, and `guards off` says confirmation prompts are shadowed too.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/shellparse.py", '            if p == "find":\n', '            if p == "find" and self.d == "bash":\n',
     "tests/test_skyguard_deletes.py::test_gnu_find_deletes_are_judged_on_every_dialect[delete-powershell]",
     "p8t6-find-on-every-dialect"),
    ("tools/skyguard/shellparse.py", '                    r = _Resolver("bash", iv, iq, iy, (), self.cd, "", self.seg, [], self.depth + 1, self.ctx,',
     '                    r = _Resolver(self.d, iv, iq, iy, (), self.cd, "", self.seg, [], self.depth + 1, self.ctx,',
     "tests/test_skyguard_deletes.py::test_gnu_find_deletes_are_judged_on_every_dialect[exec-rm-powershell]",
     "p8t6-find-exec-runs-by-argv"),
    ("tools/skyguard/engine.py", '        head = SHADOW_ASK_HEAD if v.decision == "ask" else SHADOW_HEAD\n',
     "        head = SHADOW_HEAD\n",
     "tests/test_skyguard_shadow.py::test_an_ask_is_converted_too", "p8t6-shadowed-ask-says-asked"),
    ("tools/skyguard/cli.py", "a refusal or a confirmation prompt becomes an allow", "a refusal becomes an allow",
     "tests/test_skyguard_shadow.py::test_guards_off_records_a_session_bound_expiring_guards_lift",
     "p8t6-guards-off-names-confirmation-prompts"),
]]

# Controller (p8-int): the state writer waits out a reader holding state.json open (Windows).
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/state.py", "            if time.monotonic() >= deadline:\n", "            if True:\n",
     "tests/test_skyguard_state.py::test_a_write_waits_out_a_reader_holding_the_state_file_open",
     "p8int-state-replace-retries"),
]]
# T6 fix round 1 (review T6-review.md): I1 the fence scan is linear, M1 the reaper holds its target by reference,
# M2 the Python floor is 3.10, M4 the judge reads the whole MCP name, and the pricing extractor selects the same way.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_P8P, "            end = _next_closer(closers, i, m.group(1))\n",
     "            end = next((j for j in range(i + 1, len(lines)) if _next_closer(closers, j - 1, m.group(1)) == j), None)\n",
     _P8PT + "test_a_worst_case_file_at_the_read_cap_renders_fast[never-closing-openers]", "p8t6-fix1-fence-scan-rescans"),
    ("tools/inventory-runtimes.py", "PYTHON_FLOOR = (3, 10)\n", "PYTHON_FLOOR = (3, 8)\n",
     "tests/test_inventory_runtimes.py::test_the_floor_is_the_tested_range_3_10", "p8t6-fix1-python-floor-3-8"),
    ("SETUP_PROMPT.txt", "Python.Python.3.<minor> (3.10 or newer) for python", "Python.Python.3.<minor> for python",
     "tests/test_setup_prompt.py::test_the_python_floor_is_stated_the_same_everywhere", "p8t6-fix1-prompt-omits-floor"),
    ("tools/skyguard/mcp_rules.py",
     '    return len(parts) >= 3 and parts[0] == "mcp" and is_devbench_server("__".join(parts[1:]))',
     '    return len(parts) >= 3 and parts[0] == "mcp" and is_devbench_server(parts[1])',
     "tests/test_skyguard_mcp.py::test_a_devbench_name_after_a_double_underscore_is_judged[mcp__my__devbench__console]",
     "p8t6-fix1-judge-reads-first-piece-only"),
    ("tools/skyguard/mcp_rules.py", "    if not is_devbench(name):\n",
     '    if len(parts) < 3 or parts[0] != "mcp" or not is_devbench_server(parts[1]):\n',
     "tests/test_skyguard_mcp.py::test_a_devbench_name_after_a_double_underscore_is_judged[mcp__x__DevBench__console]",
     "p8t6-fix1-decide-reads-first-piece-only"),
    ("scripts/guard-corpus/replay.py",
     '    elif name.startswith("mcp__") and "devbench" in re.sub(r"[-_.\\s]", "", name[5:].lower()):\n',
     '    elif name.startswith("mcp__devbench"):\n',
     "tests/test_price_tool.py::test_extract_wide_takes_every_devbench_spelling", "p8t6-fix1-pricing-prefix-only"),
]]
MUTATIONS += [
    pytest.param("tests/spin_reaper.py", '    if os.name == "nt":\n        k32 = _k32()', '    if False:\n        k32 = _k32()',
                 "tests/test_spin_reaper.py::test_a_target_that_exits_at_once_is_still_the_reapers_own",
                 marks=pytest.mark.skipif(sys.platform != "win32", reason="the Windows branch runs on Windows"),
                 id="p8t6-fix1-reaper-handle-windows"),
    pytest.param("tests/spin_reaper.py", '    elif hasattr(os, "pidfd_open"):\n', "    elif False:\n",
                 "tests/test_spin_reaper.py::test_a_target_that_exits_at_once_is_still_the_reapers_own",
                 marks=pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pidfd is Linux"),
                 id="p8t6-fix1-reaper-pidfd-linux"),
]
# ---- Plan 8 T8 (lane p8-paths): skyrim-paths.env at the toolkit root, .claude/ as the deprecated fallback. -------
# One twin per clause of paths.read_install / legacy_notice, the TOOL_KEYS read, the run.py / snapcli wiring, the
# SessionStart notice and the self-protection of the new location. Each is killed through the named test.
_P8T8 = "tests/test_paths_location.py::"
_P8T8_PA = "tools/skyguard/paths.py"
_P8T8_UNION = "            file[key], added = tuple(file.get(key, ())) + extra, True"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # an empty root reads no file at all (deferred item 9: Path("") was the cwd)
    (_P8T8_PA, "    if not root:\n        return PathsConfig(", "    if False:\n        return PathsConfig(",
     _P8T8 + "test_no_root_reads_no_file_at_all", "p8t8-no-root-reads-no-file"),
    # a reader that ignores the new location (re-anchored in fix round 1: the read is no longer a loop)
    (_P8T8_PA, "    cfg = read_config(new, environ)._replace(source=PATHS_FILE)",
     "    cfg = read_config(old, environ)._replace(source=PATHS_FILE)",
     _P8T8 + "test_the_root_file_is_read", "p8t8-reader-ignores-root-file"),
    # ...one that drops the legacy fallback
    (_P8T8_PA, "            return read_config(old, environ)._replace(source=LEGACY_PATHS_FILE, legacy=True)",
     "            return read_config(new, environ)",
     _P8T8 + "test_the_legacy_file_is_the_fallback", "p8t8-reader-drops-legacy-fallback"),
    # ...and one where the legacy file's roots come first when both exist (fix round 1: union, root file first)
    (_P8T8_PA, _P8T8_UNION, "            file[key], added = extra + tuple(file.get(key, ())), True",
     _P8T8 + "test_both_present_protection_roots_are_the_union_root_file_first", "p8t8-legacy-wins-over-root"),
    # SKYGUARD_PYTHON is read from the file...
    (_P8T8_PA, "            if key not in ENV_KEYS + TOOL_KEYS:", "            if key not in ENV_KEYS:",
     _P8T8 + "test_skyguard_python_is_read_and_is_no_root", "p8t8-tool-key-read-from-file"),
    # ...and from the environment, which wins for lookup
    (_P8T8_PA, "    env = {k: environ[k] for k in ENV_KEYS + TOOL_KEYS if environ.get(k)}",
     "    env = {k: environ[k] for k in ENV_KEYS if environ.get(k)}",
     _P8T8 + "test_the_environment_skyguard_python_wins_for_lookup", "p8t8-tool-key-read-from-env"),
    # the absolute-path rule binds ROOTS only (a launcher like `py -3` is a legitimate interpreter)
    (_P8T8_PA, 'value != "" and key in ENV_KEYS and norm_abs(value) is None', 'value != "" and norm_abs(value) is None',
     _P8T8 + "test_a_non_path_skyguard_python_is_kept[launcher]", "p8t8-abs-rule-roots-only"),
    # the deprecation notice names the legacy location only
    (_P8T8_PA, '    return DEPRECATION if cfg.legacy else ""', '    return DEPRECATION if cfg.source != "" else ""',
     _P8T8 + "test_no_notice_once_the_root_file_covers_the_legacy_one", "p8t8-notice-legacy-only"),
    # run.py reads the install's file through read_install...
    ("tools/skyguard/run.py", "        cfg = paths.read_install(a.root_win or a.root, dict(os.environ))",
     '        cfg = paths.read_env(Path(a.root_win or a.root) / ".claude" / "skyrim-paths.env", dict(os.environ))',
     _P8T8 + "test_run_guards_a_config_dir_declared_in_the_root_file", "p8t8-run-reads-root-file"),
    # ...and the main worktree's too
    ("tools/skyguard/run.py", "paths.read_install(main, {})", 'paths.read_env(Path(main) / ".claude" / "skyrim-paths.env", {})',
     _P8T8 + "test_a_worktree_reads_the_main_trees_root_file", "p8t8-run-worktree-reads-root-file"),
    # snapshot restore --in-place finds the declared roots at the root
    ("tools/skyguard/snapcli.py", "        cfg = paths.read_install(str(project), os.environ)",
     '        cfg = paths.read_env(Path(project) / ".claude" / "skyrim-paths.env", os.environ)',
     _P8T8 + "test_an_in_place_restore_reads_the_root_file", "p8t8-snapcli-reads-root-file"),
    # SessionStart prints the notice...
    ("tools/skyguard/session.py",
     '    banners = "\\n".join(t for t in (banners, paths.legacy_notice(project if root is None else root)) if t)',
     "    pass", _P8T8 + "test_the_legacy_location_gets_a_notice_at_session_start", "p8t8-session-notice"),
    # ...but not on compact (once per session start, not per context compaction)
    ("tools/skyguard/session.py", '        return _with_prefs(banners, project, audit), "evaluated", "session"',
     '        return _with_prefs(banners + paths.legacy_notice(project), project, audit), "evaluated", "session"',
     _P8T8 + "test_the_notice_is_not_repeated_on_compact", "p8t8-session-notice-not-on-compact"),
    # self-protection: the root file is a guard file...
    ("tools/skyguard/self_rules.py", '    if rel == ["skyrim-paths.env"]:', "    if False:",
     _P8T8 + "test_both_locations_are_guard_files[root]", "p8t8-self-guard-root-file"),
    # ...at the root only, not any file of that name deeper in the install
    ("tools/skyguard/self_rules.py", '    if rel == ["skyrim-paths.env"]:', '    if rel[-1:] == ["skyrim-paths.env"]:',
     _P8T8 + "test_only_the_root_level_name_is_a_guard_file", "p8t8-self-guard-root-level-only"),
]]


# ---- Plan 8 T7 (p8-setup): setup.sh --agent, --lock, the root paths file, the 3.x hook-in, runtime ranges, hints ----
# One twin per branch / clause, each killed through its NAMED test. Twins whose test is Windows-only say so; the
# Codex-off-Windows refusal has no twin here (its test is POSIX-only) -- the setup-smoke CI leg (ubuntu, codex)
# expects exit 3 there instead.
_T7S = "tests/test_setup_agent.py::"
_T7 = [
    # usage errors: exit 2, nothing written
    ("setup.sh", "Nothing was changed.\" >&2\n       usage >&2; exit 2 ;;", "Nothing was changed.\" >&2\n       ;;",
     _T7S + "test_a_usage_error_exits_2_and_writes_nothing[unknown-value]", "t7-unknown-value-exits-2"),
    ("setup.sh", "        *) echo \"setup.sh: unknown option '$1'. Nothing was changed.\" >&2; usage >&2; exit 2 ;;",
     "        *) shift ;;", _T7S + "test_a_usage_error_exits_2_and_writes_nothing[unknown-option]",
     "t7-unknown-option-exits-2"),
    # both = all (Windows: the [both] case renders Codex)
    ("setup.sh", '    both) AGENT_ARG="all" ;;', '    both) AGENT_ARG="claude" ;;',
     _T7S + "test_each_value_configures_exactly_its_agents[both]", "t7-both-means-all"),
    # auto: the agents on PATH; none found means all
    ("setup.sh", '        *) AGENT_SEL="all" ;;', '        *) AGENT_SEL="claude" ;;',
     _T7S + "test_auto_configures_the_agents_found_on_path[none-found-means-all]", "t7-auto-none-found-means-all"),
    ("setup.sh", '    which claude >/dev/null 2>&1 && AGENTS_FOUND="claude"', "    true",
     _T7S + "test_auto_configures_the_agents_found_on_path[claude-only]", "t7-auto-detects-claude"),
    ("setup.sh", '    if [ -n "$CODEX_WIN" ] && which codex >/dev/null 2>&1; then', "    if false; then",
     _T7S + "test_auto_configures_the_agents_found_on_path[codex-only]", "t7-auto-detects-codex"),
    # an unselected agent's files are kept as extracted
    ("setup.sh", '    *) DOC_ON="$DO_CODEX" ;;', "    *) DOC_ON=1 ;;",
     _T7S + "test_each_value_configures_exactly_its_agents[claude]", "t7-agents-md-only-for-codex"),
    ("setup.sh", '    CLAUDE.md) DOC_ON="$DO_CLAUDE" ;;', "    CLAUDE.md) DOC_ON=1 ;;",
     _T7S + "test_each_value_configures_exactly_its_agents[codex]", "t7-claude-md-only-for-claude"),
    ("setup.sh", 'if [ -z "$DO_CODEX" ]; then', "if false; then",
     _T7S + "test_each_value_configures_exactly_its_agents[claude]", "t7-codex-render-only-for-codex"),
    ("setup.sh", 'if [ -n "$DO_CLAUDE" ] && [ ! -f "$GAME_DIR/.claude/settings.local.json" ]',
     'if [ ! -f "$GAME_DIR/.claude/settings.local.json" ]',
     _T7S + "test_each_value_configures_exactly_its_agents[codex]", "t7-settings-local-only-for-claude"),
    # ...and setup says so plainly
    ("setup.sh", '    echo "  Claude Code: NOT CONFIGURED -- CLAUDE.md has no paths filled in (this run: --agent $AGENT_SEL)."',
     "    :", _T7S + "test_each_value_configures_exactly_its_agents[codex]", "t7-says-claude-unconfigured"),
    ("setup.sh", '    echo "  Codex:       NOT CONFIGURED -- .codex/hooks.json is not rendered for this folder, so Codex runs here"',
     "    :", _T7S + "test_each_value_configures_exactly_its_agents[claude]", "t7-says-codex-unconfigured"),
    ("setup.sh", 'if [ "$AGENT_SEL" = "codex" ] && { [ "$CODEX_STATE" != "configured" ]',
     'if false && { [ "$CODEX_STATE" != "configured" ]',
     _T7S + "test_codex_by_name_exits_1_when_codex_cannot_be_configured", "t7-codex-by-name-unconfigured-is-exit-1"),
    # partial state: any placeholder left makes the file unconfigured
    ("setup.sh", 'elif grep -q "$DOC_PLACEHOLDERS" "$GAME_DIR/$DOC"; then', "elif grep -q '{{GAME_ROOT}}' \"$GAME_DIR/$DOC\"; then",
     _T7S + "test_a_half_filled_claude_md_is_completed_by_a_rerun", "t7-every-placeholder-completed"),
    # the root paths file carries the interpreter
    ("setup.sh", "    printf 'SKYGUARD_PYTHON=%s\\n' \"$(sq \"$PYTHON_PATH\")\"", "    :",
     _T7S + "test_the_paths_file_is_written_at_the_root_and_no_3x_copy_is_created", "t7-paths-file-records-python"),
    # the 3.x paths file under the union reader: retired only when it adds nothing and can be compared
    ("setup.sh", '    if [ -n "$extra" ]; then', "    if false; then",
     _T7S + "test_a_3x_paths_file_that_adds_a_folder_is_kept_and_its_folders_named", "t7-legacy-adding-a-folder-kept"),
    ("setup.sh", '    if [ -z "$PY_FOUND" ] || [ "${MIG_RC-unset}" != 0 ]; then', '    if [ -z "$PY_FOUND" ]; then',
     _T7S + "test_a_3x_paths_file_is_kept_while_the_migration_has_not_finished", "t7-legacy-kept-until-migrated"),
    ("setup.sh", 'mv -n "$PATHS_ENV_3X" "$dest/skyrim-paths.env"', 'cp "$PATHS_ENV_3X" "$dest/skyrim-paths.env"',
     _T7S + "test_a_3x_paths_file_that_adds_nothing_is_retired_by_a_move", "t7-legacy-retired-by-a-move"),
    # the 3.x hook-in
    ("setup.sh", "    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $PY_FOUND -B \"$PY_ROOT/tools/migrate-3x.py\" \\",
     "    true \\", _T7S + "test_setup_runs_the_migration", "t7-migration-runs"),
    ("setup.sh", '    if [ "$MIG_RC" != 0 ]; then', "    if false; then",
     _T7S + "test_a_failed_migration_is_said_and_setup_carries_on", "t7-migration-failure-said"),
    ("setup.sh", '[ -d "$v" ] && [ "${SKYRIM_PREFER_DETECTED:-}" != 1 ]', '[ "${SKYRIM_PREFER_DETECTED:-}" != 1 ]',
     _T7S + "test_a_3x_path_is_not_applied_when_it_is_not_a_folder_or_detection_is_preferred[not-a-folder]",
     "t7-carried-path-must-be-a-folder"),
    ("setup.sh", '[ -d "$v" ] && [ "${SKYRIM_PREFER_DETECTED:-}" != 1 ]', '[ -d "$v" ]',
     _T7S + "test_a_3x_path_is_not_applied_when_it_is_not_a_folder_or_detection_is_preferred[prefer-detected]",
     "t7-prefer-detected-honoured"),
    ("setup.sh", '    CONFIG_DIR=$(pick_3x CONFIG_DIR "$CONFIG_DIR")', "    :",
     _T7S + "test_a_path_the_3x_install_used_is_kept_when_it_is_a_folder", "t7-carried-config-dir-applied"),
    # runtime ranges
    ("setup.sh", '            if [ "$NODE_MAJOR" -lt 24 ]; then', '            if [ "$NODE_MAJOR" -lt 22 ]; then',
     _T7S + "test_an_old_node_gets_a_plain_message[v22.12.0-True]", "t7-node-below-24-is-old"),
    ("setup.sh", "    if printf '%s\\n' \"$DOTNET_SDKS\" | grep -qE '^10\\.'; then",
     "    if printf '%s\\n' \"$DOTNET_SDKS\" | grep -qE '^(1[0-9])\\.'; then",
     _T7S + "test_the_dotnet_check_wants_exactly_major_10[sdk11-only]", "t7-dotnet-exactly-major-10"),
    ("setup.sh", "        if ! printf '%s\\n' \"$DOTNET_SDKS\" | grep -qxF \"$DOTNET_SDK_PIN\"; then", "        if false; then",
     _T7S + "test_the_dotnet_check_wants_exactly_major_10[sdk10-other-band]", "t7-dotnet-tested-band-noted"),
    ("setup.sh", '            if [ "$JAVA_MAJOR" -lt 17 ]; then', '            if [ "$JAVA_MAJOR" -lt 11 ]; then',
     _T7S + 'test_the_jdk_check[openjdk version "11.0.2" 2019-01-15-True-is too old for ReSaver CLI]',
     "t7-jdk-below-17-is-old"),
    ("setup.sh", "            elif ! which javac >/dev/null 2>&1; then", "            elif false; then",
     _T7S + 'test_the_jdk_check[openjdk version "21.0.12" 2026-07-15-False-No javac]', "t7-jre-without-javac-said"),
    ("setup.sh", "        3.10|3.11|3.12|3.13) echo", "        3.10|3.11|3.12|3.13|3.14) echo",
     _T7S + "test_the_python_range_message[3.14.0-outside the tested range 3.10-3.13 (newer than tested)]",
     "t7-python-tested-range"),
    # the instruction budget
    ('setup.sh', '    [ "$DOC_BYTES" -gt "$INSTRUCTION_BUDGET" ] || continue', "    continue",
     _T7S + "test_an_installed_agents_md_past_the_budget_is_warned_about[True]", "t7-budget-warning"),
    # the lock: off unless asked, and a lock that did not apply is not a success
    ("setup.sh", 'WANT_LOCK=""', "WANT_LOCK=1", _T7S + "test_the_lock_is_not_applied_without_lock", "t7-lock-off-by-default"),
    ("setup.sh", 'if [ -n "$WANT_LOCK" ] && [ "$LOCK_RC" != 0 ]; then', "if false; then",
     _T7S + "test_lock_runs_the_lock_and_a_failure_is_exit_1", "t7-lock-failure-is-exit-1"),
    # authorization-aware hints (a pin stripped from the REAL file turns the scan red)
    ("setup.sh", '    echo "      winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade"',
     '    echo "      winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0"',
     "tests/test_install_hints.py::test_every_install_hint_is_in_an_authorized_form[setup.sh]", "t7-hint-no-upgrade"),
    ("setup.sh", 'hint_jdk() {\n    echo "    Manual step, for you to run (an agent may not install this):"',
     'hint_jdk() {\n    echo "    For you to run:"',
     "tests/test_install_hints.py::test_every_install_hint_is_in_an_authorized_form[setup.sh]", "t7-hint-jdk-is-manual"),
    ("README.md", "   Install Node.js **24.19.0**, the version this toolkit was tested with. For you to run, in Windows Terminal:",
     "   Install Node.js **24.19.0**, the version this toolkit was tested with. In Windows Terminal:",
     "tests/test_install_hints.py::test_every_install_hint_is_in_an_authorized_form[README.md]", "t7-hint-readme-label"),
]
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in _T7]

# ---- Plan 8 T7 fix round 1 (review T7-review.md): one twin per fix ------------------------------------------------
_T7W = "tests/test_wrapper_python.py::"
_T7F = [
    # C1: the Documents probe asks for UTF-8 ...
    ("setup.sh", '"[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath(\'MyDocuments\')"',
     '"[Environment]::GetFolderPath(\'MyDocuments\')"',
     _T7S + "test_a_non_ascii_documents_folder_reaches_the_paths_file_as_utf8", "t7f-documents-probe-utf8"),
    # ... and the paths file is read back with the guard's own reader
    ("setup.sh", '        _guard=$(skypaths readback 2>/dev/null) && _grc=0 || _grc=$?', "        :",
     _T7S + "test_a_paths_file_the_guard_cannot_read_is_a_loud_failure", "t7f-readback-with-the-guards-reader"),
    ("setup.sh", 'if [ -n "$PATHS_BAD" ]; then', "if false; then",
     _T7S + "test_a_paths_file_the_guard_cannot_read_is_a_loud_failure", "t7f-unreadable-paths-file-is-exit-1"),
    # I2: values spliced into sed replacements are escaped
    ("setup.sh", "sed_repl() { printf '%s' \"$1\" | sed -e 's/[\\\\|&]/\\\\&/g'; }", "sed_repl() { printf '%s' \"$1\"; }",
     _T7S + "test_an_ampersand_in_the_root_is_filled_in_once_and_rerun_changes_nothing", "t7f-sed-replacement-escaped"),
    # M1: the 3.x file is judged by the reader's own union (read_install), which takes protection keys only -- an
    # ENV_KEYS loop here was an equivalent mutant (measured). The broken case is the reader's own flag:
    ("setup.sh", '    if getattr(cfg, "legacy_broken", False):', "    if False:",
     _T7S + "test_a_3x_paths_file_the_guard_cannot_read_is_kept_and_said[malformed]", "t7f-broken-legacy-kept-and-said"),
    # M5: a missing migration tool is "not finished"
    ("setup.sh", '    if [ -z "$PY_FOUND" ] || [ "${MIG_RC-unset}" != 0 ]; then',
     '    if [ -z "$PY_FOUND" ] || [ "${MIG_RC:-0}" != 0 ]; then',
     _T7S + "test_a_3x_paths_file_is_kept_when_the_migration_tool_is_missing", "t7f-missing-migration-is-not-finished"),
    # M6: --agent with no value; the stale hooks.json.new is removed even when nothing renders (Windows)
    ("setup.sh", "            if [ $# -lt 2 ]; then", "            if false; then",
     _T7S + "test_a_usage_error_exits_2_and_writes_nothing[no-value]", "t7f-agent-without-a-value"),
    ("setup.sh", '# A leftover from an interrupted run is never taken for the result: the renderer writes a fresh one.\nrm -f "$CODEX_DEFS.new"\n',
     '# A leftover from an interrupted run is never taken for the result: the renderer writes a fresh one.\n',
     _T7S + "test_a_stale_hooks_json_new_is_removed_even_when_nothing_renders", "t7f-stale-codex-new-removed"),
    # I1: the smoke expects Codex's hooks only on Windows, AGENTS.md wherever Codex is selected
    ("scripts/setup-smoke.sh", 'WANT_HOOKS="$WANT_X"; [ -n "$WIN" ] || WANT_HOOKS=""', 'WANT_HOOKS="$WANT_X"',
     "tests/test_setup_smoke.py::test_the_linux_legs_pass[all]", "t7f-smoke-hooks-windows-only"),
    # the wrappers read SKYGUARD_PYTHON from the root paths file
    ("tools/skyguard.sh", 'for cand in "${SKYGUARD_PYTHON:-}" "$file_py" python3 python "py -3"; do',
     'for cand in "${SKYGUARD_PYTHON:-}" python3 python "py -3"; do',
     _T7W + "test_the_bash_wrappers_use_the_recorded_interpreter[skyguard.sh]", "t7f-skyguard-sh-reads-the-file"),
    ("tools/skydoctor", 'for cand in "${SKYGUARD_PYTHON:-}" "$file_py" python3 python "py -3"; do',
     'for cand in "${SKYGUARD_PYTHON:-}" python3 python "py -3"; do',
     _T7W + "test_the_bash_wrappers_use_the_recorded_interpreter[skydoctor]", "t7f-skydoctor-reads-the-file"),
    ("tools/skydoctor", '    if [ -e "$cand" ]; then run=("$cand"); else IFS=\' \' read -r -a run <<< "$cand"; fi',
     '    IFS=\' \' read -r -a run <<< "$cand"',
     _T7W + "test_the_environment_still_wins_over_the_file[skydoctor]", "t7f-skydoctor-path-is-one-word"),
    ("tools/skyguard.cmd", 'if defined SG_FILE_PY if exist "%SG_FILE_PY%" set "SKYGUARD_PYTHON=%SG_FILE_PY%"',
     'rem', _T7W + "test_the_cmd_twins_use_the_recorded_interpreter[space-skyguard.cmd]", "t7f-skyguard-cmd-reads-the-file"),
    ("tools/skydoctor.cmd", 'if defined SG_FILE_PY if exist "%SG_FILE_PY%" set "SKYGUARD_PYTHON=%SG_FILE_PY%"',
     'rem', _T7W + "test_the_cmd_twins_use_the_recorded_interpreter[space-skydoctor.cmd]", "t7f-skydoctor-cmd-reads-the-file"),
    # toolchain-check: exactly the SDK major Spriggit targets
    ("tools/toolchain-check.sh", "awk -v n=\"$NEED\" '$1+0==n{f=1} END{exit !f}'", "awk -v n=\"$NEED\" '$1+0>=n{f=1} END{exit !f}'",
     "tests/test_toolchain_check.py::test_pinned_version_on_an_sdk_11_only_machine_cannot_work",
     "t7f-toolchain-dotnet-exact-major"),
]
MUTATIONS += [pytest.param(*m[:4], id=m[4], marks=CMD_ONLY if m[4] in (
    "t7f-skyguard-cmd-reads-the-file", "t7f-skydoctor-cmd-reads-the-file") else ()) for m in _T7F]
# ---- Plan 8 T8 fix round 1 (lane p8-paths): review I1 (both halves), M2, P1, P2. One twin per clause. ---------------
_P8R1 = "tests/test_self_guard_creators.py::"
_P8R1_SR = "tools/skyguard/self_rules.py"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # I1 reader: protection roots are the union of both files...
    (_P8T8_PA, "    for key in PROTECTION_KEYS:\n", "    for key in ():\n",
     _P8T8 + "test_an_empty_root_file_hides_no_legacy_root[SKYRIM_MO2_MODS]", "p8r1-union-protection-keys"),
    # ...of the protection keys only (non-protection values from the root file)
    (_P8T8_PA, "    for key in PROTECTION_KEYS:\n", "    for key in ENV_KEYS + TOOL_KEYS:\n",
     _P8T8 + "test_non_protection_values_come_from_the_root_file_when_present", "p8r1-union-protection-only"),
    # ...of the legacy file's WELL-FORMED values (its malformed entries do not refuse everything)
    (_P8T8_PA, "    return cfg._replace(file=file, legacy=added, legacy_broken=bool(leg.malformed))",
     "    return cfg._replace(file=file, legacy=added, legacy_broken=bool(leg.malformed), "
     "malformed=cfg.malformed + leg.malformed)",
     _P8T8 + "test_a_broken_legacy_leftover_does_not_matter_once_the_root_file_exists", "p8r1-legacy-malformed-ignored"),
    # ...an undecodable legacy leftover adds nothing (no refusal)
    (_P8T8_PA, "    except (OSError, ValueError):\n        return cfg._replace(legacy_broken=True)",
     "    except ():\n        return cfg._replace(legacy_broken=True)",
     _P8T8 + "test_an_undecodable_legacy_leftover_adds_nothing_once_the_root_file_exists",
     "p8r1-legacy-unreadable-adds-nothing"),
    # ...the notice shows while the legacy file adds a root...
    (_P8T8_PA, _P8T8_UNION, "            file[key], added = tuple(file.get(key, ())) + extra, False",
     _P8T8 + "test_the_notice_shows_while_the_legacy_file_still_adds_a_root", "p8r1-legacy-flag-set"),
    # ...and only a root the root file does not already list counts
    (_P8T8_PA, "        extra = tuple(v for v in leg.file.get(key, ()) if v not in file.get(key, ()))",
     "        extra = tuple(leg.file.get(key, ()))",
     _P8T8 + "test_a_legacy_file_saying_nothing_new_is_not_in_use", "p8r1-only-new-roots-count"),
    # the review's reproduction, end to end through run.py
    (_P8T8_PA, "    for key in PROTECTION_KEYS:\n", "    for key in ('SKYRIM_GAME_ROOT',):\n",
     _P8T8 + "test_run_still_guards_a_legacy_mo2_folder_after_the_root_file_is_touched", "p8r1-union-through-run"),
    # M2: the notice follows the guard's root, and run.py passes it on both agents
    ("tools/skyguard/session.py", "paths.legacy_notice(project if root is None else root)", "paths.legacy_notice(project)",
     _P8T8 + "test_the_notice_is_keyed_on_the_root_the_guard_reads", "p8r1-notice-keyed-on-root"),
    ("tools/skyguard/run.py", "session.run_status(a.project, req.source, a.bash, audit, root=a.root_win or a.root)",
     "session.run_status(a.project, req.source, a.bash, audit, root=None)",
     _P8T8 + "test_run_keys_the_session_notice_on_its_root[codex-session-start]", "p8r1-codex-passes-root"),
    ("tools/skyguard/run.py", "                                              root=a.root_win or a.root)",
     "                                              root=None)",
     _P8T8 + "test_run_keys_the_session_notice_on_its_root[claude-session-kb-guard]", "p8r1-claude-passes-root"),
    # I1 rules: touch / install / mklink aimed at a guard file (fix round 2: re-anchored, the routes carry a verb)
    (_P8R1_SR, "        out += [(w, False, CREATES) for w in _touch_operands(argv)]", "        pass",
     _P8R1 + "test_a_creator_aimed_at_a_guard_file_is_a_guard_write[touch]", "p8r1-touch"),
    (_P8R1_SR, "            j += 1                                       # its value", "            pass  # its value",
     _P8R1 + "test_a_creator_elsewhere_is_not[reference-is-read]", "p8r1-touch-valued-option"),
    (_P8R1_SR, "        out += [(w, False) for w in _install_targets(argv)]", "        pass",
     _P8R1 + "test_a_creator_aimed_at_a_guard_file_is_a_guard_write[install]", "p8r1-install"),
    (_P8R1_SR, "        if w in _INSTALL_VALUED:", "        if False:",
     _P8R1 + "test_a_creator_aimed_at_a_guard_file_is_a_guard_write[install-target-dir]", "p8r1-install-valued"),
    (_P8R1_SR, "    return words[-1:] if len(words) >= 2 else []", "    return words",
     _P8R1 + "test_a_creator_elsewhere_is_not[install-source-is-read]", "p8r1-install-dest-only"),
    (_P8R1_SR, '        elif w in ("-d", "--directory"):\n            dirs = True',
     '        elif w in ("-d", "--directory"):\n            dirs = False',
     _P8R1 + "test_a_creator_aimed_at_a_guard_file_is_a_guard_write[install-d]", "p8r1-install-d"),
    (_P8R1_SR, "        out += [(w, False, CREATES) for w in words[:1]]", "        pass",
     _P8R1 + "test_a_creator_aimed_at_a_guard_file_is_a_guard_write[mklink]", "p8r1-mklink-link-name"),
    (_P8R1_SR, "        out += [(w, False, LINKS) for w in words[1:2]]", "        pass",
     _P8R1 + "test_a_link_to_a_guard_file_is_a_guard_write[mklink-hard]", "p8r1-mklink-target"),
    # P2: a link whose source is a guard file
    (_P8R1_SR, "        out += [(w, False, LINKS) for w in (words[:-1] if len(words) >= 2 else words)]", "        pass",
     _P8R1 + "test_a_link_to_a_guard_file_is_a_guard_write[ln-hard]", "p8r1-ln-source"),
    (_P8R1_SR, '        out += [(w, False, LINKS) for w in list(ps_param(e, "target")) + list(ps_param(e, "value"))]',
     '        out += [(w, False, LINKS) for w in list(ps_param(e, "value"))]',
     _P8R1 + "test_a_link_to_a_guard_file_is_a_guard_write[new-item-hardlink]", "p8r1-new-item-target"),
    (_P8R1_SR, '        out += [(w, False, LINKS) for w in list(ps_param(e, "target")) + list(ps_param(e, "value"))]',
     '        out += [(w, False, LINKS) for w in list(ps_param(e, "target"))]',
     _P8R1 + "test_a_link_to_a_guard_file_is_a_guard_write[new-item-symlink-value]", "p8r1-new-item-value"),
    (_P8R1_SR, "any(t.lower() in _PS_LINK_TYPES\n", "True or any(t.lower() in _PS_LINK_TYPES\n",
     _P8R1 + "test_a_link_elsewhere_is_not[new-item-file-value-is-content]", "p8r1-new-item-link-types-only"),
    # P1: an NTFS stream suffix names the same file -- on the shell and through the Write tool
    (_P8R1_SR, "        s = strip_stream(word)", "        s = None",
     _P8R1 + "test_a_stream_of_a_guard_file_is_the_guard_file[root-data]", "p8r1-stream-stripped"),
    (_P8R1_SR, "    if not m or (m.group(1) is None and len(m.group(2)) == 1):", "    if not m:",
     _P8R1 + "test_a_drive_relative_word_is_not_read_as_a_stream", "p8r1-stream-not-a-drive"),
    (_P8R1_SR, "            a = resolve_own(lambda x: targets.resolve_operand(e, x, cwd, env), w)",
     "            a = targets.resolve_operand(e, w, cwd, env)",
     _P8R1 + "test_a_stream_of_a_guard_file_is_the_guard_file[hook-data]", "p8r1-stream-shell-wired"),
    ("tools/skyguard/write_rules.py",
     '        a = self_rules.resolve_own(lambda x: targets.resolve(x, cwd, "bash", env_), file_path)',
     '        a = targets.resolve(file_path, cwd, "bash", env_)',
     _P8R1 + "test_the_write_tool_on_a_guard_file_stream_gets_the_note", "p8r1-stream-write-tool-wired"),
]]

# ---- Plan 8 T8 fix round 2 (lane p8-paths): re-review N1, N2, N4. One twin per clause. ---------------------------
_P8R2_SP = "tools/skyguard/shellparse.py"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # N1: a broken legacy file beside a root file is never silent -- undecodable...
    (_P8T8_PA, "        return cfg._replace(legacy_broken=True)", "        return cfg",
     _P8T8 + "test_a_broken_legacy_file_beside_a_root_file_raises_the_attention_notice[undecodable]",
     "p8r2-unreadable-legacy-flagged"),
    # ...or naming a protection value the guard cannot read...
    (_P8T8_PA, "legacy_broken=bool(leg.malformed))", "legacy_broken=False)",
     _P8T8 + "test_a_broken_legacy_file_beside_a_root_file_raises_the_attention_notice[malformed]",
     "p8r2-malformed-legacy-flagged"),
    # ...and the flag reaches the session-start notice
    (_P8T8_PA, "    if cfg.legacy_broken:\n        return LEGACY_BROKEN", "    if False:\n        return LEGACY_BROKEN",
     _P8T8 + "test_a_broken_legacy_file_beside_a_root_file_raises_the_attention_notice[relative]",
     "p8r2-attention-notice-shown"),
    # N2: cp -l / --link / -s / --symbolic-link, combined short flags, recursive holder
    (_P8R1_SR, '    elif p == "cp" and _cp_links(argv):', "    elif False:",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[cp-l]", "p8r2-cp-links"),
    (_P8R1_SR, '    return any(w in ("--link", "--symbolic-link") or _short(w, "ls") for w in argv)', "    return True",
     _P8R1 + "test_a_plain_copy_or_an_outside_link_is_not[plain-copy-reads-it]", "p8r2-cp-links-only"),
    (_P8R1_SR, 'w in ("--link", "--symbolic-link") or', "w in () or",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[cp-link]", "p8r2-cp-long-options"),
    (_P8R1_SR, "any(c in w[1:] for c in letters)", "w[1:] in letters",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[cp-al-folder]", "p8r2-short-cluster"),
    (_P8R1_SR, '        rec = any(_short(w, "aRr") or w in ("--recursive", "--archive") for w in argv)', "        rec = False",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[cp-al-folder]", "p8r2-cp-recursive-holder"),
    # N2: fsutil hardlink create <new> <existing>
    (_P8R1_SR, '    elif p == "fsutil" and [w.lower() for w in argv[:2]] == ["hardlink", "create"]:',
     '    elif p == "fsutil":',
     _P8R1 + "test_a_plain_copy_or_an_outside_link_is_not[fsutil-list]", "p8r2-fsutil-create-only"),
    (_P8R1_SR, "        out += [(w, False, LINKS) for w in argv[3:4]]", "        pass",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[fsutil-powershell]", "p8r2-fsutil-existing"),
    (_P8R1_SR, "        out += [(w, False, CREATES) for w in argv[2:3]]", "        pass",
     _P8R1 + "test_fsutil_creating_the_link_name_at_a_guard_path_creates_it", "p8r2-fsutil-new-name"),
    # N2: New-Item -Type (alias of -ItemType)
    (_P8R1_SR, ' + list(ps_param(e, "type")))', ")",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[new-item-type-alias]", "p8r2-new-item-type-alias"),
    # N2: .NET CreateSymbolicLink / CreateHardLink -- the parser names them, the rule reads (link, target)
    (_P8R2_SP, '        if m in _IO_LINK_METHODS:\n            return "io.file.link"',
     '        if False:\n            return "io.file.link"',
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[dotnet-file-hardlink]", "p8r2-parser-file-link"),
    (_P8R2_SP, '    if m in _IO_LINK_METHODS:\n        return "io.directory.link"',
     '    if False:\n        return "io.directory.link"',
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[dotnet-directory-symlink]",
     "p8r2-parser-directory-link"),
    (_P8R1_SR, "        out += [(w, False, CREATES) for w in argv[:1]] + [(w, False, LINKS) for w in argv[1:2]]",
     "        out += [(w, False, CREATES) for w in argv[:1]]",
     _P8R1 + "test_more_link_spellings_to_a_guard_file_get_the_note[dotnet-file-symlink]", "p8r2-dotnet-target"),
    # N4: the note's verb -- carried by the route, rendered by the rule
    ("tools/skyguard/shell_rules.py", 'advise("S-SELF-GUARD", file=own[1], op=own[2])',
     'advise("S-SELF-GUARD", file=own[1], op="edits")',
     _P8R1 + "test_the_note_says_what_the_command_does[touch]", "p8r2-verb-rendered"),
    (_P8R1_SR, "    return [r if len(r) == 3 else (r[0], r[1], EDITS) for r in out]",
     "    return [r if len(r) == 3 else (r[0], r[1], CREATES) for r in out]",
     _P8R1 + "test_the_note_says_what_the_command_does[redirect]", "p8r2-verb-default-edits"),
]]


# Narrow Python parser-inspection literal handling (watched false-refusal RED).
MUTATIONS += [
    pytest.param('tools/skyguard/self_rules.py', '    if not deletes._PYTHON.fullmatch(program):\n', '    if False:\n', 'tests/test_pricing_inspection.py::test_inspection_comments_are_data_and_other_interpreters_stay_conservative[posix]', id='review-inspection-python-only'),
    pytest.param('tools/skyguard/self_rules.py', '    if not code.isascii():\n', '    if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[unicode-posix]', id='review-inspection-ascii-only'),
    pytest.param('tools/skyguard/self_rules.py', '    except (SyntaxError, ValueError, RecursionError):\n        return code\n', '    except (SyntaxError, ValueError, RecursionError):\n        return " " * len(code)\n', 'tests/test_pricing_inspection.py::test_inspection_parser_failure_preserves_original_state_refusal[ast]', id='review-inspection-ast-fallback'),
    pytest.param('tools/skyguard/self_rules.py', '    if any(type(node) not in permitted for node in nodes):\n', '    if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[f-string-posix]', id='review-inspection-ast-grammar'),
    pytest.param('tools/skyguard/self_rules.py', '            if not (node.level == 0 and node.module == "skyguard" and len(node.names) == 1\n                    and node.names[0].name == "shellparse" and node.names[0].asname is None):\n', '            if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[unknown-import-posix]', id='review-inspection-exact-import'),
    pytest.param('tools/skyguard/self_rules.py', '        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in {"print", "shellparse"}:\n', '        if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[comprehension-print-binding-posix]', id='review-inspection-protected-binding'),
    pytest.param('tools/skyguard/self_rules.py', '        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):\n', '        if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[attribute-write-posix]', id='review-inspection-attribute-store'),
    pytest.param('tools/skyguard/self_rules.py', '            if not ((isinstance(callee, ast.Name) and callee.id == "print")\n                    or (imported and isinstance(callee, ast.Attribute) and callee.attr == "parse"\n                        and isinstance(callee.value, ast.Name) and callee.value.id == "shellparse")):\n', '            if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[unknown-call-posix]', id='review-inspection-allowed-call'),
    pytest.param('tools/skyguard/self_rules.py', '                    or (imported and isinstance(callee, ast.Attribute) and callee.attr == "parse"\n', '                    or (isinstance(callee, ast.Attribute) and callee.attr == "parse"\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[parser-without-import-posix]', id='review-inspection-parser-import-required'),
    pytest.param('tools/skyguard/self_rules.py', '    except (tokenize.TokenError, IndentationError, SyntaxError):\n        return code\n', '    except (tokenize.TokenError, IndentationError, SyntaxError):\n        return " " * len(code)\n', 'tests/test_pricing_inspection.py::test_inspection_parser_failure_preserves_original_state_refusal[tokenize]', id='review-inspection-token-fallback'),
    pytest.param('tools/skyguard/self_rules.py', '        if token.type in (tokenize.STRING, tokenize.COMMENT):\n', '        if token.type in (tokenize.COMMENT,):\n', 'tests/test_pricing_inspection.py::test_exact_historical_k18699_shell_quoting_is_inspection_data', id='review-inspection-string-mask'),
    pytest.param('tools/skyguard/self_rules.py', '        if token.type in (tokenize.STRING, tokenize.COMMENT):\n', '        if token.type in (tokenize.STRING,):\n', 'tests/test_pricing_inspection.py::test_inspection_comments_are_data_and_other_interpreters_stay_conservative[posix]', id='review-inspection-comment-mask'),
    pytest.param('tools/skyguard/self_rules.py', '        delete_api = grep(deletes.INTERP_DELETE, _python_inspection_text(e.program, code))\n', '        delete_api = grep(deletes.INTERP_DELETE, code)\n', 'tests/test_pricing_inspection.py::test_exact_historical_k18699_shell_quoting_is_inspection_data', id='review-inspection-trigger-integration'),
]

MUTATIONS += [pytest.param('tools/skyguard/self_rules.py', '    except (SyntaxError, ValueError, RecursionError):\n', '    except (SyntaxError, ValueError):\n', 'tests/test_pricing_inspection.py::test_inspection_parser_failure_preserves_original_state_refusal[ast-recursion]', id='review-inspection-ast-recursion-fallback')]
