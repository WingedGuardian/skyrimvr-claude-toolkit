#!/usr/bin/env python3
"""Snapshot latency, part 2: the per-call p95 over a REAL call stream. Reads the transcripts, never writes them.

    python scripts/measure/snapshot_stream.py --transcripts <dir> [--transcripts <dir> ...]
                                             --shapes <shapes.json> [--shapes <shapes.json> ...] [--repo <checkout>]

--transcripts: a Claude Code project transcript folder of the install being priced (for example
~/.claude/projects/<the install's project folder>); every *.jsonl under it is read, nested subagent transcripts
included. --shapes: the JSON written by snapshot_shapes.py.

1. Every tool_use in the transcripts, de-duplicated by tool_use id, with its entry timestamp. The snapshot hook is
   wired to Bash|PowerShell|Monitor (.claude/settings.json), and the store's rate limit is per INSTALL, so every
   session is merged into one timeline.
2. A hook call whose command is read-only (snapshot.read_only) costs ~0: it returns before any walk.
3. Script-change events, from the same history: Edit/Write/MultiEdit/NotebookEdit of a .psc/.pex, and shell commands
   that run Caprica / Champollion / AutoMod papyrus, or copy/move a .psc/.pex. A change makes the next hook call a
   full comparison that finds a change (inside the window too: a newer file). STATED ASSUMPTION: this heuristic is
   the change rate.
4. The rate limit (snapshot.RATE s) is replayed into classes in_window / out_unchanged / out_changed; each call's cost
   is drawn from that class's measured samples; read-only calls cost 0. Reported: the mean of 50 resamples of the p95
   over every hook call and over walking calls only (the gate reads the latter).
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import random
import re
import sys
from pathlib import Path

HOOKED = ("Bash", "PowerShell", "Monitor")
FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SCRIPT = re.compile(r"\.(psc|pex)$", re.I)
CHANGE_CMD = re.compile(r"caprica|champollion|automod-cli\.sh\s+papyrus|"
                        r"\b(cp|mv|copy-item|move-item|install)\b[^\n]*\.(pex|psc)\b", re.I)


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def q(xs, p):
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))] if xs else float("nan")


def read_events(dirs):
    seen_ids, events, files = set(), [], []
    for d in dirs:
        files.extend(sorted(Path(d).rglob("*.jsonl")))
    for f in files:
        try:
            lines = f.read_bytes().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("type") != "assistant" or not e.get("timestamp"):
                continue
            content = (e.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("id") in seen_ids:
                    continue
                seen_ids.add(b.get("id"))
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                events.append((ts(e["timestamp"]), b.get("name", ""), inp))
    events.sort(key=lambda x: x[0])
    return files, events


def classify(events, snapshot):
    hook, changes = [], 0
    for t, name, inp in events:
        cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""
        is_change = (name in FILE_TOOLS and SCRIPT.search(str(inp.get("file_path") or inp.get("notebook_path") or ""))) \
            or (name in HOOKED and bool(CHANGE_CMD.search(cmd)))
        if name in HOOKED:
            hook.append([t, snapshot.read_only(cmd) if cmd else True, bool(is_change)])
        elif is_change:
            hook.append([t, None, True])          # a change-only event (no hook call)
        changes += bool(is_change)
    last, pending, classes = None, False, []
    for t, skip, chg in hook:
        if skip is None:
            pending = True
            continue
        if skip:
            classes.append("skip")
        elif last is None or t - last >= snapshot.RATE:
            classes.append("out_changed" if pending else "out_unchanged")
            last, pending = t, False
        elif pending:
            classes.append("out_changed")         # a newer file inside the window: full comparison
            last, pending = t, False
        else:
            classes.append("in_window")
        if chg:
            pending = True                        # the command changes scripts; the NEXT call sees it
    return hook, changes, classes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--transcripts", action="append", required=True, help="transcript folder (repeatable)")
    ap.add_argument("--shapes", action="append", required=True, help="snapshot_shapes.py JSON (repeatable)")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]),
                    help="toolkit checkout whose tools/skyguard supplies RATE and read_only (default: this one)")
    ap.add_argument("--seed", type=int, default=14)
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(a.repo, "tools"))
    from skyguard import snapshot  # noqa: E402

    files, events = read_events(a.transcripts)
    hook, changes, classes = classify(events, snapshot)
    calls = [h for h in hook if h[1] is not None]
    nonskip_t = [h[0] for h in calls if not h[1]]
    gaps = sorted(b - a_ for a_, b in zip(nonskip_t, nonskip_t[1:]))
    cnt = collections.Counter(classes)
    print("transcripts {}  tool_use events {}  hook calls {}  ({} skip, {} walk)  script-change events {}".format(
        len(files), len(events), len(calls), cnt["skip"], len(calls) - cnt["skip"], changes))
    if events:
        print("history span: {} .. {}".format(datetime.datetime.fromtimestamp(events[0][0]).date(),
                                             datetime.datetime.fromtimestamp(events[-1][0]).date()))
    print("gap between consecutive walking calls (s): p10 {:.1f}  p25 {:.1f}  median {:.1f}  p75 {:.1f}  "
          "p90 {:.1f}".format(q(gaps, 10), q(gaps, 25), q(gaps, 50), q(gaps, 75), q(gaps, 90)))
    print("share of gaps < RATE ({} s): {:.1%}".format(
        snapshot.RATE, sum(g < snapshot.RATE for g in gaps) / max(1, len(gaps))))
    print("classes:", dict(cnt))
    if not classes:
        print("no hook calls found -- nothing to price")
        return 1
    rng = random.Random(a.seed)
    for path in a.shapes:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        shapes = d["shapes"]
        all_p95, walk_p95 = [], []
        for _ in range(50):
            costs = [0.0 if c == "skip" else rng.choice(shapes[c]) for c in classes]
            walk = [x for x, c in zip(costs, classes) if c != "skip"]
            all_p95.append(q(sorted(costs), 95))
            walk_p95.append(q(sorted(walk), 95))
        print("{} (hard-linked {}): stream p95 over ALL hook calls {:.1f} ms; over walking calls only {:.1f} ms "
              "(mean of 50 resamples); worst single-shape p95 {:.1f} ms".format(
                  os.path.basename(path), d["linked"], sum(all_p95) / 50, sum(walk_p95) / 50,
                  max(q(sorted(xs), 95) for xs in shapes.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
