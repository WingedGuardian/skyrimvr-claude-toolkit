#!/usr/bin/env python3
"""codex_trust -- is Codex actually RUNNING the toolkit's hooks?

    python tools/skyguard/codex_trust.py report --root <install> [--codex-config P] [--hooks-json P]

Prints one JSON report. Exit 0 = every hook trusted, 1 = any hook is not (untrusted, modified, disabled)
or the install root cannot run them (overall "unrunnable"), 2 = it cannot tell (unknown). It never reports
a state it did not read: an unreadable file is "unknown", never "trusted", and so is a key stored only
under another letter case (whether Codex folds case is unmeasured). It only READS Codex's config.

Why: Codex runs a project hook only when `[hooks.state.'<key>']` in its config.toml holds a `trusted_hash`
equal to the definition's CURRENT hash and `enabled` is not false. An unapproved, changed ("modified") or
disabled hook is skipped SILENTLY -- the session looks guarded and is not -- and nothing inside the
session can tell. So something outside has to (the canary, Task 13).

Hash scheme and state-key spelling: ported from the X4 toolkit's `codex_trust.py` (x4-claude-toolkit,
agent/guards/adapters/codex_trust.py; READ there from codex-rs hooks/src/engine/discovery.rs), with
credit. "sha256:" + sha256 of the compact canonical JSON (keys sorted recursively, None omitted) of
    {"event_name": <snake_case event>, "matcher"?: <m>, "hooks": [<handler>]}
    handler = {"type": "command", "command": <commandWindows on Windows, else command>,
               "timeout": <given, else 600>, "async": <bool, default false>, "statusMessage"?,
               "additionalContextLimit"? (left out when unset or 2500)}
`failureMode` is NOT hashed (Task 0: tolerated, ignored, absent from the hash on 0.160.0).
MEASURED on codex-cli 0.160.0: this reproduces 11/11 `currentHash` values Codex itself listed
(tests/fixtures/codex/0.160.0/trust_vectors.json), and both hashes the TUI stored after a real `/hooks`
approval. The state key is `<absolute hooks.json path>:<snake event>:<group index>:<handler index>`, the
path in Windows spelling (`C:\\Users\\...\\.codex\\hooks.json`, case as on disk) -- MEASURED from that
approval.

A future Codex may change the scheme. When stored hashes exist but NONE matches, every mismatch reads
"unknown" (scheme unconfirmed), never "modified". Stdlib only; Python >= 3.8. Without `tomllib`
(< 3.11) a deliberately narrow reader understands only the table shapes and keys it needs; any other line
where a trust entry could live makes the answer unknown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skyguard import codex_definitions  # noqa: E402

STATUSES = ("trusted", "untrusted", "modified", "disabled", "unknown")
DEFAULT_TIMEOUT_S = 600
DEFAULT_CONTEXT_LIMIT = 2500
_SHORT_EVENTS = {"session_end": (1, 3), "interrupt": (1, 3)}   # (default, max) seconds, from X4's read

# A root the hooks cannot run from (% ! $ ` "; codex_definitions.root_problem) makes the report
# "unrunnable": a trusted definition there still protects nothing.


class Unreadable(ValueError):
    pass


def snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def _on_windows() -> bool:
    return sys.platform == "win32"


def normalised_handler(event_key: str, handler: dict, windows: bool = None) -> dict:
    if windows is None:
        windows = _on_windows()
    cmd = handler.get("command")
    if windows and handler.get("commandWindows") is not None:
        cmd = handler["commandWindows"]
    t = handler.get("timeout")
    if event_key in _SHORT_EVENTS:
        dflt, mx = _SHORT_EVENTS[event_key]
        t = min(max(dflt if t is None else int(t), 1), mx)
    else:
        t = max(DEFAULT_TIMEOUT_S if t is None else int(t), 1)
    h = {"type": handler.get("type", "command"), "command": cmd, "timeout": t,
         "async": bool(handler.get("async", False))}
    if handler.get("statusMessage") is not None:
        h["statusMessage"] = handler["statusMessage"]
    acl = handler.get("additionalContextLimit")
    if acl is not None and acl != DEFAULT_CONTEXT_LIMIT:
        h["additionalContextLimit"] = acl
    return h


def hook_hash(event_key: str, group: dict, handler: dict, windows: bool = None) -> str:
    ident = {"event_name": event_key, "hooks": [normalised_handler(event_key, handler, windows)]}
    if group.get("matcher") is not None:
        ident["matcher"] = group["matcher"]
    s = json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _key_base(hooks_json: Path) -> str:
    return str(Path(hooks_json).resolve())


def expected_entries(hooks_json: Path, windows: bool = None) -> dict:
    """{state key: expected hash}, keyed as Codex keys them."""
    try:
        data = json.loads(Path(hooks_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Unreadable("hooks.json unreadable: {}".format(e)) from e
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise Unreadable("hooks.json has no 'hooks' object")
    base = _key_base(hooks_json)
    out = {}
    for event, groups in data["hooks"].items():
        if not isinstance(groups, list):
            raise Unreadable("hooks.json: {} is not a list".format(event))
        ek = snake(event)
        for i, g in enumerate(groups):
            if not isinstance(g, dict) or not isinstance(g.get("hooks", []), list):
                raise Unreadable("hooks.json: {} group {} is malformed".format(event, i))
            for j, h in enumerate(g.get("hooks") or []):
                if not isinstance(h, dict):
                    raise Unreadable("hooks.json: {} group {} hook {} is malformed".format(event, i, j))
                out["{}:{}:{}:{}".format(base, ek, i, j)] = hook_hash(ek, g, h, windows)
    return out


# ------------------------------------------------------------------------------ config.toml

def _tables_from_doc(doc: dict) -> dict:
    out = {}
    for k, v in (doc.get("projects") or {}).items():
        if isinstance(v, dict):
            out["projects:" + k] = {kk: v[kk] for kk in ("trust_level",) if kk in v}
    hooks = doc.get("hooks") or {}
    state = hooks.get("state") if isinstance(hooks, dict) else None
    for k, v in (state or {}).items():
        if isinstance(v, dict):
            out["hooks.state:" + k] = {kk: v[kk] for kk in ("trusted_hash", "enabled") if kk in v}
    return out


_HEAD = re.compile(r"""^\[(projects|hooks\.state)\.(?:'([^']*)'|"((?:[^"\\]|\\.)*)")\]\s*(?:#.*)?$""")
# tables where a trust entry could hide in a form this reader does not parse (inline or dotted keys)
_WATCHED_HEAD = re.compile(r"""^\[\s*(?:hooks|hooks\.state|projects)\s*\]\s*(?:#.*)?$""")
_KV = re.compile(r"""^(trust_level|trusted_hash|enabled)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(true|false))\s*(?:#.*)?$""")


