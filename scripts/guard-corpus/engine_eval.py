#!/usr/bin/env python3
"""Evaluate one skyguard engine over a JSONL file of items; run as a subprocess by price.py.

The engine under test is whichever `skyguard` package is first on PYTHONPATH, so price.py can run the
base and the new tree side by side without importing both into one interpreter.

Per item the driver calls the engine's `decide` with whichever keyword arguments its signature accepts
(`cwd`, `tool_name`, `tool_input`, `tool`, `shell`), so an older engine that does not know `cwd` is still
priced. It uses a recording Audit that never touches the disk, and writes one JSONL result per item:
    {"k": index, "decision": allow|advise|ask|deny|error, "text": ..., "fired": [[id, decision], ...],
     "parse_ok": bool, "parse_why": str}
parse_ok is False when a rule traced a PARSE fallback, or (for Bash/PowerShell items, when the engine tree has
`skyguard.shellparse`) when the parser cannot read the command; parse_why is the parser's reason.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys

DEFAULT_ROOT = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
DEFAULT_ROOT_WIN = "C:\\GOG Games\\The Elder Scrolls V Skyrim VR"
FILE_TOOLS = ("Edit", "Write", "MultiEdit")


def make_audit(base):
    class Recording(base):
        """Records rule firings in memory. Writes nothing: not the audit log, not a backup directory."""

        def __init__(self):
            super().__init__("")
            self.trace = []

        def raw(self, line):
            pass

        def log(self, hook, level, msg):
            pass

        def rule(self, hook, rule_id, decision, msg, level=None):
            self.trace.append([rule_id, decision])

    return Recording()


def call(fn, positional, **candidates):
    """Call fn with the positional args plus every candidate keyword its signature names."""
    params = inspect.signature(fn).parameters
    takes_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    kw = {k: v for k, v in candidates.items() if takes_any or k in params}
    return fn(*positional, **kw)


def decide_item(mods, item, root, root_win, audit):
    """-> Verdict-like (decision, reason, context) for one item."""
    name = item.get("tool_name", "")
    ti = item.get("tool_input") or {}
    cands = {"cwd": item.get("cwd", ""), "tool_name": name, "tool_input": ti, "tool": name,
             "shell": "powershell" if name == "PowerShell" else "bash"}
    if name in ("Bash", "PowerShell"):
        roots = mods["paths"].roots(root, root_win, {})
        return call(mods["shell_rules"].decide, (ti.get("command", ""), roots, audit), **cands)
    if name in FILE_TOOLS:
        return call(mods["write_rules"].decide, (ti.get("file_path", ""), audit), **cands)
    if name.startswith("mcp__"):
        mcp = mods.get("mcp_rules")
        if mcp is not None and hasattr(mcp, "decide"):
            return call(mcp.decide, (name, ti, audit), **cands)
    return None  # a tool this engine has no rule for: allow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--root-win", default=DEFAULT_ROOT_WIN)
    a = ap.parse_args(argv)
    mods = {}
    for n in ("paths", "shell_rules", "write_rules"):
        mods[n] = importlib.import_module("skyguard." + n)
    for opt in ("mcp_rules", "shellparse"):
        try:
            mods[opt] = importlib.import_module("skyguard." + opt)
        except ImportError:
            pass
    audit_cls = importlib.import_module("skyguard.audit").Audit
    with open(a.items, encoding="utf-8") as src, open(a.out, "w", encoding="utf-8", newline="\n") as out:
        for k, line in enumerate(src):
            if not line.strip():
                continue
            item = json.loads(line)
            audit = make_audit(audit_cls)
            try:
                v = decide_item(mods, item, a.root, a.root_win, audit)
                if v is None:
                    decision, text = "allow", ""
                elif v.decision in ("deny", "ask"):
                    decision, text = v.decision, v.reason
                elif v.context:
                    decision, text = "advise", v.context
                else:
                    decision, text = "allow", ""
                parse_ok = not any("PARSE" in rid for rid, _ in audit.trace)
                parse_why = ""
                sp = mods.get("shellparse")
                if sp is not None and item.get("tool_name") in ("Bash", "PowerShell"):
                    # Plan 5 Task 5: an engine that HAS the parser reports whether it can read the command, so the
                    # parse-failure rate over real history is priced even before a rule depends on it (R4 gate).
                    cmd = (item.get("tool_input") or {}).get("command", "")
                    p = sp.parse(cmd if isinstance(cmd, str) else "",
                                 "powershell" if item["tool_name"] == "PowerShell" else "bash")
                    parse_ok = parse_ok and p.ok
                    parse_why = p.why
            except Exception as e:  # a crash is a result, never a skip
                decision, text, parse_ok, parse_why = "error", f"{type(e).__name__}: {e}"[:400], False, ""
            out.write(json.dumps({"k": k, "decision": decision, "text": text, "fired": audit.trace,
                                  "parse_ok": parse_ok, "parse_why": parse_why}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
