#!/usr/bin/env python3
"""Replay this machine's REAL tool-call history through the old and the new guards (spec §7.2).

One-time equivalence check for the skyguard port. Every Bash `command` and every Edit/Write/MultiEdit
`file_path` found in `tool_use` blocks under the history dir (recursively: subagent transcripts nest)
is de-duplicated and fired at protect-bash.sh / protect-files.sh -- the two hooks those tools were
wired to that decide anything. The side-effect hooks (snapshot-before-tool, backup-before-edit) emit
no decision and are not replayed.

Raw commands can hold secrets and private paths, so they go ONLY to --out-dir, which must be outside
the repo. The script prints counts only.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("record", HERE / "record.py")
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)

FILE_TOOLS = ("Edit", "Write", "MultiEdit")


def _wide_item(name: str, inp: dict, cwd):
    """(dedupe key, item) for the tools the Plan 5 pricing tool prices; None for any other tool."""
    if name in ("Bash", "PowerShell") and isinstance(inp.get("command"), str):
        ti, prim = {"command": inp["command"]}, inp["command"]
    elif name in FILE_TOOLS and isinstance(inp.get("file_path"), str):
        ti, prim = {"file_path": inp["file_path"]}, inp["file_path"]
    elif name.startswith("mcp__devbench"):
        ti, prim = inp, json.dumps(inp, sort_keys=True, ensure_ascii=False)
    else:
        return None
    cwd = cwd if isinstance(cwd, str) else ""
    return (name, prim, cwd), {"tool_name": name, "tool_input": ti, "cwd": cwd}


def extract(history: Path, wide: bool = False) -> tuple[list[dict], dict]:
    """Unique items from every tool_use block, plus raw counts per tool.

    Default (replay.py): (hook, payload) items for Bash and Edit/Write/MultiEdit, deduplicated on the
    command / file_path. wide=True (price.py): {tool_name, tool_input, cwd} items that also take PowerShell
    and mcp__devbench*, deduplicated on (tool_name, command|file_path|mcp-input, cwd).
    """
    seen, items, counts = set(), [], collections.Counter()
    for f in sorted(history.rglob("*.jsonl")):
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    counts["unparseable_line"] += 1
                    continue
                content = (obj.get("message") or {}).get("content") if isinstance(obj, dict) else None
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    name, inp = b.get("name"), b.get("input") or {}
                    if wide:
                        w = _wide_item(name, inp, obj.get("cwd")) if isinstance(name, str) and isinstance(inp, dict) else None
                        if w is None:
                            continue
                        counts[name] += 1
                        if w[0] not in seen:
                            seen.add(w[0])
                            items.append(w[1])
                        continue
                    if name == "Bash" and isinstance(inp.get("command"), str):
                        key = ("protect-bash.sh", inp["command"])
                        payload = {"tool_name": "Bash", "tool_input": {"command": inp["command"]}}
                    elif name in FILE_TOOLS and isinstance(inp.get("file_path"), str):
                        # protect-files reads only .tool_input.file_path, so the tool name does not dedupe.
                        key = ("protect-files.sh", inp["file_path"])
                        payload = {"tool_name": name, "tool_input": {"file_path": inp["file_path"]}}
                    else:
                        if name == "PowerShell":
                            counts["PowerShell_not_wired"] += 1
                        continue
                    counts[name] += 1
                    if key not in seen:
                        seen.add(key)
                        items.append({"hook": key[0], "payload": payload})
    return items, dict(counts)


def _install_patterns(install: Path) -> re.Pattern:
    p = install.as_posix()
    forms = {p, str(install), "/" + p[0].lower() + p[2:]}
    return re.compile("|".join(re.escape(x) for x in sorted(forms, key=len, reverse=True)), re.IGNORECASE)


def run(old: Path, new: Path, history: Path, out_dir: Path, jobs: int, limit: int | None) -> int:
    out_dir = out_dir.resolve()
    if REPO in out_dir.parents or out_dir == REPO:
        raise SystemExit(f"--out-dir must be outside the repo (raw commands can hold secrets): {out_dir}")
    if rec.needs_missing_jq(old):
        raise SystemExit("the old hooks need jq and it is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    # The item list is frozen on the first run, so a resumed run replays the same N while history grows.
    frozen = out_dir / "items.json"
    if frozen.is_file():
        snap = json.loads(frozen.read_text(encoding="utf-8"))
    else:
        items, counts = extract(history)
        snap = {"items": items, "counts": counts}
        frozen.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    items, counts = snap["items"], snap["counts"]
    if limit:
        items = items[:limit]
    # Results are appended as they complete, so an interrupted run resumes instead of restarting.
    done_path = out_dir / "results.jsonl"
    done = {}
    if done_path.is_file():
        for line in done_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                # A crash or timeout (e.g. under machine load) is retried on resume, not frozen in.
                if "error" not in (r["old"]["decision"], r["new"]["decision"]):
                    done[r["k"]] = (r["old"], r["new"])
    with tempfile.TemporaryDirectory() as td:
        io = rec.make_install(Path(td) / "old", old, "declared")
        ino = rec.make_install(Path(td) / "new", new, "declared")
        pats = {id(io): _install_patterns(io), id(ino): _install_patterns(ino)}

        def one(install, item):
            try:
                r = rec.fire(install, item["hook"], item["payload"])
            except Exception as e:  # a crash is a result, not a skip
                return {"decision": "error", "text": f"{type(e).__name__}: {e}"[:400]}
            r["text"] = pats[id(install)].sub("<install>", r["text"])
            return r

        lock = threading.Lock()
        todo = [k for k in range(len(items)) if k not in done]
        print(f"{len(items)} items, {len(done)} already done, {len(todo)} to run", flush=True)

        def both(k):
            o, n = one(io, items[k]), one(ino, items[k])
            with lock:
                fh.write(json.dumps({"k": k, "old": o, "new": n}, ensure_ascii=False) + "\n")
                fh.flush()
                done[k] = (o, n)
                if len(done) % 1000 == 0:
                    print(f"  {len(done)}/{len(items)}", flush=True)

        with done_path.open("a", encoding="utf-8", newline="\n") as fh, ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(both, todo))
        results = [done[k] for k in range(len(items))]

    matrix = collections.Counter()
    agree = 0
    with (out_dir / "disagreements.jsonl").open("w", encoding="utf-8", newline="\n") as dis:
        for item, (o, n) in zip(items, results):
            matrix[(item["hook"], o["decision"], n["decision"])] += 1
            if (o["decision"], o["text"]) == (n["decision"], n["text"]):
                agree += 1
            else:
                dis.write(json.dumps({**item, "old": o, "new": n}, ensure_ascii=False) + "\n")
    summary = {"N": len(items), "agree": agree, "disagree": len(items) - agree, "raw_tool_calls": counts,
               "matrix": [{"hook": h, "old": o, "new": n, "count": c} for (h, o, n), c in sorted(matrix.items())]}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"N={summary['N']} agree={agree} disagree={summary['disagree']}")
    print(f"raw tool calls: {counts}")
    print(f"{'hook':<18}{'old':<8}{'new':<8}count")
    for (h, o, n), c in sorted(matrix.items()):
        print(f"{h:<18}{o:<8}{n:<8}{c}")
    print(f"disagreements -> {out_dir / 'disagreements.jsonl'}")
    return 0 if agree == len(items) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", type=Path, default=REPO / "tests" / "guard_corpus" / "reference")
    ap.add_argument("--new", type=Path, default=REPO / ".claude" / "hooks")
    ap.add_argument("--history", type=Path, default=Path.home() / ".claude" / "projects")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="replay only the first N items (smoke test)")
    a = ap.parse_args()
    sys.exit(run(a.old, a.new, a.history, a.out_dir, a.jobs, a.limit))
