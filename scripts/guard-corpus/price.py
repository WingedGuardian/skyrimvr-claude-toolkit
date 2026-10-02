#!/usr/bin/env python3
"""Price a guard-rule change over this machine's REAL tool-call history: old engine vs new engine.

    price.py --base <rev> [--new <dir>] --history ~/.claude/projects --out-dir <outside repo> [--only-rule ID]

The base tree is `git archive <rev>:tools/skyguard` (or --base-dir for an already-exported tree); the new
tree is --new (a skyguard package directory; default: this checkout's tools/skyguard). Each engine runs in
its own subprocess (engine_eval.py, PYTHONPATH = that tree), so two versions of one package never share an
interpreter. Items are deduplicated on (tool_name, command|file_path|mcp-input, cwd).

A rule "newly fires" for an item when its id is in new.fired and not in old.fired. Counted per rule and per
direction: newly-deny / newly-ask / newly-advise / no-longer-deny / no-longer-ask / no-longer-advise.
Decision movement (the old x new matrix) is counted separately, so an item whose decision did not change
but whose reason moved to a different rule is visible too ("reason_moved").

Raw items hold secrets and private paths, so they go ONLY to --out-dir, which must be outside the repo
(refused otherwise). Only the printed / summary.json COUNTS belong in the repo.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DIRECTIONS = ("newly-deny", "newly-ask", "newly-advise", "no-longer-deny", "no-longer-ask", "no-longer-advise")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check_out_dir(out_dir: Path) -> Path:
    out_dir = out_dir.resolve()
    repo = os.path.normcase(str(REPO))
    here = os.path.normcase(str(out_dir))
    if here == repo or here.startswith(repo + os.sep):
        raise SystemExit(f"--out-dir must be outside the repo (raw commands can hold secrets): {out_dir}")
    return out_dir


def export_base(rev: str, dest: Path) -> Path:
    """`git archive <rev>:tools/skyguard` into dest/skyguard; returns dest (the PYTHONPATH entry)."""
    r = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", f"{rev}:tools/skyguard"],
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"git archive {rev}:tools/skyguard failed: {r.stderr.decode(errors='replace')[:300]}")
    pkg = dest / "skyguard"
    pkg.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        tf.extractall(pkg)
    return dest


def stage_tree(src: Path, dest: Path) -> Path:
    """Copy a skyguard package directory to dest/skyguard (its own directory name does not matter)."""
    shutil.copytree(src, dest / "skyguard", ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def run_engine(tree: Path, items_path: Path, out_path: Path, root: str, root_win: str) -> list[dict]:
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, str(HERE / "engine_eval.py"), "--items", str(items_path),
                        "--out", str(out_path), "--root", root, "--root-win", root_win],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"engine_eval failed for {tree}: {r.stderr[-600:]}")
    return [json.loads(x) for x in out_path.read_text(encoding="utf-8").splitlines() if x.strip()]


def moves(old: dict, new: dict) -> list[tuple[str, str]]:
    """(rule id, direction) for every rule whose firing differs between the two engines on one item."""
    old_fired, new_fired = _dedup(old["fired"]), _dedup(new["fired"])
    old_ids, new_ids = {i for i, _ in old_fired}, {i for i, _ in new_fired}
    out = []
    for rid, dec in new_fired:
        if rid not in old_ids:
            out.append((rid, "newly-" + dec))
    for rid, dec in old_fired:
        if rid not in new_ids:
            out.append((rid, "no-longer-" + dec))
    return out


def _dedup(fired) -> list[tuple[str, str]]:
    seen, out = set(), []
    for rid, dec in fired:
        if rid not in seen:
            seen.add(rid)
            out.append((rid, dec))
    return out


def price(base_tree: Path, new_tree: Path, history: Path, out_dir: Path, only_rule: str | None = None,
          root: str | None = None, root_win: str | None = None) -> dict:
    out_dir = check_out_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ee = _load("engine_eval")
    root, root_win = root or ee.DEFAULT_ROOT, root_win or ee.DEFAULT_ROOT_WIN
    replay = _load("replay")
    items, raw_counts = replay.extract(history, wide=True)
    items_path = out_dir / "items.jsonl"
    items_path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items),
                          encoding="utf-8", newline="\n")
    old = run_engine(base_tree, items_path, out_dir / "old.results.jsonl", root, root_win)
    new = run_engine(new_tree, items_path, out_dir / "new.results.jsonl", root, root_win)
    if not (len(old) == len(new) == len(items)):
        raise SystemExit(f"result count mismatch: items={len(items)} old={len(old)} new={len(new)}")

    per_rule = collections.defaultdict(collections.Counter)
    matrix, per_tool = collections.Counter(), collections.Counter()
    unique_by_tool = collections.Counter(i["tool_name"] if not i["tool_name"].startswith("mcp__") else "mcp__*"
                                         for i in items)
    reason_moved = parse_fail_old = parse_fail_new = 0
    for old_dir in out_dir.glob("*__*.jsonl"):
        old_dir.unlink()                                    # a stale hand-reading file would lie about this run
    files: dict[str, io.TextIOBase] = {}
    try:
        for item, o, n in zip(items, old, new):
            matrix[f"{o['decision']}>{n['decision']}"] += 1
            parse_fail_old += not o["parse_ok"]
            parse_fail_new += not n["parse_ok"]
            if o["decision"] == n["decision"] and {i for i, _ in o["fired"]} != {i for i, _ in n["fired"]}:
                reason_moved += 1
            for rid, direction in moves(o, n):
                if only_rule and rid != only_rule:
                    continue
                per_rule[rid][direction] += 1
                key = f"{re.sub(r'[^A-Za-z0-9-]', '_', rid)}__{direction}.jsonl"
                if key not in files:
                    files[key] = (out_dir / key).open("w", encoding="utf-8", newline="\n")
                files[key].write(json.dumps({"item": item, "old": o, "new": n}, ensure_ascii=False) + "\n")
    finally:
        for fh in files.values():
            fh.close()
    totals = {d: sum(c[d] for c in per_rule.values()) for d in DIRECTIONS}
    summary = {
        "N": len(items), "N_per_tool": dict(unique_by_tool), "raw_tool_calls": raw_counts,
        "matrix": dict(sorted(matrix.items())), "reason_moved": reason_moved,
        "parse_failures": {"old": parse_fail_old, "new": parse_fail_new},
        "totals": totals,
        "rules": {rid: {d: c[d] for d in DIRECTIONS if c[d]} for rid, c in sorted(per_rule.items())},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base", help="git revision whose tools/skyguard is the OLD engine")
    g.add_argument("--base-dir", type=Path, help="an already-exported OLD skyguard package directory")
    ap.add_argument("--new", type=Path, default=REPO / "tools" / "skyguard", help="the NEW skyguard package directory")
    ap.add_argument("--history", type=Path, default=Path.home() / ".claude" / "projects")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only-rule", default=None)
    a = ap.parse_args(argv)
    check_out_dir(a.out_dir)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base_tree = export_base(a.base, td / "base") if a.base else stage_tree(a.base_dir, td / "base")
        new_tree = stage_tree(a.new, td / "new")
        s = price(base_tree, new_tree, a.history, a.out_dir, a.only_rule)
    print(f"N={s['N']}  per tool: {s['N_per_tool']}  raw tool calls: {s['raw_tool_calls']}")
    print(f"parse failures old={s['parse_failures']['old']} new={s['parse_failures']['new']}  reason_moved={s['reason_moved']}")
    print("decision matrix (old>new):")
    for k, v in s["matrix"].items():
        print(f"  {k:<16}{v}")
    print("totals:", s["totals"])
    for rid, d in s["rules"].items():
        print(f"  {rid:<26}{d}")
    print(f"per-(rule,direction) files and summary.json -> {a.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
