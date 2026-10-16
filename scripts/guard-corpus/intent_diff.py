#!/usr/bin/env python3
"""Print every intended row a rev range adds or changes, as a table the reviewer reads before the code diff.

    python scripts/guard-corpus/intent_diff.py <base>..<head>     (a single <rev> means <rev>..HEAD)

Columns: id, payload shape, old decision, new decision, rules, why. "Old" is the legacy row the new row
supersedes (decision, from expected.jsonl at <head>) or, for an id that already existed, its previous
intended row at <base>; "-" for a row that specifies something new. A superseded row's old decision is the
engine-independent recorded one, so the table never depends on running any engine.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INTENDED = "tests/guard_corpus/intended.jsonl"
EXPECTED = "tests/guard_corpus/expected.jsonl"


def git_file(rev: str, path: str, repo: Path = REPO) -> str:
    """The file at a revision as text, "" when it does not exist there. `cat-file`, never through a pipe."""
    r = subprocess.run(["git", "cat-file", "-p", f"{rev}:{path}"], cwd=str(repo), capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else ""


def parse(text: str) -> list[dict]:
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def shape(payload) -> str:
    """One short line naming the tool and what it acts on."""
    if not isinstance(payload, dict):
        return repr(payload)[:60]
    ti = payload.get("tool_input") or {}
    what = ti.get("command") or ti.get("file_path") or ""
    where = " @" + payload["cwd"] if payload.get("cwd") else ""
    what = " ".join(str(what).split())
    return f"{payload.get('tool_name', '?')}: {what[:70]}{where}"


def changed_rows(old: list[dict], new: list[dict]) -> list[dict]:
    before = {r["id"]: r for r in old}
    return [r for r in new if before.get(r["id"]) != r]


def table(old: list[dict], new: list[dict], legacy: list[dict]) -> list[list[str]]:
    before = {r["id"]: r for r in old}
    legacy_by_id = {r["id"]: r for r in legacy}
    out = []
    for r in changed_rows(old, new):
        prev = before.get(r["id"])
        sup = legacy_by_id.get(r.get("supersedes") or "")
        if prev is not None:
            was = f"{prev['decision']} (previous)"
        elif sup is not None:
            was = f"{sup['decision']} (legacy {sup['id']})"
        else:
            was = "-"
        out.append([r["id"], shape(r["payload"]), was, r["decision"], ",".join(r["rules"]) or "-",
                    str(r.get("why", ""))])
    return out


def render(rows: list[list[str]]) -> str:
    head = ["id", "payload", "old", "new", "rules", "why"]
    if not rows:
        return "no intended rows added or changed\n"
    widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(5)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(head, widths)) + "  why"]
    for r in rows:
        lines.append("  ".join(r[i].ljust(widths[i]) for i in range(5)) + "  " + r[5])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("range", help="<base>..<head>, or one <rev> meaning <rev>..HEAD")
    a = ap.parse_args(argv)
    base, _, head = a.range.partition("..")
    head = head or "HEAD"
    old = parse(git_file(base, INTENDED))
    new = parse(git_file(head, INTENDED))
    legacy = parse(git_file(head, EXPECTED))
    text = render(table(old, new, legacy))
    # Final review C-M1: UTF-8 bytes, not the console code page (cp1252 cannot encode the corpus's Kelvin sign).
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.flush()
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
