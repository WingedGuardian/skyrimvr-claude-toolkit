#!/usr/bin/env python3
"""Turn raw captured hook payloads into committed test fixtures.

Replaces every id field (any key ending in `_id`) with <id>, transcript_path and cwd with <path>,
paths under the session's cwd with <cwd>, and the user's home folder with <HOME> -- each in every
spelling a payload can carry it. The shape and every other value are kept: fixtures exist to pin
the agent's real payload format.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PATH_KEYS = {"transcript_path", "cwd"}
_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|/[A-Za-z]/)")


def _is_id_key(k: str) -> bool:
    # Agents add id fields over time (Claude Code 2.1.287 added prompt_id), so match the shape.
    return k == "id" or k.endswith("_id")


def _path_pattern(path: str) -> re.Pattern:
    """Match an absolute Windows path in any spelling: C:\\x, C:/x, a JSON-escaped C:\\\\x, /c/x."""
    h = path.replace("\\", "/").rstrip("/")
    if h.startswith("/") and len(h) > 2 and h[2] == "/":     # /c/Users/... (MSYS)
        drive, rest = h[1], h[2:]
    else:                                                    # C:/Users/...
        drive, rest = h[0], h[2:]
    parts = [re.escape(p) for p in rest.strip("/").split("/")]
    sep = r"(?:\\\\|\\|/)"                                    # \\  \  /
    body = sep + sep.join(parts)
    return re.compile(rf"(?:{drive}:|/{drive}){body}", re.IGNORECASE)


def _walk(v, subs):
    if isinstance(v, dict):
        return {k: _walk(x, subs) for k, x in v.items()}
    if isinstance(v, list):
        return [_walk(x, subs) for x in v]
    if isinstance(v, str):
        for pat, token in subs:
            v = pat.sub(token, v)
        return v.replace("<HOME>\\", "<HOME>/")
    return v


def scrub(payload: dict, home: str) -> dict:
    subs = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and _ABS.match(cwd):             # most specific first
        subs.append((_path_pattern(cwd), "<cwd>"))
    subs.append((_path_pattern(home), "<HOME>"))
    out = {}
    for k, v in payload.items():
        if _is_id_key(k):
            out[k] = "<id>"
        elif k in PATH_KEYS:
            out[k] = "<path>"
        else:
            out[k] = _walk(v, subs)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("raw_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--agent", required=True, choices=["claude", "codex"])
    ap.add_argument("--home", default=str(Path.home()))
    a = ap.parse_args(argv)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for f in sorted(a.raw_dir.glob("*.json")):
        raw = f.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        p = scrub(json.loads(raw), a.home)
        event = p.get("hook_event_name", "unknown")
        tool = p.get("tool_name", "none")
        key = f"{a.agent}-{event}-{tool}"
        counts[key] = counts.get(key, 0) + 1
        dest = a.out_dir / f"{key}-{counts[key]}.json"
        dest.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
