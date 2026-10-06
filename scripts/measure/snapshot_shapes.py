#!/usr/bin/env python3
"""Snapshot latency, part 1: the per-call cost of each shape of `snapshot.take`, on a synthetic script tree.

    python scripts/measure/snapshot_shapes.py --tree <new scratch folder> --out <shapes.json>
                                             [--linked 3049] [--runs 200] [--repo <toolkit checkout>]

Builds a tree with the Plan 6 Task 6 script counts (11,898 .psc/.pex files, 3,086-byte bodies = 36.7 MB as measured
on a real install), with --linked of them hard-linked from a staging folder (a mod manager's hard-link deployment;
3,049 on the measured install), takes the cold baseline, then times the three per-call shapes in process:
  in_window      a call inside the rate-limit window, nothing newer
  out_unchanged  a call outside the window, nothing changed (full index comparison)
  out_changed    a call outside the window after one .psc changed
The samples (ms) go to --out as JSON for snapshot_stream.py. The tree is NOT removed: delete it yourself (it is
~77 MB plus the store). --tree must not exist yet; nothing outside it is written.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

COUNTS = [("Data/Scripts", ".pex", 6336), ("Data/Scripts", ".psc", 7), ("Data/Scripts/sub", ".pex", 17),
          ("Data/Scripts/Source", ".psc", 2134), ("Data/Scripts/Source/sub", ".psc", 82),
          ("Data/Source/Scripts", ".psc", 3322)]
TOTAL = sum(c for _d, _e, c in COUNTS)            # 11,898
BODY = 36726229 // TOTAL                          # 3,086 B


def build(tree: str, linked_want: int):
    if os.path.exists(tree):
        raise SystemExit("--tree exists already (refusing to write into it): " + tree)
    staging = os.path.join(tree, "staging")
    os.makedirs(staging)
    every = max(1, TOTAL // max(1, linked_want)) if linked_want < TOTAL else 1
    n = linked = 0
    for d, ext, count in COUNTS:
        full = os.path.join(tree, *d.split("/"))
        os.makedirs(full, exist_ok=True)
        for i in range(count):
            name = "Script_{:05d}_SomeModPrefix{}".format(i, ext)
            body = ("{}/{}\n".format(d, name).encode() * (BODY // 40 + 1))[:BODY]
            target = os.path.join(full, name)
            if linked < linked_want and n % every == 0:
                src = os.path.join(staging, "{}_{}".format(d.replace("/", "_"), name))
                with open(src, "wb") as fh:
                    fh.write(body)
                os.link(src, target)
                linked += 1
            else:
                with open(target, "wb") as fh:
                    fh.write(body)
            n += 1
    return n, linked


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", required=True, help="a NEW scratch folder to build the synthetic install in")
    ap.add_argument("--out", required=True, help="JSON file for the samples")
    ap.add_argument("--linked", type=int, default=3049, help="how many scripts are hard links (default 3049)")
    ap.add_argument("--runs", type=int, default=200, help="samples per shape (default 200)")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]),
                    help="toolkit checkout whose tools/skyguard is measured (default: this one)")
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(a.repo, "tools"))
    from skyguard import audit as audit_mod, snapshot  # noqa: E402

    tree = os.path.abspath(a.tree)
    n, linked = build(tree, a.linked)
    print("files {}  hard-linked {}  body {} B  RATE {} s  RESTAT_BOX {} s".format(
        n, linked, BODY, snapshot.RATE, snapshot.RESTAT_BOX))
    au = audit_mod.Audit(tree)
    t = time.perf_counter()
    snapshot.take(tree, au, "baseline", box=900, rate=0)
    print("cold baseline {:.1f} s".format(time.perf_counter() - t))
    now = time.time() + 10 ** 7
    snapshot.take(tree, au, "settle", now=now, rate=0)
    shapes = {"out_unchanged": [], "in_window": [], "out_changed": []}
    target = os.path.join(tree, "Data", "Scripts", "Source", "Script_00003_SomeModPrefix.psc")
    for _ in range(a.runs):
        now += snapshot.RATE + 1
        t = time.perf_counter()
        snapshot.take(tree, au, "make", now=now)
        shapes["out_unchanged"].append((time.perf_counter() - t) * 1000)
    for _ in range(a.runs):
        t = time.perf_counter()
        snapshot.take(tree, au, "make", now=now + 1)
        shapes["in_window"].append((time.perf_counter() - t) * 1000)
    for _ in range(a.runs):
        with open(target, "ab") as fh:
            fh.write(b"z")
        now += snapshot.RATE + 1
        t = time.perf_counter()
        snapshot.take(tree, au, "make", now=now)
        shapes["out_changed"].append((time.perf_counter() - t) * 1000)
    for k, xs in shapes.items():
        print("{:<14} n={} min {:.1f} median {:.1f} p95 {:.1f} max {:.1f} ms".format(
            k, len(xs), min(xs), statistics.median(xs), pct(xs, 95), max(xs)))
    print("worst single-shape p95: {:.1f} ms".format(max(pct(xs, 95) for xs in shapes.values())))
    log = (snapshot.store_dir(tree).parent / "AUDIT_LOG.txt").read_text(encoding="utf-8", errors="replace")
    print("RESTAT-PARTIAL lines:", log.count("RESTAT-PARTIAL"))
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"linked": linked, "files": n, "shapes": shapes}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
