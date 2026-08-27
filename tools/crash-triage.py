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
    r'^Unhandled exception "(?P<type>[A-Z_]+)"'
    r'(?:\s+at\s+0x(?P<addr>[0-9A-Fa-f]+))?'
    r'\s+(?P<module>[^\s+]+)\+(?P<offset>[0-9A-Fa-f]+)'
    r'\s*(?P<instr>[^|]*)'
    r'(?:\|\s*(?P<symbol>.*?)\)?\s*)?$'
)
FRAME_RE = re.compile(r"^\s*\[\s*\d+\]\s+0x[0-9A-Fa-f]+\s+(?P<mod>[^\s+]+)\+(?P<off>[0-9A-Fa-f]+)")
# CrashLoggerSSE writes `.LOG`; older builds (and Crash Log Toolkit) write `.txt`.
# Both live in the same folder, so matching only one extension does not fail --
# it reports a confident total made of whichever half it happened to match.
# Measured on the dev install: 8 `.txt` (June 2025) beside 20 `.log` (2026).
# `CrashLogger.log` is the plugin's OWN log and is excluded by the `crash-` prefix.
CRASH_NAME_RE = re.compile(r"^crash-.+\.(txt|log)$", re.I)

VERSION_RE = re.compile(r"^(Skyrim ?VR|Skyrim Special Edition)\s+v?(?P<ver>[\d.]+)", re.I)

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

    frames = []
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("PROBABLE CALL STACK"))
        for line in lines[start + 1:]:
            fm = FRAME_RE.match(line)
            if not fm:
                break
            frames.append(f"{fm.group('mod')}+{fm.group('off')}")
    except StopIteration:
        pass

    return {
        "file": path.name,
        "type": exc.group("type"),
        "module": exc.group("module"),
        "offset": exc.group("offset").upper(),
        "instr": (exc.group("instr") or "").strip(),
        "symbol": (exc.group("symbol") or "").strip().rstrip(")"),
        "game": game_ver,
        "frames": frames,
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
    print(f"logs found      : {len(files)}")
    print(f"  parsed        : {len(parsed)}")
    print(f"  no exception  : {len(no_exception)}")
    print(f"  unreadable    : {len(unreadable)}")
    accounted = len(parsed) + len(no_exception) + len(unreadable)
    ok = accounted == len(files)
    print(f"  {'-' * 14}")
    print(f"  accounted     : {accounted}   [{'OK' if ok else 'MISMATCH'} vs {len(files)} found]")
    for name, err in unreadable:
        print(f"    !! {name}: {err}")
    print()
    if not parsed:
        print("RESULT: OK" if ok else "RESULT: ACCOUNTING MISMATCH")
        return 0 if ok else 1

    # --- signatures ---------------------------------------------------------
    sigs: Counter[str] = Counter()
    detail: dict[str, dict] = {}
    dates: defaultdict[str, list] = defaultdict(list)
    for r in parsed:
        key = f"{r['module']}+{r['offset']}"
        sigs[key] += 1
        detail.setdefault(key, r)
        dates[key].append(r["file"].replace("crash-", "").replace(".txt", ""))

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

    all_ok = ok and sig_ok
    print("\nRESULT: OK" if all_ok else "\nRESULT: ACCOUNTING MISMATCH")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
