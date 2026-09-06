"""crash-triage -- reduce CrashLoggerSSE dumps to signatures, and say which are known.

A crash log here is ~5,500 lines, of which ~4,600 are the raw STACK dump. The
part that identifies a crash is the exception line plus the top of PROBABLE CALL
STACK. This turns a directory of them into a ranked signature table and labels
the ones the knowledgebase has already ruled on -- so a recurring accepted crash
is recognised as accepted instead of being investigated for the fourth time.

Same discipline as papyrus-triage.py, and for the same reason:

  1. Rows SUM TO THE INPUT, checked against independently counted totals -- not
     computed as `total - shown`, which would be `x == x` and could never fail.
  2. The unaccounted row PRINTS EVEN AT ZERO.
  3. The denominator comes before any finding.

Usage:
    python tools/crash-triage.py [dir-or-file] [--frames N] [--all]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Discovered, not hardcoded. Override with $SKSE_CRASH_DIR.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

# "Unhandled exception "TYPE" at 0xADDR MODULE+OFFSET<TAB>instruction |  symbol)"
EXC_RE = re.compile(
    r'^Unhandled exception "(?P<type>[^"]+)"'
    r'(?:\s+at\s+0x(?P<addr>[0-9A-Fa-f]+))?'
    # MODULE+OFFSET is OPTIONAL. A jump to an address inside no loaded module
    # leaves CrashLogger nothing to attribute, and the line simply ends after the
    # address: `Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x000000000001`.
    # Requiring the group made that dump unparseable forever, and it was counted
    # as "no exception" -- reporting a crash we did read as one we did not.
    r'(?:\s+(?P<module>[^\s+]+)\+(?P<offset>[0-9A-Fa-f]+))?'
    r'\s*(?P<instr>[^|]*)'
    r'(?:\|\s*(?P<symbol>.*?)\)?\s*)?$'
)
# CrashLoggerSSE 1.2x marks each frame [P]robable or [S]tack-scan; older builds
# emit no marker at all. Accept both shapes.
FRAME_RE = re.compile(
    r"^\s*\[\s*\d+\]"
    r"(?:\[(?P<kind>[PS])\])?"
    r"\s+0x[0-9A-Fa-f]+\s+(?P<mod>[^\s+]+)\+(?P<off>[0-9A-Fa-f]+)"
)
# CrashLoggerSSE writes `.LOG`; older builds (and Crash Log Toolkit) write `.txt`.
# Both live in the same folder, so matching only one extension does not fail --
# it reports a confident total made of whichever half it happened to match.
# Measured on the dev install: 8 `.txt` (June 2025) beside 20 `.log` (2026).
# `CrashLogger.log` is the plugin's OWN log and is excluded by the `crash-` prefix.
CRASH_NAME_RE = re.compile(r"^crash-.+\.(txt|log)$", re.I)
# Near-miss detector. Keys on the `crash-` PREFIX, not the word "crash" anywhere
# in the name: the looser form fires on ordinary mod logs like
# LeveledListCrashPrevention.log and CrashLogger.log, and a guard that trips on
# healthy input gets ignored. Scoped to the shape that actually failed -- a real
# dump whose EXTENSION drifted out of the pattern above.
CRASH_PREFIX_RE = re.compile(r"^crash[-_]", re.I)

VERSION_RE = re.compile(r"^(Skyrim ?VR|Skyrim Special Edition)\s+v?(?P<ver>[\d.]+)", re.I)

# A C++ throw reports the THROW SITE on the exception line -- the same
# KERNELBASE address for every C++ crash on the machine. What actually
# distinguishes them lives in the `C++ EXCEPTION:` block, so parse it.
CPP_FIELD_RE = re.compile(r"^\s*(?P<field>Type|Info|Throw Location)\s*:\s*(?P<value>.+?)\s*$")

# Signatures the knowledgebase has already ruled on. The status text is quoted
# from it: a crash marked ACCEPTED must not quietly read as a new finding.
KNOWN = {
    "0B84F05": ("ACCEPTED", "Havok contact solver on a dangling rigid body "
                            "(predator Havok CTD) -- KB: accepted, do not re-investigate"),
    "0B67DCE": ("KNOWN", "Havok ragdoll add/remove during get-up (PLANCK/hdtSMP) "
                         "-- same family as 0B84F05"),
    "093D04C": ("ACCEPTED", "Papyrus VM SkyrimScript::HandlePolicy::Func4 "
                            "-- KB: accepted, do not change Papyrus memory INIs"),
}


def parse(path: Path):
    """Return a dict for one crash log, or None if it has no exception line."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    exc = None
    for line in lines[:40]:
        m = EXC_RE.match(line.strip())
        if m:
            exc = m
            break
    if not exc:
        return None

    game_ver = ""
    for line in lines[:5]:
        vm = VERSION_RE.match(line.strip())
        if vm:
            game_ver = vm.group("ver")
            break

    # CrashLoggerSSE 1.2x heads the stack "CALL STACK ([P]robable / [S]tack scan):";
    # older builds wrote "PROBABLE CALL STACK". Match both. When frames are marked,
    # keep only [P] -- an [S] frame is a stack scan and is not solid enough to key on.
    frames = []
    try:
        start = next(i for i, l in enumerate(lines)
                     if l.startswith("PROBABLE CALL STACK") or l.startswith("CALL STACK"))
        for line in lines[start + 1:]:
            fm = FRAME_RE.match(line)
            if not fm:
                break
            if fm.group("kind") == "S":
                continue
            frames.append(f"{fm.group('mod')}+{fm.group('off')}")
    except StopIteration:
        pass

    cpp = {}
    # 200, not 80: MEASURED on 28 real dumps, the pre-stack preamble already
    # reaches line 79 in one of them. One line of headroom is not a margin, and
    # overrunning it silently blanks cpp_info -- which collapses every C++ crash
    # back onto the shared throw site, the exact merge this parsing prevents.
    for i, line in enumerate(lines[:200]):
        if line.strip().startswith("C++ EXCEPTION"):
            for sub in lines[i + 1:i + 8]:
                fm = CPP_FIELD_RE.match(sub)
                if fm:
                    cpp[fm.group("field")] = fm.group("value")
                elif sub.strip():
                    break
            break

    return {
        "file": path.name,
        "type": exc.group("type"),
        # No module means the address is all we have. Naming it that way keeps
        # these grouped together and readable, instead of pretending to a module.
        "module": exc.group("module") or "(no module)",
        "offset": (exc.group("offset") or exc.group("addr") or "?").upper(),
        "instr": (exc.group("instr") or "").strip(),
        "symbol": (exc.group("symbol") or "").strip().rstrip(")"),
        "game": game_ver,
        "frames": frames,
        # MEASURED: 5 of 28 real dumps here are truncated CrashLoggerSSE writes --
        # 46-74 lines, no CALL STACK and no REGISTERS. They have no stack section
        # to read, so counting them as "frames missing" turned healthy input into a
        # format-break verdict. REGISTERS is the marker that the dump got that far.
        "complete": any(l.startswith("REGISTERS") for l in lines),
        "cpp_type": cpp.get("Type", ""),
        "cpp_info": cpp.get("Info", ""),
        "cpp_throw": cpp.get("Throw Location", ""),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="crash log or directory (default: SKSE folder)")
    ap.add_argument("--frames", type=int, default=4, help="stack frames to show (default 4)")
    ap.add_argument("--all", action="store_true", help="show every signature")
    args = ap.parse_args()

    target = (Path(args.target) if args.target
              else skyrim_paths.resolve_dir("SKSE_CRASH_DIR", "SKSE")[0])
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(p for p in target.iterdir()
                       if p.is_file() and CRASH_NAME_RE.match(p.name))
    else:
        print(f"Not a file or directory: {target}", file=sys.stderr)
        return 2
    if not files:
        print(f"No crash-*.txt or crash-*.log found in {target}", file=sys.stderr)
        return 2

    # THE INPUT SET, checked against the actual population.
    #
    # The other two guards ask whether the parser understood what it read. This one
    # asks whether it read everything it should have -- a different failure, and the
    # one that produced "8 log(s) [OK]" while 20 newer dumps sat unexamined in the
    # same folder, because only one extension matched. An accounting check cannot see
    # it: a denominator can only account for what it was handed.
    near_misses = []
    if target.is_dir():
        population = [q for q in target.iterdir() if q.is_file()]
        matched = {q.name for q in files}
        near_misses = [q.name for q in population
                       if q.name not in matched and CRASH_PREFIX_RE.match(q.name)]
    else:
        population = files

    parsed, unreadable, no_exception = [], [], []
    for f in files:
        try:
            rec = parse(f)
        except Exception as e:
            unreadable.append((f.name, str(e)))
            continue
        (parsed if rec else no_exception).append(rec if rec else f.name)

    # --- denominator FIRST -------------------------------------------------
    print("=" * 78)
    print(f"CRASH TRIAGE  --  {len(files)} log(s) in {target if target.is_dir() else target.parent}")
    print("=" * 78)
    # UNPARSED RATIO.
    #
    # The accounting line below balances by construction -- every file lands in
    # exactly one bucket -- so it cannot tell you the parser has stopped
    # understanding its input. That is not hypothetical: on CrashLoggerSSE 1.23.1
    # this tool read 1 of 7 real dumps and still printed RESULT: OK. The evidence
    # was on screen the whole time ("parsed: 1, no exception: 6") and nothing
    # treated it as a finding.
    #
    # A high unparsed share is the format-agnostic symptom of exactly that: it
    # would have flagged the 1.2x change without anyone knowing what changed, and
    # it flags the next one too. Threshold picked against real data rather than
    # taste: a Skyrim VR install with 28 genuine dumps has 1 unparsed (3.6%) -- a
    # crash at an address inside no loaded module, so there is no MODULE+OFFSET to
    # report and never will be. The 1.2x case was 86%. Requiring BOTH >25% and at
    # least 2 unparsed keeps the legitimate case quiet and a real format break loud.
    unparsed = len(no_exception) + len(unreadable)
    unparsed_pct = (100.0 * unparsed / len(files)) if files else 0.0
    # Nothing parsed at all is the WORST break, and it used to be the one case
    # that printed OK: the `if not parsed:` early return below decided the verdict
    # from `ok` alone and never consulted this flag. 6-of-7 unparsed reported
    # DEGRADED while 7-of-7 reported OK -- the worse the break, the cleaner the
    # report. It also freed single-file mode, where `unparsed >= 2` can never hold.
    total_break = unparsed > 0 and not parsed

    # FRAMES COVERAGE. `unparsed` only counts dumps whose EXCEPTION LINE failed.
    # Of the three 1.2x format changes, only one produces that; a changed stack
    # header or frame-marker syntax leaves every dump 'parsed' with zero frames
    # and no complaint. One dump legitimately lacking frames is ordinary; EVERY
    # parsed dump lacking them is the header having moved.
    complete = [r for r in parsed if r["complete"]]
    frameless = sum(1 for r in complete if not r["frames"])
    frames_broken = bool(complete) and frameless == len(complete)

    parse_degraded = (total_break
                      or (unparsed >= 2 and unparsed_pct > 25.0)
                      or frames_broken)

    if target.is_dir():
        print(f"files in folder : {len(population)}")
        print(f"  matched       : {len(files)}   (crash-*.txt / crash-*.log)")
        print(f"  near misses   : {len(near_misses)}"
              + ("   <-- LOOK: named like a dump, not matched by the pattern"
                 if near_misses else ""))
        for nm in near_misses:
            print(f"    !! {nm}")
    print(f"logs found      : {len(files)}")
    print(f"  parsed        : {len(parsed)}")
    print(f"  no exception  : {len(no_exception)}")
    print(f"  unreadable    : {len(unreadable)}")
    # Annotate only when the UNPARSED count is itself the problem. A frames-only
    # break leaves this at 0 and would otherwise read "0/3 (0%) <-- not
    # understanding these dumps", which is a contradiction on one line.
    print(f"  unparsed      : {unparsed}/{len(files)} ({unparsed_pct:.0f}%)"
          + ("   <-- the parser is not understanding these dumps"
             if (total_break or (unparsed >= 2 and unparsed_pct > 25.0)) else ""))
    accounted = len(parsed) + len(no_exception) + len(unreadable)
    ok = accounted == len(files)
    print(f"  {'-' * 14}")
    print(f"  accounted     : {accounted}   [{'OK' if ok else 'MISMATCH'} vs {len(files)} found]")
    for name, err in unreadable:
        print(f"    !! {name}: {err}")
    print()
    if parse_degraded and not parsed:
        print(f"\nRESULT: PARSER DEGRADED -- none of the {len(files)} dump(s) could be "
              f"parsed, so nothing above is a report on their contents. Either the "
              f"format moved (compare one against EXC_RE), or these are dumps with no "
              f"module attribution -- an exception line ending at the address, which "
              f"cannot be keyed on and never will be.")
        return 1

    if not parsed:
        print("RESULT: OK" if ok else "RESULT: ACCOUNTING MISMATCH")
        return 0 if ok else 1

    # --- signatures ---------------------------------------------------------
    sigs: Counter[str] = Counter()
    detail: dict[str, dict] = {}
    dates: defaultdict[str, list] = defaultdict(list)
    for r in parsed:
        key = f"{r['module']}+{r['offset']}"
        if r.get("cpp_info"):
            key += f"  {r['cpp_info']}"
        sigs[key] += 1
        detail.setdefault(key, r)
        dates[key].append(r["file"].replace("crash-", "").replace(".txt", "").replace(".log", ""))

    print(f"SIGNATURES  ({len(parsed)} crashes, {len(sigs)} distinct)")
    rows = sigs.most_common() if args.all else sigs.most_common(15)
    shown = 0
    for key, count in rows:
        shown += count
        r = detail[key]
        status, note = KNOWN.get(r["offset"], ("UNKNOWN", ""))
        tag = {"ACCEPTED": "[ACCEPTED - do not re-investigate]",
               "KNOWN": "[known]"}.get(status, "[** UNKNOWN **]")
        print(f"\n  {count} x  {key}   {tag}")
        print(f"        {r['type']}  {r['instr']}")
        if r.get("cpp_type") or r.get("cpp_info"):
            print(f"        C++    : {r.get('cpp_type', '')} {r.get('cpp_info', '')}".rstrip())
            if r.get("cpp_throw"):
                print(f"        throw  : {r['cpp_throw']}")
        if r["symbol"]:
            print(f"        symbol : {r['symbol']}")
        if note:
            print(f"        KB     : {note}")
        print(f"        seen   : {', '.join(sorted(dates[key]))}")
        if status == "UNKNOWN" and r["frames"]:
            print(f"        top {args.frames} frames:")
            for fr in r["frames"][:args.frames]:
                print(f"          {fr}")

    remainder = len(parsed) - shown
    print(f"\n  {remainder} x  (signatures not shown above)")
    sig_total = sum(sigs.values())
    sig_ok = sig_total == len(parsed)
    print(f"  {'-' * 6}")
    print(f"  {sig_total} accounted   "
          f"[{'OK' if sig_ok else 'MISMATCH'} vs {len(parsed)} parsed crashes]")

    unknown = sum(c for k, c in sigs.items() if detail[k]["offset"] not in KNOWN)
    print(f"\n  known/accepted : {len(parsed) - unknown}")
    print(f"  UNKNOWN        : {unknown}   <-- the ones worth looking at")

    all_ok = ok and sig_ok and not parse_degraded and not near_misses
    unparsed_clause = unparsed >= 2 and unparsed_pct > 25.0
    if parse_degraded and frames_broken and not unparsed_clause:
        print(f"\nRESULT: PARSER DEGRADED -- all {len(parsed)} parsed dump(s) yielded "
              f"ZERO stack frames. The exception line still parses, so this is the "
              f"stack header or frame syntax having changed; compare a dump against "
              f"FRAME_RE and the CALL STACK header match.")
    elif parse_degraded:
        print(f"\nRESULT: PARSER DEGRADED -- {unparsed} of {len(files)} dumps "
              f"({unparsed_pct:.0f}%) could not be parsed. The signatures above cover only "
              f"the {len(parsed)} that could be. This usually means CrashLogger's format "
              f"changed; compare a failing dump against EXC_RE and FRAME_RE.")
    elif near_misses:
        # Distinct from ACCOUNTING MISMATCH: the arithmetic is fine, the input set is
        # not. Calling it "accounting" sends the reader to the wrong place.
        print(f"\nRESULT: INPUT SET INCOMPLETE -- {len(near_misses)} file(s) look like "
              f"crash dumps but did not match the pattern. Widen CRASH_NAME_RE (or "
              f"narrow CRASH_PREFIX_RE if these are not dumps); do not trust the totals "
              f"above until then.")
    else:
        print("\nRESULT: OK" if all_ok else "\nRESULT: ACCOUNTING MISMATCH")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
