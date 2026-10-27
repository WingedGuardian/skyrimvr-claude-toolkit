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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import REPO, login_name

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
PROBE_RESULT_MUTATIONS = [pytest.param(
    "agent/guards/adapters/" + source,
    "    local n o b OUT RC NUL WD",
    "    local n o b " + " ".join(item for item in ("OUT", "RC", "NUL", "WD") if item != field),
    "tests/test_python_probe_state.py::test_py_good_preserves_caller_field[healthy-" + field + "-" + source + "]",
    id="probe-result-" + source.replace("/", "-").replace(".sh", "") + "-" + field,
) for source in (
    "claude/backup-before-edit.sh", "claude/protect-bash.sh", "claude/protect-files.sh",
    "claude/protect-mcp.sh", "claude/session-kb-guard.sh", "claude/snapshot-before-tool.sh",
    "codex/skyguard-entry.sh",
) for field in ("OUT", "RC", "NUL", "WD")]

ANSI_C_MUTATIONS = [pytest.param("tools/skyguard/shellparse.py", before, after,
    "tests/test_shellparse.py::test_ansi_c_arguments_match_stdout_only_bash_oracle", id="ansi-c-" + name)
    for before, after, name in (
        ('                elif nx in "01234567xuU":', '                elif False:', 'numeric-escapes'),
        ('                    data = ("\\\\" + nx).encode("utf-8", "surrogateescape")',
         '                    data = nx.encode("utf-8", "surrogateescape")', 'unknown-backslash'),
        ('                truncated = bool(separator)', '                truncated = False', 'nul-truncation'),
        ('    if value >= 0x80000000:', '    if False:', 'dropped-unicode-tail'),
    )]

SPRIGGIT_RACE_MUTATIONS = [pytest.param("tools/spriggit_publish.py", before, after,
    "tests/test_spriggit_publication_races.py::" + test, id="spriggit-race-" + name)
    for before, after, test, name in (
        ('            _rename_new(stage, target)', '            os.replace(stage, target)',
         'test_new_destination_at_publication_is_not_clobbered', 'exclusive-publication'),
        ('                    _rename_new(backup, target)', '                    os.replace(backup, target)',
         'test_publication_failure_with_new_destination_keeps_both_versions', 'exclusive-rollback'),
        ('            if moved and _state(backup) != observed:', '            if False:',
         'test_change_during_retirement_is_restored_with_its_bytes', 'retired-state'),
    )]

EVIDENCE_STATUS_MUTATIONS = [pytest.param(path, before, after, test, id="evidence-status-" + name)
    for path, before, after, test, name in (
        ('scripts/check_ci_leg.py', 'and re.search(r"\\sPASSED(?:\\s+\\[\\s*\\d+%\\])?\\s*$", line)',
         'and " PASSED" in line', 'tests/test_ci_leg.py::test_pass_text_inside_case_or_skip_reason_is_not_an_outcome', 'ci-outcome'),
        ('tools/bench-vr.py', '        if not math.isfinite(value) or value < 0 or (positive and value == 0):',
         '        if False:', 'tests/test_bench_vr.py::test_invalid_timing_is_refused_before_contacting_devbench', 'finite-inputs'),
        ('tools/bench-vr.py', '            if not pooled["samples"]:', '            if False:',
         'tests/test_bench_vr.py::test_empty_retained_windows_are_a_failed_measurement_and_restore', 'empty-window'),
        ('tools/bench-vr.py', '    return 2 if not compared else (1 if missing else 0)', '    return 0',
         'tests/test_bench_vr.py::test_comparison_without_any_joint_measurement_is_not_success', 'empty-comparison'),
        ('tools/nexus-render.py', '        partial = bool(req.get("errors")) or len(usable) != len(nodes)',
         '        partial = False', 'tests/test_nexus.py::test_requirements_graphql_errors_are_partial_even_with_a_nodes_list', 'partial-requirements'),
        ('scripts/guard-corpus/intent_diff.py', '        if prev is not None:', '        if prev is not None and sup is None:',
         'tests/test_intent_diff.py::test_existing_row_uses_previous_decision_even_when_it_supersedes_legacy', 'previous-intent'),
    )]

COMPOUND_SNAPSHOT_MUTATIONS = [pytest.param("tools/skyguard/snapshot.py",
    '    parsed = shellparse.parse(command, "bash" if bash_prefix else "powershell")',
    '    return True\n    parsed = shellparse.parse(command, "bash" if bash_prefix else "powershell")',
    'tests/test_snapshot_nested.py::test_read_prefix_does_not_skip_a_mutating_command_snapshot', id='snapshot-complete-command')]

PRESERVATION_STATUS_MUTATIONS = [pytest.param(path, before, after, test,
    id="preservation-status-" + name) for path, before, after, test, name in (
    ("tools/skyguard/health.py",
     '            hooked = sum(min(n, observed.get(key, 0)) for key, n in expected.items())',
     '            hooked = min(called, sum(observed.values()))',
     'tests/test_skyguard_coverage.py::test_shell_receipts_cannot_cover_unobserved_mcp_calls', 'mcp-attribution'),
    ("tools/kb-guard.sh", '    [ -z "$failed" ] && exit 1', '    exit 1',
     'tests/test_kb_guard.py::test_an_alarm_cannot_mask_another_files_failed_snapshot', 'mixed-alarm'),
    ("tools/skyguard/snapshot.py", '"-fprintf", "-fls")', '"-fprintf")',
     'tests/test_snapshot_nested.py::test_read_prefix_does_not_skip_a_mutating_command_snapshot', 'find-fls'),
)]

RELOCK_RETRY_MUTATIONS = [pytest.param(path, before, after, "tests/test_relock_retries.py::" + test,
    id="relock-retry-" + name) for path, before, after, test, name in (
    ("tools/skyguard/lock.py", "        if not relock_failed(res):", "        if True:",
     "test_failed_expiry_relock_is_retried_until_success", "success-before-retire"),
    ("tools/skyguard/cli.py", '(x["kind"] == "lock" or not state.lift_expired(x, now))',
     'not state.lift_expired(x, now)', "test_unrelated_guard_write_keeps_expired_lock_obligation", "guard-writer"),
    ("tools/skyguard/doctor.py", 'x["kind"] != "lock" and _expired(x, now)', '_expired(x, now)',
     "test_doctor_does_not_report_failed_relock_as_cleared", "doctor-writer"),
    ("tools/skyguard/session.py", '"agent": "system", "created": now, "expires_at": now}] if failure else []',
     '"agent": "system", "created": now, "expires_at": now}] if False else []',
     "test_failed_session_relock_keeps_an_expired_retry_without_success_claim", "session-writer"),
)]
RELOCK_RETRY_MUTATIONS += [pytest.param("tools/skyguard/health.py",
    'native_name = tool[len("functions."):] if tool.startswith("functions.") else tool',
    'native_name = tool.rsplit(".", 1)[-1]',
    "tests/test_skyguard_coverage.py::test_mcp_native_looking_tool_names_keep_their_identity",
    id="coverage-dotted-mcp-keeps-identity")]
RELOCK_RETRY_MUTATIONS += [pytest.param("tools/skyguard/lock.py",
    '    pending += [dict(x, expires_at=min(x["expires_at"], now)) for x in evicted if x["kind"] == "lock"]',
    '    pending += []',
    "tests/test_relock_retries.py::test_live_lift_cap_preserves_each_files_relock_obligation",
    id="relock-retry-live-cap-preserves-scope")]

CLI_FLOOR_MUTATIONS = [pytest.param(path, before, after, "tests/test_cli_python_floor.py::" + test,
    id="cli-floor-" + name) for path, before, after, test, name in (
    ("tools/skydoctor", "sys.version_info[:2] >= (3, 10)", "sys.version_info[:2] >= (3, 8)",
     "test_bash_cli_checks_version_and_disables_manager_installation", "doctor-minimum"),
    ("tools/skydoctor", "export PYTHON_MANAGER_AUTOMATIC_INSTALL=false", "export PYTHON_MANAGER_AUTOMATIC_INSTALL=true",
     "test_bash_cli_checks_version_and_disables_manager_installation", "no-auto-install"),
    ("tools/skydoctor.cmd", "if errorlevel 1 goto probe", "rem incompatible interpreter accepted",
     "test_cmd_cli_checks_version_and_disables_manager_installation", "cmd-minimum"),
)]

MUTATIONS = E2E_VERDICT_MUTATIONS + PROBE_RESULT_MUTATIONS + ANSI_C_MUTATIONS + SPRIGGIT_RACE_MUTATIONS + EVIDENCE_STATUS_MUTATIONS + COMPOUND_SNAPSHOT_MUTATIONS + PRESERVATION_STATUS_MUTATIONS + RELOCK_RETRY_MUTATIONS + CLI_FLOOR_MUTATIONS + [
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
    pytest.param("tools/skyguard/session.py", "    with state.locked(str(sf)):", "    if True:",
                 "tests/test_skyguard_session_start.py::test_session_reads_the_state_to_be_rewritten_under_the_writer_lock",
                 id="review-session-state-read-locked"),
    pytest.param("tools/skyguard/session.py", "    lock._assert_sandbox(sf)\n    with state.locked(str(sf)):\n        st = state.read(str(sf))", "    st = state.read(str(sf))\n    lock._assert_sandbox(sf)\n    with state.locked(str(sf)):",
                 "tests/test_skyguard_session_start.py::test_session_start_preserves_a_newer_persistent_decision[True]",
                 id="review-session-state-refresh"),
    pytest.param("tools/skyguard/session.py", 'if source != "compact" and not st.rejected and (st.lifts or _engaged(root)):',
                 'if not st.rejected and (st.lifts or _engaged(root)):',
                 "tests/test_skyguard_session_start.py::test_compact_does_not_clear_or_report_lifts",
                 id="review-session-compact-state-read-only"),
    pytest.param("tools/skyguard/mcp_rules.py", "    if not is_devbench(name):\n        return Verdict()",
                 '    if len(parts) < 3 or parts[0] != "mcp" or not parts[1].startswith("devbench"):\n        return Verdict()',
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
        'if [ "$RC" -eq 0 ] && [ -z "$NUL" ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
        'if true; then',
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
        '    1:/*)          ROOT_WIN=',
        '    1:/__never__)  ROOT_WIN=',
        "tests/test_hooks.py::test_a_root_on_an_msys_mount_still_gets_its_windows_spelling",
        marks=CMD_ONLY,
        id="wrapper-msys-mount-root-spelling",
    ),
    # The same block must be the same CODE in every hook, or the protect-bash tests above stop covering
    # the other four.
    pytest.param(
        ".claude/hooks/snapshot-before-tool.sh",
        '    1:/*)          ROOT_WIN=',
        '    1:/__never__)  ROOT_WIN=',
        "tests/test_repo_invariants.py::test_every_hook_locates_its_install_with_the_same_block",
        id="wrapper-location-block-drift",
    ),
    # Git for Windows 2.55 (bash 5.3, MSYS2 runtime 3.6) reports $OSTYPE `cygwin`. Keying the Windows spelling on
    # msys* alone handed python.exe /d/... with conversion off: every call GUARD INERT (windows-latest CI, Git 2.55).
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        "cygwin*) [ -e /usr/bin/msys-2.0.dll ] && _MSYS=1 ;;",
        "cygwin*) ;;",
        "tests/test_hooks.py::test_a_drive_root_is_converted_to_its_windows_spelling[cygwin]",
        marks=CMD_ONLY,
        id="wrapper-git255-ostype-cygwin-drive",
    ),
    pytest.param(
        ".claude/hooks/protect-bash.sh",
        "cygwin*) [ -e /usr/bin/msys-2.0.dll ] && _MSYS=1 ;;",
        "cygwin*) ;;",
        "tests/test_hooks.py::test_a_root_on_an_msys_mount_still_gets_its_windows_spelling[cygwin]",
        marks=CMD_ONLY,
        id="wrapper-git255-ostype-cygwin-mount",
    ),
    pytest.param(
        ".codex/hooks/skyguard-entry.sh",
        "cygwin*) [ -e /usr/bin/msys-2.0.dll ] && _MSYS=1 ;;",
        "cygwin*) ;;",
        "tests/test_repo_invariants.py::test_the_codex_entry_locates_its_install_like_the_claude_hooks",
        id="wrapper-git255-ostype-codex-drift",
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
        "if command -v cygpath >/dev/null 2>&1; then\n    BASH_FOR_HOOKS=",
        "if false; then\n    BASH_FOR_HOOKS=",
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
    # kb-guard must name UNCOMMITTED local notes: nothing else makes an agent commit what it appended.
    pytest.param(
        "tools/kb-guard.sh",
        "status --porcelain -- KNOWLEDGEBASE.local.md 2>/dev/null)",
        "status --porcelain -- NO-SUCH-FILE 2>/dev/null)",
        "tests/test_kb_guard.py::test_uncommitted_local_notes_are_named_until_committed",
        id="kb-guard-uncommitted-notes-unchecked",
    ),
    # ...and only uncommitted ones: a clean or ignored file must not nag every session.
    pytest.param(
        "tools/kb-guard.sh",
        '    && [ -n "$dirty" ]; then',
        "    ; then",
        "tests/test_kb_guard.py::test_no_commit_note_outside_git_or_for_an_ignored_file",
        id="kb-guard-commit-note-on-clean-file",
    ),
    # The commit rule itself lives in the instructions every agent reads.
    pytest.param(
        "agent/instructions/core.md",
        "commit it alone by explicit path",
        "keep it by explicit path",
        "tests/test_kb_guard.py::test_the_instructions_tell_the_agent_to_commit_its_notes_and_never_push",
        id="core-md-kb-commit-rule-dropped",
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
        '        status, note = known(r)',
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

    # --- skse-load-status ---------------------------------------------------
    pytest.param(
        # Count verdicts from the rows instead of as each result line is applied: the sum is then x == x,
        # and a plugin given two verdicts reads as a clean log.
        "tools/skse-load-status.py",
        "    kinds = Counter(verdicts)",
        '    kinds = Counter(r["kind"] for r in rows if r["status"] != NO_RESULT)',
        "tests/test_skse_load_status.py::test_two_verdicts_for_one_plugin_is_an_accounting_mismatch",
        id="skse-status-accounting-tautological",
    ),
    pytest.param(
        # Require the version block on every verdict: VR's "does not appear to be an SKSE plugin" line has none,
        # so that plugin silently becomes "no result line".
        "tools/skse-load-status.py",
        r'    r"(?:\((?P<data>[0-9A-Fa-f]{8}) (?P<name>.*) (?P<ver>[0-9A-Fa-f]{8})\) )?"',
        r'    r"\((?P<data>[0-9A-Fa-f]{8}) (?P<name>.*) (?P<ver>[0-9A-Fa-f]{8})\) "',
        "tests/test_skse_load_status.py::test_vr_log_every_verdict_and_the_rows_sum",
        id="skse-status-version-block-required",
    ),
    pytest.param(
        # Take an unknown flag as the filter: "-x" then matches nothing and reads as "not installed".
        "tools/skse-load-status.py",
        "    if flags or len(args) > 1:",
        "    if len(args) > 1:",
        "tests/test_skse_load_status.py::test_bad_usage_is_exit_2",
        id="skse-status-unknown-flag-as-filter",
    ),
    pytest.param(
        # Pair a verdict with the plugin checked last (the VR order): every SE/AE "loaded correctly" line, printed
        # after all checks, lands on the wrong plugin.
        "tools/skse-load-status.py",
        '        row = by_name.get(basename(m.group("dll")).lower())',
        "        row = rows[-1] if rows else None",
        "tests/test_skse_load_status.py::test_se_verdicts_printed_after_all_checks_are_matched_by_name",
        id="skse-status-verdict-by-position",
    ),
    pytest.param(
        # Drop the GOG / Epic folders from the one list: a GOG SE/AE install's SKSE log is never found by the tools.
        "tools/skyguard/paths.py",
        'GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition", "Skyrim Special Edition GOG", "Skyrim Special Edition EPIC",',
        'GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition",',
        "tests/test_skse_load_status.py::test_discovery_finds_a_gog_se_folder",
        id="game-dirs-without-gog",
    ),
    pytest.param(
        # The same, judged by the guard: a DevBench save over an existing GOG save is no longer recognised.
        "tools/skyguard/paths.py",
        'GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition", "Skyrim Special Edition GOG", "Skyrim Special Edition EPIC",',
        'GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition",',
        "tests/test_skyguard_mcp.py::test_the_gog_and_epic_se_folders_are_documents_fallbacks",
        id="guard-game-dirs-without-gog",
    ),
    pytest.param(
        # GOG before Steam SE: a stale GOG plugins.txt would outrank the Steam one.
        "tools/skyguard/paths.py",
        'GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition", "Skyrim Special Edition GOG", "Skyrim Special Edition EPIC",',
        'GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition GOG", "Skyrim Special Edition", "Skyrim Special Edition EPIC",',
        "tests/test_paths_precedence.py::test_python_lookup",
        id="guard-game-dirs-gog-before-steam",
    ),
    pytest.param(
        # The JS twin of the list loses the GOG / Epic folders: Python and Node disagree on plugins.txt.
        "tools/xelib/active-plugins.js",
        "const GAME_FOLDERS = ['Skyrim VR', 'Skyrim Special Edition', 'Skyrim Special Edition GOG',",
        "const GAME_FOLDERS = ['Skyrim VR', 'Skyrim Special Edition',",
        "tests/test_paths_precedence.py::test_node_agrees_with_python",
        id="js-game-dirs-without-gog",
    ),
    pytest.param(
        # A SKYRIM_GAME_ROOT the guard's reader refuses quietly becomes "the toolkit folder".
        "tools/skyrim_paths.py",
        '    if any(key == "SKYRIM_GAME_ROOT" for _, key in cfg.malformed):',
        "    if False:",
        "tests/test_skyrim_paths.py::test_game_root_refuses_a_malformed_value_instead_of_falling_back",
        id="game-root-malformed-falls-back",
    ),
    pytest.param(
        # Ignore the environment: the paths file's value answers even when the user set SKYRIM_GAME_ROOT.
        "tools/skyrim_paths.py",
        '    if env.get("SKYRIM_GAME_ROOT"):',
        "    if False:",
        "tests/test_skyrim_paths.py::test_game_root_environment_beats_the_paths_file",
        id="game-root-env-ignored",
    ),
    pytest.param(
        # A SEVENZIP that names no file falls through to whatever 7-Zip is on PATH.
        "tools/skyrim_paths.py",
        '        return None, f"SEVENZIP is set to {override}, which is not a file"',
        "        pass",
        "tests/test_skyrim_paths.py::test_a_wrong_sevenzip_override_is_reported_not_replaced",
        id="sevenzip-wrong-override-falls-through",
    ),

    # --- archive-triage -----------------------------------------------------
    pytest.param(
        # Read "D" anywhere in the attributes: a file whose hex word contains a D vanishes as a "folder".
        "tools/archive-triage.py",
        '    return any("D" in t for t in letters)',
        '    return "D" in entry.get("Attributes", "")',
        "tests/test_archive_triage.py::test_a_file_whose_hex_attributes_contain_d_is_not_dropped_as_a_folder",
        id="archive-triage-hex-d-is-a-folder",
    ),
    pytest.param(
        # Print archive names raw: a CR or an escape sequence in an entry name acts on the terminal.
        "tools/archive-triage.py",
        '        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):',
        "        if False:",
        "tests/test_archive_triage.py::test_control_characters_in_entry_names_are_escaped",
        id="archive-triage-control-chars-raw",
    ),
    pytest.param(
        # Split the listing on every line break, a CR inside an entry name included.
        "tools/archive-triage.py",
        '    for ln in out.split("\\n") + [""]:',
        '    for ln in out.splitlines() + [""]:',
        "tests/test_archive_triage.py::test_control_characters_in_entry_names_are_escaped",
        id="archive-triage-cr-splits-an-entry",
    ),
    pytest.param(
        # Treat every destination as inside Data/: the SKSE loader, an ENB DLL or a `..` entry is no longer flagged.
        "tools/archive-triage.py",
        "    return dest, inside",
        "    return dest, True",
        "tests/test_archive_triage.py::test_destinations_outside_data_are_shown_and_flagged_not_refused",
        id="archive-triage-outside-data-unflagged",
    ),
    pytest.param(
        # Compare an empty CRC as a value: a listing without CRCs reads as CHANGED, a claim nobody measured.
        "tools/archive-triage.py",
        '            kind = "NO CRC" if not HEX8.fullmatch(crc) else ("SAME" if crc == crc_of(dest) else "CHANGED")',
        '            kind = "SAME" if crc == crc_of(dest) else "CHANGED"',
        "tests/test_archive_triage.py::test_a_missing_crc_is_no_crc_never_changed",
        id="archive-triage-no-crc-as-changed",
    ),
    pytest.param(
        # Run the VR-marker heuristic on SE/AE, where "no VR markers" means nothing.
        "tools/archive-triage.py",
        '    markers = None if runtime == "SE/AE" else [m.decode() for m in VR_MARKERS if m in b]',
        "    markers = [m.decode() for m in VR_MARKERS if m in b]",
        "tests/test_archive_triage.py::test_the_vr_heuristic_is_skipped_on_se_and_says_so",
        id="archive-triage-vr-heuristic-on-se",
    ),
    pytest.param(
        # Count every classified entry as NEW: the per-kind accounting must catch a counter that stops matching.
        "tools/archive-triage.py",
        '        out["tally"][kind] += 1',
        '        out["tally"]["NEW"] += 1',
        "tests/test_archive_triage.py::test_new_same_changed_and_the_rows_sum",
        id="archive-triage-tally-not-independent",
    ),
    pytest.param(
        # Exit 0 when an archive could not be listed: a batch run reads as clean.
        "tools/archive-triage.py",
        "    return 2 if unreadable else 0",
        "    return 0",
        "tests/test_archive_triage.py::test_an_unreadable_archive_is_exit_2_and_the_others_still_run",
        id="archive-triage-unreadable-exit-0",
    ),
    pytest.param(
        # Keep the trailing CR on a path from a Windows-written list: every archive is "not found".
        "tools/archive-triage.py",
        "    for a in (x.strip() for x in args if x.strip()):",
        "    for a in (x for x in args if x.strip()):",
        "tests/test_archive_triage.py::test_an_archive_argument_with_a_trailing_cr_is_found",
        id="archive-triage-trailing-cr-kept",
    ),
    pytest.param(
        # Forget ENB / ReShade folders: enbseries/ content no longer maps to the game folder, so it is not flagged.
        "tools/archive-triage.py",
        "        if sl in GAME_ROOT_DIRS and i + 1 < len(segs):",
        "        if False:",
        "tests/test_archive_triage.py::test_everything_outside_data_is_listed_by_default",
        id="archive-triage-enb-folders-unmapped",
    ),
    pytest.param(
        # Summarise outside-Data assets per folder like in-Data ones: twelve ENB files become one line.
        "tools/archive-triage.py",
        '            listed = [x for x in items if show_all or not x["inside"] or x["shown"].lower().endswith(CODE_EXT)]',
        '            listed = [x for x in items if show_all or x["shown"].lower().endswith(CODE_EXT)]',
        "tests/test_archive_triage.py::test_everything_outside_data_is_listed_by_default",
        id="archive-triage-outside-data-summarised",
    ),
    pytest.param(
        # List entries under no Data root only with --all: a readme or unknown folder bound for the game root is
        # hidden by default.
        "tools/archive-triage.py",
        "            if not is_fomod_meta(u):",
        "            if show_all and not is_fomod_meta(u):",
        "tests/test_archive_triage.py::test_everything_outside_data_is_listed_by_default",
        id="archive-triage-unmapped-hidden-by-default",
    ),

    # v4.0.0 review L4a M1: the SHORTEST shipped knowledgebase prefix won, handing v3.9.1's own text over as notes.
    pytest.param("tools/migrate-3x.py",
                 '    for spec in sorted(known["docs"].get(KB, []), key=lambda s: s["bytes"], reverse=True):',
                 '    for spec in known["docs"].get(KB, []):',
                 "tests/test_migrate_3x.py::test_the_longest_shipped_knowledgebase_prefix_wins_whatever_the_table_order",
                 id="migrate-3x-kb-shortest-prefix"),
    pytest.param("tools/migrate-3x.py",
                 '    for spec in sorted(known["docs"].get(KB, []), key=lambda s: s["bytes"], reverse=True):',
                 '    for spec in sorted(known["docs"].get(KB, []), key=lambda s: s["bytes"]):',
                 "tests/test_migrate_3x.py::test_notes_appended_to_the_391_knowledgebase_are_exactly_the_notes",
                 id="migrate-3x-kb-shortest-prefix-real-391"),

    # --- mod-install ----------------------------------------------------------
    *[pytest.param("tools/mod-install.py", find, replace, "tests/test_mod_install.py::" + test, id="mod-install-" + tid)
      for tid, find, replace, test in [
          ("config-overwritten", '            if (dest.name.lower().endswith(CFG_EXT) and not overwrite_cfg\n                    and not rel.lower().replace("\\\\", "/").startswith(GENERATED_DATA_DIRS)):',
           "            if False:", "test_an_existing_config_gets_a_new_file_beside_it"),
          ("no-backup", '            if target.is_file():                     # OVERWRITE, or an older <file>.new: keep the original first',
           '            if False:',
           "test_apply_installs_backs_up_and_writes_a_manifest"),
          ("writes-through-hardlink", "        os.replace(tmp, str(target))", "        shutil.copyfile(tmp, str(target))",
           "test_a_hardlinked_file_is_replaced_not_written_through"),
          ("rehash-mismatch-ignored", "                bad += 1", "                pass",
           "test_a_written_file_that_does_not_rehash_is_exit_1"),
          ("name-unchecked", "    bad = sorted(c for c in set(name) if c in NAME_BAD or ord(c) < 32 or ord(c) == 127)",
           "    bad = []", "test_a_name_that_cannot_be_a_file_name_is_refused"),
          ("unstaged-file-not-checked-first", "            if not src.is_file():", "            if False:",
           "test_a_file_7zip_did_not_stage_stops_before_any_write"),
          ("fomod-groups-merged", "        if len(groups) > 1:", "        if False:",
           "test_an_option_archive_needs_one_group"),
          ("mo2-ignored", '    mods = "" if data_arg else mo2_mods()', '    mods = ""',
           "test_mo2_installs_a_new_mod_folder_and_holds_back_game_root_files"),
          ("mo2-game-root-without-flag", '        if base == "game" and mo2 and not root_files:', "        if False:",
           "test_mo2_installs_a_new_mod_folder_and_holds_back_game_root_files"),
          ("mo2-existing-folder-merged", "        if data.exists() and any(data.iterdir()):", "        if False:",
           "test_mo2_refuses_an_existing_mod_folder"),
          ("vortex-unwarned", '    if (game / "Data" / VORTEX_MARKER).is_file():', "    if False:",
           "test_vortex_is_warned_about_not_refused"),
          ("mo2-existing-replaced-without-update", "            if not update:", "            if False:",
           "test_mo2_refuses_an_existing_mod_folder"),
          ("mo2-update-no-whole-backup", "                aside = keep_previous(replacing, old_folder, old_hashes)",
           "                aside = None; shutil.rmtree(str(replacing))",
           "test_mo2_update_replaces_the_folder_after_keeping_the_old_one_whole"),
          ("mo2-update-configs-lost", '            keep = [q[1] for q in todo if q[2] == "CONFIG-NEW"]',
           "            keep = []", "test_mo2_update_replaces_the_folder_after_keeping_the_old_one_whole"),
          ("mo2-update-meta-ini-lost", 'CARRY_OVER = ("meta.ini",)', "CARRY_OVER = ()",
           "test_mo2_update_replaces_the_folder_after_keeping_the_old_one_whole"),
          ("update-without-mo2-ignored", "    if update and not mods:", "    if False:",
           "test_update_without_mo2_is_refused"),
          ("cross-drive-copy-unverified", "        if tree_hashes(kept) != before:\n            raise OSError(\"the copy",
           "        if False:\n            raise OSError(\"the copy",
           "test_a_copy_that_does_not_match_leaves_the_original_and_installs_nothing"),
          ("cross-drive-aside-not-recycled", '            why = recycle([aside]).get(str(aside), "no result")',
           "            why = None", "test_update_across_drives_copies_verifies_and_only_then_recycles"),
          # Parent ruling 2026-10-20: one plain line BEFORE the bin, so Windows' "too big" prompt is no surprise.
          ("cross-drive-bin-notice-dropped", "            print(BIN_NOTICE.format(aside), flush=True)",
           "            pass", "test_update_across_drives_copies_verifies_and_only_then_recycles"),
          ("cross-drive-bin-refusal-unreported", '            why = recycle([aside]).get(str(aside), "no result")',
           "            recycle([aside]); why = None", "test_an_aside_folder_the_bin_refuses_is_left_and_named"),
          # H1 (v4.0.0 review): the live folder is never removed file by file; an unmovable folder is left whole.
          ("live-folder-removed-in-place", "            os.rename(str(live), str(aside))",
           "            shutil.rmtree(str(live))",
           "test_a_folder_that_cannot_be_moved_is_left_whole_and_the_update_refused"),
          ("open-files-not-probed", "            if ctypes.get_last_error() in (32, 33, 5):", "            if False:",
           "test_an_open_file_is_named_and_the_live_folder_is_untouched"),
          ("unchanged-assumed-not-observed", "                if tree_hashes(replacing) == old_hashes:",
           "                if True:",
           "test_a_keep_that_fails_after_moving_the_folder_does_not_claim_nothing_changed"),
          ("post-move-failure-unexplained", "        except BaseException as e:\n            if old_folder is None:",
           "        except ZeroDivisionError as e:\n            if old_folder is None:",
           "test_a_failure_after_the_old_folder_moved_names_the_backup"),
          ("cross-drive-failure-unhandled", "            except OSError as e:\n                if tree_hashes(replacing)",
           "            except ZeroDivisionError as e:\n                if tree_hashes(replacing)",
           "test_a_copy_that_does_not_match_leaves_the_original_and_installs_nothing"),
      ]],

    # --- nexus.sh / nexus-render ---------------------------------------------
    *[pytest.param(path, find, replace, "tests/test_nexus.py::" + test, id="nexus-" + tid)
      for tid, path, find, replace, test in [
          ("vkey-trailing-zero", "tools/nexus-render.py", '    while len(parts) > 1 and parts[-1] == (0, ""):',
           "    while False:", "test_vkey_treats_trailing_zeros_and_a_leading_v_as_equal"),
          ("vkey-as-strings", "tools/nexus-render.py",
           "        parts.append((int(m.group(1)) if m.group(1) else -1, m.group(2).lower()))",
           "        parts.append((m.group(1), m.group(2).lower()))", "test_vkey_orders_versions_numerically"),
          ("changelogs-string-sorted", "tools/nexus-render.py", "    vers = sorted(logs, key=vkey, reverse=True)",
           "    vers = sorted(logs, reverse=True)",
           "test_changelogs_since_the_installed_version_are_all_shown_newest_first"),
          ("nulls-printed-as-none", "tools/nexus-render.py", "    return default if v is None else str(v)",
           "    return str(v)", "test_find_tolerates_null_fields"),
          ("requirements-failure-as-none", "tools/nexus-render.py",
           '        print("  (could NOT fetch requirements -- not the same as none)")',
           '        print("  (none declared -- the description may still list some)")',
           "test_page_says_requirements_could_not_be_fetched_not_none"),
          ("changelog-failure-as-none", "tools/nexus-render.py",
           '        logs = None if os.environ.get("LOG_STATUS") == "failed" else (load_env("LOG", required=False) or {})',
           '        logs = load_env("LOG", required=False) or {}',
           "test_a_missing_changelog_does_not_fail_the_page_but_is_not_called_none"),
          ("403-as-generic-failure", "tools/nexus-render.py", "        if e.code == 403:", "        if False:",
           "test_fetch_never_sends_a_browser_user_agent"),
          ("browser-user-agent", "tools/nexus-render.py", 'UA = "skyrim-ai-assistant-toolkit-nexus/1',
           'UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) skyrim-ai-assistant-toolkit-nexus/1',
           "test_fetch_never_sends_a_browser_user_agent"),
          ("http-status-ignored", "tools/nexus.sh", '    if [ "$code" != "200" ]; then', "    if false; then",
           "test_an_http_error_is_could_not_check_exit_3"),
      ]],

    # --- bench-vr -------------------------------------------------------------
    *[pytest.param("tools/bench-vr.py", find, replace, "tests/test_bench_vr.py::" + test, id="bench-vr-" + tid)
      for tid, find, replace, test in [
          ("restore-only-on-success", "    finally:\n        for what, cmd in reversed(restore):",
           "    else:\n        for what, cmd in reversed(restore):",
           "test_a_failure_partway_still_restores_everything"),
          ("window-one-kept", "    return [x for w in windows[1:] for x in w]", "    return [x for w in windows[0:] for x in w]",
           "test_window_one_is_discarded"),
          ("re-enters-current-cell",
           '            if cmd.lower().startswith("coc ") and here.lower() == cmd[4:].lower():', "            if False:",
           "test_the_current_cell_is_never_re_entered"),
          ("measures-with-no-save", '    if not isinstance(h.get("frame"), int) or h["frame"] < 0:', "    if False:",
           "test_no_save_loaded_is_refused"),
      ]],

    # --- vr-headless ----------------------------------------------------------
    *[pytest.param("tools/vr-headless.py", find, replace, "tests/test_vr_headless.py::" + test, id="vr-headless-" + tid)
      for tid, find, replace, test in [
          ("no-restore-on-failure", "        problems = restore_entries(entries)\n        if problems:\n            print(\"ERROR: `on`",
           "        problems = []\n        if problems:\n            print(\"ERROR: `on`",
           "test_a_failure_midway_restores_both_files"),
          ("backup-unverified", "            if not bak.is_file() or sha(bak) != want:", "            if not bak.is_file():",
           "test_off_refuses_a_backup_that_does_not_verify_and_keeps_the_state"),
          ("se-not-refused", '    if not (game / "SkyrimVR.exe").is_file():', "    if False:",
           "test_se_and_ae_are_refused"),
          ("state-recorded-after-change", "    save_state(state)                              # recorded BEFORE the first change",
           "    pass                              # recorded BEFORE the first change",
           "test_backups_are_taken_and_recorded_before_the_first_change"),
          ("steamvr-running-ignored", "    if steamvr_running() and not force:", "    if False:",
           "test_steamvr_running_is_refused_unless_forced"),
          # Review L5 F1: a file changed after `on` is kept before the backup goes back over it ...
          ("changed-after-on-overwritten", '            if current is not None and current != e.get("applied_sha256"):',
           "            if False:", "test_off_keeps_a_file_that_changed_after_on_before_restoring_over_it"),
          # ... and only such a file: what `on` itself wrote is recognised, so no copy piles up on every `off`.
          ("applied-hash-not-recorded", '        entries[0]["applied_sha256"] = sha(p["vrsettings"])',
           "        pass", "test_off_keeps_a_file_that_changed_after_on_before_restoring_over_it"),
          # Review L5 F4: the state file is closed by a rename, never deleted.
          ("state-file-deleted",
           '        os.replace(str(p), str(p.with_name("state-closed-{}.json".format(stamp))))',
           "        p.unlink()", "test_off_closes_the_state_file_by_renaming_it_never_deleting"),
          # 4.0 managed lifecycle. The exit test, one twin per clause: a wrong "exited" restores files under a
          # running game and (the recorded defect) sent the quit command to a game that was still loading.
          ("exit-no-miss-count", '        if self.misses < EXIT_MISSES or now - self.first_miss < EXIT_WINDOW:', "        if now - self.first_miss < EXIT_WINDOW:",
           "test_a_long_window_with_too_few_misses_is_not_an_exit"),
          ("exit-no-window", '        if self.misses < EXIT_MISSES or now - self.first_miss < EXIT_WINDOW:', "        if self.misses < EXIT_MISSES:",
           "test_three_quick_misses_still_need_the_time_window"),
          ("exit-on-one-miss", '        if self.misses < EXIT_MISSES or now - self.first_miss < EXIT_WINDOW:', "        if self.misses < 1:", "test_one_missed_process_read_is_never_an_exit"),
          ("run-ends-on-dropped-snapshot", '        if self.misses < EXIT_MISSES or now - self.first_miss < EXIT_WINDOW:', "        if self.misses < 1:",
           "test_run_survives_a_dropped_snapshot_and_restores_only_after_the_real_exit"),
          ("exit-no-reset-on-sight",
           "            self.misses, self.first_miss = 0, None              # seen: start counting again",
           "            pass", "test_seeing_the_game_resets_the_miss_count"),
          ("exit-ignores-devbench", '    if health and health.get("pid") == game.get("Id"):\n        return True',
           "    if False:\n        return True", "test_devbench_naming_the_pid_overrules_the_process_list"),
          ("exit-ignores-handle", '    return process_alive(game.get("Id"), game.get("StartTime"))', "    return False",
           "test_the_live_process_handle_overrules_the_process_list"),
          ("exit-unknown-is-absent", "            return False                                        # unknown is never a miss",
           "            running = []", "test_an_unreadable_process_list_is_not_a_miss"),
          # The quit command: only an owned game that DevBench names, with an advancing counter, outside a load.
          ("quit-while-frozen", "    return isinstance(f1, int) and isinstance(f2, int) and 0 <= f1 < f2",
           "    return isinstance(f1, int) and isinstance(f2, int)",
           "test_stop_never_quits_a_game_whose_frame_counter_is_frozen"),
          ("quit-while-loading", '    if h2.get("lastLifecycle") in LOADING_LIFECYCLES:', "    if False:",
           "test_stop_never_quits_a_game_that_is_loading_a_save"),
          ("quit-any-pid", '    if any(h.get("pid") != pid for h in (h1, h2)):', "    if False:",
           "test_stop_needs_devbench_to_name_the_owned_pid"),
          ("stop-unowned-game", "    if any(not any(same_process(g, o) for o in owned) for g in games):", "    if False:",
           "test_stop_refuses_an_unowned_game_and_sends_nothing"),
          # Restoring under a running session, or when that cannot be known.
          ("off-under-running-game", '    if alive:\n        raise Refused("the session\'s', '    if False:\n        raise Refused("the session\'s',
           "test_off_refuses_while_a_recorded_process_still_runs"),
          ("off-unknown-is-idle",
           '        raise Refused("{}; cannot tell whether the session\'s game or SteamVR still runs, so nothing is restored. Try "\n                      "again, or use `stop`".format(e))',
           "        alive = []", "test_off_refuses_when_it_cannot_tell_whether_the_session_still_runs"),
          ("on-over-headless", '    if already_headless(json.loads(p["vrsettings"].read_text(encoding="utf-8"))):', "    if False:",
           "test_on_refuses_settings_that_already_force_the_null_driver"),
          ("on-over-forced-null", '    return (settings.get("steamvr", {}).get("forcedDriver") == "null" or', "    return (False or",
           "test_on_refuses_settings_that_already_force_the_null_driver"),
          ("on-over-enabled-null", '            settings.get("driver_null", {}).get("enable") is True)', "            False)",
           "test_on_refuses_settings_that_already_enable_the_null_driver"),
          ("park-deletes", '                set_aside(Path(e["path"]), bdir / "set-aside")', '                os.remove(e["path"])',
           "test_park_sets_oc_plugins_aside_and_off_puts_them_back"),
          # The emulator swap.
          ("emu-pin-unchecked", "    if sha(files[fetch.DLL]) != fetch.DRIVER_DLL_SHA256:", "    if False:",
           "test_emu_refuses_a_package_that_is_not_the_pinned_build"),
          ("emu-ignores-steam", '        if busy:\n            raise Refused("close Steam and SteamVR first',
           '        if False:\n            raise Refused("close Steam and SteamVR first', "test_emu_refuses_while_steam_or_steamvr_runs"),
          ("emu-unknown-is-idle", '            raise Refused("{}; cannot confirm Steam and SteamVR are closed".format(e))',
           "            busy = []", "test_launch_and_emu_refuse_when_the_process_list_is_unreadable"),
          # Launch preconditions and the desktop boundary.
          ("background-without-steam", '        if not any(x["ProcessName"] == "steam" for x in prior_steam):', "        if False:",
           "test_background_launch_needs_the_users_steam_client_open"),
          ("launch-over-running-vr", '    if busy:\n        raise Refused("close the game and SteamVR before launching',
           '    if False:\n        raise Refused("close the game and SteamVR before launching',
           "test_launch_refuses_while_a_game_or_steamvr_runs"),
          ("launch-unknown-is-idle", '        raise Refused("{}; cannot confirm that no game or SteamVR process is running".format(e))',
           "        running = []", "test_launch_and_emu_refuse_when_the_process_list_is_unreadable"),
          ("escape-ignored", '        if desktop == "background" and not session["boundary"].get("isolated"):', "        if False:",
           "test_a_window_on_the_users_desktop_stops_the_launch_before_the_game"),
          ("room-setup-unproved", "                if pending:", "                if False:",
           "test_room_setup_with_an_unproved_window_never_launches_the_game"),
          ("room-setup-no-start", '                    if not x.get("StartTime") or not x.get("Path") or Path(x["Path"]).resolve() != p["room_setup"].resolve():',
           '                    if not x.get("Path") or Path(x["Path"]).resolve() != p["room_setup"].resolve():',
           "test_room_setup_with_unproved_identity_is_refused[no-start]"),
          ("room-setup-no-path", '                    if not x.get("StartTime") or not x.get("Path") or Path(x["Path"]).resolve() != p["room_setup"].resolve():',
           '                    if not x.get("StartTime") or Path(x["Path"]).resolve() != p["room_setup"].resolve():',
           "test_room_setup_with_unproved_identity_is_refused[no-path]"),
          ("room-setup-path", '                    if not x.get("StartTime") or not x.get("Path") or Path(x["Path"]).resolve() != p["room_setup"].resolve():',
           '                    if not x.get("StartTime") or not x.get("Path"):',
           "test_room_setup_with_unproved_identity_is_refused[path]"),
          # Restoration is part of exit and failure, not a caller step.
          ("run-no-restore", "            rc = cmd_off()                       # nothing was started: restore only", "            rc = 0",
           "test_run_restores_when_the_launch_fails"),
          ("run-no-stop", "            rc = cmd_stop()                      # owned processes exist: orderly exit, then restore",
           "            rc = 0", "test_run_restores_through_stop_when_startup_times_out"),
      ]],

    # --- review L5 (tools) fix batch ------------------------------------------
    # F3: a staged patch name is compared as a name; as a glob, `[Fix]` is a character class.
    pytest.param("tools/housecarl_integration/gateway.py",
                 "            if p.is_file() and p.name.casefold() == name.casefold()]",
                 '            if p in list(root.glob("*/" + name))]',
                 "tests/test_housecarl.py::test_a_staged_patch_name_with_brackets_is_a_name_not_a_glob",
                 id="housecarl-staged-name-is-a-glob"),
    # F5: the god-mode restore is queued only after the toggle went through ...
    pytest.param("tools/bench-vr.py", '        bench.console("tgm")\n        # Only once',
                 '        restore.append(("god mode", "tgm"))\n        bench.console("tgm")\n        # Only once',
                 "tests/test_bench_vr.py::test_a_god_mode_toggle_that_failed_is_not_toggled_back_on",
                 id="bench-vr-tgm-restore-queued-first"),
    # ... and a run that moved the player says where it left them.
    pytest.param("tools/bench-vr.py", "                moved = True\n", "",
                 "tests/test_bench_vr.py::test_the_run_says_where_it_left_the_player",
                 id="bench-vr-player-location-unreported"),
    # F6: the House Carl download goes to a part file, verified, before it takes the archive's name.
    pytest.param("tools/housecarl_integration/setup.py",
                 '(ARCHIVE_URL, timeout=60) as src, part.open("xb") as dst:',
                 '(ARCHIVE_URL, timeout=60) as src, archive.open("xb") as dst:',
                 "tests/test_housecarl.py::test_an_interrupted_download_never_leaves_a_file_at_the_archive_name",
                 id="housecarl-download-straight-to-the-archive-name"),
    pytest.param("tools/housecarl_integration/setup.py", "    if sha(part) != ARCHIVE_SHA256:", "    if False:",
                 "tests/test_housecarl.py::test_an_interrupted_download_never_leaves_a_file_at_the_archive_name",
                 id="housecarl-download-unverified-before-rename"),
    # F8: a crash ruling needs its module as well as its offset.
    pytest.param("tools/crash-triage.py",
                 '    return KNOWN.get((r["module"].lower(), r["offset"].upper()), ("UNKNOWN", ""))',
                 '    return next((v for (_m, o), v in KNOWN.items() if o == r["offset"].upper()), ("UNKNOWN", ""))',
                 "tests/test_triage.py::test_an_accepted_offset_in_another_module_is_unknown_not_accepted",
                 id="crash-triage-offset-only-ruling"),
    # F9: a BOM-headed plugins.txt keeps its first active plugin (the node suite runs under this anchor).
    pytest.param("tools/xelib/active-plugins.js", "fs.readFileSync(pluginsTxt, 'utf8').replace(/^\\uFEFF/, '');",
                 "fs.readFileSync(pluginsTxt, 'utf8');",
                 "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests",
                 id="active-plugins-bom-drops-the-first-plugin"),
    # F10: the Nexus key reaches curl on stdin, never on its command line.
    pytest.param("tools/nexus.sh", """printf 'apikey: %s\\n' "$KEY" | curl -s --max-time 30 -w $'\\n%{http_code}' -H @- "$@")""",
                 """curl -s --max-time 30 -w $'\\n%{http_code}' -H "apikey: $KEY" "$@")""",
                 "tests/test_nexus.py::test_page_end_to_end_and_the_key_is_used_but_never_printed",
                 id="nexus-key-on-the-curl-command-line"),
    # Read-through of a skimmed file (test_triage.py): the zero remainder row was never actually tested at zero.
    pytest.param("tools/papyrus-triage.py", """        print(f"  {remainder:>6}  {'-':<34}  (not shown above)")""",
                 """        if remainder: print(f"  {remainder:>6}  {'-':<34}  (not shown above)")""",
                 "tests/test_triage.py::test_papyrus_reports_the_not_shown_remainder_even_at_zero",
                 id="papyrus-triage-zero-remainder-silent"),

    # --- mirror-launch-logs -------------------------------------------------
    pytest.param(
        # Drop the previous copy on a replacement: the last launch's log is gone, which is the loss the mirror exists
        # to prevent.
        "tools/mirror-launch-logs.py",
        '                kept, status = _keep_previous(destination), "REPLACED"',
        '                kept, status = destination.unlink(), "REPLACED"',
        "tests/test_mirror_launch_logs.py::test_a_truncated_or_replaced_log_keeps_the_previous_copy",
        id="mirror-logs-replacement-loses-the-copy",
    ),
    pytest.param(
        # Compare only the first 4 KB: a replaced log that starts the same is taken for an append.
        "tools/mirror-launch-logs.py",
        "    for pos in sorted({0, max(0, old_size - SAMPLE)}):",
        "    for pos in [0]:",
        "tests/test_mirror_launch_logs.py::test_a_truncated_or_replaced_log_keeps_the_previous_copy",
        id="mirror-logs-head-only-identity",
    ),
    pytest.param(
        # Touch an unchanged copy instead of leaving it alone.
        "tools/mirror-launch-logs.py",
        "            if same and old_size == size:",
        "            if False:",
        "tests/test_mirror_launch_logs.py::test_new_then_unchanged_does_not_rewrite_the_copy",
        id="mirror-logs-unchanged-not-detected",
    ),
    pytest.param(
        # Report a copy cut short by a mid-read truncation as complete.
        "tools/mirror-launch-logs.py",
        '                    status = "SHORT"',
        "                    status = status",
        "tests/test_mirror_launch_logs.py::test_a_log_that_shrinks_mid_read_is_short_and_kept_on_the_next_pass",
        id="mirror-logs-short-read-unreported",
    ),
    pytest.param(
        # Mirror a file once per folder it is found through: the crash folder is usually the SKSE folder.
        "tools/mirror-launch-logs.py",
        "                if p.is_file() and key not in seen:",
        "                if p.is_file():",
        "tests/test_mirror_launch_logs.py::test_the_command_mirrors_each_log_once_then_finds_nothing_new",
        id="mirror-logs-duplicate-sources",
    ),

    # --- temp hygiene: a leaked test temp dir comes back ----------------------
    pytest.param("tests/test_skyguard_mcp.py", 'NOHOME = scratch_dir("t11-nohome-")',
                 'import tempfile; NOHOME = tempfile.mkdtemp(prefix="t11-nohome-")',
                 "tests/test_temp_hygiene.py::test_no_python_test_makes_an_uncleaned_temp_dir_at_import",
                 id="temp-hygiene-module-mkdtemp"),
    pytest.param("tools/xelib/active-plugins.test.js", "const dir = scratch('toolkit-plugins-');",
                 "const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'toolkit-plugins-'));",
                 "tests/test_temp_hygiene.py::test_no_node_test_makes_a_temp_dir_outside_its_cleanup_helper",
                 id="temp-hygiene-node-mkdtemp"),
    # --- session-start disk note: each agent's path, and the no-sweep-under-tests guard -------------------------
    pytest.param("tools/skyguard/run.py", '_session_snapshot(a.project, req.source, audit)\n        out = _disk_note(',
                 '_session_snapshot(a.project, req.source, audit)\n        _ = _disk_note(',
                 "tests/test_session_disk_note.py::test_the_codex_session_start_shows_the_disk_note",
                 id="disk-note-codex"),
    pytest.param("tools/skyguard/run.py", 'p["source"], audit)\n        out = _disk_note(',
                 'p["source"], audit)\n        _ = _disk_note(',
                 "tests/test_session_disk_note.py::test_the_claude_session_start_shows_the_disk_note",
                 id="disk-note-claude"),
    pytest.param("tools/skyguard/run.py", '    if tool.is_file() and own and not off and not in_test:',
                 '    if tool.is_file() and own and not off:',
                 "tests/test_session_disk_note.py::test_a_sweep_is_launched_detached_outside_tests_and_never_inside_them",
                 id="disk-note-sweeps-under-tests"),
    # --- housekeep ----------------------------------------------------------
    *[pytest.param("tools/housekeep.py", anchor, repl, "tests/test_housekeep.py::" + test, id="housekeep-" + mid)
      for mid, anchor, repl, test in [
          # Recycling: the too-big prompt and the junction skip, each broken alone.
          ("recycle-no-nuke-warning", "fFlags = 0x4000 | 0x40", "fFlags = 0x40",
           "test_the_tool_never_deletes_permanently"),
          ("sweep-follows-junctions", '"st_file_attributes", 0) & 0x400:', '"st_file_attributes", 0) & 0:',
           "test_the_sweep_never_follows_a_link_out_of_temp"),
          # The sweep's gates, each broken alone.
          ("sweep-any-name", "        if prefixes and e.name.startswith(prefixes):", "        if True:",
           "test_the_sweep_deletes_a_stale_toolkit_temp_dir_and_keeps_a_foreign_one"),
          ("sweep-no-age-gate", "            if _newest_shallow(p) > cutoff:", "            if False:",
           "test_the_sweep_keeps_a_dir_whose_child_changed_recently"),
          ("sweep-no-in-use-probe", "        if _in_use(p):", "        if False:",
           "test_the_sweep_keeps_a_dir_a_process_has_a_file_open_in"),
          # A save copy loses its Forever status: a byte-identical .ess fixture would be recycled.
          ("save-not-protected", '            return "save"', "            pass",
           "test_saves_and_backup_folders_are_never_planned"),
          # The doc-linked evidence file becomes fair game.
          ("links-not-protected", "        if k in self.links:", "        if False:",
           "test_a_file_a_project_doc_links_to_is_never_planned"),
          # A git-tracked file becomes fair game.
          ("tracked-not-protected", "        if k in self.tracked:", "        if False:",
           "test_a_git_tracked_file_is_never_planned"),
          # The backup-folder rule judges the root's own ancestors: an install under ".../backups/" plans nothing.
          ("backup-rule-whole-path", '        if not rel.startswith(".."):', "        if False:",
           "test_a_root_that_sits_under_a_backups_folder_is_not_all_protected"),
          # Another session's live task folder is planned like an idle one.
          ("activity-gate-off", '                out.append((p, "active" if newest_mtime(p) > cutoff else "idle"))',
           '                out.append((p, "idle"))', "test_an_active_task_folder_is_skipped_whole"),
          # A loose file in a root -- a service's append-only log -- becomes a candidate (read-only breaks it).
          ("loose-files-planned", '                out.append((p, "loose"))', '                out.append((p, "idle"))',
           "test_a_loose_file_in_a_root_is_never_planned"),
          # apply trusts the plan: a file changed since planning is still recycled.
          ("apply-skips-hash-recheck",
           '        if p.stat().st_size != action["bytes"] or blake(p) != action["hash"]:', "        if False:",
           "test_apply_refuses_a_file_changed_since_the_plan"),
          # apply recycles a fixture after the live file moved on -- now the only copy of those bytes.
          ("apply-skips-live-recheck",
           '        if not Path(action["live"]).is_file() or memo.hash(action["live"]) != action["hash"]:',
           "        if False:", "test_apply_refuses_when_the_live_file_changed"),
          # A live database counts as a reference copy: its snapshots are recycled and lost at its next write.
          ("state-file-as-reference", "                if os.path.splitext(f)[1].lower() in state:",
           "                if False:", "test_a_snapshot_of_a_live_database_is_not_recreatable_but_still_deduplicated"),
          # A gzip that does not reproduce the log still sends the original to the bin.
          ("gzip-no-round-trip", '    if d.hexdigest() != action["hash"]:', "    if False:",
           "test_a_gzip_that_does_not_round_trip_keeps_the_original"),
          # Review L3 LOW-3 / L2 LOW-7: the retired guard-store batches reach the bin, and only settled ones do.
          ("retired-never-swept",
           '    ret = sweep_retired([cfg["root"]] + [p for p in [_project_of(state)] if p is not None])',
           '    ret = sweep_retired([cfg["root"]])',
           "test_session_recycles_settled_retired_guard_store_batches_and_keeps_a_fresh_one"),
          ("retired-no-settle-gate", "                settling = _newest_shallow(p) > now - RETIRED_SETTLE_S",
           "                settling = False",
           "test_session_recycles_settled_retired_guard_store_batches_and_keeps_a_fresh_one"),
          # v4.0.0 review L4a M2: each whole-folder class, plan and apply, still answers to the Forever class.
          ("scratch-ignores-guard", "            why = protected_inside(guard, p)\n            if why:",
           '            why = ""\n            if why:',
           "test_a_scratch_folder_holding_a_save_or_a_backup_is_never_planned"),
          ("launch-ignores-guard", '            why = protected_inside(guard, p) if age >= cfg["recycle_after_days"] else ""',
           '            why = ""', "test_an_old_launch_folder_holding_a_save_is_never_planned"),
          ("snapshot-ignores-guard",
           '            why = protected_inside(guard, p, "snapshot") if sig == prev and i != len(stamps) - 1 else ""',
           '            why = ""', "test_a_repeated_snapshot_under_a_backup_folder_is_never_planned"),
          ("apply-folder-ignores-guard", '        why = protected_inside(guard, p, action["class"])\n        if why:',
           '        why = ""\n        if why:',
           "test_apply_refuses_a_scratch_folder_a_save_appeared_in_after_the_plan"),
          ("protected-inside-blind", "        if why:\n            return why\n    return \"\"",
           '        if False:\n            return why\n    return ""',
           "test_a_scratch_folder_holding_a_save_or_a_backup_is_never_planned"),
          # User ruling 2026-10-20: auto_snapshots stamps are Rolling despite sitting under .claude/backups/ --
          # but only that store, and never a save or a backup folder inside a stamp. One twin per clause.
          ("auto-snapshots-not-rolling",
           '    store = folder.parent if cls == "snapshot" and folder.parent.name.lower() == ROLLING_SNAPSHOT_STORE else None',
           "    store = None", "test_repeated_auto_snapshots_stamps_are_rolling"),
          ("rolling-exemption-any-backup-folder",
           '    store = folder.parent if cls == "snapshot" and folder.parent.name.lower() == ROLLING_SNAPSHOT_STORE else None',
           '    store = folder.parent if cls == "snapshot" else None',
           "test_a_repeated_snapshot_under_a_backup_folder_is_never_planned"),
          ("rolling-exemption-covers-saves", '        if why == "backup folder" and store is not None \\',
           "        if why and store is not None \\", "test_a_save_or_backup_inside_an_auto_snapshots_stamp_keeps_it"),
          ("rolling-exemption-covers-inner-backups",
           '                and not any("backup" in part.lower() for part in f.relative_to(store).parts[:-1]):',
           "                and True:", "test_a_save_or_backup_inside_an_auto_snapshots_stamp_keeps_it"),
          # L4a L2: a relative scratch path is the toolkit folder's, not the current directory's.
          ("scratch-relative-to-cwd", "        p = _abs(cfg, s)                  # relative to the toolkit folder",
           "        p = Path(s)                  # relative to the toolkit folder",
           "test_a_relative_scratch_path_is_the_toolkit_folder_not_the_current_directory"),
          # L4a L1: off Windows the bin says "left in place" instead of crashing the sweep; session always writes.
          ("no-bin-raises", '        return {p: "no Recycle Bin on this platform: left in place" for p in paths}',
           '        raise RuntimeError("Recycle Bin removal is implemented for Windows only")',
           "test_off_windows_the_bin_leaves_everything_in_place_and_says_so"),
          ("session-sweep-crash-unhandled",
           "    except Exception as e:                    # the state file must still be written",
           "    except ZeroDivisionError as e:                    # the state file must still be written",
           "test_session_writes_its_state_even_when_the_sweep_fails"),
      ]],
    pytest.param("tools/housekeep.py",
                 '                reparse = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400',
                 "                reparse = 0", "tests/test_housekeep.py::"
                 "test_a_retired_batch_is_never_deleted_and_a_link_is_never_offered",
                 marks=pytest.mark.skipif(sys.platform != "win32", reason="a junction exists on Windows only"),
                 id="housekeep-retired-follows-junctions"),
    # Both the explicit symlink check and is_dir(False) reject links; regress the behavior, not one redundant term.
    pytest.param("tools/housekeep.py", "                if e.is_symlink() or reparse or not e.is_dir(follow_symlinks=False):",
                 "                if reparse or not e.is_dir(follow_symlinks=True):", "tests/test_housekeep.py::"
                 "test_a_retired_batch_is_never_deleted_and_a_link_is_never_offered",
                 marks=pytest.mark.skipif(sys.platform == "win32", reason="the test links with a junction there"),
                 id="housekeep-retired-follows-symlinks"),

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
        "            cleared = done > failed[0]",   # fix-limits 8 moved the stamp comparison into its own branch
        "            cleared = done >= failed[0]",
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
        "            cleared = ds > fs\n",          # fix-limits 8 moved the comparison into its own branch
        "            cleared = done > failed[0]\n",
        "tests/test_skyguard_health.py::test_a_clock_stepped_back_cannot_hide_a_failure",
        id="health-orders-by-invocation-sequence",
    ),
    pytest.param(
        "tools/skyguard/health.py",
        "        if recs[\"evaluated\"][1] == failed[1]:\n            cleared = False",   # fix-limits 8: the same id
        "        if recs[\"evaluated\"][1] == failed[1]:\n            cleared = True",
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
    # v4.0.0 review L4a M3: the rotation deletes outright again instead of using the Recycle Bin.
    pytest.param(
        "tools/kb-guard.sh",
        '        victims+=("$d")',
        '        rm -rf "$d"',
        "tests/test_kb_guard.py::test_pruned_snapshots_go_to_the_recycle_bin",
        id="kb-guard-rotation-deletes-outright",
    ),
    # ...and what the bin refuses goes unreported (the store silently keeps growing, or the user believes it pruned).
    pytest.param(
        "tools/kb-guard.sh",
        '    if [ "$ok" -lt "$total" ]; then',
        "    if false; then",
        "tests/test_kb_guard.py::test_snapshots_the_bin_refuses_stay_and_the_report_says_so",
        id="kb-guard-rotation-refusal-unreported",
    ),
    pytest.param(
        "tools/kb-guard.sh",
        '            echo "Moving $total old knowledgebase snapshot(s)',
        '            : "Moving $total old knowledgebase snapshot(s)',
        "tests/test_kb_guard.py::test_pruned_snapshots_go_to_the_recycle_bin",
        id="kb-guard-rotation-bin-notice-dropped",
    ),
    pytest.param(
        "tools/housekeep.py",
        '            print("OK' + BS + 't" + p if err is None else "FAILED' + BS + 't{}' + BS + 't{}".format(p, err))',
        '            print("OK' + BS + 't" + p)',
        "tests/test_housekeep.py::test_the_recycle_command_reports_every_path_the_bin_refused",
        id="housekeep-recycle-cli-hides-refusals",
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
        'if got["text"] != expected:',
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
    pytest.param("tools/modding_harness/preferences.py", "if not fenced[i] and _HEADING.match(line)), None)",
                 "if _HEADING.match(line)), None)",
                 "tests/test_skyguard_prefs.py::test_heading_inside_a_code_fence_is_not_the_section",
                 id="prefs-ignore-a-fenced-heading"),
    pytest.param("tools/modding_harness/preferences.py", "    if len(data) > CAP:", "    if False:",
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
        'tools/modding_harness/patches.py', '        trimmed = line.strip(_RUST_WS)', '        trimmed = line',
        "tests/test_patch_paths.py::test_indented_headers_are_headers_like_codex_reads_them",
        id="patch-headers-are-matched-on-the-trimmed-line",
    ),
    pytest.param(
        'tools/modding_harness/patches.py', '            self.fail(f"{trimmed!r} is not a valid hunk header")', '            return',
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
         "tests/test_bounded_kill_wait.py::test_bounded_waits_for_kill_within_both_deadlines",
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
        # the Codex PowerShell entry's clock starts at process start, not at its first line (T14b 2026-10-14)
        ("agent/guards/adapters/codex/skyguard-entry.ps1",
         "if ($st -le $script:T0ms -and $st -gt $script:T0ms - 120000) { $script:T0ms = $st }", "",
         "tests/test_codex_entry.py::test_the_deadline_clock_starts_when_powershell_starts_not_when_the_script_does",
         "codex-ps-clock-starts-at-the-first-line"),
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
        "sys.version_info[:2]>=(3,10) else 1)' >/dev/null 2>&1; then",
        "sys.version_info[:2]>=(3,0) else 1)' >/dev/null 2>&1; then",
        'tests/test_python_path.py::test_setup_refuses_a_python_older_than_3_10',
        id="setup-accepts-python-older-than-3-10",
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
        'tools/skyguard/portable.py',
        '    elif req.failure or req.event != "pre_tool":',
        '    elif req.event != "pre_tool":',
        'tests/test_skyguard_codex_adapter.py::test_an_untranslatable_request_is_a_failed_deny',
        id='codex-untranslatable-request-allowed',
    ),
    pytest.param(
        'tools/skyguard/portable.py',
        '    elif req.failure or req.event != "pre_tool":',
        '    elif req.failure:',
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
        # Copies omit .git. The independent measured vectors pin every definition
        # field without inventing release history; the real freeze gate stays intact.
        "tests/test_codex_definitions.py::test_the_template_hashes_to_the_measured_vectors",
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
        ('tools/modding_harness/patches.py',
         '            ops.append((h["op"], h["path"]))', '            if h["op"] != "delete": ops.append((h["op"], h["path"]))',
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
        # Removing .esm must be killed by a call that actually evaluates plugin policy.
        # The relative captured shell-patch variant now refuses missing execution cwd
        # before that rule, so it cannot detect this mutation. The absolute .esm write
        # requires shell.DENY_WRITE_PLUGIN and preserves the original capture shape.
        ("tools/skyguard/targets.py",
         r'''PLUGIN_FILE = r"\.(esp|esm|esl|bsa|ba2)[\s.]*" + STREAM + "$"''',
         r'''PLUGIN_FILE = r"\.(esp|esl|bsa|ba2)[\s.]*" + STREAM + "$"''',
         "tests/test_codex_conformance.py::test_conformance[derived:0.160.0/codex-PreToolUse-Bash-2:absolute-plugin-write]",
         "codex-plugin-rule-misses-esm"),
        # An `ask` loses the text that tells the model the user must run it themselves.
        ("tools/skyguard/adapters/codex.py",
         'ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- Codex cannot ask',
         'ASK_PREFIX = ("Codex cannot ask',
         'tests/test_skyguard_codex_adapter.py::test_ask_becomes_a_deny_that_says_why',
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
    ("bash-c", '                self._decoded(code)\n                self.reparse(code, "bash")',
     '                self._decoded(code)\n                pass', "test_bash_shell_strings_reparsed_in_their_dialect"),
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
    ("eval", '\n                    self.reparse(code, "bash")', "\n                    pass",
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
    ("r1-iex-opaque", "if not lits or not self._code(k + 1)[1]:", "if False:",
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
    ("rule-S-DEL-INSTALL-resolved", _DL, "                elif kind and resolved is None:\n",
     "                elif False:\n", "rel-rm-data"),
    ("rule-S-DEL-INSTALL-scratch", "tools/skyguard/paths.py",
     'SCRATCH_GLOBS = ("**/__pycache__/**", "**/node_modules/**", "**/.pytest_cache/**")', "SCRATCH_GLOBS = ()",
     "pycache"),
    ("rule-S-DEL-CONFIG-zone", _DL,
     'RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG", "mo2": "S-DEL-MO2"}',
     'RULE_OF = {"install": "S-DEL-INSTALL", "mo2": "S-DEL-MO2"}', "config-pwsh"),
    ("rule-S-DEL-MO2-zone", _DL,
     'RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG", "mo2": "S-DEL-MO2"}',
     'RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG"}', "mo2-bash"),
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
# guard3: the literal-name reader (interpreter_delete_targets) also refuses rows python-rmtree and node-rmsync, so
# the text clause these two twins pin is now proven on code that BUILDS its name (tests/test_interpreter_deletes.py).
_IDB = "tests/test_interpreter_deletes.py::test_a_built_name_naming_the_install_is_still_refused_by_text"
MUTATIONS += [
    pytest.param(_DL, "        if code and resolved is None and (has(DESTROYER, code) or has(INTERP_DELETE, code) or (",
                 "        if code and resolved is None and False and (has(DESTROYER, code) or has(INTERP_DELETE, code) or (",
                 _IDB + "[python-joined-name]", id="rule-S-DEL-INSTALL-interpreter-code"),
    pytest.param(_DL, "(has(DESTROYER, code) or has(INTERP_DELETE, code) or (", "(has(DESTROYER, code) or (",
                 _IDB + "[node-joined-name]", id="rule-S-DEL-INSTALL-interpreter-api"),
]
MUTATIONS.append(pytest.param(_DL, "    return _covers_kind(abs_path, roots)", '    return ""',
    "tests/test_skyguard_deletes.py::test_protected_kind[c:/Synthetic Games-install]",
    id="rule-S-DEL-INSTALL-covers-ancestor"))
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


# ---- Batch-3 ruling: S-WRITE-ROOT, one twin per clause, each naming the B3 row (in-process,
# tests/test_skyguard_writes.py::test_b3_row) or unit test it must turn red.
def _b3(row):
    return "tests/test_skyguard_writes.py::test_b3_row[b3root-" + row + "]"


_ROOT_CLAUSES = [
    ("rule-S-WRITE-ROOT-write-primitive",
     "            elif not sed_note and a is not None and in_install_root(a, roots):\n",
     "            elif False:\n", _b3("redirect-loader-ini")),
    ("rule-S-WRITE-ROOT-copy-named", "                if in_install_root(root_dst, roots):",
     "                if False:", _b3("cp-dll")),
    ("rule-S-WRITE-ROOT-copy-into-folder", '                if is_install_root(root_dst.rstrip("/"), roots):',
     "                if False:", _b3("cp-into-root-folder")),
    ("rule-S-WRITE-ROOT-literal-code",
     "        elif a is not None and in_install_root(a, roots):\n            notes[\"S-WRITE-ROOT\"].append(_name(word))",
     "        elif False:\n            notes[\"S-WRITE-ROOT\"].append(_name(word))",
     "tests/test_skyguard_writes.py::test_a_root_write_in_interpreter_code_is_noted"),
    ("rule-S-WRITE-ROOT-root-only", "    return rel is not None and len(rel) == 1 and root_game_file(rel[-1])",
     "    return rel is not None and len(rel) >= 1 and root_game_file(rel[-1])", _b3("ctl-subfolder")),
    ("rule-S-WRITE-ROOT-game-files-only",
     '    return not name.startswith(".") and name.lower().endswith(ROOT_GAME_EXT)', "    return True",
     _b3("ctl-scratch-output")),
    ("rule-S-WRITE-ROOT-dotfile-exempt",
     '    return not name.startswith(".") and name.lower().endswith(ROOT_GAME_EXT)',
     "    return name.lower().endswith(ROOT_GAME_EXT)", _b3("ctl-dotfile-ini")),
    ("rule-S-WRITE-ROOT-ini-is-a-game-file", '".exe", ".dll", ".asi", ".ini", ".toml"', '".exe", ".dll", ".asi", ".toml"',
     _b3("redirect-loader-ini")),
    ("rule-S-WRITE-ROOT-rename-in-source-folder",
     '            if e.program == "move-item" and srcs and not re.search(r"[/' + BS * 2 + ']", dst):',
     "            if False:", _b3("ctl-rename-in-data")),
]
MUTATIONS += [pytest.param(_WR, find, rep, test, id=i) for i, find, rep, test in _ROOT_CLAUSES]


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
     '''(?:"()"|'()'|''', "fallback-redirect-esp-heredoc"),
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
     "    if False:", "reg-opaque-heredoc"),
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
    ("rule-S-SED-WINPATH-replaces-install-note", _WR,
     "            elif not sed_note and a is not None and in_install_area(a, roots):",
     "            elif a is not None and in_install_area(a, roots):", "sed-winpath"),
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
    ("rule-W-INSTALL-cwd", _WRR, 'a = targets.resolve_literal(file_path, cwd, os.environ', 'a = targets.resolve_literal(file_path, "", os.environ',
     "install-relative-inside"),
]
MUTATIONS += [pytest.param(f, find, rep, _t09(row), id=i) for i, f, find, rep, row in _T9_CLAUSES]
MUTATIONS += [
    pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in (
        # the hook must hand the write guard its roots, or the zone notes fall back to the text tests
        ("rule-W-INSTALL-wired", "tools/skyguard/run.py",
         'v = engine.evaluate_write(p["file_path"], audit, roots=roots, cwd=p.get("cwd", ""),',
         'v = engine.evaluate_write(p["file_path"], audit,', "tests/test_guard_corpus.py::test_intended_rows_reproduce"),
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
    # The parsed known-read classifier now rejects a lookalike even if its early regex fence is removed.
    # Exercise the live classifier instead: Get-* reaches it, but an unknown Get-DateX must snapshot.
    pytest.param(_SNAP, '"get-date", "get-process",', '"get-date", "get-datex", "get-process",',
                 "tests/test_skyguard_side_effects.py::test_snapshot_does_not_skip_powershell_writes[Get-DateX]",
                 id="snapshot-pwsh-lookalike-is-not-a-known-read"),
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
    ("rule-S-DEVBENCH-CRASH-mcp", _MC, "    hit = judge(parts[-1], args, roots, None, env, found=found)",
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
     _T11 + "test_an_unusable_save_name_is_refused[backslash]"),
    ("rule-S-DEVBENCH-SAVE-name-colon", _MC, "_BAD_NAME = re.compile(r'[/" + BS * 2 + ":*?", "_BAD_NAME = re.compile(r'[/" + BS * 2 + "*?",
     _T11 + "test_an_unusable_save_name_is_refused[colon]"),
    ("rule-S-DEVBENCH-SAVE-name-wildcards", _MC, ":*?" + '"<>|', ':"<>|', _T11 + "test_an_unusable_save_name_is_refused[star]"),
    ("rule-S-DEVBENCH-SAVE-name-brackets", _MC, '"<>|' + BS + "x00", BS + "x00", _T11 + "test_an_unusable_save_name_is_refused[angle]"),
    ("rule-S-DEVBENCH-SAVE-name-control", _MC, BS + "x00-" + BS + "x1f]", "]", _T11 + "test_an_unusable_save_name_is_refused[control]"),
    # rt-fix-b: `.` and `..` are now emptied by the trailing-dot strip (Windows drops trailing dots), so their own
    # clause was dead and is gone; the strip is what the [dot] / [dotdot] cases now guard.
    ("rule-S-DEVBENCH-SAVE-name-dotdot", _MC, '    n = name.strip().rstrip(" .")', '    n = name.strip().rstrip(" ")',
     _T11 + "test_an_unusable_save_name_is_refused[dotdot]"),
    ("rule-S-DEVBENCH-SAVE-name-dot", _MC, '    n = name.strip().rstrip(" .")', '    n = name.strip()',
     _T11 + "test_an_unusable_save_name_is_refused[dot]"),
    ("rule-S-DEVBENCH-SAVE-name-string", _MC, "    if not isinstance(name, str):\n        return None\n    n = name.strip()",
     "    n = str(name).strip()", _T11 + "test_an_unusable_save_name_is_refused[number]"),
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
    ("rule-S-DEVBENCH-SAVE-console", _MC, "        for name in _console_save_names(line):",
     "        for name in ():", _t11row("t11-mcp-console-save-existing")),
    ("rule-S-DEVBENCH-SAVE-console-case", _MC, '        if words and words[0].lower() in SAVE_COMMANDS:',
     '        if words and words[0] in SAVE_COMMANDS:', _T11 + "test_the_console_save_word_is_case_insensitive"),
    ("rule-S-DEVBENCH-SAVE-console-whole-word", _MC, '        if words and words[0].lower() in SAVE_COMMANDS:',
     '        if words and words[0].lower().startswith(SAVE_COMMANDS):', _T11 + "test_the_console_save_word_is_case_insensitive"),
    ("rule-S-DEVBENCH-SAVE-wired", _SHR, "    mcp_rules.scan_shell(command, parsed, roots, env, found=dev)",
     "    pass", _t11row("t11-cli-tool-save-existing")),
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
    # (rule-S-DEVBENCH-SAVE-asks-every-save retired by the save policy, said so: a NEW name reported as a hit is counted
    # again by saves.count, which finds no file and backs nothing up -- the verdict cannot change, an equivalent mutant.)
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
    ("rule-S-DEVBENCH-SAVE-config-dirs", _MC, '    cands = [os.path.join(d, "Saves") for d in roots.physical_config_dirs or roots.config_dirs]', "    cands = []",
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
    ("rule-S-DEVBENCH-SAVE-env-wired", _MC, "hit = judge(parts[-1], args, roots, None, env, found=found)",
     "hit = judge(parts[-1], args, roots, None, None, found=found)", _t11row("t11-mcp-save-docs-existing")),
    ("rule-S-DEVBENCH-SAVE-env-wired-shell", _MC, "hit = judge(tool, args, roots, memo, env, found=found)",
     "hit = judge(tool, args, roots, memo, None, found=found)", _t11row("t11-curl-save-docs-existing")),
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
                 'reason = rules.message("S-GIT-DISCARD", file=git)',
                 'reason = rules.message("S-GIT-DISCARD", file=git) + " " + command',
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
    # Review L5 F2: the old name class, which never saw a master with ( ) [ ] ' & + or a comma.
    pytest.param("tools/esp-verify-wrapper.sh",
                 """grep -oE '[0-9A-Fa-f]{6}:[^:*?"<>|/\\\\]+\\.(esp|esm|esl)'""",
                 """grep -oE '[0-9A-Fa-f]{6}:[A-Za-z0-9 ._-]+\\.(esp|esm|esl)'""",
                 "tests/test_esp_verify.py::"
                 "test_a_master_with_brackets_parentheses_or_an_apostrophe_is_inventoried_and_its_remap_caught",
                 id="esp-verify-odd-master-names-unseen"),

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
    # Review L3 LOW-2: a Codex call is recorded only once the engine decided; a crash must not leave it "hooked".
    ("tools/skyguard/run.py",
     "    v = codes.classify(engine.pre_tool(req, a.project, roots_fn, audit, telemetry=a.telemetry,",
     "    _session_line(a, req.session_id, tool=req.tool_name)\n"
     "    v = codes.classify(engine.pre_tool(req, a.project, roots_fn, audit, telemetry=a.telemetry,",
     _COV + "test_a_codex_call_whose_engine_crashed_is_not_counted_as_hooked", "coverage-codex-crash-is-not-hooked"),
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
     _COV + "test_only_the_twenty_newest_session_files_are_kept_and_the_rest_are_retired_not_deleted",
     "session-files-are-pruned"),
    # Review L3 LOW-3 / L2 LOW-7: pruning retires (then housekeep recycles); it never deletes.
    ("tools/skyguard/retire.py", '            os.rename(str(path), str(d / "{}-{}".format(self.count, Path(path).name)))',
     "            os.unlink(str(path))",
     _COV + "test_only_the_twenty_newest_session_files_are_kept_and_the_rest_are_retired_not_deleted",
     "session-files-are-retired-not-deleted"),
    ("tools/skyguard/snapshot.py", "            batch.retire(store / \"manifests\" / n)",
     "            os.unlink(str(store / \"manifests\" / n))",
     "tests/test_snapshot_nested.py::test_pruning_retires_what_it_removes_and_deletes_nothing",
     "snapshot-prune-retires-manifests"),
    ("tools/skyguard/snapshot.py", "                batch.retire(d)\n",
     "                __import__(\"shutil\").rmtree(str(d))\n",
     "tests/test_snapshot_nested.py::test_pruning_retires_what_it_removes_and_deletes_nothing",
     "snapshot-prune-retires-bash-era-folders"),
    ("tools/skyguard/recovery.py", "            removed += batch.retire(ent.path)",
     "            os.unlink(ent.path)",
     "tests/test_recovery_snapshots.py::test_pruning_drops_old_lines_but_keeps_each_paths_newest",
     "recovery-prune-retires-blobs"),
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
    ("tools/skyguard/health.py", "                observed[key] = observed.get(key, 0) + max(0, n - refused.get(tool, 0))",
     "                observed[key] = observed.get(key, 0) + n",
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
    ("setup.sh", '    echo "Start Codex in the game folder. Subfolder loading is version-dependent: it worked in an isolated"',
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


def _anchor_skipped_off_windows(returncode: int, stdout: str, platform: str = sys.platform) -> str:
    """Linux CI run 38187755448: ~90 twins name an anchor test that is itself skipped off Windows (the Layer 2 lock,
    NTFS junctions, Codex hooks -- each skip carries its own platform reason). Such a twin proves nothing there, and
    "not killed" would misreport it. Off Windows ONLY, an anchor run that ran nothing but skips (exit 0, "N skipped",
    nothing passed) returns the anchor's skip reason. On Windows -- where every one of these twins runs -- this is
    always "", so the gate there is exactly as strict as before. A twin whose anchor PASSES is never excused."""
    if platform == "win32" or returncode != 0:
        return ""
    tail = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not re.search(r"\b\d+ skipped\b", tail) or re.search(r"\b\d+ (?:passed|failed|errors?|xpassed)\b", tail):
        return ""
    reasons = [ln.split("] ", 1)[-1].strip() for ln in stdout.splitlines() if ln.startswith("SKIPPED [")]
    return "; ".join(reasons) or tail


def test_a_skipped_anchor_excuses_its_twin_only_off_windows():
    out = "s                                                                        [100%]\n" \
          "SKIPPED [1] tests/test_x.py:36: the lock is a Windows mechanism (R11)\n1 skipped in 0.10s\n"
    assert _anchor_skipped_off_windows(0, out, "linux") == "tests/test_x.py:36: the lock is a Windows mechanism (R11)"
    assert _anchor_skipped_off_windows(0, out, "win32") == ""                    # Windows: never excused
    assert _anchor_skipped_off_windows(0, ".\n1 passed in 0.1s\n", "linux") == ""   # a surviving twin stays red
    assert _anchor_skipped_off_windows(0, "1 passed, 1 skipped in 0.1s\n", "linux") == ""
    assert _anchor_skipped_off_windows(5, "no tests ran in 0.1s\n", "linux") == ""  # a stale anchor stays red


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


def _anchor_outcomes(report: Path) -> dict[tuple[str, str, str], str]:
    """Read testcase failures, including call-time exceptions; reject pytest errors."""
    try:
        root = ET.parse(report).getroot()
        suites = [root] if root.tag == "testsuite" else list(root)
        assert root.tag in ("testsuite", "testsuites") and suites, "missing test suites"
        outcomes = {}
        for suite in suites:
            cases = suite.findall("testcase")
            assert suite.tag == "testsuite", "invalid suite"
            counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
            assert counts == {"tests": len(cases),
                              "failures": sum(len(c.findall("failure")) for c in cases),
                              "errors": sum(len(c.findall("error")) for c in cases),
                              "skipped": sum(len(c.findall("skipped")) for c in cases)}, "incomplete report"
            assert not counts["errors"] and suite.find("error") is None, "test execution error"
            for case in cases:
                identity = tuple(case.get(key, "") for key in ("file", "classname", "name"))
                assert all(identity) and identity not in outcomes, "invalid or duplicate testcase"
                assert sum(len(case.findall(key)) for key in ("failure", "error", "skipped")) <= 1, "conflicting outcomes"
                outcomes[identity] = ("failed" if case.find("failure") is not None else
                                      "skipped" if case.find("skipped") is not None else "passed")
        assert outcomes, "no executed testcases"
        return outcomes
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        raise AssertionError(f"unqualified anchor report: {exc}") from exc


def _run_mutation_anchor(work: Path, test_id: str, scratch: Path, report: Path):
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if test_id == "tests/test_repo_invariants.py::test_npm_test_actually_runs_tests":
        # Mutation copies deliberately omit node_modules. This anchor also runs
        # the mandatory FOMOD smoke, so both legs need the pristine install's
        # locked dependencies. Keep required-dependency checks enabled and leave
        # every other anchor's dependency environment unchanged.
        environment["NODE_PATH"] = str(REPO / "node_modules")
    if test_id.startswith("tests/test_verify_release.py::"):
        # The artifact fixture filters by the origin's real tracked membership.
        # Copies omit .git; pass only that inventory, never an index or history.
        git_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        git_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                                 check=True, capture_output=True, timeout=60, env=git_env).stdout
        inventory = report.parent / "tracked-files.nul"
        if inventory.exists():
            assert inventory.read_bytes() == tracked, "source inventory changed between mutation twins"
        else:
            inventory.write_bytes(tracked)
        environment["TOOLKIT_TEST_TRACKED_FILES"] = str(inventory)
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", test_id, "-q", "--no-header",
         "-p", "no:cacheprovider", "-rs", "--basetemp", str(scratch),
         "-o", "junit_family=legacy", "--junitxml", str(report)],
        cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        env=environment,
    )


@pytest.mark.parametrize("relpath,find,replace,test_id", MUTATIONS)
def test_guard_actually_bites(tmp_path, relpath, find, replace, test_id):
    """Revert one fix; the test guarding it must fail."""
    work = _mutate(tmp_path, relpath, find, replace)
    target = work / relpath
    mutant_bytes = target.read_bytes()
    pristine_bytes = (REPO / relpath).read_bytes()
    # The inherited evidence TEMP may flatten into >255-character backup
    # components. Both legs use the SAME short, uniquely owned fixture path.
    with tempfile.TemporaryDirectory(prefix="anchor-", dir=tmp_path.parent) as owned:
        private = Path(owned)
        assert private.resolve().parent == tmp_path.parent.resolve()
        scratch = private / "tests"
        target.write_bytes(pristine_bytes)
        try:
            baseline = _run_mutation_anchor(work, test_id, scratch, private / "baseline.xml")
        finally:
            target.write_bytes(mutant_bytes)
        why = _anchor_skipped_off_windows(baseline.returncode, baseline.stdout)
        if why:
            pytest.skip("the pristine anchor is skipped on this platform: " + why)
        unjudged = _unjudged_twin_message(baseline.returncode, baseline.stdout)
        assert not unjudged, unjudged
        assert baseline.returncode == 0, (
            f"PRISTINE ANCHOR DID NOT PASS: {test_id}; no mutant kill can be measured.\n"
            f"--- pristine stdout ---\n{baseline.stdout[-4000:]}\n"
            f"--- pristine stderr ---\n{baseline.stderr[-2000:]}"
        )
        pristine = _anchor_outcomes(private / "baseline.xml")
        passed = {identity for identity, outcome in pristine.items() if outcome == "passed"}
        assert passed and not any(outcome == "failed" for outcome in pristine.values()), "pristine anchor did not execute cleanly"
        result = _run_mutation_anchor(work, test_id, scratch, private / "mutant.xml")
        # The pristine anchor PASSED here, so a mutant leg that only skips is the revert turning the check off, not
        # the platform: a survivor on every platform (review L7 LOW; it used to be excused off Windows and labelled
        # "NOT JUDGED (not a survivor)" on Windows).
        why = _anchor_skipped_off_windows(result.returncode, result.stdout, platform="any")
        assert not why, ("SURVIVOR: the anchor test PASSED on the pristine tree and was only SKIPPED with this fix "
                         "reverted -- the revert made the test stop checking instead of failing. Skip reason: " + why)
        # Read before cleanup. A setup/teardown error can also exit1 and is not
        # evidence of a failure in a previously passing testcase.
        mutated = _anchor_outcomes(private / "mutant.xml") if result.returncode in (0, 1) else {}
    failed = {identity for identity, outcome in mutated.items() if outcome == "failed"}
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
    assert failed and failed <= passed, "mutant must fail a testcase that actually passed in its pristine leg"
    # Each copy is a whole toolkit. pytest keeps every tmp_path until a LATER run rotates it out, so one local run
    # held 3,805 copies (~25 GiB) on 2026-10-18. A killed mutation leaves its copy behind, and so does a failing one,
    # which is kept on purpose for debugging.
    shutil.rmtree(work, ignore_errors=True)


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
    ("tools/skyguard/engine.py", 'cwd=req.cwd or "", shell=shell, parsed=parsed,', 'shell=shell, parsed=parsed,',
     _MERGE + "test_guard_corpus.py::test_intended_rows_reproduce", "merge-engine-drops-cwd"),
    ("tools/skyguard/engine.py", "    return codes.classify(write_rules.decide(path, audit, roots=roots, cwd=cwd, op=op, transcript=transcript))",
     "    return codes.classify(write_rules.decide(path, audit, op=op, transcript=transcript))",
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


# Plan 6 Task 9: engine shadow mode (R8). Cover ordered policy, Claude fallback and Codex wiring separately.
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
    (_EN, '            audit.log_strict("skyguard", "SHADOW", record)   # raises', "            pass  # raises",
     _SH + "test_every_converted_verdict_writes_a_shadow_audit_line_naming_the_rule", "l2-shadow-logs"),
    ('tools/skyguard/portable.py', '    side = (engine.side_effects(req, str(project), audit, value, lambda: roots, deadline)', '    side = ((lambda *args: value)(req, str(project), audit, value, lambda: roots, deadline)',
     _SH + "test_the_backup_of_a_shadowed_delete_is_still_taken", "l2-shadow-side-effects-run"),
    (_EN, "_state.honoured(st, session_id, now, start)", "st.lifts",
     _SH + "test_another_sessions_lift_is_not_honoured", "l2-shadow-session-bound"),
    (_EN, 'if x["kind"] == "guards"]', "if True]",
     _SH + "test_a_lock_lift_does_not_switch_the_guards_off", "l2-shadow-kind"),
    (_EN, '            audit.log("skyguard", "STATE-REJECTED", st.reason)\n', "            pass\n",
     _SH + "test_an_unverifiable_state_reads_as_guards_on_with_a_log_line", "l2-shadow-rejected-logs"),
    (_EN, "    except Exception:  # noqa: BLE001 -- shadow mode", "    except OSError:  # noqa: BLE001 -- shadow mode",
     _SH + "test_a_failure_inside_shadow_mode_keeps_the_original_deny", "l2-shadow-fails-closed"),
    ("tools/skyguard/portable.py",
     '        value = engine.shadow(codes.classify(value), str(project), req.session_id,\n'
     '                              _legacy.telemetry if _legacy else "", audit, now=_legacy.now if _legacy else None,\n'
     '                              **shadow_options)',
     '        value = codes.classify(value)',
     _SH + "test_every_denied_or_asked_corpus_row_is_shadowed_except_the_inert_ones",
     "l2-shadow-claude-exit"),
    ("tools/skyguard/run.py", "        v = engine.shadow(v, a.project, p[\"session_id\"], a.telemetry, audit)",
     "        pass", _SH + "test_a_websocket_monitor_uses_the_legacy_shadow_fallback[guards-off]",
     "l2-shadow-claude-fallback-monitor"),
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
    (_SS, "        if not current.rejected and (current.lifts or failure):", "        if False:",
     _SST + "test_a_guards_lift_is_reported_and_cleared_at_start", "l2-sess-clear"),
    (_SS, '        try:\n            failure = _relock(root) if engaged else ""', '        try:\n            failure = ""',
     _SST + "test_relock_happens_before_the_report_and_names_the_lifted_files", "l2-sess-relock"),
    (_SS, "\n        lifted, drift = _scan(root, st.lifts) if engaged else ([], [])", "\n        lifted, drift = [], []",
     _SST + "test_relock_happens_before_the_report_and_names_the_lifted_files", "l2-sess-scan"),
    (_SS, "        elif p.name in manifest:", "        elif False:",
     _SST + "test_drift_is_a_file_unlocked_with_no_lift_record", "l2-sess-drift"),
    (_SS, "            banners.append(disabled)", "            pass",
     _SST + "test_a_terminal_disable_is_honoured_and_shown_every_session", "l2-sess-disabled-banner"),
    (_SS, "        engaged = _engaged(root) and not st.lock_disabled", "        engaged = _engaged(root)",
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
    (_EN, '            audit.log_strict("skyguard", "SHADOW", record)   # raises',
     '            audit.log("skyguard", "SHADOW", record)   # raises',
     _SH + "test_a_refusal_is_not_converted_when_its_shadow_line_cannot_be_written", "l2-shadow-line-must-land"),
    (_ST, "        if best is None or t > best:", "        if True:",
     _SH + "test_a_forged_early_start_line_cannot_revive_a_pre_resume_lift", "l2-state-start-max"),
    ("tools/skyguard/run.py", '    if a.role in DECISION_ROLES and not getattr(a, "ordered_dispatch", False):', "    if True:",
     _SH + "test_a_tool_call_logs_one_shadow_line_not_one_per_hook", "l2-shadow-decision-roles-only"),
    (_EN, "        if not readonly and _state_reject_is_new(project, st.reason):", "        if not readonly:",
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
    (_SS, '        try:\n            failure = _relock(root) if engaged else ""', '        try:\n            failure = ""',
     _SST + "test_the_files_are_already_relocked_when_the_banner_is_written", "l2-sess-report-requires-relock"),
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
    (_SF, '[".claude", "hooks"]', '[".claude", "unprotected"]',
     _p12row("guard-hook-redirect"), "rule-S-SELF-GUARD-hooks"),
    (_SF, '["tools", "skyguard"]', '["tools", "unprotected"]',
     _p12row("guard-engine-file"), "rule-S-SELF-GUARD-engine"),
    (_SF, '["agent", "guards"]', '["agent", "unprotected"]',
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
    # .claude is also a permanent save-store holder now, so its row stops earlier at S-SAVE-STORE.
    (_SF, '        return GUARD if (rel == [] or rel in _HOLDERS) else ""', '        return ""',
     _SFT + "test_non_store_guard_holder_move_keeps_its_guard_advice[tools]",
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
    # Removing this branch alone is redundant with install_kind in the fallback loop. Misclassification is not.
    (_SF, '    if ".skyguard" in comps[:-1] or (comps and comps[-1] == ".skyguard"):\n        return STATE',
     '    if ".skyguard" in comps[:-1] or (comps and comps[-1] == ".skyguard"):\n        return GUARD',
     _SFT + "test_without_roots_the_path_words_decide", "rule-W-SELF-STATE-text-kind"),
    (_EN, 'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE", "write.W-SELF-STATE"))',
     'NEVER_SHADOW = NEVER_SHADOW | frozenset(("write.W-SELF-STATE",))',
     _SFT + "test_guards_off_does_not_open_the_state_file[shell]", "rule-S-SELF-STATE-never-shadowed"),
    (_EN, 'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE", "write.W-SELF-STATE"))',
     'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-STATE",))',
     _SFT + "test_guards_off_does_not_open_the_state_file[write]", "rule-W-SELF-STATE-never-shadowed"),
    # l2-self-applypatch-move: the `*** Move to:` path dropped from the patch's path list.
    ('tools/modding_harness/patches.py', '            ops += [("move_from", h["path"]), ("move_to", h["move"])]', '            ops += [("move_from", h["path"])]',
     _SFT + "test_apply_patch_move_to_into_the_state_is_denied", "l2-self-applypatch-move"),
    # ---- GUARD-FILES.sha256: the drift gate bites, setup.sh's rewritten file stays out
    ("tools/skyguard/verdict.py", "from __future__ import annotations", "from __future__ import annotations  # edited",
     "tests/test_guard_manifest_components.py::test_manifest_matches_actual_guard_bytes", "l2-self-manifest-drift"),
    ("scripts/gen-guard-hashes.py", 'SETUP_WRITES = frozenset((".codex/hooks.json",))', "SETUP_WRITES = frozenset()",
     "tests/test_guard_manifest_components.py::test_guard_inventory_excludes_setup_writes_from_owned_tracked_tree", "l2-self-manifest-setup-writes"),
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
     _SFT + "test_a_computed_interpreter_delete_keeps_the_state_text_fallback", "rule-S-SELF-STATE-delete-interpreter"),
    # Re-anchored in the final fix pass (lane A C1 put the raw-unlock check between this and the deletes).
    (_SR6, '    if own and own[0] == self_rules.STATE:\n        return deny("S-SELF-STATE", own[1])\n    # Plan 6 final fix',
     '    if own and own[0] == self_rules.STATE and not dels.hit:\n        return deny("S-SELF-STATE", own[1])\n'
     '    # Plan 6 final fix',
     _SFT + "test_guards_off_does_not_let_the_lock_manifest_be_deleted", "rule-S-SELF-STATE-delete-before-del-install"),
    ('tools/skyguard/self_rules.py', '            if placed and save_store_target(placed, roots, holder, cache):', '            if False:', 'tests/test_skyguard_self_rules.py::test_a_delete_of_the_whole_install_cannot_destroy_permanent_backups', 'save-store-install-ancestor-protection'),
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
    (_BK, '            durable.write_all(fd, line)', '            for byte in line: durable.write_all(fd, bytes([byte]))',
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
    (_SN, "backup._copy_hashed(src, part, fsync=False)", "backup._copy_hashed(src, part)",
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
    (_SN, '                os.replace(str(part), str(target))',
     '                shutil.copyfile(str(part), str(target))\n                part.unlink()',
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
    (_DR, 'cstate, why in health._coverage_rows(Path(root), "claude"):', 'cstate, why in []:',
     _DRT + "test_claude_coverage_short_is_not_live_like_the_canary_says", "l2-doctor-claude-coverage"),
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
    (_RC, '        else:\n            idx["files"].update(updates)          # recorded only once on disk',
     '        idx["files"].update(updates)\n        if True:          # recorded only once on disk',
     _RCT + "test_a_failed_manifest_append_is_retried_by_the_next_call", "l2-rec-index-after-append"),
    (_RC, "    if last == base:\n        n = 2", "    if False:\n        n = 2",
     _RCT + "test_two_snapshots_in_one_second_get_different_ids", "l2-rec-unique-id"),
    # ---- the save a command changes (the save policy, user ruling 2026-10-13: saves.py finds it, recovery.backup_saves
    # keeps it). No twin, said so: the old rate limit, unchanged-skip and size bound are GONE by policy (every change is
    # backed up), and save_name / shell_save_names went with them (judge() reports every save it sees, `found`).
    ("tools/skyguard/shell_rules.py", "    resaver = resaver_write_calls(command, shell, parsed)", "    resaver = None",
     _RCT + "test_resaver_apply_snapshots_the_named_save_and_its_cosave_only", "l2-rec-resaver"),
    # The pair: the backup takes both files whichever one was named (recovery.pair_members), and the count calls a
    # save that has no co-save an existing save.
    ("tools/skyguard/recovery.py", "    for p in (ess, skse):", "    for p in (ess,):",
     'tests/test_legacy_save_store.py::test_legacy_writer_retains_both_pair_members', "l2-rec-cosave"),
    ("tools/skyguard/recovery.py", "    for p in (ess, skse):", "    for p in (skse,):",
     'tests/test_legacy_save_store.py::test_legacy_writer_retains_both_pair_members', "l2-rec-cosave-names-its-save"),
    ("tools/skyguard/saves.py", "        le, ls = _listed(ess), _listed(skse)", "        le, ls = None, _listed(skse)",
     "tests/test_save_policy.py::test_a_missing_cosave_is_recorded_as_missing_not_an_error", "save-count-a-save-alone-exists"),
    ("tools/skyguard/saves.py", "            if j:\n                cands.append(j)", "            if False:\n                cands.append(j)",
     _RCT + "test_resaver_by_a_path_relative_to_the_cwd", "l2-rec-resaver-cwd"),
    ("tools/skyguard/saves.py", "            if folder:\n                cands.append(", "            if False:\n                cands.append(",
     _RCT + "test_resaver_by_bare_name_finds_it_in_the_saves_folder", "l2-rec-resaver-saves-folder"),
    ("tools/skyguard/mcp_rules.py", "    return saves.judge(saves.devbench_change(found, roots, env), Verdict(), audit, HOOK)",
     "    return Verdict()", _RCT + "test_devbench_game_save_over_an_existing_save_snapshots_it", "l2-rec-devbench-mcp"),
    ("tools/skyguard/saves.py", "    if dev_found:\n        c = merge([c, devbench_change(", "    if False:\n        c = merge([c, devbench_change(",
     _RCT + "test_devbench_console_save_through_curl_snapshots_it", "l2-rec-devbench-shell"),
    ("tools/skyguard/saves.py", "            if n.casefold() == name.casefold():", "            if n == name:",
     _RCT + "test_the_save_snapshot_reads_a_call_exactly_as_the_rule_does[game-case]", "l2-rec-save-name-case"),
    ("tools/skyguard/mcp_rules.py", "    if found is not None and hit[0]:\n        found.append(", "    if False:\n        found.append(",
     _RCT + "test_the_save_snapshot_reads_a_call_exactly_as_the_rule_does[game]", "l2-rec-save-found"),
    ("tools/skyguard/mcp_rules.py", "            hit = judge(stool, sargs, roots, memo, env, depth + 1, found)",
     "            hit = judge(stool, sargs, roots, memo, env, depth + 1)",
     "tests/test_save_policy.py::test_a_devbench_scenario_saving_three_existing_saves_needs_approval", "l2-rec-save-found-steps"),
    ("tools/skyguard/saves.py", "            unknown.append((rid, tuple(sorted(fields.items()))))", "            pass",
     _RCT + "test_a_save_that_cannot_be_identified_is_logged", "l2-rec-devbench-unknown-refused"),
    # ---- the time box: never past the hook's budget, and never silent (a copy cut short DENIES)
    (_RC, "        want, copied, digest = backup._copy_hashed(src, part, fsync=True, deadline=deadline)",
     "        want, copied, digest = backup._copy_hashed(src, part, fsync=True)",
     'tests/test_legacy_save_store.py::test_legacy_writer_copy_deadline_cleans_partial_candidate', "l2-rec-save-deadline"),
    ("tools/modding_harness/dispatch.py", '"surface": action.surface}, deadline, plan.known_writer_active)',
     '"surface": action.surface}, None, plan.known_writer_active)',
     _RCT + "test_a_save_copy_that_runs_out_of_time_is_partial_never_silent", "l2-rec-save-caller-deadline"),
    ("tools/skyguard/backup.py", "                if deadline is not None and _clock() >= deadline:", "                if False:",
     'tests/test_legacy_save_store.py::test_legacy_writer_copy_deadline_cleans_partial_candidate', "l2-rec-copy-deadline"),
    (_RC, "    except backup.CopyTimeout:\n        backup._discard(part)\n        raise SaveBackupFailed(",
     "    except backup.CopyTimeout:\n        raise SaveBackupFailed(",
     'tests/test_legacy_save_store.py::test_legacy_writer_copy_deadline_cleans_partial_candidate', "l2-rec-timeout-is-partial"),
    # ---- the state a snapshot stands for, and pruning
    (_RC, "    for e in entries[:mine[-1] + 1]:", "    for e in entries:",
     _RCT + "test_resolve_gives_the_full_state_at_a_snapshot", "l2-rec-resolve-at-id"),
    (_RC, '    kept = {parsed[i]["snap"] for i in newest.values()}', "    kept = set()",
     _RCT + "test_pruning_drops_old_lines_but_keeps_each_paths_newest", "l2-rec-prune-keeps-newest"),
    (_RC, '        if parsed[i]["kind"] == "save" or now - parsed[i]["t"] < KEEP_DAYS * DAY:',
     '        if now - parsed[i]["t"] < KEEP_DAYS * DAY:',
     "tests/test_legacy_save_store.py::test_legacy_save_snapshots_keep_superseded_bytes_forever", "l2-rec-prune-save-days"),
    (_RC, "    if any(e is None for _r, e in keep):", "    if False:",
     _RCT + "test_an_unreadable_manifest_line_stops_blob_pruning_and_is_kept", "l2-rec-prune-unreadable"),
    (_RC, "        if (_BLOB.match(ent.name) and ent.name not in live) or snapshot._stale_part(ent, now):", "        if False:",
     _RCT + "test_pruning_drops_old_lines_but_keeps_each_paths_newest", "l2-rec-prune-blobs"),
    # ---- the hooks: the copy is made by the PreToolUse hook, before the command
    ("tools/skyguard/run.py", "        _recovery(a, p, audit, None if env is None else paths.roots(a.root, a.root_win, env))",
     "        pass", _RCT + "test_the_snapshot_hook_takes_the_load_order_but_never_the_save", "l2-rec-claude-wired"),
    ("tools/skyguard/run.py", '        v = _saves(a, p, audit, v, p["tool_name"])          # an existing save',
     '        pass          # an existing save',
     _RCT + "test_the_mcp_hook_copies_the_save_before_devbench_overwrites_it", "l2-rec-claude-mcp-wired"),
    ("tools/skyguard/run.py", '                v = _saves(a, p, audit, v, p["command"])', "                pass",
     _RCT + "test_the_save_is_copied_by_the_hook_before_the_command_can_change_it[claude-protect-bash]",
     "l2-rec-claude-bash-wired"),
    ("tools/skyguard/run.py", '        v = _saves(a, p, audit, v, "{} {}".format(p["tool_name"], p["file_path"]))', "        pass",
     "tests/test_save_policy.py::test_claude_protect_files_backs_up_before_it_answers", "l2-rec-claude-files-wired"),
    ("tools/skyguard/portable.py", 'preserve=not preview)',
     'preserve=False)',
     _RCT + "test_the_save_is_copied_by_the_hook_before_the_command_can_change_it[codex-pre-tool]",
     "l2-rec-codex-enforce-wired"),
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
    (_SC, '                    saved = backup.take(str(project), "skyguard snapshot restore", str(target), audit)',
     '                    saved = target',
     _SCT + "test_in_place_backs_up_what_it_replaces_first", "l2-restore-cli-in-place-backup"),
    (_SC, "        if odd:", "        if False:",
     # Save IDs dispatch to legacy_saves before this generic config/script restore branch now.
     _SCT + "test_in_place_refuses_an_ini_outside_the_configured_config_dir[other-file-in-config-dir]", "l2-restore-cli-in-place-names"),
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
    # I1 (the save follows the rule, not the read-only first word): no twin since the save policy -- the save is found
    # by the rules' own parse (saves.shell_change), and recovery.run takes no save, so no first-word skip is left to put
    # back. test_a_read_only_looking_prefix_does_not_hide_the_save still pins the behaviour.
    # I2: the save ReSaver WRITES (its out), not the input
    ("tools/skyguard/shell_rules.py", "calls.append((_resaver_op(e), ws[1:] or ws, e))",
     "calls.append((_resaver_op(e), ws[:1], e))",
     _RCT + "test_resaver_keeps_the_save_it_writes_not_the_one_it_reads", "l2-rec-fr1-i2-out"),
    ("tools/skyguard/saves.py", '        pairs.append(Pair(le or ess, ls or skse, stem, tuple(d["ops"]), bool(le or ls)))',
     '        pairs.append(Pair(le or ess, ls or skse, stem, tuple(d["ops"]), True))',
     _RCT + "test_resaver_writing_a_new_out_keeps_nothing_and_says_so", "l2-rec-fr1-i2-new-out-logged"),
    ("tools/skyguard/saves.py", '        if WILD.search(os.path.splitdrive(word)[1]):\n            unknown.append(', "        if False:\n            unknown.append(",
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
MUTATIONS.append(pytest.param(_RC, "    if free - total < SAVE_RESERVE_BYTES:", "    if False:",
                              "tests/test_legacy_save_store.py::test_legacy_writer_reserves_space_for_the_complete_pair",
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
     "tests/test_snapshot_parser.py::test_snapshot_parser_refuses_abbreviations_before_dispatch[--in]", "l2-restore-fr2-no-abbrev-snapshot"),
    ("tools/skyguard/cli.py", 'prog="skyguard guards", add_help=False, allow_abbrev=False)',
     'prog="skyguard guards", add_help=False)',
     _SCT + "test_no_skyguard_subcommand_takes_an_abbreviated_option[guards]", "l2-restore-fr2-no-abbrev-guards"),
    ("tools/skyguard/lock.py", '    sp = sub.add_parser("status", parents=[common], allow_abbrev=False)',
     '    sp = sub.add_parser("status", parents=[common])',
     _SCT + "test_no_skyguard_subcommand_takes_an_abbreviated_option[lock-json]", "l2-restore-fr2-no-abbrev-lock-status"),
    # No twin for the common parent parser's allow_abbrev: a parent only lends its arguments; the subparser's own
    # setting decides (the status subparser's twin above covers it).
    (_RC, '(?:-(?:\\d+|[0-9a-f]{32}))?\\Z")', '(?:-(?:\\d+|[0-9a-f]{32}))?$")',
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
    # The save policy: the Codex MCP route returns the judge's verdict, and pre_tool backs the save up before allowing.
    # Dropping that verdict's save plan lets the overwrite run with no backup.
    ("tools/skyguard/engine.py", _T17_JUDGE,
     _T17_JUDGE.replace("        return evaluate_mcp(", "        v = evaluate_mcp(") + "\n        return v._replace(saves=None)",
     _T17 + "test_a_devbench_save_over_an_existing_save_is_backed_up_then_allowed_on_codex",
     "t17-codex-devbench-save-backed-up"),
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
    ("tools/skyguard/engine.py", "shell=shell, parsed=parsed,\n", "shell=shell,\n",
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
    ("rule-S-WRITE-PLUGIN-ps-method-calls", _WR, "    lit = [(None, w) for w in text_write_targets_ps(command)]", "    lit = []",
     _rtb("ps-downloadfile")),
    ("rule-S-WRITE-PLUGIN-interpreter-literals", _WR, "            lit += [(e, w) for w in text_write_targets(code, python_literals=bool(shellparse._PYTHON.fullmatch(e.program)))]", "            pass",
     _rtb("python-open-write")),
    ("rule-S-WRITE-PLUGIN-open-write-mode", _WR, 'name in ("open", "io.open", "builtins.open") and writing(node, 1)', 'name in ("open", "io.open", "builtins.open")',
     _rtb("python-open-read")),
    ("rule-S-WRITE-PLUGIN-pathlib", _WR, 'isinstance(func, ast.Attribute) and func.attr == "Path" and', 'isinstance(func, ast.Attribute) and func.attr == "NoPath" and',
     _rtb("python-pathlib")),
    ("rule-S-WRITE-PLUGIN-node-writefile", _WR, "(?:writeFile|appendFile|", "(?:appendFile|", _rtb("node-writefile")),
    # --- S-SAVE-BACKUP (the save policy, 2026-10-13; was S-WRITE-SAVE's ask): a write, copy or move onto a save in
    # the settings folder is a save change, backed up first. Each route that finds one, and the wiring that judges it.
    ("rule-S-SAVE-BACKUP-wired", _SHR, "    return saves.judge(change, v, audit, HOOK)", "    return v", _rtb("save-redirect")),
    ("rule-S-SAVE-BACKUP-write", _WR, "            if is_save(a, roots):", "            if False:", _rtb("save-redirect")),
    ("rule-S-SAVE-BACKUP-copy-onto", _WR, "            if is_save(a_dst, roots):", "            if False:", _rtb("save-cp")),
    ("rule-S-SAVE-BACKUP-copy-into-folder", _WR, "                if save_dests:",
     "                if False:",
     "tests/test_save_policy.py::test_a_copy_into_the_saves_folder_that_takes_a_saves_name_is_a_change"),
    ("rule-S-SAVE-BACKUP-interpreter-literal", _WR, '        elif is_save(a, roots):\n            actual_base = cwd if e is None else (targets.physical_cwd(e, cwd, env) or "")\n            saves.append((targets.physical(word, actual_base, "cmd", env, literal=True) or "", "write"))',
     '        elif False:\n            saves.append((a, "write"))', _rtb("save-python")),   # fix-limits 9 moved it
    ("rule-S-SAVE-BACKUP-cosave", _WR, 'SAVE_EXTS = (".ess", ".skse")', 'SAVE_EXTS = (".ess",)', _rtb("save-cosave")),
    ("rule-S-SAVE-BACKUP-settings-folder-only", _WR, '.endswith(SAVE_EXTS) and targets.zone(abs_path, roots) == "config"',
     ".endswith(SAVE_EXTS)", "tests/test_save_policy.py::test_an_existing_save_outside_the_settings_folder_is_free"),
    # --- S-MOVE-ROOT: moving the install or its folders away
    # Merge (2026-10-05): a root moved OUT of its zone now reaches S-MOVE-ROOT through the delete branch too, so this
    # clause is pinned by a move that stays in its zone (no delete hit): Saves renamed beside itself.
    ("rule-S-MOVE-ROOT-wired", _SHR, "    if wr.move_root:                                         # rt-fix-b",
     "    if False:                                         # rt-fix-b", _rtb("move-saves")),
    ("rule-S-MOVE-ROOT-protected-roots", _WR, "    if targets.covers_protected(abs_path, roots):", "    if False:",
     "tests/test_skyguard_deletes.py::test_moving_config_root_away_names_the_move_without_store_preemption[mv]"),
    ("rule-S-MOVE-ROOT-data-folder", _WR, '    if _rel(abs_path, roots.install_dirs) == ["data"] and', "    if False and",
     _rtb("move-data-relative")),
    ("rule-S-MOVE-ROOT-saves-folder", _WR, '    return _rel(abs_path, roots.config_dirs) == ["saves"] or', "    return False or",
     _rtb("move-saves")),
    ("rule-S-MOVE-ROOT-mv", _WR, 'MOVE_PROGRAMS = frozenset(("mv", "move-item"))', 'MOVE_PROGRAMS = frozenset(("move-item",))',
     "tests/test_skyguard_deletes.py::test_moving_config_root_away_names_the_move_without_store_preemption[mv]"),
    ("rule-S-MOVE-ROOT-move-item", _WR, 'MOVE_PROGRAMS = frozenset(("mv", "move-item"))', 'MOVE_PROGRAMS = frozenset(("mv",))',
     "tests/test_skyguard_deletes.py::test_moving_config_root_away_names_the_move_without_store_preemption[move-item]"),
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
    # --- Edit/Write onto a save (was W-SAVE's ask): the same save change, backed up first
    ("rule-S-SAVE-BACKUP-edit-wired", _WRR, "        if is_save(a, roots):", "        if False:", _rtb("write-save")),
    ("rule-S-SAVE-BACKUP-edit-cosave", _WR, 'SAVE_EXTS = (".ess", ".skse")', 'SAVE_EXTS = (".ess",)', _rtb("write-cosave")),
    ("rule-S-SAVE-BACKUP-edit-settings-folder-only", _WRR, "        if is_save(a, roots):",
     "        if a is not None and a.lower().endswith(('.ess', '.skse')):",
     "tests/test_save_policy.py::test_an_existing_save_outside_the_settings_folder_is_free"),
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
     _MDEL + "test_moving_config_root_away_names_the_move_without_store_preemption[mv]", "merge-rt-move-root-wins-over-delete"),
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
     "    for w, how in recycle_targets(recycle_text(command, parsed)):",
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
    ("p5f-core-md-ask-count-stale", "agent/instructions/core.md", "### ASKS THE USER -- five rules",
     "### ASKS THE USER -- four rules", _DOCS + "[core]"),
    ("p5f-readme-ask-count-stale", "README.md", "Only five rules ask *you*", "Only four rules ask *you*",
     _DOCS + "[readme]"),
    ("p5f-philosophy-ask-count-stale", "docs/safety-philosophy.md", "Five rules in the toolkit ask you",
     "Four rules in the toolkit ask you", _DOCS + "[philosophy]"),
    # Privacy: a fixture carries the home of whoever runs the gate again (built at collection time, so this file
    # never names anyone).
    ("p5f-fixture-carries-home", "tests/fixtures/routes-harness/cases9.py", '"file_path":"<HOME>/.codex/config.toml"',
     '"file_path":"C:/Users/' + (login_name() or Path.home().name) + '/.codex/config.toml"',
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
    ("t10-build-ships-tests", _BZ, 'EXCLUDE = ("tests", ".github", ', 'EXCLUDE = (".github", ',
     _RB + "test_tests_and_github_are_not_shipped"),
    # scripts/layer2-spike/ (developer-only Layer 2 probes): the entry, and each clause of the prefix rule.
    ("t10-build-ships-layer2-spike", _BZ, '".github", "scripts/layer2-spike", "tools/resaver-cli/tests",',
     '".github", "tools/resaver-cli/tests",',
     _RB + "test_the_layer2_spike_folder_is_excluded_by_prefix_not_its_siblings"),
    ("t10-build-ships-native-authoring", _BZ, '"scripts/layer2-spike", "tools/resaver-cli/tests",',
     '"scripts/layer2-spike",',
     _RB + "test_native_fixture_authoring_is_excluded_without_hiding_runtime_or_siblings"),
    ("t10-build-exclude-top-level-only", _BZ, '    return any(path == e or path.startswith(e + "/") for e in EXCLUDE)',
     '    return path.split("/", 1)[0] in EXCLUDE',
     _RB + "test_the_layer2_spike_folder_is_excluded_by_prefix_not_its_siblings"),
    ("t10-build-exclude-prefix-no-boundary", _BZ,
     '    return any(path == e or path.startswith(e + "/") for e in EXCLUDE)',
     '    return any(path.startswith(e) for e in EXCLUDE)',
     _RB + "test_the_layer2_spike_folder_is_excluded_by_prefix_not_its_siblings"),
    ("t10-build-info-wrong-commit", _BZ, 'built-from=git archive (committed objects only)\\n".format(sha, version)',
     'built-from=git archive (committed objects only)\\n".format("0" * 40, version)',
     _RB + "test_a_clean_build_has_one_versioned_root_and_build_info"),
    # T10.2 the one payload checker
    ("t10-payload-routes-harness-allowed", _CRP, '    "tests/fixtures/routes-harness",\n', "",
     _RP + "test_every_check_can_fail_on_its_own_line[routes-harness-shipped]"),
    ("t10-payload-github-allowed", _CRP, '    ".github",\n', "",
     _RP + "test_every_check_can_fail_on_its_own_line[github-shipped]"),
    ("t10-payload-layer2-spike-allowed", _CRP,
     '    "scripts/layer2-spike",    # Plan 6 Layer 2 lock research probes: developer-only, kept in the repo, never shipped\n',
     "", _RP + "test_every_check_can_fail_on_its_own_line[layer2-spike-shipped]"),
    # review L4b L1: the maintainer-only corpus scripts crash in a zip (they read tests/guard_corpus/).
    ("t10-payload-guard-corpus-allowed", _CRP,
     '    "scripts/guard-corpus",    # guard-corpus tooling and the latency gate read tests/guard_corpus/: maintainer-only\n',
     "", _RP + "test_every_check_can_fail_on_its_own_line[guard-corpus-shipped]"),
    ("t10-payload-guard-latency-allowed", _CRP, '    "scripts/guard-latency.py",\n', "",
     _RP + "test_every_check_can_fail_on_its_own_line[guard-latency-shipped]"),
    ("t10-payload-docs-superpowers-allowed", _CRP,
     '    "docs/superpowers",       # maintainer plans/handoffs/measurements; cited evidence ships as docs/evidence/\n',
     "", _RP + "test_every_check_can_fail_on_its_own_line[docs-superpowers-shipped]"),
    ("t10-build-ships-docs-superpowers", _BZ, 'EXCLUDE += ("docs/superpowers",)\n', "",
     _RB + "test_the_maintainer_superpowers_tree_is_excluded_but_cited_evidence_ships"),
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
    ("t10fr-config-allowed-unvalidated", _CRP, "        if any(config_dir_ok(c, h) for h in cfg_homes):", "        if True:",
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
     '    problems += ["missing from the zip: " + p for p in sorted(set(want) - set(got))]', "    problems += []",
     _RB + "test_the_post_build_proof_catches_a_missing_or_altered_member"),
    ("t10fr2-proof-misses-extra-files", _BZ,
     '    problems += ["not in the commit: " + p for p in sorted(set(got) - set(want))]', "    problems += []",
     _RB + "test_the_post_build_proof_catches_a_missing_or_altered_member"),
    ("t10fr2-proof-misses-altered-bytes", _BZ,
     '        problems.append("bytes differ from the committed blob: " + p)', "        pass",
     _RB + "test_the_post_build_proof_catches_a_missing_or_altered_member"),
    ("t10fr2-proof-duplicate-accepted", _BZ, "            if identity in seen:", "            if False:",
     _RB + "test_the_post_build_proof_rejects_duplicate_members_even_if_last_is_correct"),
    ("t10fr2-proof-binary-normalized", _BZ, '        if (text and b"\\0" not in got[p] and b"\\0" not in original and',
     '        if (True and', _RB + "test_the_post_build_proof_does_not_normalize_binary_or_undeclared_bytes"),
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

# Git for Windows 2.55.0.5 pin (2026-10-13): every delivery path names the tested Git, and the audit reads all of
# them. One twin per path and per audit clause the pin test leans on.
_GIT_PIN = _TPT + "test_every_git_for_windows_pin_names_the_tested_version"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("setup.sh", "(tested with Git for Windows 2.55.0.5)", "(tested with Git for Windows 2.48.1)", _GIT_PIN,
     "git-pin-setup-says-an-older-git"),
    ("tools/inventory-runtimes.py", "d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6",
     "ce45e23275049f4b36edd90d5fd986a1e230efb6c511e9260a90176ce8e825df", _GIT_PIN, "git-pin-manual-steps-old-hash"),
    ("tools/inventory-runtimes.py", "releases/download/v2.55.0.windows.5/", "releases/download/v2.48.1.windows.1/",
     _GIT_PIN, "git-pin-manual-steps-old-release"),
    (_TPA, '              {"pattern": r"tested with Git for Windows \\*{0,2}(?P<v>\\d+(?:\\.\\d+){2,3})"}],',
     "              ],", _GIT_PIN, "git-pin-audit-stops-reading-setup"),
    (_TPA, r'r"Git-(?P<v>\d+(?:\.\d+){2,3})-64-bit\.exe")],', r'r"Git-(?P<v>\d+\.\d+\.\d+)-64-bit\.exe")],', _GIT_PIN,
     "git-pin-site-reads-three-parts-only"),
    (_TPA, '    return (m.group(1) + ("" if m.group(2) == "1" else "." + m.group(2))) if m else v', "    return v",
     _TPT + "test_a_git_for_windows_tag_compares_as_its_installer_version[2.55.0.5-v2.55.0.windows.5-OK]",
     "git-tag-compares-as-a-tag"),
    (_TPA, '         "published_format": "github-release", "artifact": "Git-{pinned}-64-bit.exe"}]})],',
     '         "published_format": "github-release", "artifact": "Git-{pinned}-64-bit.exe"}]})][:0],',
     _TPT + "test_the_pinned_git_installer_hash_is_the_one_github_publishes[" + "f" * 64 + "-DRIFT]",
     "git-installer-hash-unchecked"),
]]

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
    ("tools/check-release-payload.py", '            "adapter": check_adapter, "guards": check_guards, "shared-core": check_shared_core}',
     '            "adapter": check_adapter, "shared-core": check_shared_core}', _MM_RP + "[guard-file-edited]", "p6mm-payload-guards-section-dropped"),
    ("tools/check-release-payload.py", '    ".claude/retired",', "", _MM_RP + "[retired-shipped]",
     "rr2-payload-retired-store-allowed"),
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
    ('t6d-published-hash-any-artifact', 'scripts/thirdparty-audit.py', '            ok = _published_checksum(f.get_ok(url), url, it, fields, len(want)) == want', '            ok = want in f.get_ok(url).text().lower()', 'tests/test_thirdparty_audit.py::test_published_hash_belongs_to_the_exact_artifact'),
    ('t6d-published-hash-not-compared', 'scripts/thirdparty-audit.py', '            ok = _published_checksum(f.get_ok(url), url, it, fields, len(want)) == want', '            ok = True', 'tests/test_thirdparty_audit.py::test_a_pinned_hash_upstream_publishes_is_ok_and_a_different_one_is_drift'),
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
    ('t6d-package-json-xeditlib-loses-its-commit', 'package.json', '"xeditlib": "https://codeload.github.com/WingedGuardian/xeditlib/tar.gz/06f73eac215970c427ede049a9198c8002c43c65"', '"xeditlib": "github:WingedGuardian/xeditlib"', 'tests/test_dependency_locks.py::test_xeditlib_and_koffi_are_locked_so_npm_ci_installs_them'),
    ('t6d-readme-xeditlib-back-to-unpinned', 'README.md', 'toolkit root: `npm ci --ignore-scripts --no-audit --no-fund`)', 'toolkit root: `npm install github:WingedGuardian/xeditlib`)', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-manual-steps-lose-their-hash', 'tools/inventory-runtimes.py', '+ "d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6 -- and run it. Then open a new terminal.",', '+ "-- and run it. Then open a new terminal.",', 'tests/test_thirdparty_audit.py::test_the_shipped_tree_has_no_unpinned_delivery_path'),
    ('t6d-container-doc-states-jdk-17-again', 'docs/container-vs-windows.md', 'ReSaver CLI (JDK 21.0.12 is in the image)', 'ReSaver CLI (JDK 17 is in the image)', 'tests/test_thirdparty_audit.py::test_the_real_tree_install_refs_agree_with_their_pins'),
]
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in _T6D_CLAUSES]
# Fix round 1, review P1/M6: tools/resaver-cli.sh's platform classpath separator and JDK-change recompile.
_T6D_CLAUSES_R1 = [
    ('t6d-resaver-linux-gets-windows-separator', 'tools/resaver-cli.sh', "  *) S=':' ;;", "  *) S=';' ;;", 'tests/test_resaver_cli_classpath.py::test_the_classpath_separator_is_the_platforms'),
    ('t6d-resaver-windows-gets-linux-separator', 'tools/resaver-cli.sh', "  MINGW*|MSYS*|CYGWIN*|Windows*) S=';' ;;", "  MINGW*|MSYS*|CYGWIN*|Windows*) S=':' ;;", 'tests/test_resaver_cli_classpath.py::test_the_classpath_separator_is_the_platforms'),
    ('t6d-resaver-seam-outside-test-mode', 'tools/resaver-cli.sh', '[ "${TOOLKIT_TEST_MODE:-}" = 1 ] && [ -n "${RESAVER_CLI_UNAME:-}" ] && UNAME="$RESAVER_CLI_UNAME"', '[ -n "${RESAVER_CLI_UNAME:-}" ] && UNAME="$RESAVER_CLI_UNAME"', 'tests/test_resaver_cli_classpath.py::test_the_platform_seam_is_ignored_outside_test_mode'),
    ('t6d-resaver-jdk-change-ignored', 'tools/resaver_cli_cache.py', 'records.update(jdk=jdk, compiler=compiler,', 'records.update(jdk="ignored", compiler=compiler,', 'tests/test_resaver_cli_classpath.py::test_a_jdk_change_recompiles_the_driver'),
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
    pytest.param(".gitattributes", ".devcontainer/**   text eol=lf", ".devcontainer/**   text eol=crlf",
                 "tests/test_gen_agent_trees.py::test_files_linux_tools_read_are_pinned_lf", id="eol-devcontainer-lf"),
    # CI run 38178385999: unpinned text files checked out CRLF on windows-latest and the release build refused
    # the pristine checkout as dirty. The catch-all gives every text file an eol.
    pytest.param(".gitattributes", "* text=auto eol=lf\n", "",
                 _RB + "test_an_autocrlf_checkout_under_our_attributes_is_not_a_dirty_tree",
                 id="eol-catch-all-keeps-autocrlf-checkout-clean"),
    # CI run 38178385999 (ubuntu-latest): setup's fallback config folder C:/Users/<login>/... is validated below each
    # --home; checking only one of them reports the six files naming it as leaks.
    pytest.param("tools/check-release-payload.py", "        if any(config_dir_ok(c, h) for h in cfg_homes):",
                 "        if config_dir_ok(c, cfg_homes[-1]):",
                 "tests/test_release_payload.py::test_the_linux_runner_config_folder_is_allow_listed_and_nothing_else",
                 id="config-folder-validated-below-every-home"),
    pytest.param("tools/verify-release.sh",
                 '  login=$(whoami 2>/dev/null) && [ -n "$login" ] && cfg_homes+=(--home "C:/Users/$login")',
                 '  login=""',
                 "tests/test_verify_release.py::"
                 "test_the_config_folder_setup_writes_is_allow_listed_in_the_scrubbed_install",
                 id="verify-passes-setups-fallback-home"),
    # Off Windows setup leaves .codex/hooks.json unrendered by design; the placeholder grep must not count it there.
    pytest.param("tools/verify-release.sh", '       codex_defs="" ;;', "       : ;;",
                 "tests/test_verify_release.py::test_the_scrubbed_install_runs_and_its_canary_is_invisible",
                 marks=pytest.mark.skipif(sys.platform == "win32", reason="the off-Windows branch runs off Windows"),
                 id="verify-codex-defs-unrendered-off-windows"),
    # ...and on Windows the rendered file is still checked: the drive-letter branch must not take the other one.
    pytest.param("tools/verify-release.sh", "    [A-Za-z]:/*) ;;", "    __not_a_drive_root__) ;;",
                 "tests/test_verify_release.py::test_the_scrubbed_install_runs_and_its_canary_is_invisible",
                 marks=CMD_ONLY, id="verify-codex-defs-rendered-on-windows"),
    # v4.0.0 review L4a L4: an exported value stands in for a line skyrim-paths.env lacks (the test could not fail).
    pytest.param("setup.sh",
                 "    _got=$( unset SKYRIM_GAME_ROOT SKYRIM_CONFIG_DIR SKYRIM_LOADORDER_DIR SKYRIM_MO2_MODS "
                 "SKYRIM_MO2_OVERWRITE SKYGUARD_PYTHON",
                 "    _got=$( :",
                 "tests/test_setup_agent.py::test_an_exported_value_cannot_stand_in_for_a_missing_line",
                 id="setup-readback-trusts-exported-values"),
    # v4.0.0 review L4a L5: the in-memory audit goes back to the disk one, which writes below C:/home/u on Windows.
    pytest.param("tests/test_posix_host_paths.py", "class Audit(_DiskAudit):",
                 "Audit = _DiskAudit\n\n\nclass _MemoryAudit(_DiskAudit):",
                 "tests/test_posix_host_paths.py::test_a_powershell_delete_in_the_install_is_denied_on_a_posix_host",
                 id="posix-host-tests-write-to-disk"),
    # v4.0.0 review L4b M1: the whole lead-in line counted as history, so README's Node install was never compared.
    pytest.param("scripts/thirdparty-audit.py",
                 r'        lead = re.split(r"[;.!?](?:\s|$)", lines[idx - 1].rstrip())[-1]',
                 "        lead = lines[idx - 1]",
                 "tests/test_thirdparty_audit.py::"
                 "test_an_instruction_under_a_lead_in_with_an_earlier_history_sentence_is_compared",
                 id="thirdparty-lead-in-whole-line-history"),
    # v4.0.0 review L4b L3: setup.sh's install hints drift from their pins; the offline CI test must see it.
    pytest.param("setup.sh", "or: dotnet tool install --global Spriggit.CLI --version 0.41.0",
                 "or: dotnet tool install --global Spriggit.CLI --version 0.40.0",
                 "tests/test_thirdparty_audit.py::test_the_real_tree_install_refs_agree_with_their_pins",
                 id="thirdparty-setup-sh-drift-hidden"),
    # v4.0.0 review L4b L4: a truncated body escapes the retry and kills the run with the DRIFT exit code.
    pytest.param("scripts/thirdparty-audit.py",
                 "            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:",
                 "            except (urllib.error.URLError, OSError, ValueError) as exc:",
                 "tests/test_thirdparty_audit.py::test_a_truncated_body_is_could_not_check_not_a_crash",
                 id="thirdparty-incomplete-read-crashes"),
    # Coordinator finding 2026-10-20 (X4 session): hooks run by env-cleaning test helpers launched the REAL temp sweep.
    # One twin per clause of each guard.
    pytest.param("tools/skyguard/run.py", "    if tool.is_file() and own and not off and not in_test:",
                 "    if tool.is_file() and not off and not in_test:",
                 "tests/test_session_disk_note.py::test_no_sweep_for_a_project_that_is_not_this_toolkit", id="sweep-any-project"),
    pytest.param("tools/skyguard/run.py", "    if tool.is_file() and own and not off and not in_test:",
                 "    if tool.is_file() and own and not in_test:",
                 "tests/test_session_disk_note.py::test_the_switch_stops_the_session_sweep", id="sweep-switch-ignored"),
    pytest.param("tools/housekeep.py", '    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or ',
                 "    return False or ",
                 "tests/test_housekeep.py::test_the_real_bin_refuses_under_the_test_harness[PYTEST_CURRENT_TEST]",
                 id="real-bin-under-pytest"),
    pytest.param("tools/housekeep.py", ' or bool(os.environ.get("HARNESS_FIXTURE_ROOT"))\n',
                 ' or False\n',
                 "tests/test_housekeep.py::test_the_real_bin_refuses_under_the_test_harness[HARNESS_FIXTURE_ROOT]",
                 id="real-bin-under-launcher"),
    # review2 B-tools L2 / C-docs L7: the user's sweep switch must not make the bin refuse.
    pytest.param("tools/housekeep.py",
                 '    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or bool(os.environ.get("HARNESS_FIXTURE_ROOT"))\n',
                 '    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or bool(os.environ.get("HARNESS_FIXTURE_ROOT"))'
                 ' or sweep_switched_off()\n',
                 "tests/test_housekeep.py::test_the_users_sweep_switch_never_blocks_the_recycle_bin",
                 id="real-bin-refuses-under-the-user-switch"),
    # ... and run.py's launch guard reads the same markers and opt-in: one twin per clause.
    pytest.param("tools/skyguard/run.py", '    in_test = ((os.environ.get("PYTEST_CURRENT_TEST") or ',
                 "    in_test = ((None or ",
                 "tests/test_session_disk_note.py::test_a_sweep_is_launched_detached_outside_tests_and_never_inside_them"
                 "[PYTEST_CURRENT_TEST]", id="sweep-launch-ignores-pytest"),
    pytest.param("tools/skyguard/run.py", ' or os.environ.get("HARNESS_FIXTURE_ROOT"))\n',
                 " or None)\n",
                 "tests/test_session_disk_note.py::test_a_sweep_is_launched_detached_outside_tests_and_never_inside_them"
                 "[HARNESS_FIXTURE_ROOT]", id="sweep-launch-ignores-launcher"),
    pytest.param("tools/skyguard/run.py", '               and not os.environ.get("HOUSEKEEP_REAL_BIN_IN_TESTS"))',
                 '               and not os.environ.get("SKYGUARD_SWEEP_IN_TESTS"))',
                 "tests/test_session_disk_note.py::test_the_launch_guard_and_the_bin_refusal_read_the_same_markers",
                 id="sweep-launch-own-opt-in"),
    pytest.param("tools/housekeep.py", '    if os.environ.get("HOUSEKEEP_REAL_BIN_IN_TESTS"):\n        return False',
                 '    if False:\n        return False',
                 "tests/test_housekeep.py::test_an_integration_test_opts_in_to_the_real_bin_explicitly",
                 id="real-bin-opt-in-ignored"),
    pytest.param("tools/housekeep.py", "    if under_test_harness():\n        raise RuntimeError",
                 "    if False:\n        raise RuntimeError",
                 "tests/test_housekeep.py::test_the_real_bin_refuses_under_the_test_harness[HARNESS_FIXTURE_ROOT]",
                 id="real-bin-never-refuses"),
    # review2 B-tools L3: a retired-store failure must not hide the temp sweep's LEFT IN PLACE note.
    pytest.param("tools/housekeep.py", '    if r["recycled"] or (r["failed"] and not temp_raised):',
                 '    if r["recycled"] or (r["failed"] and not notes):',
                 "tests/test_housekeep.py::test_a_failed_retired_sweep_never_hides_temp_dirs_left_in_place",
                 id="session-retired-failure-hides-left-in-place"),
    pytest.param("tools/housekeep.py", "    if sweep_switched_off():\n        return 0", "    if False:\n        return 0",
                 "tests/test_housekeep.py::test_the_switch_turns_the_session_sweep_off", id="session-ignores-switch"),
    pytest.param("tools/housekeep.py", '            yield Path(e.path), "toolkit temp"',
                 '            yield Path(e.path), "toolkit temp"\n'
                 '        elif e.name.startswith("pytest-of-"):\n'
                 '            yield from ((Path(k.path), "pytest") for k in os.scandir(e.path) if k.is_dir())',
                 "tests/test_housekeep.py::test_the_sweep_never_touches_another_projects_pytest_runs", id="sweep-other-pytest-runs"),
    pytest.param("tests/conftest.py", 'os.environ["SKYGUARD_HOUSEKEEP_SWEEP"] = "off"',
                 'os.environ.setdefault("UNRELATED_SWITCH", "off")',
                 "tests/test_housekeep.py::test_every_test_helper_switches_the_real_sweep_off", id="conftest-sweep-on"),
    pytest.param("tests/run_harness_tests.py", 'SKYGUARD_HOUSEKEEP_SWEEP="off",', "",
                 "tests/test_housekeep.py::test_every_test_helper_switches_the_real_sweep_off", id="launcher-sweep-on"),
    pytest.param("scripts/setup-smoke.sh", "export SKYGUARD_HOUSEKEEP_SWEEP=off", ":",
                 "tests/test_housekeep.py::test_every_test_helper_switches_the_real_sweep_off", id="setup-smoke-sweep-on"),
    pytest.param("tools/verify-release.sh", "LANG=C.UTF-8 SKYGUARD_HOUSEKEEP_SWEEP=off)", "LANG=C.UTF-8)",
                 "tests/test_housekeep.py::test_every_test_helper_switches_the_real_sweep_off", id="verify-scrub-sweep-on"),
    # review2 B-tools L1: the two maintainer scripts that run real hooks on a configured copy.
    pytest.param("scripts/guard-latency.py", 'CLAUDE_PROJECT_DIR=str(inst), SKYGUARD_HOUSEKEEP_SWEEP="off")',
                 "CLAUDE_PROJECT_DIR=str(inst))",
                 "tests/test_guard_latency.py::test_every_timed_hook_runs_with_the_session_sweep_off",
                 id="guard-latency-sweep-on"),
    pytest.param("scripts/e2e-codex.py", '    os.environ["SKYGUARD_HOUSEKEEP_SWEEP"] = "off"\n', "    pass\n",
                 "tests/test_e2e_codex_verdict.py::test_main_switches_the_session_sweep_off_for_every_hook_it_starts",
                 id="e2e-codex-sweep-on"),
    # v4.0.0 review L4b M2: guard-latency timed dead hooks as fast ones and printed PASS. One twin per check.
    *[pytest.param("scripts/guard-latency.py", find, repl, "tests/test_guard_latency.py::" + test,
                   id="guard-latency-" + tid)
      for tid, find, repl, test in [
          ("result-discarded", '            why = answer_problem(r, json_out=hook != "session-kb-guard")',
           '            why = ""', "test_hooks_that_crash_fail_the_gate"),
          ("missing-wrapper-timed", "        if not os.path.isfile(wrapper):", "        if False:",
           "test_an_install_with_no_hooks_fails_the_gate"),
          ("exit-code-ignored", "    if r.returncode != 0:", "    if False:", "test_hooks_that_crash_fail_the_gate"),
          ("garbage-accepted", "    if out and json_out:", "    if False:",
           "test_a_tool_hook_answering_garbage_fails_the_gate"),
          ("failures-not-gated", "    ok = worst <= 1000 and not failed and bool(results)", "    ok = worst <= 1000",
           "test_hooks_that_crash_fail_the_gate"),
      ]],
    # v4.0.0 review L4b L2: the residual "no hook ran" check read only the last 1800 characters.
    pytest.param("scripts/e2e-codex.py", '"hook_ran": "hook: " in out,', '"hook_ran": "hook: " in out[-1800:],',
                 "tests/test_e2e_codex_verdict.py::test_a_hook_line_early_in_long_output_still_counts",
                 id="e2e-residual-reads-the-tail"),
    # v4.0.0 review L4b L5: the profile-regex claim passed on the word "whole" alone.
    pytest.param("ADAPTING.md", "   A regex is a full match over the whole output stream",
                 "   A regex is a search over the whole output stream",
                 "tests/test_adapting_claims.py::test_the_profile_regex_rule_is_stated_the_way_the_tool_applies_it",
                 id="adapting-regex-claim-weak-disjunct"),
    # v4.0.0 review L4b L6: spike leftovers go to the Recycle Bin, inside the sandbox only, refusals stop the probe.
    pytest.param("scripts/layer2-spike/sandbox.py", "    target = assert_inside(path)\n    if recycler is None:",
                 "    target = resolve(path)\n    if recycler is None:",
                 "tests/test_layer2_spike_sandbox.py::"
                 "test_a_leftover_goes_to_the_injected_bin_and_never_outside_the_box",
                 id="l2-spike-recycle-outside-box"),
    pytest.param("scripts/layer2-spike/sandbox.py", '    if why:\n        raise SystemExit("left in place',
                 '    if False:\n        raise SystemExit("left in place',
                 "tests/test_layer2_spike_sandbox.py::test_a_leftover_the_bin_refuses_stays_and_stops_the_probe",
                 id="l2-spike-recycle-refusal-ignored"),
    # v4.0.0 review L4a L3: a DEGRADED hook-canary (exit 1) was accepted as a report.
    pytest.param("tools/verify-release.sh",
                 """  if { [ $rc -eq 0 ] || [ $rc -eq 2 ]; } && grep -q '^RESULT:' <<<"$out"; then""",
                 """  if [ $rc -le 2 ] && grep -q '^RESULT:' <<<"$out"; then""",
                 "tests/test_verify_release.py::test_a_degraded_hook_canary_is_red",
                 id="verify-accepts-degraded-canary"),
]

# Final gate followups: launcher fallback, isolated barrier evidence, and accurate setup output.
MUTATIONS += [
    pytest.param("tests/harness_pytest.py", "    log = Path(name)\n", "    log = Path(previous)\n    log.touch(exist_ok=True)\n", "tests/test_harness_tripwire_scope.py::test_overlapping_control_attempt_does_not_accuse_the_innocent_test[native]", id="review-tripwire-shared-log"),
    pytest.param("tests/harness_pytest.py", "    os.environ[\"HARNESS_TRIPWIRE_LOG\"] = str(log)\n", "    pass\n", "tests/test_harness_tripwire_scope.py::test_overlapping_control_attempt_does_not_accuse_the_innocent_test[shell]", id="review-tripwire-scope-env"),
    pytest.param("tests/harness_pytest.py", "            assert not attempts, \"PACKAGE-MANAGER TRIPWIRE: \" + repr(attempts)\n", "            assert True\n", "tests/test_harness_tripwire_scope.py::test_an_unmarked_tests_own_attempt_still_fails_and_keeps_its_evidence[native]", id="review-tripwire-own-attempt-refuses"),
    pytest.param("tests/harness_pytest.py", "        os.environ[\"HARNESS_TRIPWIRE_LOG\"] = previous\n", "        pass\n", "tests/test_harness_tripwire_scope.py::test_aborted_scope_restores_the_parent_log", id="review-tripwire-parent-log-restored"),
    # Review L7 MEDIUM / LOW: the write barrier's copy and truncate events, and every pip/npx spelling. One per clause;
    # the anchors judge the sibling harness_isolation.py by file spec, so the mutated copy is what they read.
    *[pytest.param("tests/harness_isolation.py", find, repl, "tests/test_harness_controls.py::" + test,
                   id="review-l7-" + name) for name, find, repl, test in [
        ("barrier-copyfile2", 'WRITE_EVENTS = {"_winapi.CopyFile2": 1, ', "WRITE_EVENTS = {",
         "test_barrier_refuses_a_copy_or_truncate_outside_the_fixture"),
        ("barrier-shutil-copyfile", '"shutil.copyfile": 1, "os.truncate": 0}', '"os.truncate": 0}',
         "test_barrier_refuses_a_copy_or_truncate_outside_the_fixture"),
        ("barrier-truncate", '"shutil.copyfile": 1, "os.truncate": 0}', '"shutil.copyfile": 1}',
         "test_barrier_refuses_a_copy_or_truncate_outside_the_fixture"),
        ("pip-attached-module", r'm = re.fullmatch(r"-[A-Za-z]*m(\S*)", t)', r'm = re.fullmatch(r"-m()", t)',
         "test_every_package_manager_spelling_trips"),
        ("pip-in-isolated-shell-string", "(no_site and pip_module(inner))", "False",
         "test_every_package_manager_spelling_trips"),
        ("pip-shell-string-needs-no-site", "(no_site and pip_module(inner))", "pip_module(inner)",
         "test_ordinary_commands_do_not_trip"),
        ("npx-cli", '("/npm-cli.js", "/npx-cli.js")', '("/npm-cli.js",)',
         "test_every_package_manager_spelling_trips"),
        ("npx-manager", "npm npx corepack", "npm corepack", "test_every_package_manager_spelling_trips"),
    ]],
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
     # ReSaver now permits a preserved small save edit, so that decoy no longer carries a competing refusal.
     _FXA + "test_a_never_shadow_refusal_stands_under_guards_off_beside_a_decoy[disable-after-decoy-plugin-write]",
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
    # A DACL without SE_DACL_AUTO_INHERITED is written as it is (SetNamedSecurityInfoW would add `AI`)...
    ("tools/skyguard/lock.py", "        if protected == now and not control & 0x400:", "        if False:",
     _FXI2 + "test_lock_and_unlock_keep_an_inheriting_dacl_without_the_auto_inherited_bit", "ci-dacl-keep-no-ai"),
    # ...with the protection it had: the as-is write must not turn an inheriting no-`AI` DACL protected either.
    ("tools/skyguard/lock.py", "adv.SetSecurityDescriptorControl(sd, 0x1000, 0x1000 if protected else 0)",
     "adv.SetSecurityDescriptorControl(sd, 0x1000, 0x1000)",
     _FXI2 + "test_lock_and_unlock_keep_an_inheriting_dacl_without_the_auto_inherited_bit", "ci-dacl-as-is-keeps-inheriting"),
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
     "tests/test_backup_filter_decision.py::test_state_filter_runs_before_backup_copy", "fixA-b1-skip-state"),
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
    # B-M2: an unreadable paths file never silently skips the save a command rewrites (now: never lets it run unkept)
    ("tools/skyguard/saves.py", '        n = paths.physical_abs(word, msys=(shell != "powershell"))',
     '        n = paths.norm_abs(word, msys=(shell != "powershell")) if roots is not None else None',
     _RCT + "test_an_absolute_save_is_kept_even_when_the_paths_file_is_unreadable", "p6fb-m2-absolute-save-needs-no-roots"),
    ("tools/skyguard/saves.py", "        if hit is None and roots is None and not n:", "        if False:",
     _RCT + "test_a_bare_save_name_with_an_unreadable_paths_file_is_logged_not_kept", "p6fb-m2-bare-name-not-new"),
    ("tools/skyguard/mcp_rules.py", '        return ("S-DEVBENCH-SAVE", {"op": f"Cannot tell if save {clean} exists."})',
     '        return ("", {})',
     _RCT + "test_a_devbench_save_with_an_unreadable_paths_file_is_logged", "p6fb-m2-devbench-unknown-refused"),
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
     # Snapshot save restores now use the neutral store; exercise this retained edit-backup API itself.
     "tests/test_backup_save_reserve.py::test_edit_backup_refuses_a_save_below_reserve[ess]", "p6fb-m6-save-reserve"),
    ("tools/skyguard/backup.py", '    if not str(file_path).lower().endswith((".ess", ".skse")):\n        return ""',
     '    if False:\n        return ""', _SCT + "test_the_save_reserve_applies_to_saves_only", "p6fb-m6-saves-only"),
    ("tools/skyguard/backup.py", "        short = _save_room(backups, file_path)", '        short = ""',
     "tests/test_backup_save_reserve.py::test_edit_backup_refuses_a_save_below_reserve[ess]", "p6fb-m6-wired-into-take"),
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
_P8P = "tools/modding_harness/preferences.py"
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
    ("setup.sh", '    *) DOC_ON="${DO_CODEX}${DO_PORTABLE}${DO_OPENCODE}" ;;', "    *) DOC_ON=1 ;;",
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
    ('tools/skyguard/portable.py', '                    (local_policy.restriction(project, deadline, lambda: save_plan[0]), *local_restrictions))', '                    tuple(local_restrictions))', 'tests/test_local_policy.py::test_registered_policy_denies_before_any_permanent_save_backup', 'registered-local-policy-before-preservation'),
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
    # [print-file], not [f-string]: on Python 3.12 an f-string tokenizes as FSTRING_* (PEP 701), never STRING, so
    # it is not masked even without the grammar check and that twin survived on the 3.12 leg (CI run 38178385999).
    # A keyword argument is outside the grammar on every version, and without the check its literal IS masked.
    pytest.param('tools/skyguard/self_rules.py', '    if any(type(node) not in permitted for node in nodes):\n', '    if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[print-file-posix]', id='review-inspection-ast-grammar'),
    pytest.param('tools/skyguard/self_rules.py', 'node.level == 0 and ', '', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[relative-parser-posix]', id='review-inspection-import-absolute'),
    pytest.param('tools/skyguard/self_rules.py', 'node.module == "skyguard" and ', '', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[wrongmodule-parser-posix]', id='review-inspection-import-module'),
    pytest.param('tools/skyguard/self_rules.py', ' and len(node.names) == 1', '', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[multi-parser-posix]', id='review-inspection-import-single-name'),
    pytest.param('tools/skyguard/self_rules.py', 'node.names[0].name == "shellparse" and ', '', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[wrongname-parser-posix]', id='review-inspection-import-parser-name'),
    pytest.param('tools/skyguard/self_rules.py', ' and node.names[0].asname is None', '', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[parser-import-alias-posix]', id='review-inspection-import-unaliased'),
    pytest.param('tools/skyguard/self_rules.py', '        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in {"print", "shellparse"}:\n', '        if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[comprehension-print-binding-posix]', id='review-inspection-protected-binding'),
    pytest.param('tools/skyguard/self_rules.py', '        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):\n', '        if False:\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[attribute-write-posix]', id='review-inspection-attribute-store'),
    pytest.param('tools/skyguard/self_rules.py', 'callee.id == "print"', 'True', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[unknown-call-posix]', id='review-inspection-call-print-name'),
    pytest.param('tools/skyguard/self_rules.py', 'callee.attr == "parse"', 'True', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[unknown-parser-method-posix]', id='review-inspection-call-parser-method'),
    pytest.param('tools/skyguard/self_rules.py', 'callee.value.id == "shellparse"', 'True', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[unknown-parser-namespace-posix]', id='review-inspection-call-parser-namespace'),
    pytest.param('tools/skyguard/self_rules.py', '                    or (imported and isinstance(callee, ast.Attribute) and callee.attr == "parse"\n', '                    or (isinstance(callee, ast.Attribute) and callee.attr == "parse"\n', 'tests/test_pricing_inspection.py::test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal[parser-without-import-posix]', id='review-inspection-parser-import-required'),
    pytest.param('tools/skyguard/self_rules.py', '    except (tokenize.TokenError, IndentationError, SyntaxError):\n        return code\n', '    except (tokenize.TokenError, IndentationError, SyntaxError):\n        return " " * len(code)\n', 'tests/test_pricing_inspection.py::test_inspection_parser_failure_preserves_original_state_refusal[tokenize]', id='review-inspection-token-fallback'),
    pytest.param('tools/skyguard/self_rules.py', '        if token.type in (tokenize.STRING, tokenize.COMMENT):\n', '        if token.type in (tokenize.COMMENT,):\n', 'tests/test_pricing_inspection.py::test_exact_historical_k18699_shell_quoting_is_inspection_data', id='review-inspection-string-mask'),
    pytest.param('tools/skyguard/self_rules.py', '        if token.type in (tokenize.STRING, tokenize.COMMENT):\n', '        if token.type in (tokenize.STRING,):\n', 'tests/test_pricing_inspection.py::test_inspection_comments_are_data_and_other_interpreters_stay_conservative[posix]', id='review-inspection-comment-mask'),
    pytest.param('tools/skyguard/self_rules.py', '        delete_api = grep(deletes.INTERP_DELETE, _python_inspection_text(e.program, code))\n', '        delete_api = grep(deletes.INTERP_DELETE, code)\n', 'tests/test_pricing_inspection.py::test_exact_historical_k18699_shell_quoting_is_inspection_data', id='review-inspection-trigger-integration'),
]

MUTATIONS += [pytest.param('tools/skyguard/self_rules.py', '    except (SyntaxError, ValueError, RecursionError):\n', '    except (SyntaxError, ValueError):\n', 'tests/test_pricing_inspection.py::test_inspection_parser_failure_preserves_original_state_refusal[ast-recursion]', id='review-inspection-ast-recursion-fallback')]

MUTATIONS += [
    pytest.param('tools/verify-release.sh', 'if "$PY" -B "$HERE/check-release-payload.py" "$(winpath "$ROOT")"', 'if "$PY" "$HERE/check-release-payload.py" "$(winpath "$ROOT")"', 'tests/test_verify_release.py::test_verification_does_not_create_identity_bearing_payload_bytecode', id='review-verifier-no-payload-bytecode'),
    pytest.param('tools/verify-release.sh', ' --allow-prefix "$(winpath "$PYDIR")"', '', 'tests/test_verify_release.py::test_scrubbed_verification_accepts_the_measured_venv_interpreter', id='review-verifier-measured-venv-directory'),
]


# Release review T13 (p8-rr-fix): I1 printed text that runs, I2 Recycle-Bin deletes through a variable.
# One twin per clause, each killed through the named test. Clauses the verdict tests cannot isolate (another route also
# refuses those commands) are witnessed by the unit tests named here. Not twinned, on purpose: the cost-only early
# return in _printed_state_delete (`if not code or ".skyguard" not in code.lower()`), whose removal changes no answer.
_RR = "tests/test_state_output_routes.py::"
_RRX = _RR + "test_printed_state_delete_that_runs_is_refused_and_never_shadowed[%s-relative]"
_RRI = _RR + "test_inspection_whose_output_runs_nowhere_stays_allowed[%s]"
_RRU = _RR + "test_output_is_not_inert_when_the_parser_cannot_vouch_for_the_command[%s]"
_RRR = _RR + "test_recycle_delete_of_the_state_through_a_variable_is_refused_and_never_shadowed[%s-relative]"
MUTATIONS += [pytest.param("tools/skyguard/self_rules.py", *m[:3], id=m[3]) for m in [
    # I1, the mask site: the original quoted scan runs whenever the output is not inert.
    # (Re-anchored in the re-review F3 round: `command` now reaches _output_inert.)
    ("    if delete_api and _output_inert(parsed, e, command):\n", "    if delete_api:\n",
     _RR + "test_the_original_quoted_scan_runs_when_the_output_is_not_inert", "rr-i1-mask-only-when-inert"),
    ("_state_delete(e, cwd, env, roots, parsed, command)\n", "_state_delete(e, cwd, env, roots, parsed, None)\n",
     "tests/test_pricing_inspection.py::test_exact_historical_k18699_shell_quoting_is_inspection_data",
     "rr-i1-scan-passes-the-parse"),
    # I1, _output_inert: each clause.
    ("    if parsed is None or not parsed.ok:\n", "    if not parsed.ok:\n", _RRU % "no-parse", "rr-i1-inert-needs-a-parse"),
    ("    if parsed is None or not parsed.ok:\n", "    if parsed is None:\n", _RRU % "unreadable",
     "rr-i1-inert-needs-a-readable-command"),
    ("    if parsed.codes or parsed.xargs or parsed.unread:\n", "    if parsed.xargs or parsed.unread:\n",
     _RRU % "shell-string", "rr-i1-inert-no-shell-string"),   # re-review F3's attribution clause also sees it
    ("    if parsed.codes or parsed.xargs or parsed.unread:\n", "    if parsed.codes or parsed.unread:\n",
     _RRU % "xargs", "rr-i1-inert-no-xargs"),
    ("    if parsed.codes or parsed.xargs or parsed.unread:\n", "    if parsed.codes or parsed.xargs:\n",
     _RRU % "decoded", "rr-i1-inert-no-decoded-text"),
    ("    if any(_STDOUT_REDIRECT.match(op)", "    if False and any(_STDOUT_REDIRECT.match(op)",
     _RR + "test_inspection_output_to_a_script_file_is_not_inert", "rr-i1-inert-no-script-redirect"),
    ("_STDOUT_REDIRECT.match(op) and ", "", _RRI % "stderr-to-script-name", "rr-i1-inert-redirect-is-stdout"),
    ("_SCRIPT_FILE.search(target", "bool(target) or _SCRIPT_FILE.search(target", _RRI % "redirect-text-file",
     "rr-i1-inert-redirect-target-is-a-script"),
    ("(x.program, x.segment) == (e.program, e.segment) or ", "", _RRI % "node-print-alone",
     "rr-i1-inert-skips-the-exec-itself"),
    (" or x.program in _INERT_PROGRAMS", "", _RRI % "pipe-head", "rr-i1-inert-read-only-programs"),
    (" or _is_inspection(x):", ":", _RRI % "two-inspections", "rr-i1-inert-other-inspections"),
    ("            continue\n        return False\n    return True\n", "            continue\n    return True\n",
     _RRX % "pipe-powershell-command-dash", "rr-i1-inert-anything-else-is-not"),
    # I1, _printed_state_delete: each clause.
    ("                   or _printed_state_delete(e, parsed, cwd, env, roots, command))",
     "                   or \"\")", _RRX % "node-pipe-powershell-dash", "rr-i1-printed-route-wired"),
    ("grep(deletes.INTERP_DELETE, code) or grep(deletes.DESTROYER_LOOSE, code) or parsed.xargs",
     "grep(deletes.DESTROYER_LOOSE, code) or parsed.xargs", _RRX % "node-pipe-powershell-dash",
     "rr-i1-printed-delete-api"),
    ("grep(deletes.INTERP_DELETE, code) or grep(deletes.DESTROYER_LOOSE, code) or parsed.xargs",
     "grep(deletes.INTERP_DELETE, code) or parsed.xargs", _RRX % "pipe-cat-bash", "rr-i1-printed-delete-word"),
    ("grep(deletes.INTERP_DELETE, code) or grep(deletes.DESTROYER_LOOSE, code) or parsed.xargs",
     "grep(deletes.INTERP_DELETE, code) or grep(deletes.DESTROYER_LOOSE, code)", _RRX % "pipe-xargs-rm",
     "rr-i1-printed-xargs"),
    ("    if _output_inert(parsed, e, command):\n        return \"\"\n", "    if False:\n        return \"\"\n",
     _RRI % "node-print-alone", "rr-i1-printed-only-when-it-may-run"),
    ("            if i != line_start and code[i - 1] not in _WORD_START:\n", "            if False:\n",
     _RR + "test_scratch_copies_printed_into_an_interpreter_are_not_the_live_state", "rr-i1-printed-word-boundary"),
    ("            if a is not None and _reaches_state(a, roots):\n", "            if False:\n",
     _RRX % "node-pipe-powershell-dash", "rr-i1-printed-resolved-state"),
    ("        if not placed:\n", "        if False:\n",
     _RR + "test_a_printed_state_path_no_resolver_can_place_counts", "rr-i1-printed-unplaceable"),
    # I2, _recycle_delete's fallback: each clause.
    ("    if placed:\n        return \"\"\n", "    if True:\n        return \"\"\n", _RRR % "python-env-var",
     "rr-i2-fallback-when-unplaced"),
    ("        placed = placed and a is not None and bool", "        placed = placed and bool", _RRR % "python-env-var",
     "rr-i2-unplaced-argument-noticed"),
    ("        a = _recycle_abs(w, how, base, env)\n        if a is not None and _reaches_state(a, roots):\n",
     "        a = _recycle_abs(w, how, base, env)\n        if False:\n", _RRR % "python-env-var",
     "rr-i2-fallback-resolved-state"),
    ("        if a is None and text_kind(w) == STATE:\n            return _hit_name(None, w)\n",
     "        if False:\n            return _hit_name(None, w)\n",
     _RR + "test_recycle_delete_through_a_variable_no_resolver_can_place_counts", "rr-i2-fallback-unresolved-word"),
    ("        out += [(n.group(1) if n.group(1) is not None else n.group(2)",
     "        out += [] and [(n.group(1) if n.group(1) is not None else n.group(2)",
     _RRR % "powershell-command-variable", "rr-i2-fallback-nested-quotes"),
    # Re-review F1: a format hole or an empty literal only looks placed.
    (" and bool(w.strip()) and not _FORMAT_HOLE", " and not _FORMAT_HOLE", _RRR % "node-concat",
     "rr-f1-empty-literal-is-unplaced"),
    (" and not _FORMAT_HOLE.search(w)\n", "\n", _RRR % "python-fstring", "rr-f1-format-hole-is-unplaced"),
    (r'_FORMAT_HOLE = re.compile(r"\{[^{}\n]{0,80}\}|', '_FORMAT_HOLE = re.compile(r"',
     _RRR % "python-format", "rr-f1-hole-braces"),
    # (Re-anchored in the final-review round: the percent hole grew width, precision and the `a` conversion.)
    (r'|%(?:\([^)\n]{0,80}\))?[-#0 +]*(?:\*|\d+)?(?:\.(?:\*|\d*))?[sra]")', '")', _RRR % "python-percent",
     "rr-f1-hole-percent"),
    # Final review: printf star width, precision (digits and star), and the `a` conversion.
    (r'[-#0 +]*(?:\*|\d+)?(?:\.', r'[-#0 +]*\d*(?:\.', _RRR % "python-percent-star", "fr-f1-hole-star-width"),
    (r'(?:\.(?:\*|\d*))?[sra]")', r'[sra]")', _RRR % "python-percent-precision", "fr-f1-hole-precision"),
    (r'(?:\.(?:\*|\d*))?[sra]")', r'(?:\.\d*)?[sra]")', _RRR % "python-percent-dot-star", "fr-f1-hole-star-precision"),
    (r'(?:\.(?:\*|\d*))?[sra]")', r'(?:\.(?:\*|\d*))?[sr]")', _RRR % "python-percent-ascii-precision",
     "fr-f1-hole-ascii-conversion"),
    (r'%(?:\([^)\n]{0,80}\))?[-#0 +]', r'%[-#0 +]', _RRR % "python-percent-named", "rr-f1-hole-percent-named"),
    # Re-review F3: the inert allowlist vouches only for steps the parser saw.
    ("    if command is None or _unattributed(command, parsed):\n", "    if command is None:\n",
     _RRX % "pwsh-file-procsubst", "rr-f3-inert-every-step-attributed"),
    ("    if command is None or _unattributed(command, parsed):\n", "    if _unattributed(command or \"\", parsed):\n",
     _RRU % "no-command", "rr-f3-inert-needs-the-command"),
    ("        rest = rest.replace(seg, \" \")\n", "        pass\n", _RRI % "pipe-head", "rr-f3-segments-attributed"),
    ("_printed_state_delete(e, parsed, cwd, env, roots, command))", "_printed_state_delete(e, parsed, cwd, env, roots, None))",
     _RRI % "node-print-alone", "rr-f3-printed-gets-the-command"),
]]

# Final review: shell structure is glue between parsed steps only where it is structure (_structure_only).
_RRS = _RR + "test_only_shell_structure_is_glue_between_parsed_steps[%s]"
_RRD = _RR + "test_structure_must_be_glue_in_every_dialect_the_command_was_read_as"
MUTATIONS += [pytest.param("tools/skyguard/self_rules.py", *m[:3], id=m[3]) for m in [
    ("    if keywords is None:\n        return _SEPARATORS_ONLY", "    if False:\n        return _SEPARATORS_ONLY",
     _RRS % "cmd-no-keywords", "fr-f3-cmd-separators-only"),
    ("            if not _GLUE_NAME.fullmatch(tok):\n                return False\n",
     "            if not _GLUE_NAME.fullmatch(tok):\n                pass\n", _RRS % "for-variable-is-a-name",
     "fr-f3-name-checked"),
    ("            want_name = False\n        elif case == 1:", "        elif case == 1:", _RRS % "for-list",
     "fr-f3-name-consumed"),
    ("            if not _GLUE_PLAIN.fullmatch(tok):\n                return False\n            case = 2\n",
     "            case = 2\n", _RRS % "case-subject-is-plain", "fr-f3-case-subject-plain"),
    ("            if tok != \"in\":\n                return False\n", "", _RRS % "case-in-follows-subject",
     "fr-f3-case-in-follows-subject"),
    ("            case, pattern = 3, True\n", "            case, pattern = 3, False\n", _RRS % "case-patterns",
     "fr-f3-case-body-opens-a-pattern"),
    ("        elif case == 3 and pattern and tok in (\"(\", \"|\"):\n            pass\n", "", _RRS % "case-patterns",
     "fr-f3-case-pattern-punctuation"),
    ("        elif case == 3 and pattern and tok == \")\":\n            pattern = False\n",
     "        elif case == 3 and pattern and tok == \")\":\n            pass\n", _RRS % "case-word-before-paren",
     "fr-f3-case-pattern-ends-at-paren"),
    ("        elif case == 3 and pattern and nxt in (\")\", \"|\"):\n", "        elif case == 3 and pattern:\n",
     _RRS % "case-pattern-is-one-word", "fr-f3-case-pattern-one-word"),
    ("            if not _GLUE_PATTERN.fullmatch(tok):\n                return False\n",
     "            if False:\n                return False\n", _RRS % "case-pattern-is-plain", "fr-f3-case-pattern-plain"),
    ("            loop_list = loop_list and tok not in (\";\", \"\\n\")\n", "            pass\n",
     _RRS % "for-list-ends-at-separator", "fr-f3-for-list-ends"),
    ("            pattern = case == 3 and tok == \";;\"\n", "            pattern = case == 3\n",
     _RRS % "case-pattern-after-double-semicolon-only", "fr-f3-case-pattern-after-double-semicolon"),
    ("        elif loop_list:\n            if not _GLUE_PLAIN.fullmatch(tok):\n                return False\n",
     "        elif loop_list:\n            pass\n", _RRS % "for-list-is-plain", "fr-f3-for-list-plain"),
    ("            if dialect == \"bash\":\n                want_name", "            if True:\n                want_name",
     _RRS % "ps-for-takes-no-bash-name", "fr-f3-loop-and-case-are-bash"),
    ("want_name = tok in (\"for\", \"select\", \"function\")", "want_name = False", _RRS % "for-list",
     "fr-f3-for-takes-a-name"),
    ("loop_list = tok == \"in\" and i >= 2 and toks[i - 2] in (\"for\", \"select\")", "loop_list = False",
     _RRS % "for-list", "fr-f3-for-in-opens-a-list"),
    (" and i >= 2 and toks[i - 2] in (\"for\", \"select\")", "", _RRS % "in-starts-a-list-only-after-for-name",
     "fr-f3-in-opens-a-list-only-after-for"),
    ("                if tok == \"case\":\n                    case = 1\n", "", _RRS % "case-patterns",
     "fr-f3-case-opens"),
    ("                if tok == \"esac\":\n                    case, pattern = 0, False\n", "",
     _RRS % "case-ends-at-esac", "fr-f3-esac-closes"),
    ("        elif dialect == \"bash\" and _BASH_ASSIGNMENT.fullmatch(tok):\n",
     "        elif _BASH_ASSIGNMENT.fullmatch(tok):\n", _RRS % "ps-no-bash-assignment", "fr-f3-assignment-is-bash"),
    ("        elif dialect == \"bash\" and _BASH_ASSIGNMENT.fullmatch(tok):\n", "        elif False:\n",
     _RRI % "assignment-then-inspect", "fr-f3-assignment-is-glue"),
    ("        elif dialect == \"powershell\" and _PS_VARIABLE.fullmatch(tok):\n",
     "        elif _PS_VARIABLE.fullmatch(tok):\n", _RRS % "bash-dollar-word", "fr-f3-shown-variable-is-powershell"),
    ("        elif dialect == \"powershell\" and _PS_VARIABLE.fullmatch(tok):\n", "        elif False:\n",
     _RRI % "ps-variable-shown", "fr-f3-shown-variable-is-glue"),
    ("        elif tok in keywords:\n", "        elif False:\n", _RRI % "if-then-inspect", "fr-f3-keywords-are-glue"),
    ("\"(\", \")\", \"{\", \"}\", \"\\n\"))", "\"(\", \")\", \"\\n\"))", _RRI % "group-pipe-head",
     "fr-f3-braces-are-glue"),
    ("in function select time !\".split()", "in function select time\".split()", _RRS % "bang", "fr-f3-bang-is-glue"),
    ("\"powershell\": frozenset(\"if elseif", "\"powershell\": frozenset(\"elseif", _RRI % "ps-if-block",
     "fr-f3-powershell-keywords"),
    ("    return not all(_structure_only(rest, d)", "    return not any(_structure_only(rest, d)", _RRD,
     "fr-f3-glue-in-every-dialect"),
    (" for d in {x.dialect for x in parsed.execs} or {\"\"})", " for d in {x.dialect for x in parsed.execs})", _RRD,
     "fr-f3-no-step-is-not-glue"),
]]

# Release review T13 M3 (the allowance floor in the leak checker) and M5 (the expected root from the artifact).
_RRF = "tests/test_release_payload.py::test_an_allow_prefix_at_or_above_the_home_or_a_root_is_never_applied[%s]"
_RRV = "tests/test_verify_release.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_CRP, '    if not p or not (p.startswith("/") or re.match(r"^[a-z]:/", p)):\n', "    if False:\n",
     _RRF % "relative", "rr-m3-floor-absolute-only"),
    (_CRP, '    if p == "/" or re.fullmatch(r"[a-z]:/", p):\n', "    if False:\n", _RRF % "other-drive-root",
     "rr-m3-floor-no-root"),
    (_CRP, "        if hf and hf == p:\n", "        if False:\n", _RRF % "home", "rr-m3-floor-not-the-home"),
    (_CRP, '        if hf and hf.startswith(p.rstrip("/") + "/"):\n', "        if False:\n", _RRF % "above-home",
     "rr-m3-floor-not-above-the-home"),
    (_CRP, 'os.environ.get("USERPROFILE"), ', "", _RRF % "home", "rr-m3-floor-windows-home"),
    (_CRP, ', os.environ.get("HOME")) if h]', ") if h]", _RRF % "posix-home", "rr-m3-floor-posix-home"),
    (_CRP, "    homes = [h for h in (*cfg_homes, ", "    homes = [h for h in (", _RRF % "home-argument",
     "rr-m3-floor-home-argument"),
    (_CRP, "    allow = kept\n", "    pass\n", _RRF % "home", "rr-m3-floor-applied"),
    (_CRP, '    if m:\n        s = m.group(1) + ":" + (m.group(2) or "/")\n',
     '    if False:\n        s = m.group(1) + ":" + (m.group(2) or "/")\n', _RRF % "msys-home", "rr-m3-fold-msys"),
    (_CRP, 'else s.rstrip("/")', "else s", _RRF % "home-trailing-slash", "rr-m3-fold-trailing-slash"),
    (_VR, 'RELEASE_SLUG=$(slug_of "$ROOT/package.json")', 'RELEASE_SLUG=$CHECKOUT_SLUG',
     _RRV + "test_the_expected_root_comes_from_the_artifact_and_another_product_is_named", "rr-m5-root-from-artifact"),
    (_VR, 'elif [ "$RELEASE_SLUG" != "$CHECKOUT_SLUG" ]; then', "elif false; then",
     _RRV + "test_the_expected_root_comes_from_the_artifact_and_another_product_is_named", "rr-m5-other-product-named"),
    (_VR, 'if [ -z "$RELEASE_SLUG" ]; then', "if false; then",
     _RRV + "test_an_artifact_without_a_valid_product_name_is_red[absent]", "rr-m5-product-name-required"),
    (_VR, ' or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", n) is None:', ":",
     _RRV + "test_an_artifact_without_a_valid_product_name_is_red[not-a-slug]", "rr-m5-product-name-is-a-slug"),
]]


# First native-Linux CI run (38178385999): the python-free PATH dropped /usr/bin, and cat with it, so every hook
# read an empty payload. One twin per fix; each names the test that must go red.
_PFP = "tests/test_python_free_path.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tests/conftest.py", "                _mirror_without_python(d, link_dir)\n",
     "                link_dir.mkdir(parents=True, exist_ok=True)\n",
     _PFP + "test_a_dropped_directory_keeps_its_other_commands", "linux-ci-python-free-path-mirrors"),
    ("agent/guards/adapters/claude/protect-files.sh",
     '    [ "${SKYGUARD_NO_SYSTEM_PYTHON:-}" = 1 ] || for d in /usr/bin/python3', "    for d in /usr/bin/python3",
     _PFP + "test_the_knob_skips_the_fixed_system_location", "linux-ci-no-system-python-knob"),
    ("tests/conftest.py", '                SKYGUARD_NO_SYSTEM_PYTHON="1", **extra)', "                **extra)",
     _PFP + "test_no_python_env_switches_the_system_locations_off", "linux-ci-no-python-env-sets-the-knob"),
    ("scripts/check_ci_leg.py", "    except OSError as e:\n", "    except ImportError as e:\n",
     "tests/test_ci_leg.py::test_a_missing_log_is_could_not_check_not_a_traceback", "linux-ci-leg-missing-log"),
    ("scripts/check_could_not_check.py", "    except OSError as e:\n", "    except ImportError as e:\n",
     "tests/test_could_not_check.py::test_a_missing_log_is_could_not_check_not_a_traceback",
     "linux-ci-could-not-check-missing-log"),
]]
# windows-latest CI (runs 38178385999, 38187755448): each twin killed by the named test.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # The leak grep allows the spelling the filesystem gives an allowed folder (8.3 short TEMP on the runner).
    ("tools/check-release-payload.py", "            out.append(real)\n", "            pass\n",
     "tests/test_release_payload.py::test_an_allowed_prefix_also_allows_the_spelling_the_filesystem_gives_it",
     "ci-allow-long-spelling"),
    # The p5a corpus rows about "the cwd's own drive" take the install's drive, not a hard-coded C:.
    ("scripts/guard-corpus/record.py", '.replace("<drive>", drive)', "",
     "tests/test_guard_corpus.py::test_payload_drive_template_is_the_installs_own_drive", "ci-corpus-drive-template"),
]]


# Second native-Linux CI run (38187755448): on a POSIX host a PowerShell/cmd/apply_patch `/x` named no drive root, so
# every operand under a POSIX install was unknown and allowed. One twin per clause of the fix.
_PHP = "tests/test_posix_host_paths.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/paths.py", "    if p.startswith(\"/\") and POSIX_HOST:\n", "    if False:\n",
     _PHP + "test_a_powershell_absolute_posix_path_resolves_on_a_posix_host", "linux-ci-posix-norm-abs"),
    ("tools/skyguard/targets.py", "    if paths.POSIX_HOST and (not cwd or cwd.startswith(\"/\")):\n",
     "    if False:\n", _PHP + "test_a_powershell_save_overwrite_is_seen_on_a_posix_host", "linux-ci-posix-drive-root"),
    ("tools/skyguard/targets.py", "    if paths.POSIX_HOST and (not cwd or cwd.startswith(\"/\")):\n",
     "    if paths.POSIX_HOST and cwd.startswith(\"/\"):\n",
     _PHP + "test_a_powershell_absolute_posix_path_resolves_on_a_posix_host", "linux-ci-posix-drive-root-no-cwd"),
    ("tools/skyguard/targets.py", "    if paths.POSIX_HOST and (not cwd or cwd.startswith(\"/\")):\n",
     "    if not cwd or cwd.startswith(\"/\"):\n",
     _PHP + "test_on_windows_a_slash_path_is_still_the_cwd_drives_root", "linux-ci-posix-drive-root-host-gated"),
]]


# Linux CI run 38187755448: twins that mutate a WINDOWS-ONLY branch of the product. Off Windows the mutated line
# never runs (or the defect it guards cannot occur), so the anchor passes and "not killed" would be a false alarm.
# Each stays fully enforced on Windows; where the POSIX branch has its own guard, it has its own twin below.
_WINDOWS_ONLY_TWINS = {
    "deadline-kill-by-windows-pid": "taskkill by /proc/<pid>/winpid runs only under MSYS/Cygwin; POSIX takes kill -KILL",
    "deadline-kill-is-not-waited-for": "taskkill by /proc/<pid>/winpid runs only under MSYS/Cygwin; POSIX takes kill -KILL",
    "l2-backup-manifest-lock": "POSIX O_APPEND writes are atomic; the lost-line race is the Windows CRT's "
                               "seek-then-write O_APPEND emulation",
    "engine-reads-declared-env-from-the-windows-root": "the wrappers set ROOT_WIN only under Git Bash (msys*, or cygwin* with the MSYS runtime); off "
                                                       "Windows it is empty and a.root is the only root",
    "t7-codex-render-only-for-codex":"Codex hooks are rendered on Windows only; off Windows nothing renders them anyway",
    "t7-says-codex-unconfigured": "the Codex NOT CONFIGURED line is the Windows branch; off Windows setup says "
                                  "Codex is Windows-only instead",
    # CI run 39145224156: the next two survived only on Linux; each is killed on Windows.
    "setup-omits-the-codex-subfolder-warning": "the Codex start-folder line is printed only where Codex is set up "
                                               "(Windows); its anchor test skips off Windows",
    "review-verifier-measured-venv-directory": "winpath is the identity off Windows, so dropping the converted "
                                               "--allow-prefix changes nothing there",
}
assert set(_WINDOWS_ONLY_TWINS) <= {p.id for p in MUTATIONS}, set(_WINDOWS_ONLY_TWINS) - {p.id for p in MUTATIONS}
MUTATIONS[:] = [
    pytest.param(*p.values, id=p.id, marks=tuple(p.marks) + (
        pytest.mark.skipif(sys.platform != "win32", reason=_WINDOWS_ONLY_TWINS[p.id]),))
    if p.id in _WINDOWS_ONLY_TWINS else p
    for p in MUTATIONS
]
# ... and the POSIX branch of the same kill: without it a hung engine outlives the wrapper off Windows.
MUTATIONS += [pytest.param(
    "agent/guards/adapters/claude/protect-files.sh", '        kill -KILL "$1" 2>/dev/null\n', "        :\n",
    "tests/test_hook_deadline.py::test_a_hung_engine_on_the_healthy_path_is_killed_by_its_pid",
    marks=pytest.mark.skipif(sys.platform == "win32", reason="the POSIX kill branch runs off Windows"),
    id="linux-ci-deadline-kill-posix")]
# The PowerShell fallback chain's probe budget (CI run 38201545601: a working Python refused, GUARD INERT).
MUTATIONS += [pytest.param(
    "agent/guards/adapters/codex/skyguard-entry.ps1", *m[:3],
    marks=pytest.mark.skipif(sys.platform != "win32", reason="the PowerShell entry is tested on Windows"), id=m[3])
    for m in [
        # the old per-probe cut: a healthy selftest slower than 3 s is refused
        ("$script:ProbeMs = [long]15000", "$script:ProbeMs = [long]3000",
         "tests/test_codex_entry.py::test_a_slow_but_healthy_fallback_interpreter_is_accepted[pwsh]",
         "ci-ps1-probe-budget-per-probe"),
        # the old 8 s for all probing: the second probe of one candidate runs out of time
        ("$script:ProbeEndMs = $script:T0ms + ([long]$limit - 10) * 1000", "$script:ProbeEndMs = $script:T0ms + 8000",
         "tests/test_codex_entry.py::test_a_slow_but_healthy_fallback_interpreter_is_accepted[pwsh]",
         "ci-ps1-probe-budget-total"),
        # the lookups go back to Get-Command (~24 s per absent name on windows-latest 5.1)
        ("        $found = Find-OnPath $cand[0]",
         "        $found = @(Get-Command -Name $cand[0] -CommandType Application -ErrorAction SilentlyContinue) |"
         " ForEach-Object { $_.Source } | Select-Object -First 1",
         "tests/test_codex_entry.py::test_the_fallback_chain_never_asks_get_command", "ci-ps1-lookup-no-get-command"),
        # the PATHEXT order inside a directory is lost: python.cmd (the slow test's only interpreter) is never found
        ("        foreach ($e in ([string]$env:PATHEXT).Split(';')) { if ($e) { $exts += $e } }",
         "        $exts = @('.EXE')",
         "tests/test_codex_entry.py::test_a_slow_but_healthy_fallback_interpreter_is_accepted[pwsh]",
         "ci-ps1-lookup-honours-pathext"),
    ]]
# Linux CI run 38201545601: the unknown dialect on a POSIX host places `/tmp/x` (outside) and `/x` in the install.
MUTATIONS += [pytest.param(
    "tools/skyguard/targets.py", "    if paths.POSIX_HOST and (not cwd or cwd.startswith(\"/\")):\n", "    if False:\n",
    "tests/test_skyguard_deletes.py::test_unknown_dialect_on_a_posix_host_places_slash_paths",
    id="linux-ci-posix-unknown-dialect")]
# P8-SED: S-SED-CRLF (deny) -- sed -i without -b rewrites every CRLF line to LF (Git for Windows' sed, MEASURED).
# One twin per clause of the condition; each names the test that must go red.
_SEDT = "tests/test_skyguard_shell.py::"
_SEDB = "tests/test_skyguard_writes.py::test_sed_binary["
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/writes.py", "        text_mode = bool(sed_files) and not sed_binary(e.argv)\n",
     "        text_mode = False\n", _SEDT + "test_sed_crlf_in_the_install_area_denies", "rule-S-SED-CRLF-anchor"),
    ("tools/skyguard/writes.py", "        text_mode = bool(sed_files) and not sed_binary(e.argv)\n",
     "        text_mode = bool(sed_files)\n", _SEDT + "test_sed_crlf_binary_mode_is_allowed", "rule-S-SED-CRLF-binary"),
    ("tools/skyguard/writes.py", "            if text_mode and w in sed_files and (kind or",
     "            if text_mode and (kind or", _SEDT + "test_sed_crlf_judges_only_the_files_sed_edits",
     "rule-S-SED-CRLF-sed-operands"),
    ("tools/skyguard/writes.py", "w in sed_files and (kind or in_zone):", "w in sed_files and (in_zone):",
     _SEDT + "test_sed_crlf_names_a_game_ini_wherever_it_resolves", "rule-S-SED-CRLF-game-file-by-name"),
    ("tools/skyguard/writes.py", "in_zone = targets.zone(a, roots) in SED_ZONES if a is not None else False",
     "in_zone = a is not None", _SEDT + "test_sed_crlf_outside_the_install_area_is_silent", "rule-S-SED-CRLF-zone"),
    ("tools/skyguard/writes.py", "in_zone = targets.zone(a, roots) in SED_ZONES if a is not None else False",
     "in_zone = False", _SEDT + "test_sed_crlf_in_the_install_area_denies", "rule-S-SED-CRLF-zone-any-file"),
    ("tools/skyguard/shell_rules.py", "    if wr.sed_crlf:  ", "    if False:  ",
     _SEDT + "test_sed_crlf_in_the_install_area_denies", "rule-S-SED-CRLF-wired"),
    ("tools/skyguard/writes.py", '        if w == "--binary":\n            return True\n', "",
     _SEDB + "binary-long]", "rule-S-SED-CRLF-binary-long"),
    ("tools/skyguard/writes.py", '                if c == "b":\n                    return True\n', "",
     _SEDB + "bi-cluster]", "rule-S-SED-CRLF-binary-short"),
    ("tools/skyguard/writes.py", "                if c in _SED_ATTACHED:\n                    break\n", "",
     _SEDT + "test_sed_crlf_backup_suffix_b_is_not_binary", "rule-S-SED-CRLF-attached-value"),
    ("tools/skyguard/writes.py", '        if w == "--":\n            return False\n', "",
     _SEDB + "dashdash]", "rule-S-SED-CRLF-end-of-options"),
    ("tools/skyguard/writes.py",
     '        if w in _SED_VALUED:\n            k += 2\n            continue\n        if w.startswith("-") and not w.startswith("--"):',
     '        if w.startswith("-") and not w.startswith("--"):', _SEDB + "f-valued]", "rule-S-SED-CRLF-option-value"),
    # -bi is in-place too: before P8-SED the write scan did not see it as a write at all
    ("tools/skyguard/writes.py", 'r"-[nrEsuzb]*i', 'r"-[nrEsuz]*i', _SEDT + "test_sed_crlf_binary_mode_is_allowed",
     "rule-S-SED-CRLF-bi-is-in-place"),
]]
# P8-SED item 2: messages-no-echo and messages-two-lines break a message INSIDE the engine, but their anchor fired all
# 1,258 corpus rows through the real wrapper (0.43 s a row, MEASURED: ~540 s; the twin timed out at 595 s against the
# 600 s cap). Retarget them to the in-process twin of the same contract check (~2 s). The wrapper-fired test stays in
# the suite; it only stops being the anchor. An appended override keeps the Task 13 block untouched; in place ([:]),
# because the parametrize decorator above already holds this list object.
_FAST_ECHO = "tests/test_rule_messages.py::test_no_engine_message_echoes_its_command"
_RETARGET = ("messages-no-echo", "messages-two-lines")
assert sum(p.id in _RETARGET for p in MUTATIONS) == len(_RETARGET)
MUTATIONS[:] = [pytest.param(*p.values[:3], _FAST_ECHO, marks=p.marks, id=p.id) if p.id in _RETARGET else p
                for p in MUTATIONS]
# P8-SED follow-up: every twin anchored on test_guard_corpus.py::test_intended_rows_reproduce, which fires all 982
# intended rows through the real wrapper (0.43 s a row). MEASURED before, serial, below-normal: four of the eight
# ran 457 s or hit a 580 s limit against the runner's 600 s cap; the others were killed early only because the
# wrapper and the engine disagreed on the first broken row. All eight break the engine path the wrapper runs
# (record.py's check, deletes/shellparse/shell_rules, run.py, engine.py), so they anchor on the in-process twin of
# the same check, which calls run.py's main() per row (~4 s). The wrapper-fired test stays in the suite.
# Appended in-place override ([:]): the decorator above holds this list object, and the original blocks stay as
# they are.
_FAST_IR = "tests/test_guard_corpus.py::test_intended_rows_reproduce_in_process"
_RETARGET_IR = ("corpus-intended-text-exact", "rule-S-DEL-UNKNOWN-DIR-whole-folder", "rule-S-SHELL-UNREAD-denies",
                "rule-S-SHELL-UNREAD-pipe-into-shell", "rule-W-INSTALL-wired", "rule-S-DEVBENCH-SAVE-roots",
                "merge-engine-drops-cwd", "merge-engine-write-drops-roots")
assert sorted(p.id for p in MUTATIONS if p.values[3] == "tests/test_guard_corpus.py::test_intended_rows_reproduce") \
    == sorted(_RETARGET_IR)
MUTATIONS[:] = [pytest.param(*p.values[:3], _FAST_IR, marks=p.marks, id=p.id) if p.id in _RETARGET_IR else p
                for p in MUTATIONS]

# fix-unclear triage (pre-tag): older review findings closed here, each with the twin that proves its test bites.
MUTATIONS += [
    # Plan 2 minor: `0 in (None, False)` is True in Python, so a numeric tool_name fell back to "unknown" where jq
    # (`// "unknown"`: null and false only) prints the number.
    pytest.param("tools/skyguard/adapters/claude.py", "if raw_name is None or raw_name is False else",
                 "if raw_name in (None, False) else",
                 "tests/test_skyguard_claude_adapter.py::test_tool_name_falls_back_only_where_jq_does[0-0]",
                 id="fixu-claude-tool-name-zero-is-not-unknown"),
    # Plan 2 minor: the `_SELF` twins covered protect-bash, backup and session-kb-guard but not the snapshot wrapper.
    pytest.param(".claude/hooks/snapshot-before-tool.sh",
                 '_SELF="${BASH_SOURCE[0]//' + BS * 2 + '//}"', '_SELF="${BASH_SOURCE[0]//' + BS + '//}"',
                 "tests/test_hooks.py::test_nonblocking_hooks_find_their_engine_however_launched[forward-slash]",
                 id="fixu-snapshot-wrapper-self-path-normalisation"),
    pytest.param(".claude/hooks/protect-files.sh",
                 '_SELF="${BASH_SOURCE[0]//' + BS * 2 + '//}"', '_SELF="${BASH_SOURCE[0]//' + BS + '//}"',
                 "tests/test_hooks.py::test_protect_files_works_however_it_is_launched[forward-slash]",
                 id="fixu-protect-files-wrapper-self-path-normalisation"),
]
# Plan 8 T6c review: ci.yml's `npm ci` installs and the FOMOD control step had no structural assertion, and the
# exact-pin test had no twin for "exact, but not the version the lock resolves".
_DL = "tests/test_dependency_locks.py::"
_NPM_CI = "        run: npm ci --ignore-scripts --no-audit --no-fund\n"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (".github/workflows/ci.yml", "      - name: Install Node dependencies for the FOMOD smoke\n" + _NPM_CI, "",
     _DL + "test_ci_installs_node_deps_with_npm_ci_before_the_tests_that_require_them", "fixu-ci-behavior-npm-ci-dropped"),
    (".github/workflows/ci.yml", "      - name: Install dependencies\n" + _NPM_CI, "",
     _DL + "test_ci_installs_node_deps_with_npm_ci_before_the_tests_that_require_them", "fixu-ci-node-npm-ci-dropped"),
    (".github/workflows/ci.yml", '          TOOLKIT_REQUIRE_HARNESS: "1"\n          TOOLKIT_REQUIRE_NODE_DEPS: "1"\n',
     '          TOOLKIT_REQUIRE_HARNESS: "1"\n',
     _DL + "test_ci_installs_node_deps_with_npm_ci_before_the_tests_that_require_them", "fixu-ci-suite-node-deps-not-required"),
    (".github/workflows/ci.yml", "fast-xml-parser@4.3.6", "fast-xml-parser@5.7.3",
     _DL + "test_ci_keeps_the_fomod_smoke_control_that_must_go_red", "fixu-ci-fomod-control-on-a-safe-version"),
    (".github/workflows/ci.yml", 'injectable fast-xml-parser 4.3.6: it cannot fail"; exit 1',
     'injectable fast-xml-parser 4.3.6: it cannot fail"',
     _DL + "test_ci_keeps_the_fomod_smoke_control_that_must_go_red", "fixu-ci-fomod-control-pass-not-fatal"),
    ("package.json", '"zod": "3.25.76"', '"zod": "3.25.75"',
     _DL + "test_package_json_pins_exact_versions_that_the_lock_resolves_to", "fixu-package-json-exact-but-not-the-lock"),
]]


def _unjudged_twin_message(returncode: int, stdout: str) -> str:
    """Plan 8 FINAL (T6 test infra): with PowerShell missing, the pwsh/powershell anchors SKIP, exit 0, and the gate
    reported each twin as "The test still PASSED -- it does not guard this bug": 24 false survivors. On Windows (where
    `_anchor_skipped_off_windows` excuses nothing) a twin whose anchor ran nothing but skips is still red, but it says
    what happened: NOT JUDGED, with the anchor's own skip reason. "" when the anchor ran anything."""
    reason = _anchor_skipped_off_windows(returncode, stdout, platform="any")
    if not reason:
        return ""
    return ("NOT JUDGED (not a survivor): the anchor test was SKIPPED, so this twin could not be killed or survive. "
            "Skip reason: " + reason + ". Provide what the anchor needs (e.g. pwsh / Windows PowerShell on PATH) "
            "and re-run.")


def test_a_skip_only_anchor_is_reported_unjudged_not_as_a_survivor():
    out = ("s                                                                        [100%]\n"
           "SKIPPED [1] tests/test_codex_entry.py:45: pwsh not installed (Windows only)\n1 skipped in 0.10s\n")
    msg = _unjudged_twin_message(0, out)
    assert msg.startswith("NOT JUDGED") and "pwsh not installed" in msg
    assert _unjudged_twin_message(0, ".\n1 passed in 0.1s\n") == ""           # a real survivor keeps its own message
    assert _unjudged_twin_message(1, "F\n1 failed in 0.1s\n") == ""           # a kill is a kill
    assert _unjudged_twin_message(5, "no tests ran in 0.1s\n") == ""          # a stale anchor keeps its own message


MUTATIONS.append(pytest.param(
    "tests/test_mutations.py", '    reason = _anchor_skipped_off_windows(returncode, stdout, platform=' + '"any")',   # split: unique
    '    reason = ""', "tests/test_mutations.py::test_a_skip_only_anchor_is_reported_unjudged_not_as_a_survivor",
    id="fixu-skip-only-anchor-reported-as-survivor"))
# Review L7 LOW: a mutant leg that only skips after a PASSED pristine leg is a survivor, on every platform.
MUTATIONS.append(pytest.param(
    "tests/test_mutations.py", '        assert not why, (' + '"SURVIVOR: the anchor test PASSED',   # split: unique
    '        assert True, (' + '"SURVIVOR: the anchor test PASSED',
    "tests/test_mutation_measurement.py::test_existing_platform_skip_is_handled_before_report_qualification[mutant]",
    id="mutant-only-skip-is-a-survivor"))

# fix-unclear 4b (v4 regression vs 3.x): a write target held in a same-command literal is judged as its literal,
# through the delete rules' Exec.subst. One twin per clause.
_W4B = "tests/test_skyguard_writes.py::"
_W4B_PAIR = _W4B + "test_a_same_command_literal_target_is_judged_as_its_literal["
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/shellparse.py",
     '    for w, val in list(zip(ws, vals)) + [(w, "".join(w.b)) for w in rec.redir_words]:',
     "    for w, val in zip(ws, vals):",
     _W4B + "test_the_parser_substitutes_redirect_targets_too", "fixu4b-parser-subst-skips-redirect-targets"),
    ("tools/skyguard/shellparse.py", "                cmd.redir_words.append(w)\n", "",
     _W4B + "test_the_parser_substitutes_redirect_targets_too", "fixu4b-parser-forgets-redirect-words"),
    ("tools/skyguard/deletes.py",
     "                      redirects=tuple((op, sub.get(w, w)) for op, w in e.redirects))   # fix-unclear 4b",
     "                      redirects=e.redirects)",
     _W4B_PAIR + "redirect-esp]", "fixu4b-substituted-leaves-redirects"),
    ("tools/skyguard/writes.py", "        e = substituted(e)\n        sed_files", "        sed_files",
     _W4B_PAIR + "cp-esp]", "fixu4b-writes-ignore-subst"),
    ("tools/skyguard/anchored.py",
     '        e = substituted(e)                      # fix-unclear 4b: `P=Out.psc; caprica -o "$P"` is that literal\n',
     "", _W4B_PAIR + "psc-out]", "fixu4b-psc-out-ignores-subst"),
    ("tools/skyguard/anchored.py",
     "        e = substituted(e)                      # fix-unclear 4b: a same-command literal operand is that literal\n",
     "", _W4B_PAIR + "champollion]", "fixu4b-champollion-ignores-subst"),
    ("tools/skyguard/anchored.py",
     '        e = substituted(e)                      # fix-unclear 4b: `R=<install>; git -C "$R" checkout -- .`\n',
     "", _W4B_PAIR + "git-discard]", "fixu4b-git-discard-ignores-subst"),
]]

# fix-unclear (find above a root): a name-filtered recursive search started above a protected root reaches its whole
# subtree (find -delete / -exec rm|mv, fd -x|-X, ForEach-Object's $_). One twin per clause.
_FA = "tests/test_skyguard_deletes.py::"
_FA_PAIR = _FA + "test_a_filtered_search_above_the_install_is_judged_as_its_filterless_control["
_FA_PARSE = _FA + "test_the_find_parse_keeps_the_start_beside_the_narrowed_name"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/shellparse.py",
     '        deep = not any(v[x] == "-maxdepth" and v[x + 1] in ("0", "1") for x in range(j, n - 1))',
     "        deep = False", _FA_PAIR + "find-name-delete]", "fixu-find-reach-never-deep"),
    ("tools/skyguard/shellparse.py", 'v[x + 1] in ("0", "1") for x in range(j, n - 1))',
     'False for x in range(j, n - 1))', _FA_PARSE, "fixu-find-maxdepth1-not-narrowed"),
    ("tools/skyguard/shellparse.py", "                ri, rq = reach(items, sq)\n", "                ri, rq = items, sq\n",
     _FA_PAIR + "find-name-delete]", "fixu-find-delete-ignores-reach"),
    ("tools/skyguard/shellparse.py", "if runs in deletes.DELETE_PROGRAMS | deletes.MOVE_PROGRAMS",
     "if False", _FA_PAIR + "find-exec-rm]", "fixu-find-exec-delete-ignores-reach"),
    ("tools/skyguard/shellparse.py", "if runs in deletes.DELETE_PROGRAMS | deletes.MOVE_PROGRAMS",
     "if runs in deletes.DELETE_PROGRAMS", _FA_PAIR + "find-exec-mv-away]", "fixu-find-exec-move-ignores-reach"),
    ("tools/skyguard/shellparse.py", '            if p in ("fd", "fdfind"):\n                return self._fd(k)\n', "",
     _FA_PAIR + "fd-exec-batch-rm]", "fixu-fd-not-parsed"),
    ("tools/skyguard/shellparse.py", "            if iv and not hit:", "            if False:",
     _FA_PAIR + "fd-exec-batch-rm]", "fixu-fd-no-placeholder-gets-no-paths"),
    ("tools/skyguard/shellparse.py", "                if v[x] in _FD_PLACEHOLDERS:", "                if False:",
     _FA_PAIR + "fd-exec-mv-away]", "fixu-fd-placeholder-not-replaced"),
    ("tools/skyguard/shellparse.py", "                sp.append(v[j + 1])\n", "",
     _FA_PAIR + "fd-search-path-rm]", "fixu-fd-search-path-dropped"),
    ("tools/skyguard/deletes.py", ' or (e.dialect == "powershell" and _PS_PIPE_ITEM.match(w))) \\', ") \\",
     _FA_PAIR + "ps-foreach-remove]", "fixu-ps-foreach-item-not-piped"),
    ("tools/skyguard/deletes.py", r'_PS_PIPE_ITEM = re.compile(r"\$(?:_|psitem)(?:\.\w+)?$", re.IGNORECASE)',
     r'_PS_PIPE_ITEM = re.compile(r"\$(?:_|psitem)$", re.IGNORECASE)',
     _FA_PAIR + "ps-foreach-psitem-fullname]", "fixu-ps-foreach-item-property-not-piped"),
    ("tools/skyguard/shellparse.py", 'if self.cd != "?" or _is_abs(s)]', "if True]",
     _FA + "test_a_filtered_search_that_cannot_reach_a_root_stays_allowed[find-after-unresolvable-cd]",   # fix-limits 14
     "fixu-find-reach-after-unknown-cd"),
    ("tools/skyguard/shellparse.py", 'if self.cd != "?" or _is_abs(s)]', 'if self.cd != "?"]',
     _FA_PAIR + "find-abs-start-after-unknown-cd]", "fixu-find-abs-start-after-unknown-cd"),
]]

# fix-limits 1: `guards off --reason TEXT` records the reason; a reason that is not one short plain line is refused
# before anything is written. One twin per clause of cli._reason_problem and of the CLI's use of it.
_FL1 = "tests/test_skyguard_shadow.py::"
_FL1_BAD = _FL1 + "test_guards_off_refuses_a_reason_that_is_not_one_short_line_and_writes_nothing["
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/cli.py", "    if not text.strip():\n        return", "    if False:\n        return",
     _FL1_BAD + "blank]", "fl1-reason-blank-accepted"),
    ("tools/skyguard/cli.py", "    if len(text) > REASON_MAX:", "    if False:",
     _FL1_BAD + "too-long]", "fl1-reason-length-unbounded"),
    ("tools/skyguard/cli.py", "    if any(ord(c) < 32 or 127", "    if any(False or 127",
     _FL1_BAD + "newline]", "fl1-reason-c0-controls-accepted"),
    ("tools/skyguard/cli.py", " or 127 <= ord(c) <= 159 or ", " or ",
     _FL1_BAD + "c1-nel]", "fl1-reason-c1-controls-accepted"),
    ("tools/skyguard/cli.py", "u2028" + BS + "u2029", "u2029",
     _FL1_BAD + "line-sep]", "fl1-reason-line-separator-accepted"),
    ("tools/skyguard/cli.py", BS + "u2028" + BS + "u2029\"", BS + "u2028\"",
     _FL1_BAD + "para-sep]", "fl1-reason-paragraph-separator-accepted"),
    ("tools/skyguard/cli.py", '"reason": a.reason.strip() if a.reason is not None else "guards off"',
     '"reason": "guards off"', _FL1 + "test_guards_off_records_the_reason_given_with_reason", "fl1-reason-not-recorded"),
    ("tools/skyguard/cli.py", 'problem = "--reason is taken only by `guards off`" if a.action != "off" else ',
     "problem = ", _FL1 + "test_a_reason_is_refused_for_on_and_status", "fl1-reason-accepted-by-on-and-status"),
]]

# fix-limits 2: a call shadow mode lets through is ONE audit record -- the rule lines are held while a guards lift is on
# record and folded into the SHADOW line; every other outcome writes them as they fired. One twin per clause.
_FL2 = "tests/test_skyguard_shadow.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/engine.py", "            audit.hold()\n", "            pass\n",
     _FL2 + "test_a_converted_call_writes_one_record_naming_the_rule_and_its_line", "fl2-never-held"),
    ("tools/skyguard/engine.py", '        if sf.is_file() and any(x["kind"] == "guards" for x in _state.read(str(sf)).lifts):',
     "        if True:", _FL2 + "test_without_a_guards_lift_on_record_nothing_is_held", "fl2-held-without-a-lift"),
    ("tools/skyguard/engine.py", "        refusals = take() if take is not None else []", "        refusals = []",
     _FL2 + "test_a_converted_call_writes_one_record_naming_the_rule_and_its_line", "fl2-not-folded"),
    ("tools/skyguard/engine.py", "            for h, lv, _d, m in refusals:                    # ...",
     "            for h, lv, _d, m in []:                    # ...",
     _FL2 + "test_a_record_that_cannot_be_written_leaves_the_refusal_and_its_rule_line", "fl2-failed-record-drops-lines"),
    ("tools/skyguard/engine.py", "    finally:\n        _release(audit)\n", "    finally:\n        pass\n",
     _FL2 + "test_held_lines_are_written_even_when_shadow_itself_fails", "fl2-failure-drops-held-lines"),
    ("tools/skyguard/engine.py", "            _release(audit)\n            audit.log_strict(\"skyguard\", \"SHADOW\", \"{} kept ({}; a lift rule)\"",
     "            audit.log_strict(\"skyguard\", \"SHADOW\", \"{} kept ({}; a lift rule)\"",
     _FL2 + "test_a_kept_refusal_writes_its_rule_line_verbatim_beside_the_kept_line", "fl2-kept-line-before-rule-line"),
    ('tools/skyguard/portable.py', '    engine.hold_if_shadowing(project, audit)\n    captured = []', '    captured = []',
     _FL2 + "test_the_codex_entry_writes_one_record_for_a_shadowed_call", "fl2-codex-entry-not-held"),
    ("tools/skyguard/run.py", "        engine.hold_if_shadowing(a.project, audit)\n", "        pass\n",
     _FL2 + "test_the_claude_entry_writes_one_record_for_a_shadowed_bash_call", "fl2-claude-entry-not-held"),
    ("tools/skyguard/audit.py", "            else:\n                self.log(hook, level, msg)\n        return refusals",
     "        return refusals", _FL2 + "test_a_converted_call_writes_one_record_naming_the_rule_and_its_line",
     "fl2-fold-drops-the-notes"),
    ("tools/skyguard/audit.py", "        if self._held is not None:\n            self._held.append",
     "        if False:\n            self._held.append",
     _FL2 + "test_a_converted_call_writes_one_record_naming_the_rule_and_its_line", "fl2-rule-never-holds"),
]]

# fix-limits 8: equal sequences on different invocation ids are concurrent calls, ordered by their stamps; the same id
# never clears its own failure. One twin per clause.
_FL8 = "tests/test_skyguard_health.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/health.py", '        if recs["evaluated"][1] == failed[1]:', "        if False:",
     _FL8 + "test_a_call_never_clears_its_own_failure_whatever_the_stamps_say", "fl8-same-id-may-clear-itself"),
    ("tools/skyguard/health.py", "        elif ds is not None and fs is not None and ds != fs:",
     "        elif ds is not None and fs is not None:",
     _FL8 + "test_a_concurrent_completion_after_a_failure_with_the_same_sequence_clears_it", "fl8-equal-sequence-never-clears"),
]]

# fix-limits 3: hook-canary / skydoctor read an adapted agent's heartbeats from what its adapter declares. One twin
# per clause.
_FL3H = "tests/test_skyguard_health.py::"
_FL3T = "tests/test_toy_agent.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/health.py", "    for name, folder in adapted_agents(root):                  # fix-limits 3",
     "    for name, folder in []:                  # fix-limits 3",
     _FL3T + "test_an_adapted_agent_that_never_ran_is_listed_not_exercised", "fl3-adapters-not-installed"),
    ("tools/skyguard/health.py", '    return {"evaluated": ADAPTED_EVALUATED} if agent.startswith(ADAPTED_DIR + "/") else None',
     "    return None", _FL3T + "test_the_health_report_certifies_an_adapted_agent_from_its_heartbeats[json-deny]",
     "fl3-engine-evaluation-not-read"),
    ("tools/skyguard/health.py", '"{}.{}".format((files or {}).get(stage, hook), stage)', '"{}.{}".format(hook, stage)',
     _FL3H + "test_an_adapted_agent_is_read_with_the_engines_evaluated_record", "fl3-hook-state-ignores-files"),
    ("tools/skyguard/health.py", '            out.append((name, re.sub(r"[^A-Za-z0-9._-]", "_", name)))',
     "            out.append((name, name))", _FL3H + "test_an_adapter_declares_its_agent_by_its_agent_name_slot",
     "fl3-name-not-sanitised-like-the-template"),
    ("tools/skyguard/health.py", '_AGENT_NAME.search(text.split("# SLOTS-END", 1)[0])', "_AGENT_NAME.search(text)",
     _FL3H + "test_an_adapter_without_a_readable_name_is_still_listed_under_its_folder", "fl3-name-read-outside-slots"),
    ("tools/skyguard/health.py", '            elif agent.endswith(" (no AGENT_NAME literal)"):', "            elif False:",
     _FL3H + "test_a_nameless_adapter_reads_unmeasurable_in_the_report", "fl3-nameless-not-unmeasurable"),
    ("tools/skyguard/health.py", "        if not unreadable and agent in COUNTING_HOOKS:", "        if not unreadable:",
     _FL3H + "test_an_adapted_agent_gets_no_transcript_coverage_even_with_session_files", "fl3-coverage-for-adapted"),
    ("tools/skyguard/doctor.py", '    rows += _guarded("Layer 3 adapted", _layer3_adapted, root, now)\n', "",
     _FL3T + "test_skydoctors_full_report_carries_the_adapted_rows", "fl3-doctor-omits-adapted"),
    ("tools/skyguard/doctor.py", '            st, detail = health.hook_state(hb / folder, h, now, files={"evaluated": health.ADAPTED_EVALUATED})',
     "            st, detail = health.hook_state(hb / folder, h, now)",
     _FL3T + "test_skydoctor_reports_the_adapted_agent", "fl3-doctor-ignores-engine-evaluation"),
]]

# fix-limits 7: a garbled `.last_snapshot` compares now (despite the window), is rewritten, and is said once.
_FL7 = "tests/test_skyguard_side_effects.py::test_a_garbled_rate_stamp_compares_now_rewrites_it_and_says_so_once[word]"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/snapshot.py", '            audit.log(HOOK, "RATE-STAMP", ', '            (lambda *a: None)(HOOK, "RATE-STAMP", ',
     _FL7, "fl7-garbled-stamp-silent"),
    ("tools/skyguard/snapshot.py", "            last, rl_ns = 0, 0\n", "            return None\n",
     _FL7, "fl7-garbled-stamp-skips-the-comparison"),
]]

# fix-limits 6: the devcontainer's version checks capture output before testing it, never `| grep -q` under pipefail.
_FL6 = "tests/test_dependency_locks.py::test_no_dockerfile_run_pipes_into_a_quiet_grep_under_pipefail"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (".devcontainer/Dockerfile", '    && java_version="$(/usr/local/jdk/bin/java -version 2>&1)" \\\n    && [[ "$java_version" == *"\\"$JDK_VERSION\\""* ]]',
     '    && /usr/local/jdk/bin/java -version 2>&1 | grep -q "\\"$JDK_VERSION\\""', _FL6, "fl6-jdk-check-pipes-into-grep-q"),
    (".devcontainer/Dockerfile", '    && sdks="$(/usr/local/dotnet/dotnet --list-sdks)" \\\n    && [[ $\'\\n\'"$sdks" == *$\'\\n\'"${DOTNET_SDK_VERSION} "* ]]',
     '    && /usr/local/dotnet/dotnet --list-sdks | grep -q "^${DOTNET_SDK_VERSION} "', _FL6, "fl6-sdk-check-pipes-into-grep-q"),
]]

# fix-limits 5: `resaver-cli.sh verify` checks ReSaver.jar and every lib/ file against the pinned SHA-256s.
_FL5 = "tests/test_resaver_cli_verify.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/resaver-cli.sh", '      [ "$kind" = required ] && problems+=("$rel is missing")', "      true",
     _FL5 + "test_a_missing_required_jar_is_named_and_a_missing_optional_one_is_not", "fl5-missing-required-jar-passes"),
    ("tools/resaver-cli.sh", '    [ "$got" = "$want" ] || problems+=', '    true || problems+=',
     _FL5 + "test_a_lib_jar_with_other_bytes_is_named", "fl5-hash-never-compared"),
    ("tools/resaver-cli.sh", "'$3 == r { f = 1 } END { exit !f }'", "'{ f = 1 } END { exit !f }'",
     _FL5 + "test_an_unknown_jar_on_the_classpath_is_named_but_another_file_is_not", "fl5-unknown-jar-accepted"),
    ("tools/resaver-cli.sh", "*.jar) ;; *) continue ;; esac", "*) ;; esac",
     _FL5 + "test_an_unknown_jar_on_the_classpath_is_named_but_another_file_is_not", "fl5-non-jar-files-flagged"),
]]

# fix-limits 4: CI runs the pinned Spriggit (serialize -> deserialize -> serialize) on a committed synthetic fixture.
_FL4 = "tests/test_spriggit_fixture.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (".github/workflows/ci.yml", '          diff -r "$work/yaml1" "$work/yaml2"\n', "          true\n",
     _FL4 + "test_the_dotnet_job_round_trips_the_fixture_after_restoring_spriggit", "fl4-ci-no-record-compare"),
    (".github/workflows/ci.yml", '          dotnet tool run spriggit -- deserialize --InputPath "$work/yaml1"',
     '          echo deserialize skipped --InputPath "$work/yaml1"',
     _FL4 + "test_the_dotnet_job_round_trips_the_fixture_after_restoring_spriggit", "fl4-ci-never-deserializes"),
    ("tests/fixtures/spriggit/make_fixture.py", "FORM_VERSION = 44", "FORM_VERSION = 43",
     _FL4 + "test_the_committed_fixture_is_what_its_generator_builds", "fl4-fixture-drifts-from-its-generator"),
]]

# fix-limits 11: a CrashLoggerSSE dump with no quoted exception type (1.24's __fastfail) parses, and is labelled.
_FL11 = "tests/test_triage.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/crash-triage.py", r"""r'^Unhandled exception(?: "(?P<type>[^"]+)"|(?=\s+at\s+0x))'""",
     r"""r'^Unhandled exception "(?P<type>[^"]+)"'""",
     _FL11 + "test_crash_parses_a_fastfail_dump_with_no_quoted_exception_type", "fl11-type-required-again"),
    ("tools/crash-triage.py", r"""|(?=\s+at\s+0x))'""", r"""|)'""",
     _FL11 + "test_a_line_that_only_starts_like_an_exception_is_not_one", "fl11-no-address-required-without-type"),
    ("tools/crash-triage.py", '        etype = ("__fastfail" if', '        etype = ("(unnamed exception code)" if',
     _FL11 + "test_crash_parses_a_fastfail_dump_with_no_quoted_exception_type", "fl11-fastfail-not-named"),
    ("tools/crash-triage.py", '                 else "(unnamed exception code)")', '                 else "__fastfail")',
     _FL11 + "test_an_unnamed_code_that_is_not_a_fastfail_is_parsed_and_labelled_unnamed", "fl11-every-unnamed-is-fastfail"),
]]

# fix-limits 12: `false`, `break` and `continue` are inert loop structure around an inspection. One twin per word.
_FL12 = "tests/test_state_output_routes.py::test_inspection_whose_output_runs_nowhere_stays_allowed["
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/self_rules.py", '    "false", "break", "continue",\n', '    "break", "continue",\n',
     _FL12 + "while-false-inspect]", "fl12-false-not-inert"),
    ("tools/skyguard/self_rules.py", '    "false", "break", "continue",\n', '    "false", "continue",\n',
     _FL12 + "break-in-loop-inspect]", "fl12-break-not-inert"),
    ("tools/skyguard/self_rules.py", '    "false", "break", "continue",\n', '    "false", "break",\n',
     _FL12 + "continue-in-loop-inspect]", "fl12-continue-not-inert"),
]]

# fix-limits 14: a cd through a same-command literal is followed; robocopy's /XF and /XD lists are exclusions.
_FL14P = "tests/test_shellparse.py::test_a_cd_through_a_same_command_literal_is_followed["
_FL14R = "tests/test_skyguard_writes.py::test_copy_targets["
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/shellparse.py", "            new = self.cd_word(cmd, args[0], args[0].bs)",
     "            new = _cd_value(args[0], args[0].bs)", _FL14P + 'bash-D=/a/b; cd "$D" && rm x-/a/b]', "fl14-bash-cd-not-resolved"),
    ("tools/skyguard/shellparse.py", "            new = self.cd_word(cmd, src[0], False) if src",
     "            new = _cd_value(src[0], False) if src",
     _FL14P + "powershell-$d = '/a/b'; Set-Location $d; Remove-Item x-/a/b]", "fl14-ps-set-location-not-resolved"),
    ("tools/skyguard/shellparse.py", " or self.unsure or self.ctx.poisoned() or self.ctx.evals:", " or self.unsure or self.ctx.evals:",
     _FL14P + 'bash-D=/a; source x.sh; cd "$D" && rm x-?]', "fl14-cd-resolved-in-a-poisoned-command"),
    ("tools/skyguard/shellparse.py", " or self.unsure or self.ctx.poisoned() or self.ctx.evals:", " or self.ctx.poisoned() or self.ctx.evals:",
     _FL14P + 'bash-D=/a; done; cd "$D" && rm x-?]', "fl14-cd-resolved-after-a-stray-closer"),
    ("tools/skyguard/deletes.py", '_ROBO_LISTS = frozenset(("XF", "XD"))', '_ROBO_LISTS = frozenset(("XD",))',
     _FL14R + "robocopy src dst /XF *.esp *.esm /E-cmd-want11]", "fl14-robocopy-xf-read-as-files"),
    ("tools/skyguard/deletes.py", '_ROBO_LISTS = frozenset(("XF", "XD"))', '_ROBO_LISTS = frozenset(("XF",))',
     _FL14R + "robocopy src dst /xd Data Scripts c.txt-cmd-want13]", "fl14-robocopy-xd-read-as-files"),
    ("tools/skyguard/deletes.py", '            excluding = w.lstrip("/")', '            excluding = excluding or w.lstrip("/")',
     _FL14R + "robocopy src dst /XF a.esp /IF c.txt-cmd-want14]", "fl14-robocopy-list-never-ends"),
]]

# fix-limits 9: a file named by interpreter code is resolved where the code runs and judged as a shell write there.
_FL9 = "tests/test_skyguard_writes.py::"
_FL9P = _FL9 + "test_an_interpreter_literal_write_is_judged_like_the_shell_write_to_the_same_place["
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/writes.py", '        base = cwd if e is None else (targets.exec_cwd(e, cwd, env) or "")',
     "        base = cwd", _FL9 + "test_an_interpreter_literal_write_follows_the_cd_before_it", "fl9-literal-ignores-the-cd"),
    ("tools/skyguard/writes.py", '        kind = file_class(word, a or "")\n        if kind == "plugin":\n            plugin.append(_name(word))\n        elif is_save(a, roots):',
     '        kind = file_class(word)\n        if kind == "plugin":\n            plugin.append(_name(word))\n        elif is_save(a, roots):',
     _FL9P + "python-Data/SKSE/Plugins/x.ini-C:/t7/Skyrim VR-echo x > Data/SKSE/Plugins/x.ini]", "fl9-class-by-name-only"),
    ("tools/skyguard/writes.py", '        elif a is not None and in_install_area(a, roots):\n            notes["S-WRITE-INSTALL"].append(_name(word))',
     '        elif False:\n            notes["S-WRITE-INSTALL"].append(_name(word))',
     _FL9P + "node-Data/notes.txt-C:/t7/Skyrim VR-echo x > Data/notes.txt]", "fl9-no-install-note"),
]]

# fix-limits sweep: rule-S-WRITE-INI-class was anchored on test_t08_row[t08-sed-i-skyrim-ini], a row the S-SED-CRLF
# batch moved to task P8-SED, so test_t08_row no longer collects it (pytest exit 4: the twin proved nothing). No T8
# row needs the INI class now, so the twin moves to the class test itself.
MUTATIONS.append(pytest.param(_TGT, '("ini", SKYRIM_INI, "S-WRITE-INI"), ', "",
                              "tests/test_skyguard_writes.py::test_file_class[SkyrimPrefs.ini-ini]", id="rule-S-WRITE-INI-class"))


# The save policy (user ruling 2026-10-13): saves.py decides every save change; the shared dispatcher (reached
# through portable.check) checks the user's typed approval (modding_harness/approval.py) and keeps the pairs. One twin
# per clause, each on a FAST in-process test (no wrapper replay) that drives that live path.
# No twin, said so: the fsync on a save copy (nothing short of a power loss can show it), the read-back after the final
# os.replace (needs the disk to change the bytes between two reads), and the `rest.decision in ("deny", "ask")` half of
# judge's early return (_with_note returns a deny/ask unchanged: equivalent).
_SV = "tools/skyguard/saves.py"
_AP = "tools/modding_harness/approval.py"
_SPT = "tests/test_save_policy.py::"
_SAT = "tests/test_save_approvals.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    # ---- counting: a pair is ONE save, only existing saves count, more than LIMIT (or a wildcard) needs approval
    (_SV, "LIMIT = 2 ", "LIMIT = 3 ", _SPT + "test_two_saves_are_allowed_and_three_need_approval", "rule-S-SAVE-APPROVAL-limit"),
    (_SV, "    return bool(c.patterns) or len(_existing(c)) > LIMIT", "    return len(_existing(c)) > LIMIT",
     _SPT + "test_a_wildcard_needs_approval_even_when_it_matches_one_save", "rule-S-SAVE-APPROVAL-wildcard"),
    (_SV, "    return bool(c.patterns) or len(_existing(c)) > LIMIT", "    return bool(c.patterns) or len(c.pairs) > LIMIT",
     _SPT + "test_a_new_save_does_not_count_toward_the_limit", "rule-S-SAVE-APPROVAL-existing-only"),
    (_SV, "    return os.path.normcase(os.path.normpath(folder or \".\")), stem.casefold()",
     "    return os.path.normcase(os.path.normpath(folder or \".\")), stem",
     _SPT + "test_one_save_spelled_in_two_cases_is_still_one_save", "rule-S-SAVE-BACKUP-pair-any-case"),
    (_SV, "        if low.endswith(ext):\n            return folder, name[:-len(ext)]", "        if False:\n            return folder, name",
     _SPT + "test_a_save_and_its_cosave_are_one_save", "rule-S-SAVE-BACKUP-pair"),
    (_SV, "    if change.unknown:\n        return refuse(*change.unknown[0])", "    if False:\n        return refuse(*change.unknown[0])",
     _SPT + "test_resaver_apply_on_a_save_it_cannot_place_is_refused[pattern]", "rule-S-RESAVER-APPLY-unplaced-refused"),
    (_SV, '        if not t.literal and WILD.search(os.path.splitdrive(folder)[1]):\n            unknown.append(name)', "        if False:\n            unknown.append(name)",
     _SPT + "test_a_wildcard_in_a_folder_name_cannot_be_listed", "rule-S-SAVE-UNKNOWN-folder"),
    (_SV, "        except OSError:\n            unknown.append(name)\n            continue", "        except OSError:\n            continue",
     _SPT + "test_a_wildcard_in_a_folder_that_cannot_be_read_cannot_be_listed", "rule-S-SAVE-UNKNOWN-unlistable"),
    (_SV, "    if c.unknown:\n        return refuse(", "    if False:\n        return refuse(",
     _SPT + "test_a_wildcard_in_a_folder_name_cannot_be_listed", "rule-S-SAVE-UNKNOWN-refused"),
    (_SV, "    if not ex or rest.decision in (\"deny\", \"ask\"):\n        return rest._replace(saves=plan)",
     "    if not ex or rest.decision in (\"deny\", \"ask\"):\n        return rest",
     _SPT + "test_a_save_change_beside_another_rules_deny_still_carries_its_plan", "rule-S-SAVE-BACKUP-plan-rides-a-deny"),
    ('tools/modding_harness/approval.py', '        grant.update(count=logical_count, command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest())',
     '        grant.update(count=logical_count)',
     _SPT + "test_a_wildcard_needs_approval_even_when_it_matches_one_save", "rule-S-SAVE-APPROVAL-pattern-line"),
    # ---- the hook (L2 MEDIUM-2: the LIVE shared dispatcher every hook reaches through portable.check, not the retired
    # saves.enforce): the backup is taken, verified, BEFORE the allow; a failure denies; bulk needs the typed approval
    ("tools/modding_harness/dispatch.py", "            transaction = target.backup(list(groups.values()), {",
     '            transaction = {"id": "none"} or target.backup(list(groups.values()), {',
     _SPT + "test_enforce_backs_up_both_files_verified_before_it_allows", "rule-S-SAVE-BACKUP-taken"),
    ("tools/modding_harness/dispatch.py",
     '            return Outcome(Result("deny", "failed", "failed", reason="preservation/policy failed: " + str(exc)))',
     '            return Outcome(Result("allow", "evaluated"))',
     _SPT + "test_a_backup_that_would_fill_the_drive_denies", "rule-S-SAVE-BACKUP-FAILED-denies"),
    ("tools/modding_harness/dispatch.py", "            if approval.needs_approval(len(groups), plan.wildcard):",
     "            if False:",
     _SPT + "test_with_no_approval_the_bulk_change_stays_refused_and_nothing_is_copied", "rule-S-SAVE-APPROVAL-enforced"),
    ("tools/modding_harness/dispatch.py", "            if not groups:\n                return Outcome(result)\n",
     "            if not groups or approval.needs_approval(len(groups), plan.wildcard):\n                return Outcome(result)\n",
     _SPT + "test_a_typed_approval_lifts_the_bulk_refusal_and_everything_is_backed_up", "rule-S-SAVE-APPROVAL-still-backed-up"),
    ("tools/modding_harness/approval.py", "self.count == count", "True",
     _SPT + "test_a_pattern_approval_needs_the_exact_pattern_and_count", "rule-S-SAVE-APPROVAL-pattern-grant"),
    ("tools/modding_harness/approval.py", " and self.operation == operation", "",
     "tests/test_save_approval_scope.py::test_canonical_grant_rejects_another_operation[move]",
     "rule-S-SAVE-APPROVAL-per-op"),
    (_SV, "    rest = fold([v.saves.rest if v.saves is not None else v for v in vs])\n    return judge(merge([p.change for p in plans]), rest)",
     "    rest = fold([v.saves.rest if v.saves is not None else v for v in vs])\n    return judge(plans[0].change, rest)",
     _SPT + "test_one_patch_that_changes_three_saves_is_judged_as_three", "rule-S-SAVE-APPROVAL-patch-together"),
    ("tools/skyguard/engine.py", "    together = saves.combine(vs, combine)\n", "    together = None\n",
     _SPT + "test_one_patch_that_changes_three_saves_is_judged_as_three", "save-engine-combine-wired"),
    ("tools/skyguard/engine.py", "        if is_save(a, roots):\n            return None                           # the save policy",
     "        if False:\n            return None                           # the save policy",
     _SPT + "test_a_patch_delete_of_one_save_is_backed_up_then_allowed_on_codex", "save-engine-patch-delete-is-a-save"),
    ("tools/skyguard/engine.py", 'op=SAVE_OP.get(op, "write"),\n', 'op="write",\n',
     _SPT + "test_a_patch_delete_or_move_is_judged_as_that_op", "save-engine-patch-op"),
    ("tools/skyguard/engine.py", "NEVER_SHADOW = NEVER_SHADOW | saves.NEVER_SHADOW", "NEVER_SHADOW = NEVER_SHADOW | frozenset()",
     _SPT + "test_guards_off_does_not_lift_a_bulk_save_change", "save-never-shadowed"),
    ("tools/skyguard/run.py", '        audit.rule(a.role, "S-SAVE-BACKUP-FAILED", "deny", reason)\n        return Verdict("deny", reason, code=',
     '        audit.rule(a.role, "S-SAVE-BACKUP-FAILED", "deny", reason)\n        return v or Verdict("deny", reason, code=',
     _SPT + "test_a_dispatcher_that_raises_inside_the_hook_denies", "rule-S-SAVE-BACKUP-FAILED-hook-raises"),
    ("tools/skyguard/neutral.py", '        return portable.legacy_pre_tool(req, project, roots_fn, audit, precomputed=v)',
     '        return v',
     _SPT + "test_the_neutral_mcp_route_backs_up_before_it_answers", "save-neutral-mcp-enforced"),
    # ---- where a save change is found: the delete, move and recycle routes no longer read it as S-DEL-CONFIG
    ("tools/skyguard/deletes.py", '                if is_save(b, roots):\n                    actual = targets.physical_operand(e, w, cwd, env)\n                    saves.append(actual if a == b and actual else "")',
     "                if False:\n                    saves.append(b)", _SPT + "test_one_save_is_allowed_with_a_backup_note",
     "rule-S-SAVE-BACKUP-delete"),
    ("tools/skyguard/deletes.py", "                if is_save(b, roots):\n                    continue",
     "                if False:\n                    continue", _SPT + "test_every_change_shape_is_one_backed_up_save[mv-out]",
     "rule-S-SAVE-BACKUP-move-out-is-no-delete"),
    ("tools/skyguard/deletes.py", "                if kind and is_save(lit, roots):", "                if False:",
     _SPT + "test_a_save_under_a_folder_with_glob_characters_in_its_name_is_still_one_save", "rule-S-SAVE-BACKUP-delete-glob"),
    ("tools/skyguard/deletes.py", '            if is_save(b, roots):\n                actual = targets.physical(w, cwd, "powershell", env, literal=how == "single")\n                saves.append(actual if a == b and actual else "")\n                continue',
     "            if False:\n                saves.append(b)\n                continue",
     _SPT + "test_every_change_shape_is_one_backed_up_save[recycle-bin]", "rule-S-SAVE-BACKUP-recycle"),
    ("tools/skyguard/writes.py", '            elif is_save(a, roots):\n                saves.append((targets.physical_operand(e, w, cwd, env) or "", "move"))',
     '            elif False:\n                saves.append((a, "move"))', _SPT + "test_every_change_shape_is_one_backed_up_save[mv-out]",
     "rule-S-SAVE-BACKUP-move"),
    # ---- recovery.backup_saves: both files, verified, recorded, one snapshot per pair, never pruned
    ('tools/modding_harness/durable.py', '    if count != before[2] or digest(target, deadline) != value:', '    if False:',
     _SPT + "test_a_copy_that_does_not_verify_denies", "rule-S-SAVE-BACKUP-FAILED-verify"),
    ('tools/modding_harness/durable.py', '    value = hashlib.sha256()', '    if Path(path).parent.name == "blobs": return Path(path).name\n    value = hashlib.sha256()',
     'tests/test_save_policy.py::test_a_damaged_reused_blob_refuses_and_is_retained_for_diagnosis', "save-backup-reverifies-a-stored-copy"),
    ('tools/modding_harness/preservation.py', '                    rec = {"path": path, "canonical": real, "missing": ident is None}', '                    rec = {"path": path, "canonical": real, "missing": False}',
     _SPT + "test_a_missing_cosave_is_recorded_as_missing_not_an_error", "save-backup-records-missing-cosave"),
    ('tools/modding_harness/preservation.py', '                records.append({"logical_save": group_index, "members": members})',
     '                if not records: records.append({"logical_save": group_index, "members": members})',
     'tests/test_save_policy.py::test_two_saves_are_one_transaction_with_two_complete_groups', "save-backup-one-snapshot-per-pair"),
    ('tools/modding_harness/preservation.py', '            durable.atomic_json(self.root / "transactions" / (transaction + ".json"), manifest)',
     '            pass', _SPT + "test_a_manifest_that_cannot_be_written_denies",
     "rule-S-SAVE-BACKUP-FAILED-manifest"),
    ('tools/modding_harness/preservation.py', '            with exclusive(self.root / ".lock", wait):',
     '            with __import__("contextlib").nullcontext():', _SPT + "test_a_busy_store_denies", "rule-S-SAVE-BACKUP-FAILED-lock"),
    ('tools/modding_harness/preservation.py', '            raise PreservationFailed(str(exc)) from exc\n        finally:',
     '            return None\n        finally:', _SPT + "test_an_unreadable_save_denies", "rule-S-SAVE-BACKUP-FAILED-unreadable"),
    ("tools/skyguard/snapshot.py", "            if backup._sha256(part) != sha:\n                raise OSError(",
     "            if False:\n                raise OSError(", _SPT + "test_a_restore_that_does_not_verify_at_its_target_writes_nothing",
     "save-restore-verifies-its-target"),
    ('tools/skyguard/save_store.py', '    for rec in (r for g in groups for r in g["members"]):\n        if rec["missing"]:', '    for rec in (r for g in groups for r in g["members"]):\n        if False:',
     _SPT + "test_a_restore_of_a_pair_without_its_cosave_says_so", "save-restore-names-a-missing-cosave"),
    # ---- modding_harness/approval.py, the LIVE reader (L2 MEDIUM-2; skyguard/approvals.py had no caller): only what the
    # USER typed. No twin, said so: read()'s own `host not in ("claude", "codex")` refusal is shadowed by human_texts'
    # identical one (an equivalent mutant), and kept as defence in depth.
    (_AP, '_LINE = re.compile(r"^[ \\t]*" + KEY', '_LINE = re.compile(r"[ \\t]*" + KEY',
     _SAT + 'test_an_approval_quoted_mid_line_grants_nothing', 'save-approval-line-anchored'),
    (_AP, 'row.get("type") == "user" and isinstance(origin, dict) and origin.get("kind") == "human"', 'row.get("type") == "user"',
     _SAT + 'test_a_user_line_that_was_not_typed_by_the_user_grants_nothing[no-origin]', 'save-approval-human-origin'),
    (_AP, '        if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):', '        if row.get("isSidechain") or row.get("isCompactSummary"):',
     _SAT + 'test_a_user_line_that_was_not_typed_by_the_user_grants_nothing[meta]', 'save-approval-not-meta'),
    (_AP, '        if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):', '        if row.get("isMeta") or row.get("isCompactSummary"):',
     _SAT + 'test_a_user_line_that_was_not_typed_by_the_user_grants_nothing[sidechain]', 'save-approval-not-sidechain'),
    (_AP, '        if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):', '        if row.get("isMeta") or row.get("isSidechain"):',
     _SAT + 'test_a_user_line_that_was_not_typed_by_the_user_grants_nothing[compact-summary]', 'save-approval-not-summary'),
    (_AP, '        if row.get("sessionId", session_id) != session_id:', '        if False:',
     _SAT + 'test_a_user_line_that_was_not_typed_by_the_user_grants_nothing[other-session-row]', 'save-approval-row-session'),
    (_AP, '                and row.get("promptSource") in (None, "typed", "queued")\n', '',
     _SAT + 'test_a_user_line_that_was_not_typed_by_the_user_grants_nothing[suggestion]', 'save-approval-typed-source'),
    (_AP, '    if isinstance(content, list) and all(isinstance(c, dict) and c.get("type") == "text"\n                                        and isinstance(c.get("text"), str) for c in content):\n        return "\\n".join(c["text"] for c in content)\n', '    if isinstance(content, list):\n        return "\\n".join(c.get("text", "") for c in content if isinstance(c, dict))\n',
     _SAT + 'test_a_tool_result_row_marked_human_still_grants_nothing', 'save-approval-no-tool-result'),
    (_AP, '            if (isinstance(att, dict) and att.get("type") == "queued_command"\n                    and att.get("commandMode") == "prompt" and isinstance(origin, dict)\n                    and origin.get("kind") == "human"):\n', '            if (isinstance(att, dict) and att.get("type") == "queued_command" and isinstance(origin, dict)\n                    and origin.get("kind") == "human"):\n',
     _SAT + 'test_a_queued_command_not_from_the_user_grants_nothing', 'save-approval-queued-prompt-only'),
    (_AP, '            if (isinstance(att, dict) and att.get("type") == "queued_command"\n                    and att.get("commandMode") == "prompt" and isinstance(origin, dict)\n                    and origin.get("kind") == "human"):\n', '            if (isinstance(att, dict) and att.get("type") == "queued_command"\n                    and att.get("commandMode") == "prompt" and True):\n',
     _SAT + 'test_a_queued_command_not_from_the_user_grants_nothing', 'save-approval-queued-human-only'),
    (_AP, 'and meta.get("originator") == "codex-tui"', '',
     _SAT + 'test_codex_exec_never_approves', 'save-approval-codex-tui-only'),
    (_AP, 'and meta.get("id") == session_id)', ')',
     _SAT + 'test_codex_another_sessions_rollout_never_approves', 'save-approval-codex-session'),
    (_AP, 'if isinstance(item, dict) and item.get("type") == "UserMessage":', 'if isinstance(item, dict):',
     _SAT + 'test_codex_context_and_agent_text_never_approve', 'save-approval-codex-user-item-only'),
    (_AP, 'data.get("v") != 1 or ', '',
     _SAT + 'test_a_malformed_approval_grants_nothing[version]', 'save-approval-version'),
    (_AP, 'data.get("session_id") != session_id\n', 'False\n',
     _SAT + 'test_a_malformed_approval_grants_nothing[other-session]', 'save-approval-grant-session'),
    (_AP, 'or data.get("op") not in ("write", "move", "delete")\n', 'or False\n',
     _SAT + 'test_a_malformed_approval_grants_nothing[unknown-op]', 'save-approval-known-op'),
    (_AP, 'or not all(isinstance(p, str) and os.path.isabs(p) for p in data["paths"])', '',
     _SAT + 'test_a_malformed_approval_grants_nothing[relative-path]', 'save-approval-absolute-paths'),
    (_AP, '                        if len(set(paths)) != len(paths):\n', '                        if False:\n',
     _SAT + 'test_a_malformed_approval_grants_nothing[duplicate-path]', 'save-approval-no-duplicate-paths'),
    ('tools/modding_harness/dispatch.py', '                grants, why = approval.read(transcript, action.host, action.session_id)', '                grants, why = approval.read(transcript, "claude", action.session_id)',
     _SPT + "test_codex_bulk_change_reads_only_the_tui_users_approval", "save-approval-reads-its-agents-transcript"),
]]

MUTATIONS += [pytest.param("tools/skyguard/save_store.py",
    '        if checked.result.decision != "allow":', '        if False:',
    "tests/test_legacy_save_store.py::test_recovery_format_in_place_obeys_local_restriction_before_current_preservation",
    id="legacy-recovery-cannot-bypass-local-restriction")]

MUTATIONS += [pytest.param("tools/skyguard/save_store.py", before, after,
    "tests/test_portable_profile.py::test_neutral_restore_carries_one_deadline_through_every_copy[" + case + "]",
    id="neutral-restore-total-deadline-" + case) for before, after, case in (
    ('        result = target.restore(ident, selected=selected, in_place=True, deadline=deadline)',
     '        result = target.restore(ident, selected=selected, in_place=True)', 'True'),
    ('    return target.restore(ident, to or None, selected, deadline=deadline)',
     '    return target.restore(ident, to or None, selected)', 'False'),
)]

MUTATIONS += [pytest.param(path, before, after,
    "tests/test_portable_profile.py::test_concrete_file_and_api_save_targets_are_literal_with_bracketed_paths[" + surface + "]",
    id="save-target-literal-provenance-" + surface) for path, before, after, surface in (
    ('tools/skyguard/write_rules.py', 'saves.Target(actual or "", op if op in saves.OPS else "write", True)',
     'saves.Target(actual or "", op if op in saves.OPS else "write")', 'files'),
    ('tools/skyguard/saves.py', 'Target(_join(folder, clean + EXTS[0]), "write", True)',
     'Target(_join(folder, clean + EXTS[0]), "write")', 'mcp'),
)]

MUTATIONS += [pytest.param("tools/skyguard/local_policy.py", before, after, test,
    id="local-policy-" + name) for before, after, test, name in (
    ('        if result["kind"] != "allow":', '        if False:',
     'tests/test_local_policy.py::test_legacy_preservation_requirements_cannot_disappear_in_unrecognized_helpers[backup]',
     'preservation-requirement-retained'),
    ('            result["warnings"] = tuple(warnings)', '            result["warnings"] = warnings',
     'tests/test_local_policy.py::test_registered_allowed_file_edit_preserves_pair_once[allow]',
     'worker-warning-tuple'),
    ('"__name__": "_registered_skyrim_local_policy"', '"__name__": "__main__"',
     'tests/test_local_policy.py::test_inspection_reports_requirements_without_authorizing_or_preserving[backup]',
     'inspection-never-runs-legacy-main'),
    ('                inspection and envelope["inspection"] is not True or', '                False or',
     'tests/test_local_policy.py::test_only_explicit_internal_inspection_mode_is_accepted_by_worker[False]',
     'inspection-mode-explicit'),
    ('    if registration(project) != registered:\n        raise ValueError("local policy registration changed during inspection")',
     '    if False:\n        raise ValueError("local policy registration changed during inspection")',
     'tests/test_local_policy.py::test_inspection_detects_policy_change_after_worker_returns',
     'inspection-source-recheck'),
    ('((sources, "move"), (destinations, "write"))', '((sources, "move"), (destinations, "move"))',
     'tests/test_local_policy.py::test_qualified_transfer_roles_preserve_move_source_and_written_destination',
     'move-destination-is-write'),
    ('            if (file_operation is None and result.get("op") in ("move", "rename") and\n                    hashlib.sha256(source).hexdigest() in QUALIFIED_POLICY_SHA256):',
     '            if (file_operation is None and result.get("op") in ("move", "rename") and\n                    True):',
     'tests/test_local_policy.py::test_unqualified_policy_cannot_relabel_its_own_transfer_requirements',
     'transfer-source-qualification'),
    ('    if registered is None or hashlib.sha256(registered[1]).hexdigest() not in QUALIFIED_POLICY_SHA256:\n        return plan\n    from modding_harness.dispatch import Mutation',
     '    if registered is None:\n        return plan\n    from modding_harness.dispatch import Mutation',
     'tests/test_save_profile.py::test_unqualified_policy_cannot_enable_additional_backup_membership',
     'backup-suffix-source-qualification'),
    ('        if registration(project) != registered:\n            raise ValueError("registered policy changed during save preservation")',
     '        if False:\n            raise ValueError("registered policy changed during save preservation")',
     'tests/test_save_profile.py::test_policy_change_after_backup_refuses_permission_and_keeps_verified_backup',
     'backup-suffix-late-source-change'),
    ('                   helper=plan.helper or helper, recheck=recheck)',
     '                   helper=plan.helper, recheck=recheck)',
     'tests/test_save_profile.py::test_bulk_backup_helper_requires_command_bound_human_scope',
     'backup-suffix-helper-command-binding'),
)]

MUTATIONS += [pytest.param("tools/skyguard/local_policy.py", before, after, test,
    id="local-policy-controlled-" + name) for before, after, test, name in (
    ('    return replace(plan, mutations=tuple(dict.fromkeys((*plan.mutations, *current()))), helper=True, recheck=recheck)',
     '    return plan',
     'tests/test_controlled_policy.py::test_qualified_controlled_scope_preserves_exact_pair_without_executing_controller[bash-prepare]',
     'scope-preserved'),
    ('        if (registration(project) != registered or _controlled_scope(action, project) != scope or',
     '        if (registration(project) != registered or False or',
     'tests/test_controlled_policy.py::test_controlled_dependencies_changed_after_backup_refuse_permission_and_retain_backup[request]',
     'request-recheck'),
    ('            if result.get("op") in ("controlled-prepare", "controlled-load") and hashlib.sha256(source).hexdigest() in QUALIFIED_POLICY_SHA256:',
     '            if result.get("op") in ("controlled-prepare", "controlled-load"):',
     'tests/test_controlled_policy.py::test_unqualified_policy_cannot_relabel_controlled_operation',
     'source-qualification'),
    ('    if bindings[0][1] != CONTROLLED_LOAD_SHA256:', '    if False:',
     'tests/test_controlled_policy.py::test_unqualified_controlled_requests_write_no_backup[controller]',
     'controller-identity'),
    ('            sorted(map(profiles.canonical, requirements[0].get("affected", ()))) != sorted(map(profiles.canonical, selected))):',
     '            tuple(map(profiles.canonical, requirements[0].get("affected", ()))) != tuple(map(profiles.canonical, selected))):',
     'tests/test_controlled_policy.py::test_qualified_controlled_scope_preserves_exact_pair_without_executing_controller[bash-prepare]',
     'requirement-order'),
    ('                              sorted(map(profiles.canonical, result.get("affected", ()))) == sorted(map(profiles.canonical, selected)))',
     '                              tuple(map(profiles.canonical, result.get("affected", ()))) == tuple(map(profiles.canonical, selected)))',
     'tests/test_controlled_policy.py::test_qualified_controlled_scope_preserves_exact_pair_without_executing_controller[powershell-prepare]',
     'worker-requirement-order'),
)]

MUTATIONS.append(pytest.param("tools/skyguard/save_profile.py", '        suffix = legacy_suffix(path)',
    '        suffix = None',
    'tests/test_save_profile.py::test_backup_suffix_stays_attached_to_both_logical_members[.bak]',
    id='legacy-backup-suffix-membership'))

MUTATIONS += [pytest.param("tools/skyguard/local_policy.py", before, after,
    "tests/test_local_policy.py::test_registered_policy_cannot_drop_malformed_or_contradictory_backup_counts[" + case + "]",
    id="local-policy-backup-count-" + name) for before, after, case, name in (
    ('    if "count" in decision and (type(count) is not int or count < 0 or\n                                kind == "backup" and count > 2):',
     '    if False:', '3', 'contradictory-threshold'),
    ('    if "count" in decision and (type(count) is not int or count < 0 or',
     '    if "count" in decision and (not isinstance(count, int) or count < 0 or', 'True', 'boolean'),
)]

MUTATIONS += [pytest.param("tools/skyguard/ordered_upgrade.py", before, after, test,
    id="ordered-policy-" + name) for before, after, test, name in (
    ('    if rollback[local_policy.LEGACY] not in local_policy.QUALIFIED_POLICY_SHA256:', '    if False:',
     'tests/test_ordered_policy.py::test_unqualified_source_groups_or_partial_runtime_cannot_produce_a_migration[original-policy]',
     'source-qualification'),
    ('    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != WRAPPER_NORMALIZED_SHA256:', '    if False:',
     'tests/test_ordered_policy.py::test_unqualified_source_groups_or_partial_runtime_cannot_produce_a_migration[original-wrapper]',
     'wrapper-qualification'),
    ('        if any(upgrade.json_bytes(group) not in keys for group in merged.get("hooks", {}).get("PreToolUse", [])):',
     '        if False:',
     'tests/test_ordered_policy.py::test_unqualified_source_groups_or_partial_runtime_cannot_produce_a_migration[original-windows-command]',
     'complete-native-group'),
    ('    if not required <= candidate.keys():', '    if False:',
     'tests/test_ordered_policy.py::test_unqualified_source_groups_or_partial_runtime_cannot_produce_a_migration[original-missing-runtime]',
     'complete-runtime'),
    ('    if review["observed_local_files"].get(local_policy.CONFIG) is not None and upgrade.json_bytes(existing) != upgrade.json_bytes(registration):',
     '    if existing and upgrade.json_bytes(existing) != upgrade.json_bytes(registration):',
     'tests/test_ordered_policy.py::test_unqualified_source_groups_or_partial_runtime_cannot_produce_a_migration[original-empty-registration]',
     'existing-empty-registration'),
)]

MUTATIONS += [pytest.param("tools/skyguard/local_policy.py", before, after, test,
    id="local-policy-finite-" + name) for before, after, test, name in (
    ('    return replace(plan, mutations=mutations, wildcard=wildcard, helper=True, recheck=recheck)',
     '    return plan',
     'tests/test_finite_policy.py::test_finite_legacy_scope_preserves_all_required_members_once[restore]',
     'requirements-retained'),
    ('                    requirement["roles"] = [list(role) for role in _transfer_roles(namespace, payload)]',
     '                    requirement["roles"] = []',
     'tests/test_finite_policy.py::test_finite_legacy_scope_preserves_all_required_members_once[suffix-move]',
     'qualified-transfer-roles'),
    ('        if registration(project) != registered or inspect_policy(action, project, deadline) != report:',
     '        if False:',
     'tests/test_finite_policy.py::test_finite_requirement_change_after_backup_refuses_and_retains_original_bytes',
     'requirement-recheck'),
    ('    if registered is None or hashlib.sha256(registered[1]).hexdigest() not in QUALIFIED_POLICY_SHA256:\n        return plan\n    report = inspect_policy(action, project, deadline)\n    requirements = report["requirements"]\n    if report["refusal"] is not None or len(requirements) != 1:',
     '    if registered is None:\n        return plan\n    report = inspect_policy(action, project, deadline)\n    requirements = report["requirements"]\n    if report["refusal"] is not None or len(requirements) != 1:',
     'tests/test_finite_policy.py::test_unqualified_policy_cannot_add_finite_shell_scope',
     'source-qualification'),
)]

MUTATIONS += [pytest.param("tools/skyguard/local_policy.py", before, after, test,
    id="local-policy-declared-" + name) for before, after, test, name in (
    ('            if not groups or not grant.matches(action.session_id, operation, complete, action.command, True, len(groups)):',
     '            if not groups or not grant.matches(action.session_id, operation, complete, action.command, False, len(groups)):',
     'tests/test_declared_scope.py::test_unbound_grant_cannot_create_a_declared_plan',
     'command-binding'),
    ('                _declared_choice(action, transcript, operation, profile) != choice):',
     '                False):',
     'tests/test_declared_scope.py::test_declared_helper_rechecks_human_evidence_after_preserving_bytes',
     'human-recheck'),
    ('                                declared=hashlib.sha256(source).hexdigest() in QUALIFIED_POLICY_SHA256)',
     '                                declared=True)',
     'tests/test_declared_scope.py::test_unqualified_policy_cannot_use_a_declared_game_plan',
     'source-qualification'),
    ('    return next(iter(choices.values())) if len(choices) == 1 else None',
     '    return next(iter(choices.values())) if choices else None',
     'tests/test_declared_scope.py::test_different_complete_declarations_for_the_same_opaque_command_are_ambiguous',
     'ambiguous-declarations'),
)]

MUTATIONS += [pytest.param("tools/skyguard/legacy_saves.py", before, after, test,
    id="legacy-backup-suffix-reader-" + name) for before, after, test, name in (
    ('            or save_profile.member_kind(originals[0]) != "ess"',
     '            or Path(originals[0]).suffix.lower() != ".ess"',
     'tests/test_legacy_save_store.py::test_save_backups_format_retains_backup_suffix_and_complete_pair_authority[True]',
     'save-backups'),
    ('            if (not path.is_absolute() or save_profile.member_kind(path) is None or',
     '            if (not path.is_absolute() or path.suffix.lower() not in (".ess", ".skse") or',
     'tests/test_legacy_save_store.py::test_recovery_writer_backup_suffix_snapshot_can_be_imported[False]',
     'recovery'),
    ('            or canonical(save_profile.literal_members(originals[0])[1]) != canonical(originals[1])):',
     '            or False):',
     'tests/test_legacy_save_store.py::test_legacy_backup_suffix_cannot_claim_a_different_pair[A.skse.other]',
     'same-logical-pair'),
)]

# Sample retirement must prove ownership; matching filenames alone are not permission to replace.
MUTATIONS += [
    pytest.param('scripts/guard-corpus/price.py', '                    files[key] = (out_dir / key).open("x", encoding="utf-8", newline="\\n")', '                    files[key] = (out_dir / key).open("w", encoding="utf-8", newline="\\n")', 'tests/test_price_tool.py::test_unowned_sample_collision_is_preserved', id='review-price-unowned-sample-clobber'),
    pytest.param('scripts/guard-corpus/price.py', '        if _sample_stamp(source) != expected:', '        if False:', 'tests/test_price_tool.py::test_modified_owned_samples_are_preserved_and_cannot_be_overwritten', id='review-price-modified-owned-retired'),
    pytest.param('scripts/guard-corpus/price.py', '    _retire_samples(out_dir, owned)', '    for direction in DIRECTIONS:\n        for stale in out_dir.glob(f"*__{direction}.jsonl"):\n            stale.unlink()', 'tests/test_price_tool.py::test_only_this_tools_stale_files_are_cleared', id='review-price-unowned-glob-cleanup'),
]

MUTATIONS += [pytest.param("scripts/thirdparty-audit.py", '                    if urllib.parse.unquote(fields["url"]) != urllib.parse.unquote(download):', "                    if False:", "tests/test_thirdparty_audit.py::test_addon_download_url_must_belong_to_the_release_whose_checksum_is_published", id="review-published-hash-wrong-release-url")]

# Resumable results are evidence for one exact instrument, never only an item index.
MUTATIONS += [
    pytest.param('scripts/guard-corpus/replay.py', '"modding_harness": _tree_identity(rec.REPO / "tools/modding_harness")', '"modding_harness": {}', 'tests/test_replay_identity.py::test_changed_inputs_refuse_cached_rows_without_overwriting_evidence[runtime]', id='review-replay-identity-core'),
    pytest.param('scripts/guard-corpus/replay.py', '"skyguard": _tree_identity(rec.REPO / "tools/skyguard")', '"skyguard": {}', 'tests/test_replay_identity.py::test_changed_inputs_refuse_cached_rows_without_overwriting_evidence[helper]', id='review-replay-identity-engine'),
    pytest.param('scripts/guard-corpus/replay.py', '_digest(Path(rec.__file__).read_bytes())', '"unbound"', 'tests/test_replay_identity.py::test_changed_inputs_refuse_cached_rows_without_overwriting_evidence[record]', id='review-replay-identity-record'),
    pytest.param('scripts/guard-corpus/replay.py', '"items_sha256": _digest(frozen)', '"items_sha256": "unbound"', 'tests/test_replay_identity.py::test_changed_inputs_refuse_cached_rows_without_overwriting_evidence[items]', id='review-replay-identity-items'),
    pytest.param('scripts/guard-corpus/replay.py', '_digest(json.dumps(inherited, sort_keys=True).encode("utf-8"))', '"unbound"', 'tests/test_replay_identity.py::test_environment_binding_keeps_secret_values_out_of_the_identity', id='review-replay-identity-environment'),
    pytest.param('scripts/guard-corpus/replay.py', '    return 2 if summary["errors"] else 0 if agree == len(items) else 1', '    return 0 if agree == len(items) else 1', 'tests/test_replay_identity.py::test_identical_engine_errors_are_nonanswers_and_are_retried', id='review-replay-identity-errors'),
]

MUTATIONS += [pytest.param("scripts/guard-corpus/replay.py", '        out[relative + "/"] = {"kind": "directory", "mode": before.st_mode, "mtime_ns": before.st_mtime_ns}', "        pass", "tests/test_replay_identity.py::test_empty_runtime_directory_changes_require_new_evidence", id="review-replay-empty-directory-identity")]

# A transport success is not a verdict until complete, nonce-bound validation succeeds.
MUTATIONS += [pytest.param("agent/guards/adapters/claude/" + hook, before, after,
    "tests/test_python_fallback.py::" + test, id="review-claude-output-" + name + "-" + hook)
    for hook in ("protect-bash.sh", "protect-files.sh", "protect-mcp.sh")
    for before, after, test, name in (
        ('if [ "$RC" -eq 0 ] && [ -z "$NUL" ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
         'if true; then', 'test_blocking_wrapper_rejects_incomplete_or_wrong_schema_engine_output', 'schema'),
        ('[ -z "$NUL" ] || inert emission "engine output contained a NUL byte -- run: bash tools/hook-canary.sh" "engine output contained a NUL byte"',
         ': # incorrectly discard the engine NUL flag',
         'test_blocking_wrapper_rejects_incomplete_or_wrong_schema_engine_output', 'engine-nul'),
        ('if [ "$RC" -eq 0 ] && [ -z "$NUL" ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
         'if [ -z "$NUL" ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
         'test_output_validator_fault_is_an_on_time_emission_refusal[nonzero-' + hook + ']', 'exit-code'),
        ('if [ "$RC" -eq 0 ] && [ -z "$NUL" ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
         'if [ "$RC" -eq 0 ] && [ -z "$NUL" ]; then',
         'test_output_validator_fault_is_an_on_time_emission_refusal[wrong-nonce-' + hook + ']', 'nonce'),
        ('if [ "$RC" -eq 0 ] && [ -z "$NUL" ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
         'if [ "$RC" -eq 0 ] && [ "$OUT" = "skyguard-claude-output:$NONCE" ]; then',
         'test_output_validator_fault_is_an_on_time_emission_refusal[nul-' + hook + ']', 'validator-nul'),
    )]

# Comparison aliases must never choose a different physical save's backup bytes.
MUTATIONS += [pytest.param(path, before, after, "tests/test_save_physical_paths.py::" + test,
    id="save-physical-" + name) for path, before, after, test, name in (
    ("tools/skyguard/paths.py", '    return p.replace("/", "\\\\")', '    return norm_abs(path, msys=msys)',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[files-.]', 'namespace-io'),
    ("tools/skyguard/targets.py", '        return paths.physical_abs(w)', '        return compared',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[files-.]', 'explicit-operand'),
    ("tools/skyguard/targets.py", '            return paths.physical_abs(paths._join(base, s))', '            return compared',
     'test_relative_file_and_shell_keep_raw_namespace_cwd[files]', 'relative-cwd'),
    ("tools/skyguard/targets.py", '    return physical(e.cd_before, cwd, e.dialect, env)',
     '    return resolve(e.cd_before, cwd, e.dialect, env)',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[cd-.]', 'changed-cwd'),
    ('tools/skyguard/write_rules.py', '            actual = targets.physical(file_path, cwd, "cmd", env_, literal=True)',
     '            actual = a', 'test_raw_namespace_preserves_requested_pair_not_folded_peer[files-.]', 'file-route'),
    ("tools/skyguard/writes.py", '                saves.append((targets.physical_operand(e, w, cwd, env) or "", "write"))',
     '                saves.append((a, "write"))', 'test_raw_namespace_preserves_requested_pair_not_folded_peer[write-.]', 'shell-write'),
    ("tools/skyguard/deletes.py", '                    saves.append(actual if a == b and actual else "")', '                    saves.append(b)',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[delete-.]', 'shell-delete'),
    ("tools/skyguard/writes.py", '                saves.append((targets.physical_operand(e, w, cwd, env) or "", "move"))',
     '                saves.append((a, "move"))', 'test_raw_namespace_preserves_requested_pair_not_folded_peer[move-.]', 'shell-move'),
    ("tools/skyguard/writes.py", '                saves.append((targets.physical_operand(e, dst, cwd, env) or "", "write"))',
     '                saves.append((a_dst, "write"))', 'test_raw_namespace_preserves_requested_pair_not_folded_peer[copy-.]', 'shell-copy'),
    ("tools/skyguard/writes.py", '                    physical_dst = targets.physical_operand(e, dst, cwd, env)', '                    physical_dst = a_dst',
     'test_copy_to_raw_folder_preserves_destination_pair', 'folder-copy'),
    ("tools/skyguard/writes.py", '            saves.append((targets.physical(word, actual_base, "cmd", env, literal=True) or "", "write"))',
     '            saves.append((a, "write"))', 'test_raw_namespace_preserves_requested_pair_not_folded_peer[python-.]', 'interpreter'),
    ("tools/skyguard/deletes.py", '                actual = targets.physical(w, cwd, "powershell", env, literal=how == "single")\n                saves.append(actual if a == b and actual else "")',
     '                actual = targets.physical(w, cwd, "powershell", env, literal=how == "single")\n                saves.append(b)',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[recycle-.]', 'recycle'),
    ("tools/skyguard/saves.py", '        n = paths.physical_abs(word, msys=(shell != "powershell"))',
     '        n = paths.norm_abs(word, msys=(shell != "powershell"))',
     'test_resaver_retains_raw_io_target_and_namespace_prefix_is_not_a_wildcard', 'resaver'),
    ("tools/skyguard/paths.py", '        d = physical_abs(v)', '        d = norm_abs(v)',
     'test_raw_config_root_is_retained_for_devbench_and_profile_membership', 'configured-root'),
    ("tools/skyguard/saves.py", '        if not name or (not WILD.search(name) and not name.lower().endswith(EXTS)):', '        if False:',
     'test_unqualified_raw_member_or_dot_segment_refuses_without_peer_backup[Distinct.ess.]', 'unsupported-member'),
    ("tools/skyguard/saves.py", '        if not t.literal and WILD.search(os.path.splitdrive(folder)[1]):',
     '        if not t.literal and WILD.search(folder):',
     'test_ordinary_namespace_aliases_still_preserve_the_same_pair', 'namespace-not-wildcard'),
    ("tools/skyguard/targets.py", '            absolute = _drive_relative(p, base, "cmd")', '            absolute = None',
     'test_drive_relative_file_operand_uses_known_same_drive_cwd', 'drive-relative-file'),
    ("tools/skyguard/writes.py", '    if python_literals:', '    if False:',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[python-escaped-.]', 'python-literal-dispatch'),
    ("tools/skyguard/writes.py", 'func.id == "Path" or (', 'func.id == "NoPath" or (',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[python-path-.]', 'python-direct-path'),
    ("tools/skyguard/writes.py", '        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None',
     '        return repr(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else None',
     'test_raw_namespace_preserves_requested_pair_not_folded_peer[python-escaped-.]', 'python-decoded-value'),
    ("tools/skyguard/writes.py", '            return str(Path(*parts))', '            return parts[0]',
     'test_python_path_absolute_later_argument_selects_the_actual_save', 'python-complete-path'),
    # Classification and I/O have separate resolvers. Restoring expansion only in
    # the classifier is masked by the correct I/O identity; revert that I/O flag.
    ("tools/skyguard/writes.py", '            saves.append((targets.physical(word, actual_base, "cmd", env, literal=True) or "", "write"))',
     '            saves.append((targets.physical(word, actual_base, "cmd", env) or "", "write"))',
     'test_python_literal_quoting_does_not_hide_an_owned_save_name', 'literal-not-cmd-expansion'),
    ("tools/skyguard/writes.py", '        # The invoked Python can have a different syntax version from this guard.\n        raise ValueError("Python literal write scope could not be parsed") from None',
     '        return []', 'test_unknown_python_syntax_refuses_before_any_save_permission', 'unparsed-not-readonly'),
)]

MUTATIONS += [pytest.param("tools/skyguard/writes.py", before, after,
    "tests/test_python_literal_writes.py::" + test, id="python-literal-" + name) for before, after, test, name in (
    ('    if len(code) > 1024 * 1024:', '    if False:',
     'test_oversized_python_analysis_refuses_instead_of_guessing_an_identity', 'analysis-bound'),
    ('        tree = ast.parse(code)', '        tree = ast.parse("pass")',
     'test_python_write_literals_decode_to_the_filename_value', 'syntax-reader'),
)]

# Permanent store rules must have behavioral counterfactuals, including configured
# destinations outside every ordinary install/config protection zone.
MUTATIONS += [
    pytest.param("tools/skyguard/shell_rules.py", "    if store_hit:\n", "    if False:\n",
                 "tests/test_save_store_rule_coverage.py::test_configured_store_mutations_are_denied_before_ordinary_location_rules[shell]",
                 id="rule-S-SAVE-STORE-configured-destination"),
    pytest.param("tools/skyguard/write_rules.py",
                 '    if placed and self_rules.save_store_target(placed, roots, holder=op in ("delete", "move")):',
                 "    if False:",
                 "tests/test_save_store_rule_coverage.py::test_configured_store_mutations_are_denied_before_ordinary_location_rules[files]",
                 id="rule-W-SAVE-STORE-configured-destination"),
    pytest.param("tests/test_repo_invariants.py",
                 '    assert local not in tracked, local + " is tracked and would ship over the user\'s own notes"',
                 "    assert True",
                 "tests/test_repo_invariants.py::test_knowledgebase_membership_allows_local_notes_but_refuses_shipping_them[tracked-local]",
                 id="users-local-knowledgebase-index-membership"),
]

# Setup smoke covers portable targets and keeps native-Python paths separate
# from the root spelling used to render a non-Windows host configuration.
MUTATIONS += [
    pytest.param("setup.sh",
                 '        "$PY_ROOT/tools/skyguard/upgrade_render.py" encode-powershell',
                 '        "$GAME_ROOT_WIN/tools/skyguard/upgrade_render.py" encode-powershell',
                 "tests/test_setup_smoke.py::test_the_linux_legs_pass[all]",
                 id="setup-native-python-renderer-path"),
    pytest.param("setup.sh", '        "$PY_ROOT/.codex/hooks/skyguard-entry.ps1"; then',
                 '        "$GAME_ROOT_WIN/.codex/hooks/skyguard-entry.ps1"; then',
                 "tests/test_setup_smoke.py::test_the_linux_legs_pass[all]",
                 id="setup-native-python-encoding-target"),
    pytest.param("scripts/setup-smoke.sh", 'case "$AGENT" in claude|codex|all|both|auto|generic|opencode)',
                 'case "$AGENT" in claude|codex|all|both|auto|generic)',
                 "tests/test_setup_smoke.py::test_the_smoke_script_passes_on_the_real_setup[opencode]",
                 id="setup-smoke-opencode-accepted"),
    pytest.param("tests/test_skyguard_codes.py",
                 '        assert row["text"] == "WARN AND ALLOW: this tool surface was not evaluated", row["id"]',
                 "        assert True",
                 "tests/test_skyguard_codes.py::test_unknown_corpus_warning_exemption_cannot_hide_an_evaluated_rule",
                 id="unknown-corpus-code-exemption-narrow"),
]

# A read-only proposal must neither execute policy workers nor produce execution evidence.
MUTATIONS += [
    pytest.param('tools/skyguard/portable.py', 'preserve=not preview)', 'preserve=True)', 'tests/test_approval_preview.py::test_preview_cli_preserves_exact_scope_without_writes', id='approval-preview-preservation'),
    pytest.param('tools/skyguard/portable.py', 'audit = _PreviewAudit(str(project)) if preview else', 'audit = Audit(str(project)) if preview else', 'tests/test_approval_preview.py::test_preview_cli_preserves_exact_scope_without_writes', id='approval-preview-audit'),
    pytest.param('tools/skyguard/portable.py', '        if not preview:\n            plan = local_policy.supplement_native_backups', '        if True:\n            plan = local_policy.supplement_native_backups', 'tests/test_approval_preview.py::test_preview_never_runs_unqualified_workers_or_callbacks', id='approval-preview-worker-gate'),
    pytest.param('tools/skyguard/portable.py', '        if local_surface and local_policy.registration(project) is not None:', '        if False:', 'tests/test_approval_preview.py::test_normal_check_fallback_exposes_full_scope_with_registered_policy', id='approval-preview-personal-policy'),
    pytest.param('tools/skyguard/engine.py', '        if not readonly:\n            _relock_due', '        if True:\n            _relock_due', 'tests/test_approval_preview.py::test_preview_shadow_does_not_write_reject_markers_or_relock[False]', id='approval-preview-relock'),
    pytest.param('tools/skyguard/engine.py', 'if not readonly and _state_reject_is_new(project, st.reason):', 'if _state_reject_is_new(project, st.reason):', 'tests/test_approval_preview.py::test_preview_shadow_does_not_write_reject_markers_or_relock[True]', id='approval-preview-reject-marker'),
    pytest.param('tools/skyguard/engine.py', '        if not readonly:\n            _relock_due', '        if False:\n            _relock_due', 'tests/test_approval_preview.py::test_readonly_shadow_does_not_change_normal_relock_path', id='approval-preview-preserve-normal-relock'),
    pytest.param('tools/skyguard/portable.py', 'reason=reason)\n    elif result.preservation == "failed":', 'reason=reason + chr(10) + result.reason)\n    elif result.preservation == "failed":', 'tests/test_approval_preview.py::test_compact_bulk_hook_and_preview_scope_oracle[sp-bash-three]', id='approval-bulk-reason-inline-scope'),
]

# CI run 39145224156: Windows-only twins defined after _WINDOWS_ONLY_TWINS was applied. Each is killed on Windows.
_LATE_WINDOWS_ONLY = {
    "setup-native-python-renderer-path": "$PY_ROOT and $GAME_ROOT_WIN name the same folder off Windows",
    "setup-native-python-encoding-target": "$PY_ROOT and $GAME_ROOT_WIN name the same folder off Windows",
    # 0x400 is FILE_ATTRIBUTE_REPARSE_POINT; off Windows st_file_attributes is absent and is_symlink() alone skips the link
    "housekeep-sweep-follows-junctions": "the junction attribute exists only on Windows",
}
_LATE_WINDOWS_ONLY.update({"ordered-policy-" + name: "the reviewed personal-policy upgrade refuses off Windows, so "
                           "every tests/test_ordered_policy.py anchor skips there" for name in (
    "source-qualification", "wrapper-qualification", "complete-native-group", "complete-runtime",
    "existing-empty-registration")})
assert set(_LATE_WINDOWS_ONLY) <= {p.id for p in MUTATIONS}, set(_LATE_WINDOWS_ONLY) - {p.id for p in MUTATIONS}
MUTATIONS[:] = [
    pytest.param(*p.values, id=p.id, marks=tuple(p.marks) + (
        pytest.mark.skipif(sys.platform != "win32", reason=_LATE_WINDOWS_ONLY[p.id]),))
    if p.id in _LATE_WINDOWS_ONLY else p
    for p in MUTATIONS
]

# CI run 39145224156: the per-operand store-config re-read (20-70x slowdown) must not come back unnoticed on a fast machine.
MUTATIONS += [pytest.param("tools/skyguard/self_rules.py", "    cache = _StoreCache()\n", "    cache = None\n",
                           "tests/test_skyguard_self_rules.py::test_a_many_operand_command_reads_each_store_config_once",
                           id="save-store-config-read-once-per-decision")]

# CI run 39210090486: the mutation gate became its own CI part per leg, checked against its own numbers.
MUTATIONS += [
    pytest.param(".github/workflows/ci.yml", "        part: [suite, mutations]", "        part: [suite]",
                 "tests/test_workflows.py::test_the_tripwires_bracket_the_suite_on_every_leg",
                 id="ci-mutation-part-dropped"),
    pytest.param("scripts/check_ci_leg.py", "        spec = dict(spec, **spec[\"parts\"][part])", "        pass",
                 "tests/test_ci_leg.py::test_a_part_is_checked_against_its_own_numbers",
                 id="ci-leg-part-numbers-ignored"),
]
# 2026-10-19 procsubst: an assignment prefix holding `<( )` hid the program behind it ("?"), and an unnamed program
# word (`$R rm`, `$(true) rm`) hid the command after it. Each fix is put back and its parser test must go red.
_PS1 = "tests/test_shellparse.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SP, "            # It joins the word it touches, as bash does",
     "            self.end_word(f)\n            # It joins the word it touches, as bash does",
     _PS1 + "test_assignment_prefix_holding_a_substitution_is_one_word[X=<(echo a) rm -rf x]",
     "procsubst-ends-the-assignment-word"),
    (_SP, "                        self.after.append(k)", "                        pass",
     _PS1 + "test_an_unnamed_program_word_also_reads_the_next_word_as_the_program[$R rm -rf x]",
     "unnamed-program-no-next-word-reading"),
    (_SP, "            self._sub_run([], j, alt=False)", "            self._sub_run([], j)",
     _PS1 + "test_the_next_word_reading_gets_the_program_candidates_too[$R $(echo rm) -rf x]",
     "unnamed-program-next-word-without-candidates"),
    (_SP, '                    if self.y[k] and k + 1 < n and (self.d in ("bash", "cmd") or self.call):',
     '                    if k + 1 < n and (self.d in ("bash", "cmd") or self.call):',
     _PS1 + "test_a_glob_program_word_gets_no_next_word_reading", "unnamed-program-glob-read-past"),
]]

# 2026-10-19 procsubst, part 2: the same class in cmd, PowerShell and through eval-like wrappers (MEASURED on Windows),
# and the user's ruling that an unnamed program naming a save asks first. Each clause broken alone.
_PS2 = _PS1 + "test_an_unnamed_program_hides_nothing_in_cmd_powershell_or_eval"
_SPP = "tests/test_save_policy.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SP, '                    if self.y[k] and k + 1 < n and (self.d in ("bash", "cmd") or self.call):',
     '                    if self.y[k] and k + 1 < n and self.d == "bash":', _PS2, "unnamed-program-bash-only"),
    (_SP, "            if any(self.q[j + 1:]):", "            if False:", _PS2, "unnamed-program-no-eval-reading"),
    (_SP, "                if _CMD_DELAYED.search(m.group()):", "                if False:", _PS2,
     "cmd-delayed-expansion-not-dynamic"),
    (_SP, "                if _CMD_DELAYED.search(t, self.i + 1, j):", "                if False:", _PS2,
     "cmd-quoted-delayed-expansion-not-dynamic"),
    (_SP, r'        expanded = _CMD_DELAYED.sub("*", re.sub(r"%[^%\s]{1,256}%", "*", expanded))', "        pass", _PS2,
     "cmd-concatenated-name-no-candidates"),
    (_SP, '    if re.search(r"[a-z0-9]", base) and ("*" in base or dialect == "bash" and _glob_word(base)):',
     '    if dialect == "bash" and re.search(r"[a-z0-9]", base) and ("*" in base or _glob_word(base)):', _PS2,
     "concatenated-name-candidates-bash-only"),
    (_SP, ' or "%" in b or "!" in b):', ' or "%" in b):', _PS2, "cmd-delayed-word-is-a-name"),
    ("tools/skyguard/shell_rules.py", "    for e in parsed.unnamed:", "    for e in ():",
     _SPP + "test_an_unnamed_program_naming_a_save_asks_and_a_change_it_hides_is_still_backed_up",
     "rule-S-SAVE-OPAQUE-dropped"),
    ("tools/skyguard/shell_rules.py", "        if _resaver_op(e):                  # read as ReSaver",
     "        if False:                  # read as ReSaver",
     _SPP + "test_known_resaver_does_not_hide_later_opaque_resaver_outputs", "save-opaque-asks-a-known-resaver"),
    (_SP, "                    self.no_candidate = not cands", "                    self.no_candidate = True",
     _SPP + "test_an_unnamed_program_naming_a_save_asks_and_a_change_it_hides_is_still_backed_up",
     "save-opaque-asks-a-word-with-candidates"),
    (_SP, '        if program == "?" and self.no_candidate:', "        if False:",
     _SPP + "test_an_unnamed_program_naming_a_save_asks_and_a_change_it_hides_is_still_backed_up",
     "unnamed-execs-not-recorded"),
    (_SP, "    except ParseError:\n        pass\n    try:\n        _records_to_execs(sc)",
     "    except ParseError:\n        return\n    try:\n        _records_to_execs(sc)",
     _PS1 + "test_a_speculative_eval_reading_keeps_what_it_completed", "speculative-reading-drops-completed"),
    ("tools/skyguard/saves.py", '"shell.S-RESAVER-APPLY", "shell.S-SAVE-OPAQUE"))', '"shell.S-RESAVER-APPLY"))',
     _SPP + "test_guards_off_does_not_lift_the_unnamed_program_save_ask", "save-opaque-ask-shadowed"),
]]

# 2026-10-19 procsubst, part 3: a dynamic word's literal part is a candidate (`"$X"reg` with X empty runs reg).
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_SP, "                    if lit and program_name(lit, self.d) not in cands:", "                    if False:",
     _PS2, "concatenated-literal-not-a-candidate"),
]]

# A shared-core ask still has to pass the save policy before reaching the host.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/modding_harness/dispatch.py", '                if restriction.decision == "deny":',
     '                if restriction.decision in ("deny", "ask"):',
     "tests/test_shared_core_ask_order.py", "core-local-ask-skips-save-preservation"),
    ("tools/modding_harness/dispatch.py", '            if result.decision == "deny":',
     '            if result.decision in ("deny", "ask"):',
     "tests/test_shared_core_ask_order.py", "core-profile-ask-skips-save-preservation"),
]]


# Incremental pre-release R1: unknown/failed coverage cannot be promoted by a prompt.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/modding_harness/dispatch.py", '                if (plan.mutations or plan.unresolved or plan.wildcard or plan.helper or\n                        plan.recheck is not None or plan.known_writer_active):',
     '                if False:', "tests/test_shared_core_ask_order.py::test_unguarded_save_obligation_refuses",
     "core-unguarded-save-obligations-discarded"),
    ("tools/modding_harness/dispatch.py", '                if restriction.evaluation in ("failed", "partial"):',
     '                if False:', "tests/test_shared_core_ask_order.py::test_incomplete_local_ask_refuses",
     "core-incomplete-local-ask-accepted"),
    ("tools/modding_harness/dispatch.py", '                    result = replace(result, decision="ask", reason=asking.reason)',
     '                    result = replace(asking, warnings=result.warnings)',
     "tests/test_shared_core_ask_order.py::test_unknown_local_ask_does_not_claim_profile_evaluation",
     "core-local-ask-promotes-unguarded-coverage"),
]]

# Incremental pre-release R4: JSON syntax alone is not a usable hook verdict.
MUTATIONS.append(pytest.param(
    "scripts/guard-latency.py", '        if not valid_pretooluse(r.stdout):',
    '        if False:',
    "tests/test_guard_latency.py::test_invalid_pretooluse_schema_fails_the_gate[scalar]",
    id="guard-latency-valid-json-invalid-schema"))
# L2 HIGH-1 (v4.0.0 review): another rule's ASK beside a save change. The shared core (pinned 221292e) holds the ask
# until the typed bulk approval and the verified pair backup pass; these twins restore its early returns and require
# the LIVE hook-path tests (Claude run.py in process, Codex engine.pre_tool, portable.check) to go red.
_PSAVE = "tests/test_save_policy.py::"
_CORE_PROFILE_ASK = ('            if result.decision == "deny":', '            if result.decision in ("deny", "ask"):')
_CORE_LOCAL_ASK = ('                if restriction.decision == "deny":', '                if restriction.decision in ("deny", "ask"):')
MUTATIONS += [pytest.param("tools/modding_harness/dispatch.py", *m[:3], id=m[3]) for m in [
    (*_CORE_PROFILE_ASK, _PSAVE + "test_three_saves_beside_an_ask_are_refused_without_the_typed_approval[opaque]",
     "rule-S-SAVE-APPROVAL-wins-over-an-ask"),
    (*_CORE_PROFILE_ASK, _PSAVE + "test_one_save_beside_an_ask_is_backed_up_before_the_ask[git-discard]",
     "rule-S-SAVE-BACKUP-before-an-ask"),
    (*_CORE_PROFILE_ASK, _PSAVE + "test_codex_save_change_beside_an_ask_keeps_the_save_policy[one-save-asks-after-backup]",
     "save-codex-ask-after-backup"),
    (*_CORE_LOCAL_ASK, "tests/test_portable_profile.py::test_a_local_restriction_ask_waits_for_the_save_policy",
     "save-local-restriction-ask-held"),
]]

# v4.0.0 review L3 LOW-1: a malformed inline snippet is the engine's parse refusal, not a crash (GUARD INERT).
MUTATIONS += [pytest.param("tools/skyguard/engine.py",
    '        return Verdict("deny", UNPARSED + str(exc), status="failed", code="engine.UNPARSED")', "        raise",
    "tests/test_skyguard_engine.py::test_the_claude_hook_answers_a_malformed_inline_snippet",
    id="engine-unparsed-snippet-refused")]

# v4.0.0 review L2 LOW-4: an expired unlock-all relock settles on the same criterion as session start.
_RLR = "tests/test_relock_retries.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/lock.py", "        if not relock_failed(res):", "        if res.exit == 0:",
     _RLR + "test_a_structural_skip_settles_an_expired_relock", "relock-expired-structural-skip-settles"),
    ("tools/skyguard/lock.py", '    bad = [x for x in res.data.get("not_locked", []) if x[1].startswith(("did not verify", "error"))]',
     '    bad = [x for x in res.data.get("not_locked", []) if x[1].startswith(("did not verify",))]',
     _RLR + "test_failed_expiry_relock_is_retried_until_success", "relock-expired-error-is-retried"),
    ("tools/skyguard/lock.py", "    return res.exit == 2 or bool(bad)", "    return bool(bad)",
     _RLR + "test_a_structural_skip_settles_an_expired_relock", "relock-refused-is-retried"),
]]


# User ruling 2026-10-20 (v4.0.0 review L2 MEDIUM-3): the agent's session transcripts are never the agent's to change.
_TRG = "tests/test_transcript_guard.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/shell_rules.py", '    if spoken:\n        return deny("S-SELF-TRANSCRIPT"',
     '    if False:\n        return deny("S-SELF-TRANSCRIPT"',
     _TRG + "test_a_shell_write_to_the_transcript_is_refused[append]", "rule-S-SELF-TRANSCRIPT-shell"),
    ("tools/skyguard/write_rules.py", "    if spoken and self_rules.transcript_target(",
     "    if False and self_rules.transcript_target(",
     _TRG + "test_a_file_tool_write_to_the_transcript_is_refused[Write]", "rule-W-SELF-TRANSCRIPT-write"),
    ("tools/skyguard/engine.py",
     'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-SELF-TRANSCRIPT", "write.W-SELF-TRANSCRIPT"))',
     "NEVER_SHADOW = NEVER_SHADOW | frozenset()",
     _TRG + "test_guards_off_never_lifts_the_transcript_refusal", "rule-S-SELF-TRANSCRIPT-never-shadowed"),
    ("tools/skyguard/self_rules.py",
     '        if own and (low == own.lower().rstrip("/") or holder and own.lower().startswith(low + "/")):',
     "        if False:", _TRG + "test_missing_payload_transcript_keeps_lexical_protection",
     "rule-W-SELF-TRANSCRIPT-payload-path"),
    ("tools/skyguard/self_rules.py", '            if rel[:1] == ["projects"] and len(rel) >= 3 and rel[-1].endswith(".jsonl"):',
     "            if False:", _TRG + "test_the_transcript_tree_is_refused_whatever_the_payload_names[another-sessions-file]",
     "rule-W-SELF-TRANSCRIPT-claude-tree"),
    ("tools/skyguard/self_rules.py", '            if holder and (rel in ([], ["projects"]) or rel[:1] == ["projects"]',
     '            if False and (rel in ([], ["projects"]) or rel[:1] == ["projects"]',
     _TRG + "test_the_transcript_tree_is_refused_whatever_the_payload_names[the-projects-folder]", "rule-S-SELF-TRANSCRIPT-holder"),
    ("tools/skyguard/self_rules.py",
     '            if rel[:1] in (["sessions"], ["archived_sessions"]) and (holder or len(rel) >= 2):',
     "            if False:", _TRG + "test_codex_shell_append_to_its_rollout_is_refused",
     "rule-S-SELF-TRANSCRIPT-codex-tree"),
    ("tools/skyguard/engine.py",
     '        out.append(evaluate_write(p, audit, roots=roots, cwd=req.cwd or "", op=SAVE_OP.get(op, "write"),\n'
     '                                  transcript=req.transcript_path or ""))',
     '        out.append(evaluate_write(p, audit, roots=roots, cwd=req.cwd or "", op=SAVE_OP.get(op, "write")))',
     _TRG + "test_codex_apply_patch_on_its_payload_transcript_is_refused", "transcript-codex-patch-wired"),
    ("tools/skyguard/run.py", '._replace(transcript_path=p.get("transcript_path", "") or "")', "",
     _TRG + "test_the_payloads_own_transcript_is_refused_wherever_it_lives", "transcript-claude-shell-wired"),
]]


# Incremental pre-release R2/R3: physical transcript identity and the recorded opaque-program boundary.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/self_rules.py", '                if os.path.samefile(abs_path, own):',
     '                if False:', "tests/test_transcript_guard.py::test_current_transcript_hardlink_is_refused",
     "rule-S-SELF-TRANSCRIPT-hardlink-identity"),
    ("tools/skyguard/shell_rules.py",
     '    if not opaque:\n        return _protected_text(command, roots) if _unfinished(parsed, command) else ""\n'
     '    for e in opaque:',
     '    if True:\n        return ""\n    for e in opaque:',
     "tests/test_opaque_protected.py::test_an_unnamed_program_on_a_protected_path_asks[opaque-on-a-plugin]",
     "rule-S-OPAQUE-PROTECTED-no-verb"),
]]


# v4.0.0 review L4a M4: a release refuses entries left under an Unreleased heading above the tagged section.
_CCR = "tests/test_changelog_release.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("scripts/check_changelog_release.py", "                if populated:", "                if False:",
     _CCR + "test_entries_above_the_tagged_section_refuse[entry]", "changelog-unreleased-entries-ignored"),
    ("scripts/check_changelog_release.py", "            populated.add(section)", "            pass",
     _CCR + "test_entries_above_the_tagged_section_refuse[entry-under-subheading]", "changelog-unreleased-never-populated"),
    ("scripts/check_changelog_release.py", "title, re.I):", "title, re.I) and False:",
     _CCR + "test_entries_above_the_tagged_section_refuse[entry]", "changelog-unreleased-not-recognised"),
    ("scripts/check_changelog_release.py", '                if re.search(r"\\bunreleased\\b", title, flags=re.I):',
     "                if False:", _CCR + "test_a_tagged_heading_that_still_says_unreleased_refuses",
     "changelog-undated-tagged-heading-accepted"),
    (".github/workflows/release.yml", "          python -B scripts/check_changelog_release.py",
     "          true || python -B scripts/check_changelog_release.py",
     _CCR + "test_the_release_workflow_runs_the_check", "release-changelog-check-skipped"),
]]

# review2 B-tools L4: the spike scripts send an earlier run's leftovers to the Recycle Bin; the scan reads calls.
_L2S = "tests/test_layer2_spike_sandbox.py::"
MUTATIONS += [
    pytest.param("scripts/layer2-spike/compat_xelib.py", '        sandbox.recycle(DATA + "/Spike.esp")',
                 '        os.remove(DATA + "/Spike.esp")', _L2S + "test_no_spike_script_deletes_outright",
                 id="layer2-spike-esp-deleted-outright"),
    pytest.param("tests/test_layer2_spike_sandbox.py", ' or node.func.attr in ("unlink", "rmdir"):', ":",
                 _L2S + "test_the_outright_delete_scan_sees_every_spelling[Path(f).unlink()]",
                 id="layer2-spike-scan-misses-path-unlink"),
]

# --- Issue 11: House Carl base-archive inputs ---------------------------------------------------------------------
_HC = "tools/housecarl_integration/order.py"
_HCT = "tests/test_housecarl.py::"
MUTATIONS += [pytest.param(f, find, rep, _HCT + test, id="housecarl-archive-" + i) for i, f, find, rep, test in [
    ("game-root-ini-not-read", _HC,
     '    applied = [("game-root", game_ini), ("user", user / main)]',
     '    applied = [("user", user / main)]', "test_root_ini_base_archives_reach_the_backend[SE]"),
    ("vr-documents-skyrim-ini-applied", _HC, 'MAIN_INI = {"SE": "Skyrim.ini", "VR": "SkyrimVR.ini"}',
     'MAIN_INI = {"SE": "Skyrim.ini", "VR": "Skyrim.ini"}', "test_only_the_editions_main_user_ini_is_applied[VR]"),
    ("other-user-inis-not-marked", _HC,
     '    unqualified = [("user", user / n) for n in USER_INIS if n != main] + [("custom"', '    unqualified = [("custom"',
     "test_only_the_editions_main_user_ini_is_applied[SE]"),
    ("mixed-files-not-marked", _HC, "    if len(mixed) > 1:", "    if False:",
     "test_root_ini_base_archives_reach_the_backend[VR]"),
    ("user-key-does-not-override", _HC, '                lists[folded] = {"value": value, "source": str(path)}',
     '                lists.setdefault(folded, {"value": value, "source": str(path)})',
     "test_user_ini_precedence_and_explicit_override"),
    ("explicit-empty-not-marked", _HC, "            elif folded in lists:", "            elif False:",
     "test_user_ini_precedence_and_explicit_override"),
    ("unqualified-source-applied", _HC, "        if (role, path) not in applied:", "        if False:",
     "test_unqualified_ini_sources_are_marked_never_applied"),
    ("unqualified-source-not-marked", _HC, "            if any(v for rows in keys.values() for _k, v in rows):",
     "            if False:", "test_unqualified_ini_sources_are_marked_never_applied"),
    ("plugin-ini-not-found", _HC, "        if hit:", "        if False:",
     "test_unqualified_ini_sources_are_marked_never_applied"),
    ("mo2-global-inis-not-marked", _HC, "        if not local:", "        if False:",
     "test_mo2_profile_inis_and_game_root_list"),
    ("no-base-list-not-marked", _HC, '    if not any(resolved[k]["value"] for k in BASE_KEYS):', "    if False:",
     "test_root_ini_base_archives_reach_the_backend[VR]"),
    ("inputs-not-fingerprinted", _HC, "    extra += archive_material\n", "",
     "test_every_archive_input_is_fingerprinted[root-edit]"),
    ("unqualified-vr-key-not-marked", _HC, "            if folded not in allowed:", "            if False:",
     "test_vr_supplemental_list_and_unqualified_vr_keys"),
    ("vr-fallback-dropped", _HC, '{"value": VR_FALLBACK, "source": "engine fallback"}',
     '{"value": "", "source": "engine fallback"}', "test_vr_supplemental_list_and_unqualified_vr_keys"),
    ("absence-always-conclusive", "tools/housecarl_integration/gateway.py",
     '"absence_conclusive": snap.archive_coverage["complete"],', '"absence_conclusive": True,',
     "test_asset_answers_carry_machine_readable_archive_coverage"),
]]

# Corpus fix 2026-10-24: the guard corpus runs against a SYNTHETIC install root, and no file under tests/ may name the
# real one. One twin per spelling the scan must see, one for the exemption, and one that puts the real root back.
_NRI = "tests/test_no_real_install_path.py"
_NRI_T = _NRI + "::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_NRI, r'r"og(\\*) games")', r'r"og() games")', _NRI_T + "test_the_scan_sees_every_spelling_of_the_real_install",
     "corpus-root-scan-misses-escaped-space"),
    (_NRI, '_PARENT = re.compile(r"(?i)\\b" + "g"', '_PARENT = re.compile(r"\\b" + "G"',
     _NRI_T + "test_the_scan_sees_every_spelling_of_the_real_install", "corpus-root-scan-is-case-sensitive"),
    (_NRI, " + [m.group(0) for m in _SHORT.finditer(text)]", "",
     _NRI_T + "test_the_scan_sees_every_spelling_of_the_real_install", "corpus-root-scan-misses-short-name"),
    (_NRI, "    for tok in _B64.findall(text):", "    for tok in []:",
     _NRI_T + "test_the_scan_sees_every_spelling_of_the_real_install", "corpus-root-scan-misses-base64"),
    (_NRI, "        if hashlib.sha256(data).hexdigest() in exempt_hashes:", '        if path.suffix == ".fixture":',
     _NRI_T + "test_only_an_unedited_released_copy_is_exempt", "corpus-root-exempts-by-name-not-hash"),
    ("tests/guard_corpus/game-root.txt", "C:/Synthetic Games/", "C:/G" + "OG Games/",
     _NRI_T + "test_no_file_under_tests_names_the_real_install", "corpus-root-is-the-real-install"),
]]


# guard3 precision (v4.0.0 review A-guard LOW-5, user-approved): a same-command literal names the program and the text an
# eval-like call runs; an eval the guard cannot prove keeps resolution off.
_OPT = "tests/test_opaque_protected.py::"
_G3P = "tools/skyguard/shellparse.py"
_G3A = "tools/skyguard/assign.py"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_G3P, "                return [val]\n            return None\n        return words or None",
     "                return None\n            return None\n        return words or None",
     _OPT + "test_ordinary_commands_and_unprotected_targets_do_not_ask[program-path-with-spaces]",
     "guard3-quoted-program-path"),
    (_G3P, '                    code = self._code(k + 1)[0]                 # guard3', '                    code = " ".join(v[k + 1:])  # guard3',
     _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[eval-literal]", "guard3-eval-reads-literal"),
    (_G3P, "                code, certain = self._code(j, j + 1)        # guard3",
     "                code, certain = v[j], not self.y[j]        # guard3",
     _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[bash-c-literal]", "guard3-bash-c-reads-literal"),
    (_G3P, '                    lits = [sub.get(w, w) for w in ps_param(ex, "Command", 0)]',
     '                    lits = list(ps_param(ex, "Command", 0))',
     _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[pwsh-iex-literal]", "guard3-iex-reads-literal"),
    (_G3P, "                    else:\n                        strings.append(val)", "                    else:\n                        known = False",
     _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[pwsh-pipe-iex-literal]",
     "guard3-pipe-iex-reads-literal"),
    (_G3A, '        if w == "eval" and evals is not None and len(rest) == 1 and _EVAL_VAR.fullmatch(rest[0]):',
     "        if False:", _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[eval-literal]",
     "guard3-eval-deferred"),
    (_G3A, "        for rx in (_PS_IEX_VAR, _PS_PIPE_IEX):", "        for rx in ():",
     _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[pwsh-iex-literal]", "guard3-iex-deferred"),
    (_G3A, "        for rx in (_PS_IEX_VAR, _PS_PIPE_IEX):", "        for rx in (_PS_IEX_VAR,):",
     _OPT + "test_an_eval_of_a_same_command_literal_is_the_command_it_runs[pwsh-pipe-iex-literal]",
     "guard3-pipe-iex-deferred"),
    (_G3P, '        if val is None or "$" in val or "`" in val or assign.poisoned(val, sc.DIALECT):',
     '        if val is not None and ("$" in val or "`" in val or assign.poisoned(val, sc.DIALECT)):',
     _OPT + "test_an_eval_the_guard_cannot_prove_still_turns_resolution_off[value-unproven]", "guard3-settle-unproven"),
    (_G3P, '        if val is None or "$" in val or "`" in val or assign.poisoned(val, sc.DIALECT):',
     '        if val is None or "`" in val or assign.poisoned(val, sc.DIALECT):',
     _OPT + "test_an_eval_the_guard_cannot_prove_still_turns_resolution_off[value-expands]", "guard3-settle-expands"),
    (_G3P, '        if val is None or "$" in val or "`" in val or assign.poisoned(val, sc.DIALECT):',
     '        if val is None or "$" in val or "`" in val:',
     _OPT + "test_an_eval_the_guard_cannot_prove_still_turns_resolution_off[value-poisons]", "guard3-settle-poisons"),
    (_G3P, "    if found != len(ctx.evals):", "    if False:",
     _OPT + "test_an_eval_the_guard_cannot_prove_still_turns_resolution_off[eval-not-a-plain-statement]",
     "guard3-settle-unseen-eval"),
    (_G3P, "    if sc.depth == 0:\n        _settle_evals(sc)", "    if False:\n        _settle_evals(sc)",
     _OPT + "test_an_eval_the_guard_cannot_prove_still_turns_resolution_off[value-unproven]", "guard3-settle-runs"),
    (_G3P, "or self.ctx.poisoned() or self.ctx.evals:", "or self.ctx.poisoned():",
     _OPT + "test_an_eval_the_guard_cannot_prove_still_turns_resolution_off[cd-before-the-eval-settles]",
     "guard3-cd-waits-for-settle"),
]]


# guard3 (v4.0.0 review A-guard MEDIUM-1, MEDIUM-2, LOW-4, INFO): S-OPAQUE-PROTECTED's widened reading, one twin per clause.
_OPA = _OPT + "test_an_unnamed_program_on_a_protected_path_asks"
_OPS = _OPT + "test_ordinary_commands_and_unprotected_targets_do_not_ask"
MUTATIONS += [pytest.param(_SR, *m[:3], id=m[3]) for m in [
    ('        if e.program == "?":\n            if e not in out:', '        if False:\n            if e not in out:',
     _OPA + "[source-variable]", "rule-S-OPAQUE-PROTECTED-every-unknown-program"),
    ("any(_DYNAMIC.search(w) for w in s.argv)", "any(_DYNAMIC.search(w) for w in e.argv)",
     _OPS + "[eval-of-a-literal-beside-data]", "rule-S-OPAQUE-PROTECTED-eval-after-substitution"),
    ("            evals = evals or (len(e.argv) == 1 and e.argv[0] in _TEXT_READERS)", "            evals = evals",
     _OPA + "[pwsh-pipe-iex-root-alone]", "rule-S-OPAQUE-PROTECTED-reader-runs-text"),
    ("            evals = True", "            evals = False", _OPA + "[eval-root-alone]",
     "rule-S-OPAQUE-PROTECTED-eval-runs-text"),
    ("evals or any(_CODE_VARIABLE.fullmatch(t) for t in dynamic)", "True",
     _OPS + "[pwsh-command-with-its-own-variables]", "rule-S-OPAQUE-PROTECTED-code-variable-only"),
    ("evals or any(_CODE_VARIABLE.fullmatch(t) for t in dynamic)", "evals", _OPA + "[bash-c-root-alone]",
     "rule-S-OPAQUE-PROTECTED-code-runs-text"),
    ('        hit = _protected_text(m.group(1) or m.group(2) or m.group(3) or "", roots, root_alone=True)',
     '        hit = _protected_text(m.group(1) or m.group(2) or m.group(3) or "", roots, root_alone=False)',
     _OPA + "[eval-root-alone]", "rule-S-OPAQUE-PROTECTED-root-in-evaluated-text"),
    ("                if (not rest or rest[0] in _TERMINATOR) and root_alone:",
     "                if (not rest or rest[0] in _TERMINATOR) and False:", _OPA + "[pwsh-iex-root-alone]",
     "rule-S-OPAQUE-PROTECTED-root-alone-clause"),
    ('    text = _MSYS.sub(r"\\1:/", text)', "    text = text", _OPT + "test_msys_text_is_compared_with_the_drive_root_on_every_host",
     "rule-S-OPAQUE-PROTECTED-msys-spelling"),
    ('        return _protected_text(command, roots) if _unfinished(parsed, command) else ""', '        return ""',
     _OPA + "[token-cap]", "rule-S-OPAQUE-PROTECTED-token-cap"),
    ("    return not parsed.ok and bool(_DYNAMIC.search(command))", "    return not parsed.ok",
     _OPS + "[token-cap-no-expansion]", "rule-S-OPAQUE-PROTECTED-token-cap-needs-expansion"),
    ("    return not parsed.ok and bool(_DYNAMIC.search(command))", "    return bool(_DYNAMIC.search(command))",
     _OPS + "[expansion-beside-data]", "rule-S-OPAQUE-PROTECTED-token-cap-needs-cap"),
    ("    if _unfinished(parsed, command):\n        m = RESAVER_TEXT_SAVE.search(command)",
     "    if False:\n        m = RESAVER_TEXT_SAVE.search(command)",
     _OPT + "test_past_the_token_cap_a_save_in_the_text_still_asks", "rule-S-SAVE-OPAQUE-token-cap"),
    ("    opaque = _opaque_save(parsed, roots, cwd, env, command)", "    opaque = _opaque_save(parsed, roots, cwd, env)",
     _OPT + "test_past_the_token_cap_a_save_in_the_text_still_asks", "rule-S-SAVE-OPAQUE-token-cap-wired"),
]]
MUTATIONS.append(pytest.param(
    _G3P, '        if fp[1] and fp[0] in sub and "$" not in sub[fp[0]] and "`" not in sub[fp[0]]:', "        if False:",
    _OPS + "[start-process-of-a-literal-path]", id="guard3-start-process-reads-literal"))


# guard3 (v4.0.0 review C-docs MEDIUM-2): a literal name inline interpreter code deletes resolves where the code runs.
_IDT = "tests/test_interpreter_deletes.py::test_a_relative_interpreter_delete_from_the_install_is_refused"
MUTATIONS += [pytest.param("tools/skyguard/deletes.py", *m[:3], id=m[3]) for m in [
    ("        if code and resolved is None:\n            # guard3", "        if False:\n            # guard3",
     _IDT + "[python-rmtree]", "guard3-interpreter-delete-read"),
    ("        named = isinstance(f, ast.Name) and f.id in _PY_DELETE_CALLS", "        named = False",
     _IDT + "[python-from-import]", "guard3-interpreter-delete-bare-name"),
    ("        owned = (isinstance(f, ast.Attribute) and f.attr in _PY_DELETE_CALLS",
     "        owned = False and (isinstance(f, ast.Attribute) and f.attr in _PY_DELETE_CALLS",
     _IDT + "[python-os-remove]", "guard3-interpreter-delete-module-call"),
    ("            p = path_of(f.value)", "            p = None", _IDT + "[python-path-unlink]",
     "guard3-interpreter-delete-path-unlink"),
    ("        return [m.group(2) for m in _INTERP_DELETE_LIT.finditer(code)]", "        return []",
     _IDT + "[node-rmsync]", "guard3-interpreter-delete-other-languages"),
    ('            base = targets.exec_cwd(e, cwd, env) or ""', '            base = ""', _IDT + "[python-rmtree]",
     "guard3-interpreter-delete-resolved-where-it-runs"),
    ("interpreter_delete_targets(code, bool(_PYTHON.fullmatch(e.program)))", "interpreter_delete_targets(code, False)",
     _IDT + "[python-os-remove]", "guard3-interpreter-delete-python-as-python"),
]]


# guard3 (v4.0.0 review A-guard MEDIUM-3 and its S-SELF-STATE note): interpreter literal writes reach both refusals.
_ITW = "tests/test_transcript_guard.py::test_interpreter_code_writing_the_transcript_is_refused"
MUTATIONS += [pytest.param("tools/skyguard/self_rules.py", *m[:3], id=m[3]) for m in [
    ("        for placed, holder in _interpreter_targets(e, cwd, env):", "        for placed, holder in []:",
     _ITW + "[python-c-append]", "rule-S-SELF-TRANSCRIPT-interpreter-code"),
    ("            for a, holder in _interpreter_targets(e, cwd, env):", "            for a, holder in []:",
     "tests/test_interpreter_deletes.py::test_interpreter_code_writing_the_guard_state_is_refused",
     "rule-S-SELF-STATE-interpreter-code"),
    ("    for words, holder in ((written, False), (deletes.interpreter_delete_targets(code, python), True)):",
     "    for words, holder in ((written, False),):", _ITW + "[python-c-delete]",
     "rule-S-SELF-TRANSCRIPT-interpreter-delete"),
    ("    for words, holder in ((written, False), (deletes.interpreter_delete_targets(code, python), True)):",
     "    for words, holder in ((deletes.interpreter_delete_targets(code, python), True),):",
     _ITW + "[node-e-append]", "rule-S-SELF-TRANSCRIPT-interpreter-write"),
]]


# guard3 (parent ruling): a bash-era snapshot stamp holding a save or a backup folder is never retired.
_SNT = "tests/test_snapshot_nested.py::test_an_old_bash_era_stamp_holding_a_save_or_a_backup_is_kept"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/skyguard/snapshot.py", "                    and not _holds_protected(d):", "                    and True:",
     _SNT + "[source/x.ess]", "guard3-legacy-stamp-checked"),
    ("tools/skyguard/snapshot.py", '        if any(f.lower().endswith(_PROTECTED_EXTS) for f in files):',
     "        if False:", _SNT + "[deploy/SKSE/x.skse]", "guard3-legacy-stamp-save"),
    ("tools/skyguard/snapshot.py", '        if rel != "." and any("backup" in part for part in rel.split("/")):',
     "        if False:", _SNT + "[source/backups/x.txt]", "guard3-legacy-stamp-backup"),
]]


# guard3 follow-up: a program that may set variables (an unproven program word, or one naming read/declare/...) ends
# literal resolution for every later word; the builtins themselves are pinned end to end beside the Task 7b units.
_UPP = "tests/test_unknown_program_poison.py::"
_UPU = _UPP + "test_after_a_possible_variable_setter_the_operand_stays_unresolved"
_UPR = _UPP + "test_a_literal_still_resolves_where_nothing_could_have_changed_it"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_G3P, "                    self.mark_unknown(cmd)", "                    pass", _UPU + "[unknown-program-reads-a-file]",
     "guard3-unknown-program-marked"),
    (_G3P, "        if self.unknown_ends and cmd.start >= self.unknown_ends[0]:", "        if False:",
     _UPU + "[substitution-program]", "guard3-unknown-program-ends-resolution"),
    (_G3P, "        if self.unknown_ends and cmd.start >= self.unknown_ends[0]:", "        if self.unknown_ends:",
     _UPR + "[use-before-the-unknown-program]", "guard3-unknown-program-only-later-words"),
    (_G3P, "        if val is None or not words or _basename(words[0]) in _VARIABLE_SETTERS:",
     "        if val is not None and (not words or _basename(words[0]) in _VARIABLE_SETTERS):",
     _UPU + "[unknown-program-reads-a-file]", "guard3-unknown-program-unproven"),
    (_G3P, "        if val is None or not words or _basename(words[0]) in _VARIABLE_SETTERS:",
     "        if val is None or _basename(words[0] if words else '') in _VARIABLE_SETTERS:",
     _UPU + "[empty-program-then-printf-v]", "guard3-unknown-program-empty"),
    (_G3P, "        if val is None or not words or _basename(words[0]) in _VARIABLE_SETTERS:",
     "        if val is None or not words:", _UPU + "[proven-program-is-printf]", "guard3-unknown-program-setter"),
    (_G3P, "            val = self.lookup(cmd, m.group(1)) if m and not self.unsure and split_safe else None",
     "            val = None", _UPR + "[proven-program-with-arguments]", "guard3-unknown-program-split-value"),
]]
# The other builtins (read, mapfile, readarray, getopts, declare, typeset, local, export) are ALSO write-counted
# (assign._KW_BASH counts every name in their statement), so removing their poison entry alone leaves D unresolved: a
# twin there survives by double coverage. Their e2e rows stay as tests; the Task 7b unit twins pin each poison entry.
MUTATIONS += [
    pytest.param(_AS, '        if w == "printf" and any(_PRINTF_V.match(r) for r in rest):', "        if False:",
                 _UPU + "[printf-v]", id="guard3-e2e-poison-printf-v"),
]
# Final sweep 2026-10-25 (X4 5e00b6e): housekeep never links or chmods a file that has a name it did not make -- a mod
# manager's hard link into a live Data/ file shares the inode, and the read-only attribute with it. One twin per clause.
_HKS = "tools/housekeep.py"
_HKS_T = "tests/test_housekeep.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_HKS, "        if f.stat().st_nlink != 1:\n", "        if False:\n",
     _HKS_T + "test_a_file_hard_linked_from_outside_is_never_linked_or_made_read_only[duplicate]",
     "sweep-hk-plan-ignores-link-count"),
    (_HKS, "        if p.stat().st_nlink != 1 or c.stat()", "        if False or c.stat()",
     _HKS_T + "test_check_refuses_a_link_whose_files_gained_a_name_after_the_plan[duplicate]",
     "sweep-hk-check-ignores-duplicate-links"),
    (_HKS, " or c.stat().st_nlink != 1 + memo.links.get(_key(c), 0):", " or False:",
     _HKS_T + "test_check_refuses_a_link_whose_files_gained_a_name_after_the_plan[canonical]",
     "sweep-hk-check-ignores-canonical-links"),
    (_HKS, "    if dup.stat().st_nlink != 1 or canon", "    if False or canon",
     _HKS_T + "test_link_itself_refuses_a_file_with_an_unexpected_name[duplicate]",
     "sweep-hk-link-ignores-duplicate-links"),
    (_HKS, " or canon.stat().st_nlink != canonical_links:\n", " or False:\n",
     _HKS_T + "test_link_itself_refuses_a_file_with_an_unexpected_name[canonical]",
     "sweep-hk-link-ignores-canonical-links"),
    (_HKS, "                    memo.links[ck] = memo.links.get(ck, 0) + 1\n", "",
     _HKS_T + "test_duplicates_become_one_read_only_copy_with_every_path_intact",
     "sweep-hk-apply-forgets-its-own-links"),
]]

# Final sweep 2026-10-25 (X4 679bc4a): a git failure must not silently switch the git-tracked protection off.
_HKG = "test_a_git_failure_in_a_repository_refuses_instead_of_unprotecting_tracked_files"
_HKP = "test_a_plain_folder_has_no_tracked_files_and_is_still_planned"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_HKS, "        if isinstance(e, OSError) and not _in_git_work_tree(root):\n", "        if True:\n",
     _HKS_T + _HKG + "[git-missing]", "sweep-hk-git-missing-unprotects"),
    (_HKS, '        if "not a git repository" in err.lower():\n', "        if True:\n",
     _HKS_T + _HKG + "[dubious-ownership]", "sweep-hk-git-error-unprotects"),
    (_HKS, '        if "not a git repository" in err.lower():\n', "        if False:\n",
     _HKS_T + _HKP + "[not-a-repository]", "sweep-hk-plain-folder-refused"),
    (_HKS, "    except ProtectionUnavailable as e:\n", "    except ZeroDivisionError as e:\n",
     _HKS_T + _HKG + "[dubious-ownership]", "sweep-hk-refusal-not-reported"),
]]

# Final sweep 2026-10-25 (X4 c6d4760): docs/RETENTION.md's Forever names are protected in code. One twin per name.
_HKF = "test_saves_and_backup_folders_are_never_planned"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_HKS, '        if any(part.startswith("known-good") for part in named):\n', "        if False:\n",
     _HKS_T + _HKF + "[t/known-good-2026-10-01-pre-stagger/Skyrim.esm]", "sweep-hk-known-good-unprotected"),
    (_HKS, '        if named[-1] == "audit_log.txt":\n', "        if False:\n",
     _HKS_T + _HKF + "[t/logs/AUDIT_LOG.txt]", "sweep-hk-audit-log-unprotected"),
]]

# Final sweep 2026-10-25 (X4 34133b7): an evidence link holding spaces, parentheses or escapes still protects its file.
_HKE = "test_a_linked_path_with_spaces_and_parentheses_is_still_evidence"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_HKS, "                    for end in ends + [len(tail)]:\n", "                    for end in ends[:1]:\n",
     _HKS_T + _HKE + "[literal]", "sweep-hk-link-stops-at-first-space"),
    (_HKS, "        views = {text, unquote(text)}\n", "        views = {text}\n",
     _HKS_T + _HKE + "[percent]", "sweep-hk-link-percent-escapes-unread"),
    (_HKS, "        views |= {re.sub(", "        views |= set() and {re.sub(",
     _HKS_T + _HKE + "[backslash]", "sweep-hk-link-backslash-escapes-unread"),
    (_HKS, "                    if len(tail) > 32768:\n", "                    if False:\n",
     _HKS_T + "test_evidence_discovery_past_its_bound_refuses_instead_of_truncating", "sweep-hk-link-bound-ignored"),
]]

# Final sweep 2026-10-25 (X4 6bf11a9): `housekeep recycle PATH` refuses what retention never removes. One twin per clause.
_HKR = "test_the_recycle_command_refuses_what_retention_never_removes"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_HKS, "    if real.parent == real:\n", "    if False:\n", _HKS_T + _HKR + "[a-drive-root]",
     "sweep-hk-recycle-takes-a-drive-root"),
    (_HKS, "    if os.path.normcase(str(real)) == os.path.normcase(str(_real(Path.home()))):\n", "    if False:\n",
     _HKS_T + _HKR + "[the-home-folder]", "sweep-hk-recycle-takes-home"),
    (_HKS, "        if os.path.normcase(str(b)) == os.path.normcase(str(real)) or _strictly_inside(b, real):\n",
     "        if False or _strictly_inside(b, real):\n", _HKS_T + _HKR + "[the-root]", "sweep-hk-recycle-takes-the-root"),
    (_HKS, "        if os.path.normcase(str(b)) == os.path.normcase(str(real)) or _strictly_inside(b, real):\n",
     "        if os.path.normcase(str(b)) == os.path.normcase(str(real)) or False:\n", _HKS_T + _HKR + "[above-the-root]",
     "sweep-hk-recycle-takes-above-the-root"),
    (_HKS, '    if any(part.lower() == "saves" for part in real.parts):\n', "    if False:\n",
     _HKS_T + _HKR + "[a-saves-folder]", "sweep-hk-recycle-takes-a-saves-folder"),
    (_HKS, "        if str(f).lower().endswith(SAVE_EXTS):\n", "        if False:\n", _HKS_T + _HKR + "[holds-a-save]",
     "sweep-hk-recycle-takes-a-save"),
    (_HKS, "    if home is None:\n", "    if False:\n", _HKS_T + _HKR + "[outside-the-allowed-roots]",
     "sweep-hk-recycle-takes-anything"),
    (_HKS, '    if any("backup" in x for x in rel):\n', "    if False:\n", _HKS_T + _HKR + "[a-backup-folder]",
     "sweep-hk-recycle-takes-a-backup"),
    (_HKS, '    if any(x.startswith("known-good") for x in rel):\n', "    if False:\n",
     _HKS_T + _HKR + "[a-known-good-baseline]", "sweep-hk-recycle-takes-known-good"),
    (_HKS, '        if low == "audit_log.txt" or any(', "        if False or any(",
     _HKS_T + _HKR + "[holds-the-audit-log]", "sweep-hk-recycle-takes-the-audit-log"),
    (_HKS, "        res = recycle([p for p in args if not refused[p]])\n", "        res = recycle(list(args))\n",
     _HKS_T + "test_a_refused_path_never_reaches_the_bin_and_the_rest_still_goes", "sweep-hk-recycle-refusal-ignored"),
]]

# Final sweep 2026-10-25 (X4 6a15443): docs/REVIEW-SCOPE.md is a maintainer note, never in the zip. Builder + checker.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("scripts/build_release_zip.py", 'EXCLUDE += ("docs/REVIEW-SCOPE.md",)\n', "",
     "tests/test_release_build.py::test_the_maintainer_review_scope_note_is_excluded_and_its_siblings_ship",
     "sweep-release-review-scope-shipped"),
    ("tools/check-release-payload.py", '    "docs/REVIEW-SCOPE.md",   # the maintainer', '    # "docs/REVIEW-SCOPE.md", the maintainer',
     _MM_RP + "[review-scope-shipped]", "sweep-payload-review-scope-allowed"),
]]

# Final sweep 2026-10-25 (X4 a0e4fd1): one red CI check must not skip the later ones. A twin per kind of step: a plain
# check, a check with its own condition, and a job without the setup marker.
_WF_T = "tests/test_workflows.py::test_one_red_check_never_skips_the_later_ones"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (".github/workflows/ci.yml", "      - name: ruff\n        if: ${{ !cancelled() && steps.setup.outcome == 'success' }}\n",
     "      - name: ruff\n", _WF_T, "sweep-ci-ruff-skipped-after-a-red-check"),
    (".github/workflows/ci.yml",
     "        if: ${{ !cancelled() && steps.setup.outcome == 'success' && matrix.part == 'suite' }}\n",
     "        if: matrix.part == 'suite'\n", _WF_T, "sweep-ci-node-tests-skipped-after-a-red-check"),
    (".github/workflows/ci.yml", "      - name: Setup complete\n        id: setup\n        run: echo setup complete\n\n"
     "      - name: Syntax and shebangs\n", "      - name: Syntax and shebangs\n", _WF_T, "sweep-ci-shell-job-has-no-marker"),
]]

# Final sweep 2026-10-25 (X4 5811619): a bare `return` in a test passes silently. The scan must see a planted one.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tests/test_no_bare_return_in_a_test.py",
     "            if in_test and isinstance(child, ast.Return) and child.value is None:\n",
     "            if False:\n",
     "tests/test_no_bare_return_in_a_test.py::test_the_guard_itself_can_go_red", "sweep-bare-return-scan-blind"),
    ("tests/test_skyguard_shell.py", "        assert got[0] == decision\n    else:\n        assert got == (decision, text)",
     "        assert got[0] == decision\n        return\n    assert got == (decision, text)",
     "tests/test_no_bare_return_in_a_test.py::test_no_test_function_bails_with_a_bare_return",
     "sweep-bare-return-reintroduced"),
]]

# Final sweep 2026-10-25 (install 6318fcf): the independent plugin parser reports what it could read only partly, and
# refuses what it cannot walk. One twin per report and per refusal.
_ESP = "tools/esp_records.py"
_ESP_T = "tests/test_esp_records.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_ESP, "        if n > 1:\n", "        if False:\n",
     _ESP_T + "test_a_duplicate_formid_is_reported_not_silently_collapsed", "sweep-esp-duplicate-formid-hidden"),
    (_ESP, "                except zlib.error as e:\n                    problems.append(",
     "                except zlib.error as e:\n                    [].append(",
     _ESP_T + "test_a_compressed_record_that_does_not_inflate_is_reported", "sweep-esp-bad-compression-hidden"),
    (_ESP, "        if (r.formid >> 24) > len(plugin.masters):\n", "        if False:\n",
     _ESP_T + "test_a_master_index_past_the_master_list_is_reported", "sweep-esp-bad-master-index-hidden"),
    (_ESP, "                if size < 24 or off + size > end:\n", "                if off + size > end:\n",
     _ESP_T + "test_a_file_that_cannot_be_walked_is_refused_with_its_offset[grup-too-small]",
     "sweep-esp-grup-size-unchecked"),
    (_ESP, "            if off + 24 + size > end:\n", "            if False:\n",
     _ESP_T + "test_a_file_that_cannot_be_walked_is_refused_with_its_offset[record-past-its-group]",
     "sweep-esp-truncation-unchecked"),
    (_ESP, "    if header is None:\n", "    if False:\n",
     _ESP_T + "test_a_file_that_cannot_be_walked_is_refused_with_its_offset[no-tes4-header]", "sweep-esp-any-file-accepted"),
    (_ESP, '        if t == "XXXX" and n == 4 and i + 10 <= len(body):\n', "        if False:\n",
     _ESP_T + "test_compressed_records_and_oversize_subrecords_are_read", "sweep-esp-xxxx-ignored"),
    ("tools/esp-summary.py", "    return 1 if plugin.problems else 0\n", "    return 0\n",
     _ESP_T + "test_summary_exits_1_on_a_problem_and_2_on_a_file_it_cannot_walk", "sweep-esp-summary-problem-exit-0"),
    ("tools/esp-record-diff.py", "    return 1 if differs or problems else 0\n", "    return 1 if differs else 0\n",
     _ESP_T + "test_diff_with_a_duplicate_formid_is_inconclusive_not_clean", "sweep-esp-diff-inconclusive-is-clean"),
    ("tools/esp-record-diff.py", "        if ra.sha1 != rb.sha1 or ra.flags != rb.flags or ra.type != rb.type:\n",
     "        if ra.flags != rb.flags or ra.type != rb.type:\n",
     _ESP_T + "test_diff_names_added_removed_and_changed_records_and_subrecords", "sweep-esp-diff-misses-body-change"),
    ("tools/esp-record-diff.py", "    if pa.header.sha1 != pb.header.sha1 or pa.header.flags != pb.header.flags:\n",
     "    if pa.header.flags != pb.header.flags:\n",
     _ESP_T + "test_diff_sees_a_header_change_and_a_reordered_record", "sweep-esp-diff-misses-header-change"),
]]

# Final sweep 2026-10-25 (install 86c18ca): a kept config identical to the archive's copy gets no .new.
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    ("tools/mod-install.py",
     '                if action == "CONFIG-NEW" and dest.is_file() and sha(dest) == sha(staged[inner]):\n',
     "                if False:\n",
     "tests/test_mod_install.py::test_a_kept_config_identical_to_the_archive_gets_no_new_file_and_is_still_recorded",
     "sweep-modinst-identical-config-gets-new"),
    ("tools/mod-install.py",
     '                if action == "CONFIG-NEW" and dest.is_file() and sha(dest) == sha(staged[inner]):\n',
     '                if action == "CONFIG-NEW" and dest.is_file():\n',
     "tests/test_mod_install.py::test_an_existing_config_gets_a_new_file_beside_it",
     "sweep-modinst-different-config-gets-no-new"),
]]
# --- v4.0.0 ReSaver Plus native backend (port plan tasks 3-6): one twin per clause of every refusal --------------------
_RN = "tools/resaver_native.py"
_RNT = "tests/test_resaver_native.py::"
_RNG = "tests/test_resaver_native_guard.py::"
_RNB = "tests/test_resaver_backend_select.py::"
_RPB = "tools/resaver-plus/build.py"
_RPBT = "tests/test_resaver_plus_build.py::"
_SRN = "tools/skyguard/shell_rules.py"
MUTATIONS += [pytest.param(f, find, rep, test, id=i) for i, f, find, rep, test in [
    # the adapter: admission
    ("resaver-native-existing-ess-admitted", _RN, '        if os.path.lexists(requested.with_suffix(".ess")):',
     "        if False:", _RNT + "test_an_existing_output_ess_is_refused"),
    ("resaver-native-existing-skse-admitted", _RN, '        if os.path.lexists(requested.with_suffix(".skse")):',
     "        if False:", _RNT + "test_an_existing_output_skse_alone_is_refused"),
    ("resaver-native-store-output-admitted", _RN, "        if output == store or store in output.parents:",
     "        if False:", _RNT + "test_an_output_inside_the_permanent_save_store_is_refused"),
    # the adapter: pinning
    ("resaver-native-sha-unchecked", _RN, '    if digest(path) != info["sha256"]:', "    if False:",
     _RNT + "test_a_changed_executable_is_refused"),
    ("resaver-native-commit-unchecked", _RN, '    if info.get("upstream_commit") != UPSTREAM_COMMIT:', "    if False:",
     _RNT + "test_a_build_of_another_upstream_commit_is_refused"),
    ("resaver-native-build-seam-outside-test-mode", _RN,
     '    if _test_mode() and os.environ.get("RESAVER_NATIVE_BUILD"):', '    if os.environ.get("RESAVER_NATIVE_BUILD"):',
     _RNT + "test_the_build_seam_is_honoured_only_in_test_mode"),
    # the adapter: backup first (the choke point), and a backup that recorded nothing
    ("resaver-native-backup-after-preview", _RN,
     '        backups = backup(project, source, output, op)\n'
     '        preview = run(binary, [arg for arg in native if arg != "--apply"], binary_hash)\n',
     '        preview = run(binary, [arg for arg in native if arg != "--apply"], binary_hash)\n'
     '        backups = backup(project, source, output, op)\n',
     _RNT + "test_the_backup_happens_before_the_preview"),
    ("resaver-native-empty-backup-accepted", _RN, "    if not ids:", "    if False:",
     _RNT + "test_a_backup_that_records_nothing_is_a_failure"),
    # the adapter: receipts
    ("resaver-native-preview-unverified-accepted", _RN, '        if not preview.get("deltaVerified"):', "        if False:",
     _RNT + "test_a_preview_without_a_verified_delta_writes_nothing"),
    ("resaver-native-receipt-equality-dropped", _RN,
     '            raise RuntimeError("Applied target/delta receipt differs from preview; candidate output retained")',
     "            pass", _RNT + "test_an_applied_receipt_that_differs_from_the_preview_is_an_error"),
    ("resaver-native-unverified-write-accepted", _RN, '        if not result.get("verified"):', "        if False:",
     _RNT + "test_an_incomplete_applied_receipt_is_an_error[verified]"),
    ("resaver-native-no-file-accepted", _RN, '        if not result.get("wroteFile"):', "        if False:",
     _RNT + "test_an_incomplete_applied_receipt_is_an_error[wroteFile]"),
    ("resaver-native-source-change-accepted", _RN,
     '    if pair_hashes(source) != before:\n        raise RuntimeError("Source pair changed',
     '    if False:\n        raise RuntimeError("Source pair changed',
     _RNT + "test_a_source_changed_during_the_write_is_an_error"),
    ("resaver-native-cosave-check-dropped", _RN,
     '            raise RuntimeError("Co-save bytes or presence changed unexpectedly; candidate output retained")',
     "            pass", _RNT + "test_a_cosave_changed_by_the_write_is_an_error"),
    # the guard
    ("rule-S-RESAVER-NATIVE-DIRECT-ignores-dash-o", _SRN,
     'RESAVER_NATIVE_WRITES = ("-o", "--batch", "--batch-all", "--apply")',
     'RESAVER_NATIVE_WRITES = ("--batch", "--batch-all", "--apply")',
     _RNG + "test_a_direct_native_write_is_denied[dash-o]"),
    ("rule-S-RESAVER-NATIVE-DIRECT-ignores-gui", _SRN, "        if program in RESAVER_NATIVE_GUI:", "        if False:",
     _RNG + "test_a_direct_native_write_is_denied[gui]"),
    ("rule-S-RESAVER-NATIVE-DIRECT-trusts-unproven-args", _SRN,
     '            if any(_native_write_word(a) for a in argv) or any("$" in a or "`" in a for a in argv):',
     "            if any(_native_write_word(a) for a in argv):",
     _RNG + "test_a_direct_native_write_is_denied[unproven-args]"),
    ("rule-S-RESAVER-NATIVE-DIRECT-ignores-launchers", _SRN, "        if program in RESAVER_NATIVE_LAUNCHERS:",
     "        if False:", _RNG + "test_a_direct_native_write_is_denied[gui-via-start]"),
    ("rule-S-RESAVER-NATIVE-DIRECT-not-wired", _SRN, "    native = resaver_native_direct(command, parsed)\n    if native:",
     '    native = ""\n    if native:', _RNG + "test_a_direct_native_write_is_denied[structured-apply]"),
    ("rule-S-RESAVER-NATIVE-DIRECT-shadowed", "tools/skyguard/engine.py",
     'NEVER_SHADOW = NEVER_SHADOW | frozenset(("shell.S-RESAVER-NATIVE-DIRECT",))', "",
     _RNG + "test_the_rule_is_never_shadowed"),
    ("resaver-native-adapter-not-a-resaver-write", _SRN, "        if _base_name(script).startswith(RESAVER_ADAPTER):",
     "        if False:", _RNG + "test_the_adapter_run_directly_is_a_resaver_write[python-B]"),
    # backend selection: Java default, no fallback either way
    ("resaver-backend-native-falls-back", "tools/resaver-cli.sh", '  exec "${PY[@]}" -B "$HERE/resaver_native.py" "$@"',
     '  "${PY[@]}" -B "$HERE/resaver_native.py" "$@" && exit 0',
     _RNB + "test_explicit_native_without_a_build_is_an_error_and_java_does_not_run"),
    ("resaver-backend-default-native", "tools/resaver-cli.sh", 'BACKEND="${BACKEND:-java}"', 'BACKEND="${BACKEND:-native}"',
     _RNB + "test_the_default_is_java"),
    ("resaver-backend-writer-scan-reaches-java", "tools/resaver-cli.sh", 'if [ "${1:-}" = writer-scan ]; then',
     "if false; then", _RNB + "test_writer_scan_on_java_says_native_only_and_runs_nothing"),
    # build.py
    ("resaver-plus-build-source-check-skipped", _RPB, "    hashes = verify_sources(source, manifest)", "    hashes = {}",
     _RPBT + "test_one_byte_off_in_a_patched_file_fails_the_source_check"),
    ("resaver-plus-build-ctest-failure-passes", _RPB, '    if problems:\n        raise BuildError("tests did not all pass',
     '    if False:\n        raise BuildError("tests did not all pass',
     _RPBT + "test_a_test_failure_installs_nothing[a-target-fails]"),
    ("resaver-plus-build-fixtures-may-run", _RPB, '            if status not in ("Skipped", "Not Run"):',
     "            if False:", _RPBT + "test_a_test_failure_installs_nothing[fixtures-not-skipped]"),
    ("resaver-plus-build-unexpected-target-accepted", _RPB,
     '        problems.append("unexpected test {} ({})".format(name, status.lower()))', "        pass",
     _RPBT + "test_a_test_failure_installs_nothing[unexpected-target]"),
    ("resaver-plus-build-ctest-exit-ignored", _RPB, "    if code != 0 and not problems:", "    if False:",
     _RPBT + "test_a_test_failure_installs_nothing[ctest-exit]"),
    ("resaver-plus-build-pin-unchecked", _RPB, "    if head.strip().lower() != commit.lower():", "    if False:",
     _RPBT + "test_a_checkout_at_another_commit_is_refused_before_anything_builds"),
    ("resaver-plus-build-dirty-cache-used", _RPB, "        if dirty:", "        if False:",
     _RPBT + "test_a_dirty_source_cache_is_refused_and_left_alone"),
    ("resaver-plus-build-crlf-cache-used", _RPB, '        if conversion.strip().lower() != "false":', "        if False:",
     _RPBT + "test_a_cache_that_converts_line_endings_is_refused"),
    ("resaver-plus-build-patch-unchecked", _RPB, '        run([*GIT, "apply", "--check", p], cwd=source, env=env)',
     "        pass", _RPBT + "test_a_patch_that_does_not_apply_stops_before_the_build"),
    ("resaver-plus-build-addition-overwrites", _RPB, "        if target.exists():", "        if False:",
     _RPBT + "test_an_addition_may_not_replace_an_upstream_file"),
    ("resaver-plus-build-deep-work-path", _RPB, "    if len(str(work)) > MAX_WORK_PATH:", "    if False:",
     _RPBT + "test_a_work_folder_too_deep_for_msvc_is_refused_up_front"),
    ("resaver-plus-build-manifest-coverage-unchecked", _RPB, "    if patched_paths(patches) != want_patched:",
     "    if False:", _RPBT + "test_a_manifest_that_does_not_cover_the_patches_is_refused"),
    ("resaver-plus-build-gui-on", _RPB, '"-DRESAVERPP_TESTS=ON", "-DRESAVERPP_GUI=OFF"',
     '"-DRESAVERPP_TESTS=ON", "-DRESAVERPP_GUI=ON"',
     _RPBT + "test_a_good_build_installs_the_tested_executable_with_its_build_info"),
    # the release payload: nothing local-only or binary ships
    ("resaver-payload-contributions-allowed", "tools/check-release-payload.py", '    "tools/resaver-plus/contributions",\n',
     "", "tests/test_release_payload.py::test_every_check_can_fail_on_its_own_line[resaver-plus-contributions-shipped]"),
    ("resaver-payload-binaries-allowed", "tools/check-release-payload.py",
     'RESAVER_BINARY_SUFFIXES = (".exe", ".dll", ".pdb", ".lib", ".obj", ".ilk", ".exp")', "RESAVER_BINARY_SUFFIXES = ()",
     "tests/test_release_payload.py::test_every_check_can_fail_on_its_own_line[resaver-plus-binary-shipped]"),
    ("resaver-payload-pr-drafts-allowed", "tools/check-release-payload.py",
     '    drafts = sorted(p.relative_to(root).as_posix() for p in root.rglob("*-PR.md"))', "    drafts = []",
     "tests/test_release_payload.py::test_every_check_can_fail_on_its_own_line[resaver-plus-pr-draft-shipped]"),
]]


# H1 (user ruling 2026-10-25): a crashed, killed or timed-out Claude guard blocks the call; setup refuses a Claude Code
# too old for the key. One twin per guard registration and per clause of the version gate.
_H1S = "agent/targets/claude/settings.json"
_H1T = "tests/test_hook_failure_blocks.py::"
MUTATIONS += [pytest.param(*m[:4], id=m[4]) for m in [
    (_H1S, '"Checking command safety...",\n            "onFailure": "block"', '"Checking command safety..."',
     _H1T + "test_every_guard_hook_blocks_on_failure_and_nothing_else_does[agent/targets/claude/settings.json]",
     "h1-protect-bash-fails-open"),
    (_H1S, '"Checking file protection...",\n            "onFailure": "block"', '"Checking file protection..."',
     _H1T + "test_every_guard_hook_blocks_on_failure_and_nothing_else_does[agent/targets/claude/settings.json]",
     "h1-protect-files-fails-open"),
    (_H1S, '"Checking DevBench call...",\n            "onFailure": "block"', '"Checking DevBench call..."',
     _H1T + "test_every_guard_hook_blocks_on_failure_and_nothing_else_does[agent/targets/claude/settings.json]",
     "h1-protect-mcp-fails-open"),
    ("setup.sh", "sort -t. -k1,1n -k2,2n -k3,3n | head -n1)", "sort | head -n1)",
     _H1T + "test_a_numerically_older_minor_is_refused_not_compared_as_text",
     "h1-version-compared-as-text"),
    ("setup.sh", '            echo "Update it first (claude update), then run setup.sh again. Nothing was changed." >&2\n'
                 "            exit 3\n",
     '            echo "Update it first (claude update), then run setup.sh again. Nothing was changed." >&2\n',
     _H1T + "test_an_older_claude_code_is_refused_before_anything_is_written",
     "h1-old-claude-not-refused"),
    ("setup.sh", 'CLAUDE_MIN="2.1.295"', 'CLAUDE_MIN="2.1.290"',
     _H1T + "test_an_older_claude_code_is_refused_before_anything_is_written",
     "h1-minimum-below-the-key"),
]]

# CI5: the native backend remains on the qualified VS 2022 runner.
MUTATIONS.append(pytest.param(
    ".github/workflows/ci.yml", "    runs-on: windows-2022\n", "    runs-on: windows-latest\n",
    "tests/test_workflows.py::test_resaver_plus_uses_the_qualified_vs2022_runner",
    id="ci5-resaver-runner-unpinned"))

# New retention classes: every eligibility/refusal clause has a synthetic behavioral witness.
_HKD = "tools/housekeep.py"
_HKDT = "tests/test_housekeep_derived.py::"
MUTATIONS += [pytest.param(_HKD, before, after, _HKDT + test, id="hk-derived-" + name)
              for name, before, after, test in [
    ("etl-protected", '        if low.endswith(".etl"):', '        if False:',
     "test_etl_cannot_be_recreatable_or_a_duplicate"),
    ("xml-shape", '    if path.suffix.lower() != ".xml" or not (path.name.lower() == "events.xml"\n'
                  '                                                   or path.parent.name.lower() == "xml-decoder"):',
     '    if False:', "test_other_xml_is_kept[unrelated-xml]"),
    ("parent-source", '    for folder in (path.parent, path.parent.parent):', '    for folder in (path.parent,):',
     "test_decoded_trace_with_a_source_is_derived[parent-xml-decoder/trace.xml]"),
    ("source-needed", '    return None\n\n\nVERSIONED_STAGE', '    return path\n\n\nVERSIONED_STAGE',
     "test_other_xml_is_kept[no-source]"),
    ("plain-path", '    return all(not stat.S_ISLNK(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400',
     '    return all(not getattr(s, "st_file_attributes", 0) & 0x400',
     "test_nonplain_paths_are_refused_without_link_privileges[symlink]"),
    ("reparse-path", '    return all(not stat.S_ISLNK(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400',
     '    return all(not stat.S_ISLNK(s.st_mode)',
     "test_nonplain_paths_are_refused_without_link_privileges[reparse]"),
    ("apply-source", '        if not source.is_file() or not plain_path(source) or derived_source(p) is None:',
     '        if False:', "test_derived_apply_rechecks_the_source_bytes_and_activity[source]"),
    ("apply-xml", '    if action["class"] in ("derived", "recreatable", "build", "link", "gzip"):',
     '    if action["class"] in ("recreatable", "build", "link", "gzip"):',
     "test_derived_apply_rechecks_the_source_bytes_and_activity[xml]"),
    ("apply-activity", '    if action["class"] in ("derived", "recreatable", "build", "link"):',
     '    if action["class"] in ("recreatable", "build", "link"):',
     "test_derived_apply_rechecks_the_source_bytes_and_activity[activity]"),
    ("default-age", '    "superseded_after_days": 30,', '    "superseded_after_days": 0,',
     "test_only_a_lower_numeric_sibling_is_superseded"),
    ("numeric-version", '    numbers = tuple(int(n) for n in match[2].split("."))',
     '    numbers = tuple(match[2].split("."))', "test_only_a_lower_numeric_sibling_is_superseded"),
    ("version-required", '    if match is None:\n        return None',
     '    if match is None:\n        return (name.casefold(), (0, 0, 0, 0))',
     "test_only_a_lower_numeric_sibling_is_superseded"),
    ("configured-direct-child", '    if path.parent != root or root not in [_abs(cfg, r) for r in cfg["staging_roots"]]:',
     '    if False:', "test_staging_apply_rechecks_eligibility[config]"),
    ("idle-age", '    if latest > now - max(cfg["superseded_after_days"] * 86400, cfg["active_hours"] * 3600):',
     '    if False:', "test_staging_eligibility_is_narrow[recent]"),
    ("active-age", 'max(cfg["superseded_after_days"] * 86400, cfg["active_hours"] * 3600)',
     'min(cfg["superseded_after_days"] * 86400, cfg["active_hours"] * 3600)',
     "test_staging_eligibility_is_narrow[active]"),
    ("same-name", 'and v[0] == version[0] and v[1] > version[1]', 'and v[1] > version[1]',
     "test_staging_eligibility_is_narrow[other-name]"),
    ("strictly-higher", 'and v[1] > version[1] for other in root.iterdir())',
     'and v[1] >= version[1] for other in root.iterdir())', "test_staging_eligibility_is_narrow[equal]"),
    ("explicit-list", '    if path.name not in listed and not higher:', '    if not higher:',
     "test_exact_superseded_list_can_retire_the_only_version"),
    ("invalid-list", '            return "invalid _superseded list"',
     '            pass', "test_invalid_superseded_list_keeps_staging"),
    ("rollback-reference", '    refs = manifest_references(cfg)', '    refs = set()',
     "test_manifest_references_or_uncertainty_keep_staging[json]"),
    ("rollback-writer", '                        remember(line.split(": ", 1)[1])', '                        pass',
     "test_manifest_references_or_uncertainty_keep_staging[writer-text]"),
    ("rollback-payload", '                if "backups" in Path(dp).relative_to(root).parts and not name.endswith(".files.txt"):',
     '                if False:', "test_operation_manifests_are_read_without_misreading_backed_up_configs[False]"),
    ("manifest-uncertainty", '                why = "staging protection unavailable ({}: {})".format(type(error).__name__, error)',
     '                why = ""', "test_manifest_references_or_uncertainty_keep_staging[unreadable-json]"),
    ("manifest-relative", '        if not Path(value).is_absolute():', '        if False:',
     "test_manifest_references_or_uncertainty_keep_staging[relative]"),
    ("empty-manifest", '                if not text.strip():', '                if False:',
     "test_unknown_manifest_layout_refuses_staging[empty]"),
    ("unknown-text", '                        raise ValueError("unknown manual-install manifest row")',
     '                        pass', "test_manifest_references_or_uncertainty_keep_staging[unknown-text]"),
    ("apply-stage", '            why = superseded_reason(p, Path(action["staging_root"]), cfg, guard, now)',
     '            why = ""', "test_staging_apply_rechecks_eligibility[manifest]"),
    ("apply-fingerprint", '    if action["class"] in ("launch", "snapshot", "scratch", "superseded"):',
     '    if action["class"] in ("launch", "snapshot", "scratch"):',
     "test_staging_apply_rechecks_eligibility[contents]"),
    ("protected-tree", '    why = guard.why_protected(path) or protected_inside(guard, path)', '    why = ""',
     "test_protected_staging_content_keeps_the_whole_folder[known-good.txt]"),
    ("staging-tracked", '                self.tracked.update(tracked_files(root))', '                pass',
     "test_staging_preserves_tracked_files_and_linked_evidence[tracked]"),
    ("staging-evidence", '    for r in cfg["agent_output"] + cfg["staging_roots"]:',
     '    for r in cfg["agent_output"]:', "test_staging_preserves_tracked_files_and_linked_evidence[linked]"),
    ("directory-activity", '            latest = max(latest, entry.stat().st_mtime)',
     '            latest = max(latest, entry.stat().st_mtime) if entry.is_file() else latest',
     "test_a_new_empty_subdirectory_keeps_staging_active"),
    ("nested-checkout", '        if any(name.casefold() == ".git" for name in dirs + files):',
     '        if False:', "test_a_nested_git_checkout_is_never_staging_cleanup"),
    ("count-once", '            if wants("superseded") and any(_strictly_inside(_real(f), _real(_abs(cfg, r)))',
     '            if False and any(_strictly_inside(_real(f), _real(_abs(cfg, r)))',
     "test_overlapping_agent_and_staging_roots_are_counted_once"),
    ("valid-age", '    if isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days) or days < 0:',
     '    if False:', "test_invalid_staging_age_is_refused"),
    ("valid-roots", '    if not isinstance(cfg["staging_roots"], list) or any(not isinstance(p, str) or not p for p in cfg["staging_roots"]):',
     '    if False:', "test_invalid_or_overlapping_staging_roots_are_refused"),
    ("overlap-roots", '    if any(a == b or _strictly_inside(a, b) or _strictly_inside(b, a)\n'
                      '           for i, a in enumerate(stage_roots) for b in stage_roots[i + 1:]):',
     '    if False:', "test_invalid_or_overlapping_staging_roots_are_refused"),
]]

# H1 ordered-upgrade integration: current guard policies and exact historical definitions.
_H1O = "tools/skyguard/ordered_upgrade.py"
_H1OC = 'CLAUDE_BLOCKING = frozenset(("protect-bash.sh", "protect-files.sh", "protect-mcp.sh"))'
_H1OU = _H1T + "test_ordered_upgrade_uses_the_shipped_guard_failure_policy"
MUTATIONS += [pytest.param(_H1O, find, rep, test, id=ident) for find, rep, test, ident in [
    (_H1OC, 'CLAUDE_BLOCKING = frozenset(("protect-files.sh", "protect-mcp.sh"))',
     _H1OU, "h1-ordered-bash-fails-open"),
    (_H1OC, 'CLAUDE_BLOCKING = frozenset(("protect-bash.sh", "protect-mcp.sh"))',
     _H1OU, "h1-ordered-files-fails-open"),
    (_H1OC, 'CLAUDE_BLOCKING = frozenset(("protect-bash.sh", "protect-files.sh"))',
     _H1OU, "h1-ordered-mcp-fails-open"),
    ('        handler["onFailure"] = "block"', '        handler["onFailure"] = "ignore"',
     _H1OU, "h1-ordered-wrong-failure-policy"),
    ("    if script in CLAUDE_BLOCKING:", "    if True:",
     _H1OU, "h1-ordered-backup-hooks-block"),
    ('            handler.pop("onFailure", None)', "            pass",
     "tests/test_ordered_policy.py::test_published_legacy_groups_migrate_to_current_blocking_guards[original-v3.9.1]",
     "h1-ordered-legacy-schema-not-recognized"),
    ("    groups = [*current, *published, installed]", "    groups = [*published, installed]",
     "tests/test_ordered_policy.py::test_published_legacy_groups_migrate_to_current_blocking_guards[original-pre-h1]",
     "h1-ordered-previous-v4-schema-not-recognized"),
]]

# Task 7 A2: unreadable authority is never an empty live set.
_RR7 = "tests/test_review_recovery.py::"
MUTATIONS += [pytest.param("tools/skyguard/recovery.py", find, rep, test, id="review-recovery-" + ident)
              for find, rep, test, ident in [
    ('    except FileNotFoundError:\n        return []', '    except OSError:\n        return []',
     _RR7 + 'test_unreadable_manifest_is_not_an_empty_snapshot_list', 'read-error-not-empty'),
    ('            if not raws:\n                raise OSError("the recovery manifest became empty")', '            if False:\n                raise OSError("the recovery manifest became empty")',
     _RR7 + 'test_manifest_authority_must_remain_readable_under_the_prune_lock[emptied]', 'empty-under-lock'),
    ('        audit.log(HOOK, "FAILED", "could not read the recovery authority -- no config snapshot was recorded")\n        return',
     '        raise', _RR7 + 'test_unreadable_authority_stops_config_snapshot_without_blocking_the_hook', 'snapshot-error-contained'),
]]

# Task 7 P5/HK1: incomplete inspection refuses; uncertain activity is active.
_HKR7 = "tests/test_review_housekeep.py::"
MUTATIONS += [pytest.param("tools/housekeep.py", find, rep, test, id="review-housekeep-" + ident)
              for find, rep, test, ident in [
    ('        newest = folder.stat().st_mtime', '        newest = 0.0',
     _HKR7 + 'test_uncertain_or_recent_directory_activity_keeps_derived_trace[plan-root]', 'root-activity'),
    ('        for dp, dirs, files in os.walk(folder, onerror=walk_error):', '        for dp, dirs, files in os.walk(folder):',
     _HKR7 + 'test_uncertain_or_recent_directory_activity_keeps_derived_trace[plan-walk]', 'walk-activity'),
    ('            for name in dirs + files:', '            for name in files:',
     _HKR7 + 'test_uncertain_or_recent_directory_activity_keeps_derived_trace[plan-empty-child]', 'directory-activity'),
    ('    except OSError:\n        return float("inf")', '    except OSError:\n        return 0.0',
     _HKR7 + 'test_uncertain_or_recent_directory_activity_keeps_derived_trace[apply-stat]', 'unknown-activity'),
    ('        files = tuple(walk_files(real, strict=True))', '        files = tuple(walk_files(real))',
     _HKR7 + 'test_recycle_refuses_an_uninspectable_descendant[stat]', 'strict-inspection-wired'),
    ('onerror=walk_error if strict else None', 'onerror=None',
     _HKR7 + 'test_recycle_refuses_an_uninspectable_descendant[walk]', 'strict-walk-error'),
    ('        if strict:\n            os.stat(dp)', '        if False:\n            os.stat(dp)',
     _HKR7 + 'test_recycle_refuses_an_uninspectable_descendant[stat]', 'strict-metadata-gate'),
    ('            os.stat(dp)\n            for name in dirs + fn:', '            pass\n            for name in dirs + fn:',
     _HKR7 + 'test_strict_recycle_inspection_checks_root_metadata[walk-root]', 'strict-walk-root-stat'),
    ('                os.stat(os.path.join(dp, name))', '                pass',
     _HKR7 + 'test_recycle_refuses_an_uninspectable_descendant[stat]', 'strict-entry-stat'),
    ('        return "unreadable tree or file metadata (inspection incomplete)"', '        return ""',
     _HKR7 + 'test_recycle_refuses_an_uninspectable_descendant[walk]', 'incomplete-refused'),
    ('                    elif re.fullmatch(r"# KEPT: [0-9a-fA-F]{64}  .+", line):',
     '                    elif False:', _HKDT + 'test_kept_install_records_remain_understood_protection[valid]', 'kept-record-understood'),
    ('                    elif re.fullmatch(r"# KEPT: [0-9a-fA-F]{64}  .+", line):',
     '                    elif re.fullmatch(r"# KEPT: [0-9a-fA-F]{63,64}  .+", line):',
     _HKDT + 'test_kept_install_records_remain_understood_protection[bad-hash]', 'kept-record-shape'),
    ('                        remember(line.split("  ", 1)[1])', '                        pass',
     _HKDT + 'test_kept_install_records_remain_understood_protection[valid]', 'kept-record-path'),
]]
