#!/usr/bin/env python3
"""Break each guard on purpose, in a throwaway copy, and require its NAMED check to go red.

    python native/tests/mutations.py --prefix <sdk prefix> --work <folder> [--only NAME]

Baseline first: an unmutated copy must pass, or no mutation result means anything. A mutant that fails for any reason
other than its named check counts as not killed: "the suite failed" is not enough. Synthetic fixtures only; no game code.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sdk", NATIVE / "sdk" / "sdk.py")
sdk = importlib.util.module_from_spec(spec)
sys.modules["sdk"] = sdk
spec.loader.exec_module(sdk)

ASM = "observation/asm/bridge_common.inc"
HOOK = "observation/include/skyrim_native/entry_hook.hpp"
FORWARD = "observation/include/skyrim_native/forward.hpp"
GATE = "observation/include/skyrim_native/capture_gate.hpp"
XSTATE = "observation/include/skyrim_native/xstate.hpp"
ENTRY_TEST = "observation/tests/entry_tests.cpp"
SDK = "sdk/sdk.py"
RECEIPTS = "provenance/receipts.py"
IMAGE = "provenance/native_image.py"
CONTEXT = "context/context_store.py"

# name, kind, file, [(old, new)...], the check that must go red
NATIVE_MUTANTS = [
    ("duplicate-original", "bridge-tests", ASM, [("    call QWORD PTR original\n", "    call QWORD PTR original\n    RESTORE_CONTEXT\n    call QWORD PTR original\n")],
     "FAIL wrapped original increments exactly once"),
    ("lost-last-error", "bridge-tests", ASM, [("    call QWORD PTR __imp_SetLastError\n", "    nop\n")],
     "FAIL original LastError preserved across observers"),
    ("lost-return-rax", "bridge-tests", ASM, [("    pop QWORD PTR [rsp+0A0h]\n    mov [rsp+20h],rax\n", "    pop QWORD PTR [rsp+0A0h]\n    mov QWORD PTR [rsp+20h],0\n")],
     "FAIL original return GPR state survives after observer"),
    ("legacy-only-state", "bridge-tests", ASM, [("    mov eax,DWORD PTR SkyrimNativeXStateMask\n    mov edx,DWORD PTR SkyrimNativeXStateMask+4\n    xsave64 [r10]\n", "    mov eax,3\n    xor edx,edx\n    xsave64 [r10]\n")],
     "FAIL enabled extended-state component"),
    ("unsafe-admission", "bridge-tests", XSTATE, [("    return f.xsave && f.osxsave && (f.enabled&3)==3 && !(f.enabled&~7ull) &&\n        !(f.enabled&~f.supported) && (!(f.enabled&4) || f.avx) &&\n        f.bytes>=576 && f.bytes<=xstateCapacity;", "    return f.xsave;")],
     "FAIL missing OS XSAVE refuses"),
    ("wrong-saved-register", "entry-tests", ENTRY_TEST, [("{1, 5, 3, 0, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0};", "{1, 5, 3, 0, 5, 0xF0, 3, 0x70, 2, 0x60, 0, 0};")],
     "FAIL the qualified unwind shapes are accepted"),
    ("wrong-unwind-written", "entry-tests", HOOK, [("target = at; replacement = detour; site = descriptor;", "target = at; replacement = detour; site = descriptor; if (site.unwind[2] == 3) site.unwind[5] = 0xF0;")],
     "FAIL unwind preserves displaced nonvolatile saves"),
    ("lost-incoming-error", "entry-tests", FORWARD, [("    SetLastError(incomingError);", "    (void)incomingError;")],
     "FAIL before observer preserves original incoming LastError"),
    ("forward-twice", "entry-tests", FORWARD, [("original(argument);", "original(argument);counts.forwarded.fetch_add(1);original(argument);")],
     "FAIL foreign caller forwards once"),
    ("stock-epilogue-continuation", "entry-tests", HOOK, [
        ("wanted[5] = 0x49; wanted[6] = 0xBB; std::memcpy(wanted.data() + 7, &continuation, 8);\n            wanted[15] = 0x41; wanted[16] = 0xFF; wanted[17] = 0xE3; wanted[18] = 0x90;",
         "wanted[5] = 0xFF; wanted[6] = 0x25; std::memcpy(wanted.data() + 11, &continuation, 8);"),
        ("jump{0x49, 0xBB, 0, 0, 0, 0, 0, 0, 0, 0, 0x41, 0xFF, 0xE3, 0x90};", "jump{0xFF, 0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};"),
        ("std::memcpy(jump.data() + 2, &continuation, 8);", "std::memcpy(jump.data() + 6, &continuation, 8);")],
     "FAIL unwind recovers original return at each displaced instruction boundary"),
    ("source-bytes-unchecked", "entry-tests", HOOK, [("if (!bytesMatch(target, site.expected, 0, 0, read)) { status = kSourceMismatch; return false; }", "")],
     "FAIL every mismatched source byte refuses before publication"),
    ("unwind-metadata-unchecked", "entry-tests", HOOK, [("!read(trampoline + 36, actual.data(), actual.size()) || actual != site.unwind", "!read(trampoline + 36, actual.data(), actual.size())")],
     "FAIL retained unwind metadata mutation invalidates evidence"),
    ("r11-claim-unchecked", "entry-tests", HOOK, [("!d.r11IsNotAnInput || !d.noStackArguments", "!d.noStackArguments")],
     "FAIL a descriptor must claim R11 is not an input"),
    ("stack-args-claim-unchecked", "entry-tests", HOOK, [("!d.r11IsNotAnInput || !d.noStackArguments ||", "!d.r11IsNotAnInput ||")],
     "FAIL a descriptor must claim there are no stack arguments"),
    ("rbp-claim-unchecked", "entry-tests", HOOK, [(" || !d.incomingRbpUnused) return false;", ") return false;")],
     "FAIL a descriptor must claim incoming RBP is unused"),
    ("unwind-not-matched-to-bytes", "entry-tests", HOOK, [("    return unwindMatchesBytes(d.expected, d.unwind);\n", "    return true;\n")],
     "FAIL unwind codes naming other registers refused"),
    ("unqualified-shape-accepted", "entry-tests", HOOK, [("        if (!homeStore) return false;\n", "        (void)homeStore;\n")],
     "FAIL a stack-changing shape we have not qualified refused"),
    ("unset-mask-accepted", "entry-tests", HOOK, [("        if ((stateMask & 3) != 3) { status = kBadStateMask; return false; }\n", "        (void)stateMask;\n")],
     "FAIL a bridge with an unset state mask refuses"),
    ("bridge-unwind-wrong-save-slot", "bridge-tests", ASM, [("    .savereg r12,80h\n", "    .savereg r12,88h\n")],
     "FAIL bridge unwind restores the caller's nonvolatile state"),
    ("bridge-unwind-wrong-xmm-slot", "bridge-tests", ASM, [("    .savexmm128 xmm9,0F0h\n", "    .savexmm128 xmm9,100h\n")],
     "FAIL bridge unwind restores the caller's nonvolatile state"),
    ("no-xrstor", "bridge-tests", ASM, [("    xrstor64 [r10]\n", "    nop\n")],
     "FAIL all XMM0..15"),
    ("no-popfq", "bridge-tests", ASM, [("    push QWORD PTR [rsp+0A0h]\n    popfq\n", "    nop\n")],
     "FAIL caller arithmetic flags"),
    ("bridged-after-on-exception", "entry-tests", ENTRY_TEST, [("bridgeBefore == 3 && bridgeAfter == 2,", "bridgeBefore == 3 && bridgeAfter == 3,")],
     "FAIL no after observation and no replay on the original's exception"),
    ("patch-without-revalidation", "entry-tests", HOOK, [("if (!created || !bridgeValid(read) || !bytesMatch(target, site.expected, 0, 0, read)) { status = kEnableRevalidation; return false; }", "if (!created) { status = kEnableRevalidation; return false; }")],
     "FAIL failed final source revalidation does not attempt a patch"),
    ("stale-ticket-arms", "gate-tests", GATE, [("return state.compare_exchange_strong(expected, desired, std::memory_order_acq_rel, std::memory_order_acquire);", "state.store(desired); return true;")],
     "FAIL preload between request read and commit invalidates the arm ticket"),
    ("deadline-ignored", "gate-tests", GATE, [(" && now < (current >> kDeadlineShift)", " && (now, true)")],
     "FAIL deadline holds on the hot path"),
    ("dropped-drain", "gate-tests", GATE, [("return admits(now) && countThenRecheck(now);", "return admits(now);")],
     "FAIL admitted producer is counted"),
    ("refused-producer-counts", "gate-tests", GATE, [("return admits(now) && countThenRecheck(now);", "return countThenRecheck(now);")],
     "FAIL a refused entry writes nothing to the gate"),
    ("admit-before-count", "gate-tests", GATE, [("        if (admits(now)) return true;\n        active.fetch_sub(1, std::memory_order_release);\n        return false;", "        (void)now;\n        return true;")],
     "FAIL a producer that counts itself after the stop is refused and uncounted"),
    ("admit-before-count-raced", "gate-tests", GATE, [("return admits(now) && countThenRecheck(now);", "if (!admits(now)) return false;\n        for (int spin = 0; spin < 200; ++spin) std::this_thread::yield();\n        active.fetch_add(1, std::memory_order_seq_cst);\n        return true;"), ("#include <cstdint>\n", "#include <cstdint>\n#include <thread>\n")],
     "FAIL a drained gate has no admitted producer left"),
    ("arm-with-producer-inside", "gate-tests", GATE, [("        if (!drained()) return false;\n", "")],
     "FAIL no new capture while an earlier producer is still inside"),
    ("drain-claimed-early", "gate-tests", GATE, [("return active.load(std::memory_order_seq_cst) == 0;", "return true;")],
     "FAIL disarm does not claim a drain while a producer is inside"),
    ("load-payload-truthy", "gate-tests", GATE, [("return encoded == 1 && size == 1;", "(void)size; return encoded != 0;")],
     "FAIL unexpected payload does not arm"),
    ("unload-after-publication", "gate-tests", GATE, [("try { start(); } catch (...) { failure(); }\n    return true;", "try { start(); } catch (...) { failure(); return false; }\n    return true;")],
     "FAIL start failure keeps the DLL loaded after callback publication"),
]

PYTHON_MUTANTS = [
    ("ctx-case-rename-deleted", "test_context", CONTEXT, [('        original = same_file_other_case(game, relative, by_lower)\n', '        original = None\n')],
     "test_a_case_only_rename_is_not_a_new_file"),
    ("ctx-lookalike-treated-as-same", "test_context", CONTEXT, [(' and os.path.samefile(game / relative, game / original)', '')],
     "test_a_name_that_only_folds_alike_is_a_new_file"),
    ("ctx-names-not-restored", "test_context", CONTEXT, [('            put_name_back(game, relative)\n', '            pass\n')],
     "test_a_case_only_rename_is_not_a_new_file"),
    ("ctx-folder-rename-unseen", "test_context", CONTEXT, [('    report["renamed"] += [relative for relative in manifest["folders"] if (game / relative).is_dir() and actual_name(game, relative) != relative]\n', '')],
     "test_a_case_rename_of_a_folder_comes_back"),
    ("ctx-attempts-forgotten", "test_context", CONTEXT, [('for n in range(1, attempt + 1):', 'for n in [attempt]:')],
     "test_receipt_keeps_what_an_interrupted_attempt_saw"),
    ("ctx-altered-forgotten-across-attempts", "test_context", CONTEXT, [('for name, values in seen.items()}', 'for name, values in seen.items() if name != "altered_additions"}, "altered_additions": before["altered_additions"]')],
     "test_receipt_keeps_an_altered_addition_seen_by_an_interrupted_attempt"),
    ("ctx-delete-through-junction", "test_context", CONTEXT, [('        require_plain_ancestors(game, game / row["target"])\n', '')],
     "test_restore_will_not_delete_through_a_junction"),
    ("ctx-slot-write-crash-forgets", "test_context", CONTEXT, [('    except Refused:\n        # A refusal means the slots were not written.', '    except BaseException:\n        # A refusal means the slots were not written.')],
     "test_interrupted_slot_write_does_not_strand_ownership"),
    ("ctx-finished-restore-redone", "test_context", CONTEXT, [('    if finished and finished["verified"]:\n', '    if False:\n')],
     "test_a_finished_restore_is_never_redone"),
    ("ctx-unfinished-receipt-trusted", "test_context", CONTEXT, [('    if finished and finished["verified"]:\n', '    if finished:\n')],
     "test_an_unfinished_receipt_does_not_stop_the_retry"),
    ("ctx-read-only-folder-stuck", "test_context", CONTEXT, [('                os.chmod(path, stat.S_IWRITE)\n                path.rmdir()\n', '                path.rmdir()\n')],
     "test_read_only_new_folder_is_removed"),
    ("ctx-stuck-folder-passes", "test_context", CONTEXT, [('report["created_folders"] = [relative for relative in folders if relative.lower() not in known_folders]', 'report["created_folders"] = []')],
     "test_a_new_folder_that_cannot_go_is_reported_not_passed"),
    ("ctx-stray-marker-left", "test_context", CONTEXT, [('        stray.unlink()\n', '        pass\n')],
     "test_stray_marker_temporary_is_cleared_with_the_context"),
    ("ctx-marker-shape-unchecked", "test_context", CONTEXT, [('if {"owner", "run", "state"} - set(record):', 'if False:')],
     "test_a_marker_of_the_wrong_shape_is_unreadable"),
    ("ctx-record-shape-unchecked", "test_context", CONTEXT, [('if {"owner", "run", "phase", "slots", "game", "store"} - set(record):', 'if False:')],
     "test_an_active_record_of_the_wrong_shape_is_unreadable"),
    ("ctx-save-as-addition", "test_context", CONTEXT, [('        reject_saves([addition["target"]])\n', '')],
     "test_an_addition_may_not_be_a_save"),
    ("ctx-cold-second-check", "test_context", CONTEXT, [('        require_cold(profile["cold_processes"])    # after ownership, so a competing launch cannot slip between\n', '')],
     "test_process_started_between_the_checks_is_seen_after_taking_ownership"),
    ("ctx-cold-ignored", "test_context", CONTEXT, [('    if alive:\n', '    if False:\n')],
     "test_running_process_is_refused"),
    ("ctx-process-unknown-means-none", "test_context", CONTEXT, [('raise Refused("cannot tell which processes are running: {}".format(error)) from error', 'return []')],
     "test_unknown_process_state_is_refused"),
    ("ctx-writer-not-seen", "test_context", CONTEXT, [('if handle == INVALID_HANDLE or handle is None:', 'if False:')],
     "test_file_held_open_by_a_writer_is_refused"),
    ("ctx-junction-followed", "test_context", CONTEXT, [('return bool(os.lstat(path).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)', 'return False')],
     "test_junction_in_scope_is_refused"),
    ("ctx-addition-through-link", "test_context", CONTEXT, [('        require_plain_ancestors(game, target)\n', '')],
     "test_addition_through_a_junction_is_refused"),
    ("ctx-hard-link-allowed", "test_context", CONTEXT, [('elif os.lstat(path).st_nlink != 1:', 'elif False:')],
     "test_hard_link_in_scope_is_refused"),
    ("ctx-escape-allowed", "test_context", CONTEXT, [(' or ".." in pure.parts', '')],
     "test_path_escapes_are_refused"),
    ("ctx-store-in-game", "test_context", CONTEXT, [('if path == game or game in path.parents:', 'if False:')],
     "test_store_or_state_inside_the_game_is_refused"),
    ("ctx-existing-record-overwritten", "test_context", CONTEXT, [('os.rename(temporary, path)', 'os.replace(temporary, path)')],
     "test_second_state_folder_on_the_same_game_is_refused"),
    ("ctx-slot-stolen", "test_context", CONTEXT, [('allowed = (None, owner) if release else (None,)\n', 'allowed = (None, owner, "other", "thief")\n')],
     "test_foreign_slot_is_refused_and_nothing_changes"),
    ("ctx-own-slot-taken-over", "test_context", CONTEXT, [('allowed = (None, owner) if release else (None,)\n', 'allowed = (None, owner)\n')],
     "test_slot_already_held_by_this_owner_is_refused_and_kept"),
    ("ctx-release-needs-holder", "test_context", CONTEXT, [('allowed = (None, owner) if release else (None,)\n', 'allowed = (owner,) if release else (None,)\n')],
     "test_crash_after_slots_released_still_finishes"),
    ("ctx-lock-elsewhere", "test_context", CONTEXT, [('path.with_suffix(".gateway.lock")', 'path.with_suffix(".other.lock")')],
     "test_the_lock_is_the_one_other_owners_use"),
    ("ctx-slot-keys-empty", "test_context", CONTEXT, [('if not slots.get("file") or not slots.get("keys"):', 'if not slots.get("file"):')],
     "test_slots_must_name_keys"),
    ("ctx-slot-settings-unknown", "test_context", CONTEXT, [('        if unknown:\n            raise Refused("unknown slots key', '        if False:\n            raise Refused("unknown slots key')],
     "test_slots_accept_no_other_settings"),
    ("ctx-reference-trusted", "test_context", CONTEXT, [('        if sha256(blob) != digest:\n            raise Refused("reference {} is damaged".format(digest))\n', '')],
     "test_damaged_reference_is_refused_at_capture"),
    ("ctx-edit-any-count", "test_context", CONTEXT, [('if data.count(old) != 1:', 'if False:')],
     "test_edit_that_does_not_match_exactly_once_changes_nothing"),
    ("ctx-edit-outside-capture", "test_context", CONTEXT, [('if relative not in captured["files"]:', 'if False:')],
     "test_edit_outside_the_captured_roots_is_refused"),
    ("ctx-addition-hash-unchecked", "test_context", CONTEXT, [(' or sha256(source) != addition["sha256"]', '')],
     "test_addition_with_the_wrong_hash_is_refused"),
    ("ctx-addition-overwrites", "test_context", CONTEXT, [('if os.path.lexists(target):\n            raise Refused', 'if False:\n            raise Refused')],
     "test_addition_over_an_existing_file_is_refused"),
    ("ctx-manifest-tamper-allowed", "test_context", CONTEXT, [('recorded.read_text(encoding="ascii").strip() != sha256(path)', 'False')],
     "test_tampered_manifest_is_refused"),
    ("ctx-changed-not-kept", "test_context", CONTEXT, [('keep_evidence(target, evidence / "changed" / relative)', 'pass')],
     "test_changed_file_is_kept_as_evidence_before_restoring"),
    ("ctx-created-left-live", "test_context", CONTEXT, [('            keep_evidence(path, evidence / "created" / relative)\n            remove_file(path)\n', '            keep_evidence(path, evidence / "created" / relative)\n')],
     "test_file_created_during_the_test_is_moved_to_evidence"),
    ("ctx-created-not-kept", "test_context", CONTEXT, [('            keep_evidence(path, evidence / "created" / relative)\n', '')],
     "test_file_created_during_the_test_is_moved_to_evidence"),
    ("ctx-times-not-restored", "test_context", CONTEXT, [('replace_verified(blob, target, row["sha256"], row["mtime_ns"], row["read_only"])', 'replace_verified(blob, target, row["sha256"], None, row["read_only"])')],
     "test_full_cycle_restores_exact_bytes_and_times"),
    ("ctx-read-only-lost", "test_context", CONTEXT, [('replace_verified(blob, target, row["sha256"], row["mtime_ns"], row["read_only"])', 'replace_verified(blob, target, row["sha256"], row["mtime_ns"])')],
     "test_read_only_original_comes_back_read_only"),
    ("ctx-attribute-not-restored", "test_context", CONTEXT, [('            if row["read_only"]:\n                os.chmod(target, stat.S_IREAD)\n', '')],
     "test_cleared_read_only_attribute_on_an_unedited_file_is_restored"),
    ("ctx-attribute-not-compared", "test_context", CONTEXT, [('if (not info.st_mode & stat.S_IWRITE) != row["read_only"]:', 'if False:')],
     "test_cleared_read_only_attribute_on_an_unedited_file_is_restored"),
    ("ctx-other-owner-restores", "test_context", CONTEXT, [('if record["owner"] != owner:', 'if False:')],
     "test_another_owner_cannot_restore"),
    ("ctx-other-game-restores", "test_context", CONTEXT, [('    require_this_game(game, record)\n    if not same_path(store', '    if not same_path(store')],
     "test_restore_against_another_game_is_refused"),
    ("ctx-other-game-verifies", "test_context", CONTEXT, [('    require_this_game(game, active)\n', '')],
     "test_verify_against_another_game_is_refused"),
    ("ctx-other-store-restores", "test_context", CONTEXT, [('if not same_path(store, record["store"]):', 'if False:')],
     "test_restore_from_another_store_is_refused"),
    ("ctx-no-game-marker", "test_context", CONTEXT, [('create_whole(marker, {"owner": owner, "run": run, "state": str(state)}, "a context is already active on this game: restore it first")', 'pass')],
     "test_the_game_marker_exists_only_while_active"),
    ("ctx-game-marker-left", "test_context", CONTEXT, [('        (game / GAME_MARKER).unlink()\n    for stray', '        pass\n    for stray')],
     "test_the_game_marker_exists_only_while_active"),
    ("ctx-foreign-marker-ignored", "test_context", CONTEXT, [('if marker and not same_path(marker["state"], state):', 'if False:')],
     "test_restore_with_a_marker_from_another_state_folder_is_refused"),
    ("ctx-slots-checked-too-late", "test_context", CONTEXT, [('    require_slots_usable(slots, owner)\n', '')],
     "test_foreign_holder_refuses_before_any_restoring"),
    ("ctx-restore-while-running", "test_context", CONTEXT, [('    require_cold(manifest["cold_processes"])\n    current, _ = walk', '    current, _ = walk')],
     "test_restore_is_refused_while_the_game_runs"),
    ("ctx-restore-over-writer", "test_context", CONTEXT, [('    for relative in current:\n        require_unheld(game / relative)\n', '')],
     "test_restore_is_refused_while_a_writer_holds_a_file"),
    ("ctx-incomplete-reported-done", "test_context", CONTEXT, [('    if not clean:\n        set_phase(state, "restore-incomplete")\n        return receipt\n', '')],
     "test_corrupt_blob_is_refused_for_that_file_and_keeps_recovery"),
    ("ctx-verification-skipped", "test_context", CONTEXT, [('clean = not (failed or leftovers or any(after.values()))', 'clean = True')],
     "test_corrupt_blob_is_refused_for_that_file_and_keeps_recovery"),
    ("ctx-addition-left-live", "test_context", CONTEXT, [('            keep_evidence(path, evidence / "altered-additions" / row["target"])\n        remove_file(path)\n', '            keep_evidence(path, evidence / "altered-additions" / row["target"])\n')],
     "test_addition_is_removed_and_its_folder_if_created"),
    ("ctx-addition-folder-left", "test_context", CONTEXT, [('for created in row["created_folders"]:', 'for created in []:')],
     "test_addition_is_removed_and_its_folder_if_created"),
    ("ctx-altered-addition-lost", "test_context", CONTEXT, [('            keep_evidence(path, evidence / "altered-additions" / row["target"])\n', '')],
     "test_altered_addition_goes_to_evidence_not_left_live"),
    ("ctx-addition-folder-swap-stranded", "test_context", CONTEXT, [('    if path.is_dir():\n        destination =', '    if False:\n        destination =')],
     "test_addition_replaced_by_a_folder_heals"),
    ("ctx-stray-temporary-left", "test_context", CONTEXT, [('    if os.path.lexists(temporary):\n        remove_file(temporary)\n', '')],
     "test_stranded_addition_temporary_file_is_removed"),
    ("ctx-read-only-created-stuck", "test_context", CONTEXT, [('    os.chmod(path, stat.S_IWRITE)       # Windows refuses to delete a read-only file\n', '')],
     "test_read_only_file_created_during_the_test_is_removed"),
    ("ctx-scope-widened", "test_context", CONTEXT, [('current, folders = walk(game, manifest["roots"])', 'current, folders = walk(game, ["Data"])')],
     "test_files_outside_scope_are_left_alone"),
    ("ctx-folders-not-compared", "test_context", CONTEXT, [('report["missing_folders"] = [relative for relative in manifest["folders"] if not (game / relative).is_dir()]', 'report["missing_folders"] = []')],
     "test_deleted_empty_folder_comes_back"),
    ("ctx-folders-not-recreated", "test_context", CONTEXT, [('            (game / relative).mkdir(parents=True, exist_ok=True)\n', '            pass\n')],
     "test_deleted_empty_folder_comes_back"),
    ("ctx-root-as-file-unseen", "test_context", CONTEXT, [('            files.append(current.relative_to(game).as_posix())\n            continue\n', '            continue\n')],
     "test_root_replaced_by_a_file_heals"),
    ("ctx-empty-new-folders-left", "test_context", CONTEXT, [('    remove_empty_new_folders(game, manifest)\n', '')],
     "test_file_created_during_the_test_is_moved_to_evidence"),
    ("ctx-originals-before-new-things", "test_context", CONTEXT, [('    remove_empty_new_folders(game, manifest)\n    for relative in before["renamed"]:', '    for relative in before["renamed"]:')],
     "test_file_replaced_by_a_folder_heals_on_the_first_run"),
    ("ctx-unknown-profile-key", "test_context", CONTEXT, [('    if unknown:\n        raise Refused("unknown profile key', '    if False:\n        raise Refused("unknown profile key')],
     "test_profile_with_an_unknown_key_is_refused"),
    ("ctx-no-process-list-allowed", "test_context", CONTEXT, [('    if not profile.get("cold_processes"):\n', '    if False:\n')],
     "test_empty_process_list_is_refused"),
    ("ctx-whole-game-root", "test_context", CONTEXT, [('if len(folder.relative_to(game).parts) < 2:', 'if False:')],
     "test_a_root_that_is_the_whole_game_or_data_is_refused"),
    ("ctx-save-in-root", "test_context", CONTEXT, [('    reject_saves(files)\n    store.mkdir', '    store.mkdir')],
     "test_a_save_inside_a_root_is_refused"),
    ("ctx-unreadable-record-means-none", "test_context", CONTEXT, [('raise Refused("the active record {} is unreadable: nothing changes until it is looked at".format(active)) from error', 'return None')],
     "test_unreadable_active_record_refuses_every_mutation"),
    ("ctx-early-crash-stranded", "test_context", CONTEXT, [('if phase == "preparing":', 'if False:')],
     "test_crash_between_manifest_and_its_hash_recovers"),
    ("ctx-apply-failure-not-rolled-back", "test_context", CONTEXT, [('    except Refused:\n        restore(game, state, store, owner)\n        raise\n', '    except Refused:\n        raise\n')],
     "test_refusal_while_applying_rolls_back"),
    ("ctx-run-name-reused", "test_context", CONTEXT, [('    if (state / run).exists():\n', '    if False:\n')],
     "test_run_name_is_never_reused"),
    ("ctx-prepare-receipt-missing", "test_context", CONTEXT, [('    write_json(folder / PREPARE_RECEIPT, {', '    (lambda *_: None)(folder / PREPARE_RECEIPT, {')],
     "test_prepare_records_a_receipt"),
    ("ctx-verify-exits-zero", "test_context", CONTEXT, [('return 1 if any(result[name] for name in DIFFERENCES) else 0', 'return 0')],
     "test_verify_command_exits_nonzero_on_differences"),
    ("ctx-verify-not-recorded", "test_context", CONTEXT, [('    if record:\n        n = 1\n', '    if False:\n        n = 1\n')],
     "test_verify_reports_a_clean_context_and_records_it"),
    ("ctx-copies-not-flushed", "test_context", CONTEXT, [('        os.fsync(writer.fileno())\n', '')],
     "test_copies_are_flushed_to_disk"),
    ("ctx-check-misses-running", "test_context", CONTEXT, [('    check_request(game, state, store, owner, run, profile)\n    require_cold(profile["cold_processes"])\n    if status(state) is not None or', '    check_request(game, state, store, owner, run, profile)\n    if status(state) is not None or')],
     "test_check_refuses_a_running_game"),
    ("ctx-check-misses-active", "test_context", CONTEXT, [('if status(state) is not None or read_marker(game) is not None:', 'if False:')],
     "test_check_refuses_an_active_context"),
    ("ctx-check-misses-marker", "test_context", CONTEXT, [(' or read_marker(game) is not None:', ':')],
     "test_check_refuses_a_marker_from_another_state_folder"),
    ("ctx-check-misses-slot", "test_context", CONTEXT, [('        slot_bytes(Path(slots["file"]).read_bytes(), slots, owner, False)\n', '        pass\n')],
     "test_check_refuses_a_foreign_slot"),
    ("ctx-check-misses-held", "test_context", CONTEXT, [('    for relative in files:\n        require_unheld(game / relative)\n    edits', '    edits')],
     "test_check_refuses_a_held_file"),
    ("ctx-bad-reference-overwrites", "test_context", CONTEXT, [('if not blob.is_file() or sha256(blob) != row["sha256"]:', "if False:"), ('    if sha256(temporary) != digest:\n        temporary.unlink()\n        raise Refused("{} does not hash to its reference".format(blob_or_source))\n', "")],
     "test_corrupt_blob_is_refused_for_that_file_and_keeps_recovery"),
    ("package-check-one-way", "test_sdk", SDK, [("for name in sorted(set(installed) | set(pinned)):", "for name in sorted(pinned):")],
     "test_package_the_pins_do_not_name_is_refused"),
    ("baseline-unchecked", "test_sdk", SDK, [("    if actual != pinned:\n        raise Refused(\"vcpkg baseline", "    if False:\n        raise Refused(\"vcpkg baseline")],
     "test_different_baseline_refuses"),
    ("stale-pins-accepted", "test_sdk", SDK, [("    if receipt.get(\"pins_sha256\") != sha256(pins_path):\n", "    if False:\n")],
     "test_changed_pins_make_the_sdk_stale"),
    ("stale-sources-accepted", "test_sdk", SDK, [("    if receipt.get(\"sources_sha256\") != sources_digest(native):\n", "    if False:\n")],
     "test_changed_observation_header_makes_the_sdk_stale"),
    ("no-required-artifacts", "test_sdk", SDK, [("    problems.extend(\"required file absent: \" + name for name in REQUIRED_FILES if name not in recorded)\n", "")],
     "test_receipt_with_no_files_and_no_artifacts_fails"),
    ("recorded-commit-ignored", "test_sdk", SDK, [("        if receipt.get(field) != (pins.get(section) or {}).get(\"commit\"):\n", "        if False:\n")],
     "test_recorded_commit_must_be_the_pin"),
    ("recorded-packages-ignored", "test_sdk", SDK, [("    if receipt.get(\"packages\") != pins.get(\"packages\"):\n", "    if False:\n")],
     "test_recorded_packages_must_be_the_pins"),
    ("qualification-not-bound", "test_sdk", SDK, [(".get(\"receipt_sha256\") == sha256(receipt)", ".get(\"receipt_sha256\") is not None")],
     "test_qualification_binds_to_this_receipt"),
    ("toolset-unchecked", "test_sdk", SDK, [("    if not version or not version.startswith(SUPPORTED_TOOLSETS):\n", "    if False:\n")],
     "test_another_toolset_refuses"),
    ("empty-import-list-passes", "test_sdk", SDK, [("    if not any(name == \"kernel32.dll\" for name in names):\n", "    if False:\n")],
     "test_an_unreadable_import_list_fails"),
    ("added-file-ignored", "test_sdk", SDK, [("        elif name not in recorded:\n            problems.append(\"unexpected: \" + name)\n", "")],
     "test_added_file_fails"),
    ("game-root-allowed", "test_sdk", SDK, [("        if (folder / \"SkyrimVR.exe\").exists() or (folder / \"SkyrimSE.exe\").exists():\n            raise Refused(\"{} is inside", "        if False:\n            raise Refused(\"{} is inside")],
     "test_inside_a_game_folder_refuses"),
    ("dependency-dll-allowed", "test_sdk", SDK, [("for name in names if name.rsplit(\".\", 1)[0].startswith(FORBIDDEN_IMPORTS))", "for name in names if False)")],
     "test_a_dependency_dll_fails"),
    ("no-inputs-accepted", "test_receipts", RECEIPTS, [("    if not current:\n        raise ValueError(\"no inputs: nothing binds this result\")\n", "")],
     "test_no_inputs_fails"),
    ("stale-output-accepted", "test_receipts", RECEIPTS, [("        if not (folder / name).is_file() or sha256(folder / name) != digest:\n            raise ValueError(\"output changed: \" + name)\n", "        pass\n")],
     "test_modified_output_fails"),
    ("reconfigure-new-test-old", "test_receipts", RECEIPTS, [(" or built.get(\"inputs\") != current", "")],
     "test_reconfigure_new_test_old_fails"),
    ("post-run-drift-ignored", "test_receipts", RECEIPTS, [("(0 if stable else 1)", "0")],
     "test_post_run_drift_fails_a_passing_run"),
    ("stored-image-writable", "test_native_image", IMAGE, [("        os.chmod(temporary / name, stat.S_IREAD)\n", "        pass\n")],
     "test_stored_image_is_read_only"),
    ("tamper-unchecked", "test_native_image", IMAGE, [("    elif sha256(image) != digest or entry.get(\"image_sha256\") != digest:\n        problems.append(\"the image no longer hashes to its reference\")\n", "")],
     "test_tampered_image_fails"),
    ("game-update-unchecked", "test_native_image", IMAGE, [("    if installed is not None and sha256(installed) != entry.get(\"source_sha256\"):\n        problems.append(\"the installed executable is not this image's source: the game changed\")\n", "")],
     "test_game_update_is_detected"),
    ("still-wrapped-accepted", "test_native_image", IMAGE, [("    elif entry[\"name\"] == \".bind\":\n        problems.append(\"the derived entry point is still in the SteamStub .bind section\")\n", "")],
     "test_entry_still_in_bind_refuses"),
    ("per-consumer-copies", "test_native_image", IMAGE, [("        return existing\n", "        pass\n"), ("    temporary = store / (\".incoming-\" + digest)\n", "    temporary = store / (\".incoming-\" + digest + owner)\n"), ("    os.replace(temporary, folder)\n", "    os.replace(temporary, store / (digest + owner))\n")],
     "test_second_consumer_shares_the_one_copy"),
    ("store-in-git-checkout", "test_native_image", IMAGE, [("        if (folder / \".git\").exists():\n", "        if False:\n")],
     "test_store_in_a_git_checkout_refuses"),
    ("edited-manifest-accepted", "test_native_image", IMAGE, [("    if not hash_file.is_file() or hash_file.read_text(encoding=\"utf-8\").strip() != sha256(store / digest / MANIFEST):\n", "    if False:\n")],
     "test_edited_manifest_fails"),
    ("damaged-entry-accepted", "test_native_image", IMAGE, [("        problems = verify(store, digest)\n        if problems:\n            raise Refused(\"this image is registered but damaged: \" + \"; \".join(problems))\n", "")],
     "test_registering_over_a_damaged_entry_refuses"),
    ("vacuous-site-accepted", "test_native_image", IMAGE, [("        if len(expected) < MIN_SITE_BYTES:\n            raise Refused(\"site {} needs at least {} expected bytes\".format(site.get(\"name\"), MIN_SITE_BYTES))\n", "")],
     "test_vacuous_site_refuses"),
    ("reference-not-checked", "test_native_image", IMAGE, [("    check_identity(SHA256, digest, \"the image reference\")\n", "")],
     "test_reference_must_be_a_hash"),
    ("chain-not-followed", "test_native_image", IMAGE, [("\"primary_begin\": self._primary(begin, unwind),", "\"primary_begin\": begin,")],
     "test_reads_unwind_facts"),
    ("store-in-game-folder", "test_native_image", IMAGE, [("    refuse_game_root(store)\n", "")],
     "test_store_in_a_game_folder_refuses"),
    ("pdata-range-ignored", "test_native_image", IMAGE, [("            elif rva >= end:\n                low = middle + 1\n", "")],
     "test_outside_any_function_has_no_entry"),
]


def apply(root: Path, relative: str, changes) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    for old, new in changes:
        if text.count(old) != 1:
            raise SystemExit("mutation anchor is absent or not unique in {}: {!r}".format(relative, old[:70]))
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline="\n")


def fresh_copy(work: Path) -> Path:
    copy = work / "mutant"
    if copy.exists():
        shutil.rmtree(copy)     # this script's own scratch, regenerated from tracked sources
    shutil.copytree(NATIVE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def build_native(copy: Path, work: Path, prefix: Path, target: str, env: dict):
    binary = work / "mb"
    if binary.exists():
        shutil.rmtree(binary)
    cmake = shutil.which("cmake", path=env.get("Path") or env.get("PATH"))
    steps = [[cmake, "-S", copy / "observation", "-B", binary, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
              "-DCMAKE_PREFIX_PATH={}".format(prefix)], [cmake, "--build", binary, "--target", target, "-j", "2"]]
    output = ""
    for step in steps:
        done = subprocess.run([str(s) for s in step], capture_output=True, text=True, env=env, check=False)
        output += done.stdout + done.stderr
        if done.returncode:
            return None, output
    return binary / (target + ".exe"), output


def run_native(name, target, relative, changes, marker, work, prefix, env) -> str:
    copy = fresh_copy(work)
    if relative:
        apply(copy, relative, changes)
    exe, log = build_native(copy, work, prefix, target, env)
    if exe is None:
        return "did not compile:\n" + log[-800:]
    try:
        done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return "timed out"
    output = done.stdout + done.stderr
    if not relative:
        return "" if done.returncode == 0 else "baseline failed:\n" + output[-800:]
    return "" if marker in output else "named check did not fire (exit {}):\n{}".format(done.returncode, output[-500:])


def run_python(name, module, relative, changes, marker, work) -> str:
    copy = fresh_copy(work)
    if relative:
        apply(copy, relative, changes)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-I", str(copy / "tests" / (module + ".py"))], capture_output=True, text=True, env=env, check=False)
    output = done.stdout + done.stderr
    if not relative:
        return "" if done.returncode == 0 else "baseline failed:\n" + output[-800:]
    return "" if ("FAIL: " + marker) in output or ("ERROR: " + marker) in output else "named test did not fail:\n" + output[-500:]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--only")
    args = parser.parse_args(argv)
    if args.only and args.only not in {m[0] for m in NATIVE_MUTANTS + PYTHON_MUTANTS}:
        parser.error("no mutant named " + args.only)
    sdk.refuse_game_root(args.work)
    args.work.mkdir(parents=True, exist_ok=True)
    env = sdk.vs_environment()
    # MinHook comes from the SDK; the sources under test come from the copy.
    for target in ("gate-tests", "bridge-tests", "entry-tests"):
        problem = run_native("baseline", target, "", [], "", args.work, args.prefix, env)
        if problem:
            print("BASELINE {}: {}".format(target, problem))
            return 1
    for module in ("test_sdk", "test_receipts", "test_native_image", "test_context"):
        problem = run_python("baseline", module, "", [], "", args.work)
        if problem:
            print("BASELINE {}: {}".format(module, problem))
            return 1
    print("baselines green")
    survivors = 0
    for name, target, relative, changes, marker in NATIVE_MUTANTS + PYTHON_MUTANTS:
        if args.only and args.only != name:
            continue
        problem = (run_native(name, target, relative, changes, marker, args.work, args.prefix, env)
                   if target.endswith("-tests") else run_python(name, target, relative, changes, marker, args.work))
        print("{:30} {}".format(name, "killed" if not problem else "SURVIVED: " + problem), flush=True)
        survivors += bool(problem)
    print("mutants: {} survivors: {}".format(len(NATIVE_MUTANTS) + len(PYTHON_MUTANTS), survivors))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
