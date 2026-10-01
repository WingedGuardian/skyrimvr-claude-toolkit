#!/usr/bin/env python3
"""Turn raw captured hook payloads into committed test fixtures.

Replaces session/turn/tool-use ids with <id>, transcript_path and cwd with <path>, and the
user's home folder -- in every spelling a payload can carry it -- with <HOME>. The shape and
every other value are kept: fixtures exist to pin the agent's real payload format.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ID_KEYS = {"session_id", "turn_id", "tool_use_id"}
PATH_KEYS = {"transcript_path", "cwd"}


def _home_pattern(home: str) -> re.Pattern:
    h = home.replace("\\", "/").rstrip("/")            # C:/Users/Name
    drive, rest = h[0], h[2:]                          # C, /Users/Name
    parts = [re.escape(p) for p in rest.strip("/").split("/")]
    sep = r"(?:\\\\|\\|/)"                             # \\  \  /
    body = sep + sep.join(parts)
    return re.compile(rf"(?:{drive}:|/{drive.lower()}|/{drive}){body}", re.IGNORECASE)


def _walk(v, pat):
    if isinstance(v, dict):
        return {k: _walk(x, pat) for k, x in v.items()}
    if isinstance(v, list):
        return [_walk(x, pat) for x in v]
    if isinstance(v, str):
        return pat.sub("<HOME>", v).replace("<HOME>\\", "<HOME>/")
    return v


def scrub(payload: dict, home: str) -> dict:
    pat = _home_pattern(home)
    out = {}
    for k, v in payload.items():
        if k in ID_KEYS:
            out[k] = "<id>"
        elif k in PATH_KEYS:
            out[k] = "<path>"
        else:
            out[k] = _walk(v, pat)
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
