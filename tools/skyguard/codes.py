"""Stable reason codes, derived from the rule modules' OWN text constants and the rule registry (Plan 4 R8).

The rule modules (shell_rules.py, write_rules.py, mcp_rules.py) are owned by Plan 5 and are not edited here.
Two sources, both templates whose {field}s are filled at runtime, so a reason text identifies its rule (the
literal parts must match in order):
  1. each DENY_/ASK_/ADV_/NO_ string constant of shell_rules / write_rules -> "<ns>.<CONSTANT>" (Plan 4's
     codes; the Codex .rules classification and the telemetry already name these);
  2. (merge of Plans 4+5) every NON-legacy rule of rules.RULES whose template is not already one of those
     constants -> "<ns>.<RULE-ID>", ns from the rule's hook (protect-bash shell, protect-files write,
     protect-mcp mcp). Plan 5 renders most messages straight from the registry (rules.message), so without
     this they would all code as "unknown".
A rule that passes code= explicitly keeps it.

Decisive texts (deny/ask) must match a template WHOLE; advice is coded by its FIRST item (a prefix match),
since advice items are joined with " | ". In a prefix match a field never spans " | ", so a later item cannot
make an earlier template match across the separator.
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

from . import rules, shell_rules, write_rules
from .verdict import Verdict

_PREFIXES = ("DENY_", "ASK_", "ADV_", "NO_")
_DECISIVE_PREFIXES = ("DENY_", "ASK_", "NO_")
_FIELD = re.compile(r"\{[a-z]+\}")
_NS = {"protect-bash": "shell", "protect-files": "write", "protect-mcp": "mcp"}
_ANY = ".*"
_ONE_ITEM = r"(?:(?! \| ).)*"
# The Claude wrappers' own refusal (Python missing, engine failed) never reaches the engine, but it is in
# the corpus; code it so the corpus check is total.
_WRAPPER = ("wrapper.INERT", re.compile(r"GUARD INERT: \S+ .*evaluated NO rules.*", re.S), None, True)


def _compile(template: str) -> Tuple[Pattern, Pattern]:
    parts = [re.escape(p) for p in _FIELD.split(template)]
    return re.compile(_ANY.join(parts), re.S), re.compile(_ONE_ITEM.join(parts), re.S)


def _templates() -> List[Tuple[str, Pattern, Pattern, bool]]:
    """(code, whole-match regex, first-item regex, decisive)."""
    out = []
    seen_text = set()
    for ns, mod in (("shell", shell_rules), ("write", write_rules)):
        for name, value in sorted(vars(mod).items()):
            if not isinstance(value, str) or not name.startswith(_PREFIXES) or name.endswith("_LOG"):
                continue
            whole, first = _compile(value)
            out.append(("{}.{}".format(ns, name), whole, first, name.startswith(_DECISIVE_PREFIXES)))
            seen_text.add(value)
    for rid, rule in sorted(rules.RULES.items()):
        if rule["legacy"] or rule["template"] in seen_text:
            continue
        whole, first = _compile(rule["template"])
        out.append(("{}.{}".format(_NS.get(rule["hook"], "rule"), rid), whole, first,
                    rule["decision"] in ("deny", "ask")))
    out.append(_WRAPPER)
    return out


TEMPLATES = _templates()


def decisive_codes() -> List[str]:
    """Every code a deny or ask verdict can carry (the Codex .rules classification must cover each one)."""
    return sorted(code for code, _w, _f, decisive in TEMPLATES if decisive and code != "wrapper.INERT")


def rule_code(rid: str) -> str:
    """The code a verdict rendered from rule `rid`'s template carries: what classify() would derive from its text
    (its first whole match), for a verdict built outside the rule modules (engine's apply_patch delete)."""
    found = matches(rules.RULES[rid]["template"], True)
    return found[0] if found else "unknown"


def matches(text: str, whole: bool) -> List[str]:
    if whole:
        return [code for code, w, _f, _d in TEMPLATES if w.fullmatch(text)]
    return [code for code, _w, f, _d in TEMPLATES if f is not None and f.match(text)]


def _inert(code: str) -> bool:
    return ".NO_" in code or "INERT" in code


def classify(v: Verdict) -> Verdict:
    if v.code:
        return v
    whole = v.decision in ("deny", "ask")
    text = v.reason if whole else v.context
    if not text:
        return v._replace(code="allow")
    found = matches(text, whole)
    if not found:
        return v._replace(code="unknown")
    code = found[0]                    # more than one is a test failure (corpus check), never decided here
    status = "failed" if _inert(code) else v.status
    return v._replace(code=code, status=status)