def _unescape(s: str) -> str:
    return json.loads('"' + s + '"')


def _read_tables_narrow(text: str) -> dict:
    out = {}
    cur = None          # the dict being filled, or None
    watched = True      # the root table: a dotted `hooks.state...` key could live here
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            m = _HEAD.match(line)
            if m:
                key = m.group(2) if m.group(2) is not None else _unescape(m.group(3))
                cur = out.setdefault("{}:{}".format(m.group(1), key), {})
                watched = False
            else:
                cur = None
                watched = bool(_WATCHED_HEAD.match(line))
            continue
        if cur is None:
            if watched and re.match(r"""^(?:hooks|projects|state|["'])""", line):
                raise Unreadable("config.toml line {} holds trust data in a form this reader does not "
                                 "parse (Python < 3.11 has no tomllib)".format(n))
            continue
        m = _KV.match(line)
        if not m:
            raise Unreadable("config.toml line {} is not understood".format(n))
        if m.group(2) is not None:
            val = _unescape(m.group(2))
        elif m.group(3) is not None:
            val = m.group(3)
        else:
            val = m.group(4) == "true"
        cur[m.group(1)] = val
    return out


def read_tables(config: Path) -> dict:
    try:
        text = Path(config).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Unreadable("codex config unreadable: {}".format(e)) from e
    try:
        import tomllib  # Python >= 3.11
    except ImportError:
        return _read_tables_narrow(text)
    try:
        return _tables_from_doc(tomllib.loads(text))
    except tomllib.TOMLDecodeError as e:
        raise Unreadable("codex config is not valid TOML: {}".format(e)) from e


def _project_trust(tables: dict, root: Path) -> str:
    rkey = os.path.normcase(str(Path(root).resolve())).rstrip("\\/").lower()
    best = "untrusted"
    for k, v in tables.items():
        if not k.startswith("projects:") or v.get("trust_level") != "trusted":
            continue
        p = os.path.normcase(k[len("projects:"):]).rstrip("\\/").lower()
        if p == rkey:
            return "trusted"
        if rkey.startswith(p + "\\") or rkey.startswith(p + "/"):
            best = "ancestor"
    return best


