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
    ("r11-claim-unchecked", "entry-tests", HOOK, [("    if (!d.r11IsNotAnInput) return false;\n", "")],
     "FAIL a descriptor must claim R11 is not an input"),
    ("descending-order-unchecked", "entry-tests", HOOK, [("offset >= previous || ", "")],
     "FAIL codes out of descending order refused"),
    ("patch-without-revalidation", "entry-tests", HOOK, [("if (!created || !bridgeValid(read) || !bytesMatch(target, site.expected, 0, 0, read)) { status = kEnableRevalidation; return false; }", "if (!created) { status = kEnableRevalidation; return false; }")],
     "FAIL failed final source revalidation does not attempt a patch"),
    ("stale-ticket-arms", "gate-tests", GATE, [("return state.compare_exchange_strong(expected, expected | 2, std::memory_order_acq_rel, std::memory_order_acquire);", "state.fetch_or(2); return true;")],
     "FAIL preload between request read and commit invalidates the arm ticket"),
    ("deadline-ignored", "gate-tests", GATE, [(" && now < deadline.load(std::memory_order_acquire)", " && (now, true)")],
     "FAIL deadline holds on the hot path"),
    ("dropped-drain", "gate-tests", GATE, [("        active.fetch_add(1, std::memory_order_seq_cst);\n        if (admits(now)) return true;\n        active.fetch_sub(1, std::memory_order_release);\n        return false;", "        return admits(now);")],
     "FAIL admitted producer is counted"),
    ("drain-claimed-early", "gate-tests", GATE, [("return active.load(std::memory_order_acquire) == 0;", "return true;")],
     "FAIL disarm does not claim a drain while a producer is inside"),
    ("load-payload-truthy", "gate-tests", GATE, [("return encoded == 1 && size == 1;", "(void)size; return encoded != 0;")],
     "FAIL unexpected payload does not arm"),
    ("unload-after-publication", "gate-tests", GATE, [("try { start(); } catch (...) { failure(); }\n    return true;", "try { start(); } catch (...) { failure(); return false; }\n    return true;")],
     "FAIL start failure keeps the DLL loaded after callback publication"),
]

PYTHON_MUTANTS = [
    ("package-check-one-way", "test_sdk", SDK, [("for name in sorted(set(installed) | set(pinned)):", "for name in sorted(pinned):")],
     "test_package_the_pins_do_not_name_is_refused"),
    ("baseline-unchecked", "test_sdk", SDK, [("    if actual != pinned:\n        raise Refused(\"vcpkg baseline", "    if False:\n        raise Refused(\"vcpkg baseline")],
     "test_different_baseline_refuses"),
    ("stale-pins-accepted", "test_sdk", SDK, [("    if receipt.get(\"pins_sha256\") != sha256(pins_path):\n", "    if False:\n")],
     "test_changed_pins_make_the_sdk_stale"),
    ("stale-sources-accepted", "test_sdk", SDK, [("    if receipt.get(\"sources_sha256\") != sources_digest(native):\n", "    if False:\n")],
     "test_changed_observation_header_makes_the_sdk_stale"),
    ("added-file-ignored", "test_sdk", SDK, [("        elif name not in recorded:\n            problems.append(\"unexpected: \" + name)\n", "")],
     "test_added_file_fails"),
    ("game-root-allowed", "test_sdk", SDK, [("        if (folder / \"SkyrimVR.exe\").exists() or (folder / \"SkyrimSE.exe\").exists():\n            raise Refused(\"{} is inside", "        if False:\n            raise Refused(\"{} is inside")],
     "test_inside_a_game_folder_refuses"),
    ("dependency-dll-allowed", "test_sdk", SDK, [("for name in FORBIDDEN_IMPORTS if name in lowered", "for name in FORBIDDEN_IMPORTS if False")],
     "test_a_dependency_dll_fails"),
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
    args.work.mkdir(parents=True, exist_ok=True)
    env = sdk.vs_environment()
    # MinHook comes from the SDK; the sources under test come from the copy.
    for target in ("gate-tests", "bridge-tests", "entry-tests"):
        problem = run_native("baseline", target, "", [], "", args.work, args.prefix, env)
        if problem:
            print("BASELINE {}: {}".format(target, problem))
            return 1
    for module in ("test_sdk", "test_receipts", "test_native_image"):
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
