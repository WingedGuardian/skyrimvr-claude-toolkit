#!/usr/bin/env python3
"""Record-level diff of two plugins: which records were ADDED, REMOVED or CHANGED, and which subrecords differ.
READ-ONLY: parses the bytes directly (tools/esp_records.py), independent of xEdit.

    python tools/esp-record-diff.py OLD.esp NEW.esp

Typical use: the installed copy against a copy from the mod's archive ("is my plugin pristine, or was it edited?"),
or an old version against an update ("what does this update remove?"). Records are compared by their decompressed
bodies and flags (the compressed bit aside), so a re-save that only recompresses is not a change, and the first line
says whether the files are byte-identical at all. Records are matched by FormID in each plugin's own master
numbering: when the master lists differ, an override's FormID can shift and show as removed + added, so read the
masters lines first.

Exit: 0 header and every record identical · 1 something differs, or a plugin was read only partly (a duplicate
FormID makes a FormID-keyed comparison incomplete: INCONCLUSIVE) · 2 usage, or a file this parser cannot walk.
"""
from __future__ import annotations

import collections
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esp_records import PluginError, read, subrecs  # noqa: E402


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def changed_tags(body_a, body_b):
    issues = []
    a, b = collections.Counter(subrecs(body_a, problems=issues)), collections.Counter(subrecs(body_b, problems=issues))
    if issues:
        return "(malformed subrecords)"
    tags = sorted({t for (t, _v) in (a - b) + (b - a)})
    return ",".join(tags) if tags else "(same subrecords, different order)"


def diff(path_a, path_b) -> int:
    pa, pb = read(path_a), read(path_b)
    same_bytes = _sha(path_a) == _sha(path_b)
    print("byte-identical: {}".format("yes" if same_bytes else "no"))
    print("masters old: {}".format(pa.masters))
    print("masters new: {}".format(pb.masters))
    print("header flags old/new: {:08X} {:08X}".format(pa.header.flags, pb.header.flags))
    print("records old={} new={}".format(len(pa.records), len(pb.records)))
    differs = 0
    if pa.header.sha1 != pb.header.sha1 or pa.header.flags != pb.header.flags:
        differs += 1
        print("HEADER  TES4 changed  subrecords: {}".format(changed_tags(pa.header.body, pb.header.body)))
    a, b = pa.by_formid, pb.by_formid
    for fid in sorted(set(a) - set(b)):
        differs += 1
        print("REMOVED {:08X} {} {}".format(fid, a[fid].type, a[fid].edid))
    for fid in sorted(set(b) - set(a)):
        differs += 1
        print("ADDED   {:08X} {} {}".format(fid, b[fid].type, b[fid].edid))
    for fid in sorted(set(a) & set(b)):
        ra, rb = a[fid], b[fid]
        if ra.sha1 != rb.sha1 or ra.flags != rb.flags or ra.type != rb.type:
            differs += 1
            what = changed_tags(ra.body, rb.body) if ra.sha1 != rb.sha1 else "(record flags only)"
            print("CHANGED {:08X} {} {}  subrecords: {}{}".format(
                fid, ra.type, ra.edid, what, "" if ra.type == rb.type else "  type -> " + rb.type))
    problems = [("old", p) for p in pa.problems] + [("new", p) for p in pb.problems]
    for side, p in problems:
        print("PROBLEM ({}) {}".format(side, p))
    if problems:
        print("INCONCLUSIVE: {} problem(s) above; the record comparison may be incomplete".format(len(problems)))
    print("differences: {}".format(differs))
    return 1 if differs or problems else 0


def main(argv) -> int:
    if len(argv) != 2 or any(a.startswith("--") for a in argv):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        return diff(argv[0], argv[1])
    except (OSError, PluginError) as e:
        print("esp-record-diff: {}".format(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
