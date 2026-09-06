"""papyrus-triage -- turn Papyrus.N.log into a bucketed, attributed triage.

Modelled on the X4 toolkit's `x4debug triage`, which exists because a hand-rolled
`grep | sort | uniq -c` has no way to notice it dropped a shape. Two properties
are non-negotiable:

  1. The rows must SUM TO THE INPUT. Every entry is accounted for.
  2. The unclassified row PRINTS EVEN WHEN IT IS ZERO. Silence about the
     remainder is how a triage tool starts lying.

Both are checked against INDEPENDENTLY COUNTED totals. The first version of this
file computed the remainder as `total - shown` and then asserted
`shown + remainder == total`, which is `x == x` -- a check that could never fail,
in the tool whose headline claim is that its checks cannot silently pass. The
counters below are incremented in the bucketing loop itself, so a shape that
fails to bucket really does make the totals disagree.

One correction to the X4 model, measured rather than inherited: a Papyrus entry
is MULTI-LINE. Papyrus.1.log has 8426 lines but only 5292 timestamped entries --
the rest are `stack:` continuations belonging to the entry above. The unit of
accounting is the ENTRY; a line-based sum is ~37% wrong.

Severity is spelled BOTH ways in the same file: `warning:` (99) and `WARNING:`
(662) in Papyrus.1.log. A case-sensitive match loses 87% of them.

Nothing is hidden. Known-benign shapes are LABELLED, never dropped: the job is
to rank attention, not to decide what the reader may see.

Usage:
    python tools/papyrus-triage.py [logfile] [--top N] [--all]
    (default logfile: the newest Papyrus.*.log in the standard location)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Discovered, not hardcoded -- and never picked silently when a machine
# carries more than one Skyrim install. Override with $PAPYRUS_LOG_DIR.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

ENTRY_RE = re.compile(r"^\[\d{2}/\d{2}/\d{4} - [^\]]+\]\s*(.*)$")
SEVERITY_RE = re.compile(r"^(error|warning)\s*:\s*(.*)$", re.I)
SEVERITIES = ("error", "warning", "info")

# Normalisation applied before bucketing. Each pattern collapses an
# instance-specific token so identical shapes group; anything carrying signal
# (plugin names, script names) is deliberately left alone.
NORMALISERS = [
    (re.compile(r"\(\s*[0-9A-Fa-f]{8}\s*\)"), "(FORMID)"),
    (re.compile(r"\[\s*[0-9A-Fa-f]{8}\s*\]"), "[FORMID]"),
    (re.compile(r"\b[0-9A-Fa-f]{8}\b"), "FORMID"),
    (re.compile(r"::temp\d+"), "::tempN"),
    (re.compile(r"::NoneVar\d*"), "::NoneVarN"),
    (re.compile(r"\bLine \d+"), "Line N"),
    (re.compile(r"\b\d{3,}\b"), "N"),
]

# Known noise, with the reason. LABELLED in the output, never removed -- a
# triage tool that silently drops rows is indistinguishable from one that
# missed them.
BENIGN = [
    (re.compile(r"dunKilkreathCrystalNodeSCRIPT|dunLabyrinthianMazeTabletScript"),
     "vanilla dungeon script, unbound property on an unloaded cell"),
    (re.compile(r"Cannot check to for a None item to be equipped"),
     "vanilla equip check on a None item"),
]

# Attribution, most specific first. The quoted-".psc" form appears only in stack
# continuations; the unquoted forms are what the runtime log actually emits and
# dominate real entries, so both are needed or attribution is mostly "-".
ATTRIBUTORS = [
    re.compile(r'File "([^"]+)" does not exist', re.I),
    re.compile(r'"([A-Za-z0-9_]+\.psc)"'),
    re.compile(r"on script ([A-Za-z0-9_]+)", re.I),
    re.compile(r"on unlinked object ([A-Za-z0-9_]+)", re.I),
    re.compile(r"used in ([A-Za-z0-9_]+)\."),
    re.compile(r"\bbind script ([A-Za-z0-9_]+)", re.I),
    re.compile(r"Script ([A-Za-z0-9_]+) cannot be bound", re.I),
    re.compile(r"variable ([A-Za-z0-9_:]+) on script", re.I),
]


def normalise(msg: str) -> str:
    out = msg
    for pattern, repl in NORMALISERS:
        out = pattern.sub(repl, out)
    return out.strip()


def attribute(msg: str, stack: list[str]) -> str:
    for pattern in ATTRIBUTORS:
        m = pattern.search(msg)
        if m:
            return m.group(1)
    for line in stack:
        m = re.search(r'"([A-Za-z0-9_]+\.psc)"', line)
        if m:
            return m.group(1)
    return "-"


def benign_reason(shape: str) -> str | None:
    for pattern, reason in BENIGN:
        if pattern.search(shape):
            return reason
    return None


def parse(path: Path):
    """Return (entries, total_lines, undated_lines).

    An entry is (severity, message, stack_lines). Continuation lines attach to
    the entry above them.
    """
    entries: list[tuple[str, str, list[str]]] = []
    total = 0
    undated = 0
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in raw:
        total += 1
        m = ENTRY_RE.match(line)
        if not m:
            undated += 1
            if entries:
                entries[-1][2].append(line)
            continue
        body = m.group(1)
        sm = SEVERITY_RE.match(body)
        if sm:
            entries.append((sm.group(1).lower(), sm.group(2), []))
        else:
            entries.append(("info", body, []))
    return entries, total, undated


def bucket(subset):
    """Group entries by normalised shape.

    Returns (shapes, who, bucketed, failed). `bucketed` and `failed` are counted
    in this loop, independently of len(subset), so the caller's accounting check
    is a real comparison rather than an identity.
    """
    shapes: Counter[str] = Counter()
    who: defaultdict[str, Counter[str]] = defaultdict(Counter)
    bucketed = 0
    failed = 0
    for msg, stack in subset:
        try:
            shape = normalise(msg)
            shapes[shape] += 1
            who[shape][attribute(msg, stack)] += 1
            bucketed += 1
        except Exception:
            failed += 1
    return shapes, who, bucketed, failed


def main() -> int:
    # The log can contain any codepoint a mod author used; Windows stdout is
    # cp1252 here, so a CJK or Cyrillic name would otherwise abort the report
    # mid-write with UnicodeEncodeError and no RESULT line.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile", nargs="?", help="Papyrus log (default: newest)")
    ap.add_argument("--top", type=int, default=15, help="rows per severity (default 15)")
    ap.add_argument("--all", action="store_true", help="show every row")
    args = ap.parse_args()

    auto_selected = not args.logfile
    if args.logfile:
        path = Path(args.logfile)
    else:
        log_dir, _ = skyrim_paths.resolve_dir("PAPYRUS_LOG_DIR", "Logs/Script")
        candidates = sorted(log_dir.glob("Papyrus.*.log"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print(f"No Papyrus.*.log found in {log_dir}", file=sys.stderr)
            return 2
        path = candidates[0]

    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 2

    STALE_DAYS = 7
    age_days = None
    if auto_selected:
        import time
        age_days = (time.time() - path.stat().st_mtime) / 86400.0

    entries, total_lines, undated = parse(path)

    # --- denominator FIRST, before any finding -------------------------------
    print("=" * 78)
    print(f"PAPYRUS TRIAGE  --  {path.name}")
    print("=" * 78)
    print(f"lines read           : {total_lines}")
    print(f"timestamped entries  : {len(entries)}")
    print(f"continuation lines   : {undated}   (stack traces, attached to the entry above)")
    print()

    if age_days is not None:
        marker = "   <-- STALE: older than %d days" % STALE_DAYS if age_days > STALE_DAYS else ""
        print("log age        : %.1f days%s" % (age_days, marker))
        print()

    by_sev = Counter(sev for sev, _, _ in entries)
    unexpected = {s: n for s, n in by_sev.items() if s not in SEVERITIES}

    print("by severity:")
    printed_total = 0
    for sev in SEVERITIES:
        n = by_sev.get(sev, 0)
        printed_total += n          # sum only what is actually shown
        print(f"  {sev:<9}: {n}")
    for sev, n in sorted(unexpected.items()):
        printed_total += n
        print(f"  {sev:<9}: {n}   <-- severity outside the expected set")
    print(f"  {'-' * 9}  {'-' * 6}")
    sev_ok = printed_total == len(entries)
    # NOTE: sev_ok cannot fail by construction. SEVERITY_RE only ever yields
    # error/warning, and everything else defaults to info, so `unexpected` is always
    # empty and the rows always sum to the entry count. Kept as a cheap internal
    # assertion and labelled as such -- the REAL guards here are the bucket()
    # accounting below (which can genuinely go red) and the staleness check above.
    print(f"  {'TOTAL':<9}: {printed_total}   "
          f"[{'OK' if sev_ok else 'MISMATCH'} vs {len(entries)} entries]")
    print("             (internal only: SEVERITY_RE yields error/warning and everything")
    print("              else defaults to info, so these rows always sum. Cannot go red.)")
    if not sev_ok:
        print("  !! the printed severity rows do not sum to the entry count")
    print()

    exit_code = 0 if sev_ok else 1

    # --- buckets, per severity ----------------------------------------------
    for sev in ("error", "warning"):
        subset = [(m, st) for s, m, st in entries if s == sev]
        if not subset:
            continue
        shapes, who, bucketed, failed = bucket(subset)
        in_buckets = sum(shapes.values())

        print(f"{sev.upper()} buckets  ({len(subset)} entries, {len(shapes)} distinct shapes)")
        print(f"  {'count':>6}  {'attribution':<34}  shape")
        rows = shapes.most_common() if args.all else shapes.most_common(args.top)
        shown = 0
        for shape, count in rows:
            shown += count
            attrib = who[shape].most_common(1)[0][0]
            reason = benign_reason(shape)
            tag = "  [benign: " + reason + "]" if reason else ""
            text = shape if len(shape) <= 96 else shape[:93] + "..."
            print(f"  {count:>6}  {attrib[:34]:<34}  {text}{tag}")

        # Remainder comes from the bucket totals, NOT from (len(subset) - shown),
        # so the sum below compares two independently derived numbers.
        remainder = in_buckets - shown
        print(f"  {remainder:>6}  {'-':<34}  (not shown above)")
        if failed:
            print(f"  {failed:>6}  {'-':<34}  (FAILED TO BUCKET)")
        print(f"  {'-' * 6}")
        accounted = in_buckets + failed
        ok = accounted == len(subset) and bucketed == in_buckets
        print(f"  {accounted:>6}  ACCOUNTED FOR"
              f"   [{'OK' if ok else 'MISMATCH'} vs {len(subset)} {sev} entries]")
        if not ok:
            print(f"  !! {len(subset) - accounted} {sev} entries are unaccounted for")
            exit_code = 1
        print()

    print("RESULT: OK" if exit_code == 0 else "RESULT: ACCOUNTING MISMATCH")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
