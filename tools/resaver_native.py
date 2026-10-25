#!/usr/bin/env python3
"""ReSaver Plus (native) backend for tools/resaver-cli.sh. Opt-in: Java stays the default backend.

    bash tools/resaver-cli.sh <operation> <input.ess> [arguments]      (with RESAVER_BACKEND=native)
    python tools/resaver_native.py --verify                            (BUILD-INFO and the executable's SHA-256)

Runs the executable YOUR tools/resaver-plus/build.py built and tested, pinned to the SHA-256 in its BUILD-INFO.json.
Nothing here falls back to the Java backend: a refusal or failure is an error, and the caller decides what next.

  * 20 operations, each with its own flag allowlist; an unknown or repeated flag is refused.
  * Reads and previews never write a save. `verify-roundtrip` runs in memory.
  * `--apply` needs ONE explicit new output in an existing folder: an existing .ess OR .skse of that pair is refused,
    so is the input itself, so is anything inside the permanent save store. Links are checked before resolution.
  * Before the native tool runs a write, the input pair and the (absent) output pair are backed up into the permanent
    save store (tools/skyguard/save_store.py). No backup, no write.
  * The write is previewed first; the applied receipt must equal the preview, be fully verified, have written the
    file, leave the input pair untouched and carry the input's co-save bytes (or its absence) over unchanged. Any
    mismatch is an error and the candidate output is left in place for inspection, never deleted.

Exit: 0 ok | 2 refused or failed (the JSON on stdout says why).
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "tools" / "resaver-plus" / "build"
EXECUTABLE = "resaverpp-cli.exe"
UPSTREAM_COMMIT = "0d48f60cb8a69b367abfbd78d58418e64c507da5"
TIMEOUT = 180
READS = {"info", "dump", "find", "find-refs", "worries", "changeform", "recon",
         "extradata-scan", "changeform-diff", "globaldata", "globaldata-diff",
         "freeze-report", "writer-scan", "verify-roundtrip"}
EDITS = {"set-var", "set-global", "clean", "reset-havok", "cleanse-formlists", "remove-created"}
VALUE_FLAGS = {"--limit", "--script", "--type", "--depth"}
FLAGS = {"info": set(), "dump": {"--limit", "--undefined-only", "--script", "--type"},
         "find": set(), "find-refs": set(), "worries": set(),
         "changeform": {"--raw", "--depth"}, "recon": set(),
         "extradata-scan": {"--limit", "--type"}, "changeform-diff": {"--quests"},
         "globaldata": set(), "globaldata-diff": set(), "freeze-report": set(),
         "writer-scan": set(), "verify-roundtrip": set(),
         "set-var": {"--apply", "--type"}, "set-global": {"--apply"},
         "clean": {"--apply", "--undefined", "--unattached", "--terminate-threads"},
         "reset-havok": {"--apply"}, "cleanse-formlists": {"--apply"}, "remove-created": {"--apply"}}
# The same watchlist the Java driver ships (tools/resaver-cli/ResaverCLI.java KNOWN_FREEZE).
KNOWN_FREEZE = ("diversedragon", "vddmainsct", "_reaper", "soulreaper")
GLOBAL_NAMES = {0: "MiscStats", 1: "PlayerLocation", 2: "TES", 3: "GlobalVariables", 4: "CreatedObjects",
                5: "Effects(IMOD)", 6: "WeatherSystem", 7: "AudioData", 8: "SkyCells", 100: "ProcessLists",
                101: "Combat", 102: "Interface", 103: "ActorCauses", 104: "Unknown104", 105: "DetectionManager",
                106: "LocationMetaData", 107: "QuestStaticData", 108: "StoryTeller", 109: "MagicFavorites",
                110: "PlayerControls", 111: "StoryEventManager", 112: "IngredientShared", 113: "MenuControls",
                114: "MenuTopicManager", 1001: "Papyrus", 1002: "AnimObjects", 1003: "Timer",
                1004: "SynchronizedAnimations", 1005: "Main"}


class Refused(Exception):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pair_hashes(path):
    return {suffix: digest(p) if p.is_file() else None
            for suffix in (".ess", ".skse") for p in [Path(path).with_suffix(suffix)]}


def _test_mode():
    return os.environ.get("TOOLKIT_TEST_MODE") == "1"


def build_dir() -> Path:
    # Test seam (like RESAVER_CLI_UNAME): another BUILD-INFO folder, honoured only under TOOLKIT_TEST_MODE=1. The pin
    # is still checked against that folder's BUILD-INFO.json.
    if _test_mode() and os.environ.get("RESAVER_NATIVE_BUILD"):
        return Path(os.environ["RESAVER_NATIVE_BUILD"])
    return BUILD_DIR


def build_info():
    folder = build_dir()
    path = folder / "BUILD-INFO.json"
    if not path.is_file():
        raise Refused("ReSaver Plus is not built: no {}. Build it with `python tools/resaver-plus/build.py` "
                      "(tools/resaver-plus/README.md), or use the Java backend".format(path.relative_to(folder.parent)
                                                                                         if folder.parent in path.parents
                                                                                         else path))
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("BUILD-INFO.json is unreadable: {}".format(exc))
    if not isinstance(info, dict):
        raise Refused("BUILD-INFO.json is not a build record")
    if info.get("upstream_commit") != UPSTREAM_COMMIT:
        raise Refused("BUILD-INFO.json names upstream {}, not the pinned {}: rebuild with build.py".format(
            info.get("upstream_commit"), UPSTREAM_COMMIT))
    if info.get("executable") != EXECUTABLE or not isinstance(info.get("sha256"), str):
        raise Refused("BUILD-INFO.json does not name the pinned executable")
    return folder, info


def executable():
    """(path, sha256) of the tested build, or Refused. The path is confined to the build folder."""
    folder, info = build_info()
    allowed = folder.resolve()
    try:
        path = (folder / EXECUTABLE).resolve(strict=True)
    except OSError:
        raise Refused("BUILD-INFO.json is present but {} is missing: rebuild with build.py".format(EXECUTABLE))
    if path.parent != allowed or path.name.lower() != EXECUTABLE:
        raise Refused("the native executable resolves outside its build folder")
    if digest(path) != info["sha256"]:
        raise Refused("the native executable differs from the build build.py tested (SHA-256 mismatch): rebuild")
    if os.name != "nt" and not _launcher():
        raise Refused("the native backend runs on Windows only; use the Java backend on this OS")
    return path, info["sha256"]


def _launcher():
    # Test seam: an interpreter the stub executable is run with, honoured only under TOOLKIT_TEST_MODE=1.
    return [os.environ["RESAVER_NATIVE_INTERPRETER"]] if _test_mode() and os.environ.get("RESAVER_NATIVE_INTERPRETER") \
        else []


def _ess(value, what="an existing ESS input"):
    path = Path(value)
    if os.path.islink(value) and not path.exists():
        raise Refused("Expected {}: {} is a dangling link".format(what, value))
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise Refused("Expected " + what)
    if not path.is_file() or path.suffix.lower() != ".ess":
        raise Refused("Expected " + what)
    return path


def store_root(project):
    sys.path.insert(0, str(ROOT / "tools"))
    from skyguard import save_store
    return Path(save_store.store(str(project)).root).resolve()


def invocation(argv, project=ROOT):
    """(op, source, output or None, native argv) after admission; Refused for anything outside the contract."""
    if len(argv) < 2 or argv[0] not in FLAGS:
        raise Refused("Expected a supported operation and one ESS input")
    op, value = argv[:2]
    source = _ess(value)
    positions, options = [], {}
    i = 2
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            if token not in FLAGS[op] or token in options:
                raise Refused("Unknown or repeated option: " + token)
            if token in VALUE_FLAGS:
                i += 1
                if i == len(argv):
                    raise Refused("Missing option value: " + token)
                options[token] = argv[i]
            else:
                options[token] = True
        else:
            positions.append(token)
        i += 1
    if op in ("find", "find-refs", "globaldata-diff", "changeform-diff"):
        if not 1 <= len(positions) <= (2 if op == "changeform-diff" else 1):
            raise Refused("Wrong positional argument count")
    elif op in ("dump", "changeform"):
        if len(positions) > 1:
            raise Refused("Too many positional arguments")
    elif op == "set-var":
        if len(positions) not in (1, 3, 4):
            raise Refused("set-var needs id or id/index/value/[output]")
    elif op == "set-global":
        if len(positions) not in (2, 3):
            raise Refused("set-global needs target/value/[output]")
    elif len(positions) > (1 if op in EDITS else 0):
        raise Refused("Too many positional arguments")
    output = None
    if "--apply" in options:
        index = 3 if op == "set-var" else 2 if op == "set-global" else 0
        if len(positions) != index + 1:
            raise Refused("Apply requires one explicit new output")
        requested = Path(positions[index]).absolute()
        # Checked BEFORE resolution, so a link (dangling or not) at either member is an existing member.
        if os.path.lexists(requested.with_suffix(".ess")):
            raise Refused("Refusing an existing output .ess: name a new output pair")
        if os.path.lexists(requested.with_suffix(".skse")):
            raise Refused("Refusing an existing output .skse: name a new output pair")
        output = requested.resolve()
        if output.suffix.lower() != ".ess" or output == source:
            raise Refused("Output must name a separate new ESS pair")
        if any(os.path.lexists(output.with_suffix(s)) for s in (".ess", ".skse")):
            raise Refused("Refusing either existing output member")
        if not output.parent.is_dir():
            raise Refused("Output directory must already exist")
        try:
            store = store_root(project)
        except (OSError, ValueError, ImportError) as exc:
            raise Refused("cannot place the permanent save store, so cannot back up first: {}".format(exc))
        if output == store or store in output.parents:
            raise Refused("Output cannot be inside the permanent save store")
        positions[index] = str(output)
    if op in ("globaldata-diff", "changeform-diff"):
        positions[0] = str(_ess(positions[0], "an existing second ESS input"))
    native = [op, str(source), *positions]
    for flag, value in options.items():
        native.append(flag)
        if flag in VALUE_FLAGS:
            native.append(str(value))
    return op, source, output, native


def launch(cmd):
    """The one place the native tool is started (tests replace it)."""
    return subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)


def run(binary, args, expected_hash):
    if digest(binary) != expected_hash:
        raise Refused("Candidate changed since admission")
    result = launch([*_launcher(), str(binary), "--json", *args])
    try:
        value = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Native backend returned invalid structured output") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Native backend returned invalid structured output")
    if result.returncode or not value.get("ok"):
        raise RuntimeError(value.get("error", "Native backend rejected operation"))
    return value


def backup(project, source, output, op):
    """Permanent pair backups of the input and the new output (absent) BEFORE any write. [] is a failure."""
    sys.path.insert(0, str(ROOT / "tools"))
    from skyguard import audit as audit_mod, save_store
    pairs = [(source, source.with_suffix(".skse")), (output, output.with_suffix(".skse"))]
    ids = save_store.backup_pairs(str(project), audit_mod.Audit(str(project)), pairs,
                                  "resaver-native {} one new output".format(op), agent="resaver-native",
                                  deadline=time.monotonic() + 120)
    if not ids:
        raise RuntimeError("the pair backup recorded nothing; refusing the write")
    return ids


def project_result(op, result):
    """Formatting and installation-independent heuristics only; no save parsing."""
    if op in ("globaldata", "globaldata-diff"):
        for block in result.get("blocks", result.get("changed", [])):
            block["name"] = GLOBAL_NAMES.get(block["type"], "Type" + str(block["type"]))
            raw = block.pop("rawBase64", None)
            if raw is not None:
                block["sha"] = hashlib.sha256(base64.b64decode(raw, validate=True)).hexdigest()[:20]
    if op == "worries":
        result["reportHtml"] = "<pre>" + html.escape(result["reportText"]) + "</pre>"
    if op == "changeform" and "bodyBase64" in result:
        result["bodyHex"] = base64.b64decode(result["bodyBase64"], validate=True).hex(" ")
    if op == "extradata-scan":
        result["scanned"] = result["examined"]
        result["failed"] = sum(row["fail"] for row in result["perType"].values())
        result["perType"] = {name: {"scanned": row["ok"] + row["fail"], "failed": row["fail"], "ok": row["ok"]}
                             for name, row in result["perType"].items()}
        result["otherFailures"] = {name: count for name, count in result["failureCategories"].items()
                                   if name != "extra-unknown-type"}
    if op == "freeze-report":
        counts = result["counts"]
        flagged = {}
        for name in ("activeByScript", "suspendedByScript"):
            for script, count in result.pop(name, {}).items():
                if any(token in script.lower() for token in KNOWN_FREEZE):
                    flagged[script] = flagged.get(script, 0) + count
        result["knownFreezeScripts"] = dict(sorted(flagged.items()))
        signals = []
        for key in ("undefinedThreads", "undefinedActiveScripts"):
            if counts[key]:
                signals.append(f"{key}={counts[key]}")
        if counts["suspendedStacks"]:
            label = "highSuspendedStacks" if counts["suspendedStacks"] > 50 else "suspendedStacks"
            signals.append(f"{label}={counts['suspendedStacks']}")
        for key, label in (("terminated", "terminatedButPresent"), ("unattached", "unattached")):
            if counts[key]:
                signals.append(f"{label}={counts[key]}")
        if flagged:
            signals.append(f"watchlist:knownFreezeScriptsActive={len(flagged)}")
        result["signals"] = signals
    result["backend"] = "ReSaver Plus native"
    return result


IGNORED_IN_RECEIPT = {"apply", "dryRun", "wroteFile", "verified"}


def main(argv=None, project=ROOT):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--help"]:
        print(json.dumps({"ok": True, "backend": "ReSaver Plus native",
                          "usage": "resaver-cli.sh <operation> <input.ess> [arguments]",
                          "operations": sorted(FLAGS),
                          "writes": "explicit separate new output; preview before --apply; permanent pair backups"}))
        return 0
    if argv == ["--verify"]:
        path, sha = executable()
        _folder, info = build_info()
        print(json.dumps({"ok": True, "backend": "native", "executable": str(path), "sha256": sha,
                          "upstream_commit": info["upstream_commit"], "ctest": info.get("ctest"),
                          "tools": info.get("tools")}))
        return 0
    op, source, output, native = invocation(argv, project)
    binary, binary_hash = executable()
    before = pair_hashes(source)
    backups = []
    preview = None
    if output is not None:
        # Exactly one source and one new output; the source is never modified. Backed up BEFORE the preview: the
        # preview reads only, but a write must never start from a state the store does not hold.
        backups = backup(project, source, output, op)
        preview = run(binary, [arg for arg in native if arg != "--apply"], binary_hash)
        if not preview.get("deltaVerified"):
            raise RuntimeError("Native plan is unverified; nothing was written")
        if pair_hashes(source) != before:
            raise RuntimeError("Input changed during planning; nothing was written")
    result = run(binary, native, binary_hash)
    if pair_hashes(source) != before:
        raise RuntimeError("Source pair changed during the operation; candidate output retained")
    if output is not None:
        if not result.get("deltaVerified"):
            raise RuntimeError("Native write lacks a verified delta; candidate output retained")
        if not result.get("verified"):
            raise RuntimeError("Native write was not verified on reopen; candidate output retained")
        if not result.get("wroteFile"):
            raise RuntimeError("Native write reports no file written; candidate output retained")
        if {k: v for k, v in result.items() if k not in IGNORED_IN_RECEIPT} != \
                {k: v for k, v in preview.items() if k not in IGNORED_IN_RECEIPT}:
            raise RuntimeError("Applied target/delta receipt differs from preview; candidate output retained")
        if pair_hashes(output)[".skse"] != before[".skse"]:
            raise RuntimeError("Co-save bytes or presence changed unexpectedly; candidate output retained")
        result["pairBackups"] = backups
        result["inputHashes"] = before
        result["outputHashes"] = pair_hashes(output)
    print(json.dumps(project_result(op, result), ensure_ascii=True, allow_nan=False))
    return 0


def entry(argv=None, project=ROOT):
    try:
        return main(argv, project)
    except (Refused, OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "backend": "ReSaver Plus native", "error": str(exc)}, allow_nan=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(entry())