def codex_home() -> Path:
    return Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"


def trust_report(hooks_json: Path, codex_config: Path, project_root: Path, windows: bool = None) -> dict:
    report = _trust_report(hooks_json, codex_config, project_root, windows)
    report["root_problem"] = codex_definitions.root_problem(str(project_root))
    if report["root_problem"]:
        # approved or not, the hooks cannot start from this root: never "trusted", never "can't tell"
        report["overall"] = "unrunnable"
    return report


def _trust_report(hooks_json: Path, codex_config: Path, project_root: Path, windows: bool = None) -> dict:
    report = {"hooks_json": str(hooks_json), "codex_config": str(codex_config), "hooks": [],
              "project_trust": None, "project_trusted": None, "root_problem": None,
              "overall": "unknown", "error": None}
    try:
        expected = expected_entries(hooks_json, windows)
        tables = read_tables(codex_config)
    except Unreadable as e:
        report["error"] = str(e)
        return report
    pt = _project_trust(tables, project_root)
    report["project_trust"], report["project_trusted"] = pt, pt in ("trusted", "ancestor")
    state = {k[len("hooks.state:"):]: v for k, v in tables.items() if k.startswith("hooks.state:")}
    rows = []
    for key, want in sorted(expected.items()):
        variants = sorted(k for k in state if k.lower() == key.lower())
        if not variants:
            rows.append({"key": key, "status": "untrusted", "matched": None, "expected_hash": want,
                         "stored_hash": None})
            continue
        if variants != [key]:
            # Only another case (or several case variants) is stored. Whether Codex matches keys case-
            # insensitively is UNMEASURED, so this is never read as trusted -- and never collapsed into one.
            rows.append({"key": key, "status": "unknown", "matched": "casefold", "expected_hash": want,
                         "stored_hash": None, "variants": variants,
                         "note": "stored under another case" if key not in variants
                                 else "stored under several case variants"})
            continue
        ent, how = state[key], "exact"
        if ent.get("enabled") is False:
            st = "disabled"
        elif ent.get("trusted_hash") == want:
            st = "trusted"
        elif ent.get("trusted_hash") is None:
            st = "untrusted"
        else:
            st = "modified"
        rows.append({"key": key, "status": st, "matched": how, "expected_hash": want,
                     "stored_hash": ent.get("trusted_hash")})
    stored = [r for r in rows if r["stored_hash"] is not None]
    if stored and not any(r["stored_hash"] == r["expected_hash"] for r in stored):
        for r in rows:                       # no stored hash matched ANY of ours: scheme unconfirmed
            if r["status"] == "modified":
                r["status"] = "unknown"
    report["hooks"] = rows
    sts = {r["status"] for r in rows}
    if not rows:
        report["error"] = "hooks.json defines no hook"
    elif "unknown" in sts:
        report["overall"] = "unknown"
    elif sts == {"trusted"}:
        report["overall"] = "trusted"
    elif "disabled" in sts:
        report["overall"] = "disabled"
    elif "modified" in sts:
        report["overall"] = "modified"
    else:
        report["overall"] = "untrusted"
    return report


def exit_code(rep: dict) -> int:
    if rep["overall"] == "unknown":
        return 2
    if rep["overall"] == "trusted":
        return 0
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codex_trust", description="Report whether Codex will run the toolkit's hooks.")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("report")
    r.add_argument("--root", required=True, type=Path, help="the toolkit install (the folder holding .codex/)")
    r.add_argument("--hooks-json", type=Path, default=None, help="default: <root>/.codex/hooks.json")
    r.add_argument("--codex-config", type=Path, default=None, help="default: $CODEX_HOME/config.toml or ~/.codex/config.toml")
    a = ap.parse_args(argv)
    if a.cmd != "report":
        ap.print_usage(sys.stderr)
        return 2
    hooks = a.hooks_json or a.root / ".codex" / "hooks.json"
    cfg = a.codex_config or codex_home() / "config.toml"
    rep = trust_report(hooks, cfg, a.root)
    sys.stdout.write(json.dumps(rep, indent=2) + "\n")
    return exit_code(rep)


if __name__ == "__main__":
    sys.exit(main())
