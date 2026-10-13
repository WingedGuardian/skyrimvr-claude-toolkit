#!/usr/bin/env python3
"""skse-load-status -- what SKSE actually did with each DLL, read from SKSE's own log.

Many SKSE plugin DLLs carry no Windows version resource, so a file's properties cannot say
which build is installed. SKSE's log records, per DLL in Data/SKSE/Plugins, the version the
plugin reports about itself and what SKSE did with it: loaded it, refused it, or found it was
not an SKSE plugin at all.

Usage:
    python tools/skse-load-status.py [filter] [--log PATH]

The filter matches the DLL file name OR the name the plugin reports about itself.
Exit: 0 printed · 2 bad usage, or the log is missing / unreadable.

Which log (the newest wins, and the one read is always named):
    Skyrim VR   <My Games>/Skyrim VR/SKSE/sksevr.log
    SE / AE     <My Games>/Skyrim Special Edition[ GOG| EPIC]/SKSE/skse64.log
                (every SKSE64 release from v2.0.20 to v2.3.1 writes this one name:
                 skse64/skse64.cpp, gLog.OpenRelative, in ianpatt/skse64)
Override the folder with $SKSE_LOG_DIR, or name the file with --log.

The line formats this reads (MEASURED on a real SKSE VR log; READ for SE/AE in
ianpatt/skse64 skse64/PluginManager.cpp, tags v2.0.20 and v2.3.1):
    checking plugin <dll, a bare name on SKSE64 2.1+, a full path on VR and 2.0.x>
    plugin <dll> (<data ver> <name> <plugin ver>) loaded correctly (handle N)
    plugin <dll> (<data ver> <name> <plugin ver>) reported as incompatible during query|load (handle N)
    plugin <dll> does not appear to be an SKSE plugin                      (VR, SKSE64 2.0.x)
    plugin <dll> (<data ver> <name> <plugin ver>) <reason> <code> (handle N)   (SKSE64 2.1+ errors)
SKSE64 2.1+ prints every "loaded correctly" line together after all loading, not next to its
"checking plugin" line, so results are matched to plugins by DLL name, never by position.

The version is printed RAW (hex as logged): authors encode it differently (one plugin's 0.8.5
logs 00000850, another's 1.5.10 logs 00010510, some always log 00000001), so decoding it would
invent precision. Compare it against a known build, not against a Nexus version string.

Accounting: every "checking plugin" line is one plugin, and the status rows must sum to that
count. Result lines that name no checked plugin are counted on their own row, printed even at
zero -- a log this tool misreads must not look like a clean one.
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

CHECK_RE = re.compile(r"^checking plugin (?P<dll>.+?\.dll)\s*$", re.I)
# The parenthesised version block is optional: VR and SKSE64 2.0.x print the "not an SKSE
# plugin" verdict without one. `plugin 12 allocated ...` has no .dll and never matches.
RESULT_RE = re.compile(
    r"^plugin (?P<dll>.+?\.dll) "
    r"(?:\((?P<data>[0-9A-Fa-f]{8}) (?P<name>.*) (?P<ver>[0-9A-Fa-f]{8})\) )?"
    r"(?P<status>.+?)(?: \(handle -?\d+\))?\s*$", re.I)
ERRCODE_RE = re.compile(r"^(?P<msg>.*\D) (?P<code>-?\d+)$")

LOADED = "loaded correctly"
NOT_SKSE = "does not appear to be an SKSE plugin"
NO_RESULT = "NO RESULT LINE"

# One log name per runtime, from the sources cited in the docstring. skse64_loader.log and
# skse64_steam_loader.log are the LOADER's logs and hold no plugin lines.
LOG_NAMES = ("sksevr.log", "skse64.log")


def basename(dll: str) -> str:
    return re.split(r"[\\/]", dll)[-1]


def find_log():
    """(path or None, a description of what was looked at, the other candidates)."""
    folder, _ = skyrim_paths.resolve_dir("SKSE_LOG_DIR", "SKSE")
    found = [folder / n for n in LOG_NAMES if (folder / n).is_file()]
    if not found:
        return None, "no {} in {}".format(" or ".join(LOG_NAMES), folder), []
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0], str(folder), found[1:]


def classify(status: str) -> str:
    s = status.lower()
    if s == LOADED:
        return "loaded"
    if s.startswith("reported as incompatible"):
        return "incompatible"
    if s.startswith(NOT_SKSE.lower()):
        return "not an SKSE plugin"
    return "other error"


def parse(lines):
    """rows (one per checked plugin, in log order), orphan result lines, the "checking plugin" count, and a
    per-verdict count kept as each result line is APPLIED -- independent of the rows, so a plugin given two
    verdicts, or two plugins collapsed into one row, makes the totals disagree instead of hiding."""
    rows, by_name, orphans, checked, verdicts = [], {}, [], 0, Counter()
    for ln in lines:
        m = CHECK_RE.match(ln)
        if m:
            checked += 1
            row = {"dll": basename(m.group("dll")), "status": NO_RESULT, "kind": "no result line",
                   "name": "", "ver": "", "code": ""}
            rows.append(row)
            by_name[row["dll"].lower()] = row
            continue
        m = RESULT_RE.match(ln)
        if not m:
            continue
        row = by_name.get(basename(m.group("dll")).lower())
        if row is None:
            orphans.append(ln)
            continue
        status, code = m.group("status"), ""
        e = ERRCODE_RE.match(status)
        if e and status.lower() != LOADED:
            status, code = e.group("msg"), e.group("code")
        row.update(status=status, kind=classify(status), name=m.group("name") or "",
                   ver=m.group("ver") or "", code=code)
        verdicts[row["kind"]] += 1
    return rows, orphans, checked, verdicts


def main(argv) -> int:
    args, log = list(argv), None
    if "--log" in args:
        i = args.index("--log")
        if i + 1 >= len(args):
            print("usage: skse-load-status.py [filter] [--log PATH]   (--log needs a path)", file=sys.stderr)
            return 2
        log = Path(args[i + 1])
        del args[i:i + 2]
    flags = [a for a in args if a.startswith("-")]
    if flags or len(args) > 1:
        # An unknown flag taken as a filter silently matches nothing and reads as "not installed".
        print("usage: skse-load-status.py [filter] [--log PATH]   (got {})".format(args), file=sys.stderr)
        return 2
    flt = args[0].lower() if args else ""

    others = []
    if log is None:
        log, where, others = find_log()
        if log is None:
            print("ERROR: no SKSE log found ({}). Set SKSE_LOG_DIR or pass --log PATH.".format(where),
                  file=sys.stderr)
            return 2
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        mtime = log.stat().st_mtime
    except OSError as e:
        print("ERROR: cannot read {}: {}".format(log, e), file=sys.stderr)
        return 2

    rows, orphans, checked, verdicts = parse(lines)
    age_h = (time.time() - mtime) / 3600.0
    print("SKSE log: {}".format(log))
    print("  written {} ({:.1f} h ago) -- it describes the LAST launch, not the files on disk now".format(
        time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)), age_h))
    for o in others:
        print("  also present, not read: {}".format(o))

    shown = [r for r in rows if flt in r["dll"].lower() or flt in r["name"].lower()]
    for r in shown:
        status = r["status"] + (" (code {})".format(r["code"]) if r["code"] else "")
        print("  {:<45} {:<9} {:<44} {}".format(r["dll"], r["ver"], status, r["name"]))

    kinds = Counter(verdicts)
    kinds["no result line"] = sum(1 for r in rows if r["status"] == NO_RESULT)
    order = ("loaded", "incompatible", "not an SKSE plugin", "other error", "no result line")
    print("  -- {} shown of {} checked".format(len(shown), checked))
    for k in order:
        print("     {:<20}: {}".format(k, kinds.get(k, 0)))
    print("     {:<20}: {}  (result lines naming no checked DLL)".format("unmatched results", len(orphans)))
    total = sum(kinds.get(k, 0) for k in order)
    if total != checked:
        print("  ACCOUNTING MISMATCH: the rows sum to {} for {} checked plugins (a DLL checked twice, or given"
              " two verdicts); the table above is not trustworthy".format(total, checked))
        return 1
    print("  RESULT: OK (rows sum to {} checked)".format(checked))
    if kinds.get("not an SKSE plugin"):
        print("  note: 'not an SKSE plugin' is expected for helper DLLs some mods ship beside their plugin"
              " (e.g. a crash logger's msdia140.dll); for a real SKSE plugin it means it never loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
