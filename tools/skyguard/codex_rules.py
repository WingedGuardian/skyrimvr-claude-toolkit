"""The Codex `.rules` file (Plan 4 Task 9; R5): the line that holds while the hooks are unapproved.

Codex runs project hooks only after the user approves them, but a `.rules` `forbidden` prefix rule blocks
regardless. So the rules file carries ONLY prefix rules that are strict subsets of an existing hook deny
(today registry deletes): a prefix rule can never refuse something the hooks would allow, and every other
verdict stays hook-only. Task 0 measured offline that `reg.exe delete` does NOT match a `reg delete`
prefix, so both spellings are listed. Task 10's generator consumes `render()`.

Merge of Plans 4+5: Plan 5 Task 9 narrowed S-REGISTRY to a delete of a Bethesda key (or an ancestor of one, or
a key that is not literal). A bare `reg delete` prefix then refused `reg delete HKCU/Software/Other` -- which
the hook ALLOWS -- even after the hooks were approved, since .rules applies always. So each prefix now names
the program, `delete` AND the Bethesda key itself (one rule per spelling below): a strict subset again. The
cost: a spelling not listed (other case, a deeper subkey, a trailing backslash) is guarded by the hooks only.

CLASSIFICATION covers every deny/ask code the engine can emit (codes.decisive_codes(): the rule-module
constants and the rule registry); a new one fails tests/test_codex_rules.py until it is classified here
(Plan 5 trips this deliberately).
"""
from __future__ import annotations

import json
from typing import Dict, List, Tuple

_PATH = "path-based: the protected path is an argument, not a prefix"
_INERT = "analysis failure, not a command"
CLASSIFICATION: Dict[str, Tuple[str, str]] = {
    "shell.DENY_REGISTRY": (
        "forbidden",
        "the hook denies a delete of a Bethesda key; `reg delete <that key>` / `reg.exe delete <that key>` "
        "prefixes are a strict subset"),
    "shell.DENY_CHAMPOLLION": ("hook_only", _PATH),
    "shell.DENY_PSC": ("hook_only", "the `-o` flag can be anywhere"),
    "shell.DENY_WRITE_PLUGIN": ("hook_only", _PATH + " (and the writer is any program or a redirect)"),
    "shell.NO_COMMAND_DENY": ("hook_only", _INERT),
    "shell.NO_ROOT_DENY": ("hook_only", _INERT),
    "shell.S-INERT-CONFIG": ("hook_only", _INERT),
    "shell.S-DEL-CONFIG": ("hook_only", _PATH),
    "shell.S-DEL-INSTALL": ("hook_only", _PATH),
    "shell.S-DEL-MO2": ("hook_only", _PATH),
    "shell.S-DEL-UNRESOLVED": ("hook_only", _PATH),
    "shell.S-DEL-FALLBACK": ("hook_only", _INERT),
    "shell.S-RESAVER-APPLY": (
        "hook_only", "a prefix would also prompt every dry-run; `--apply` position varies (R4)"),
    "shell.S-DEVBENCH-CRASH": ("hook_only", "the console command is inside a JSON body or an MCP argument"),
    "shell.S-DEVBENCH-SAVE": ("hook_only", "depends on whether the save file exists"),
    # Plan 6 Task 11: hook_only. A `forbidden` prefix could also refuse the USER's own `!` command (not measured),
    # and running skyguard-unlock.cmd / `lock disable` themselves is what a Codex user is told to do; the operand of
    # S-LIFT-RAW is path-based. The ask of S-LIFT-PERMANENT reaches Codex as a deny through the adapter (R4).
    "shell.S-LIFT-RAW": ("hook_only", _PATH),
    "shell.S-LIFT-UNLOCKCMD": ("hook_only", "the user runs it themselves; a prefix could refuse the user too"),
    "shell.S-LIFT-PERMANENT": ("hook_only", "the user runs it themselves; a prefix could refuse the user too"),
    # Plan 6 Task 15: the ask reaches Codex as a deny through the adapter (R4); the user runs the restore themselves.
    "shell.S-RESTORE-INPLACE": ("hook_only", "the user runs it themselves; a prefix could refuse the user too"),
    # Plan 6 Task 12: path-based (where the write RESOLVES). S-SELF-GUARD / W-SELF-GUARD priced to advise: not decisive.
    "shell.S-SELF-STATE": ("hook_only", _PATH),
    "write.W-SELF-STATE": ("hook_only", "a file write, not a command"),
    "mcp.S-INERT-NOINPUT": ("hook_only", _INERT),
    "write.DENY_PLUGIN": ("hook_only", "a file write, not a command"),
    "write.NO_PATH_DENY": ("hook_only", _INERT),
}

_WHY = ("Skyrim toolkit: registry deletes are refused (Bethesda keys locate the game). "
        "Ask the user to run it themselves if it is really intended.")

# The Bethesda keys as reg.exe is usually given them (anchored.BETHESDA_KEYS, in both hive spellings).
REG_PROGRAMS = ("reg", "reg.exe")
BETHESDA_REG_KEYS = (
    "HKLM\\SOFTWARE\\Bethesda Softworks",
    "HKLM\\SOFTWARE\\WOW6432Node\\Bethesda Softworks",
    "HKCU\\Software\\Bethesda Softworks",
    "HKEY_LOCAL_MACHINE\\SOFTWARE\\Bethesda Softworks",
    "HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Bethesda Softworks",
    "HKEY_CURRENT_USER\\Software\\Bethesda Softworks",
)
_OTHER_KEY = "HKCU\\Software\\SkyguardProbe"   # a key the hook ALLOWS deleting: never inside a prefix

PREFIX_RULES: List[dict] = [
    {"pattern": [prog, "delete", key], "decision": "forbidden", "code": "shell.DENY_REGISTRY",
     "justification": _WHY,
     "match": [[prog, "delete", key, "/f"]],
     "not_match": [[prog, "delete", _OTHER_KEY, "/f"], [prog, "query", key]]}
    for prog in REG_PROGRAMS for key in BETHESDA_REG_KEYS
]


def _s(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def render() -> str:
    out = ["# Generated by tools/skyguard/codex_rules.py -- do not edit.",
           "# Only prefix rules that are strict subsets of a hook deny; everything else is hook-only.", ""]
    for r in PREFIX_RULES:
        out.append("prefix_rule(")
        out.append("    pattern = {},".format(_s(r["pattern"])))
        out.append("    decision = {},".format(_s(r["decision"])))
        out.append("    justification = {},".format(_s(r["justification"])))
        out.append("    match = {},".format(_s(r["match"])))
        out.append("    not_match = {},".format(_s(r["not_match"])))
        out.append(")")
        out.append("")
    return "\n".join(out)
