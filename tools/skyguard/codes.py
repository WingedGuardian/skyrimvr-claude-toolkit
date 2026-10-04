"""Stable reason codes, derived from the rule modules' OWN text constants (Plan 4 R8).

The rule modules (shell_rules.py, write_rules.py) are owned by Plan 5 and are not edited here. Each
DENY_/ASK_/ADV_/NO_ string constant is a template whose {c}/{p} fields are filled at runtime, so a reason
text identifies its constant: the literal parts must match in order. A new constant is coded by its name
automatically. A rule that passes code= explicitly keeps it.

Decisive texts (deny/ask) must match a template WHOLE; advice is coded by its FIRST item (a prefix match),
since advice items are joined with " | " and a command may itself contain " | ".
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

from . import shell_rules, write_rules
from .verdict import Verdict

_PREFIXES = ("DENY_", "ASK_", "ADV_", "NO_")
_FIELD = re.compile(r"\{[a-z]\}")
# The Claude wrappers' own refusal (Python missing, engine failed) never reaches the engine, but it is in
# the corpus; code it so the corpus check is total.
_WRAPPER = ("wrapper.INERT", re.compile(r"GUARD INERT: \S+ .*evaluated NO rules.*", re.S))


def _templates() -> List[Tuple[str, Pattern]]:
    out = []
    for ns, mod in (("shell", shell_rules), ("write", write_rules)):
        for name, value in sorted(vars(mod).items()):
            if not isinstance(value, str) or not name.startswith(_PREFIXES) or name.endswith("_LOG"):
                continue
            rx = ".*".join(re.escape(part) for part in _FIELD.split(value))
            out.append(("{}.{}".format(ns, name), re.compile(rx, re.S)))
    out.append(_WRAPPER)
    return out


TEMPLATES = _templates()


def matches(text: str, whole: bool) -> List[str]:
    return [code for code, rx in TEMPLATES if (rx.fullmatch(text) if whole else rx.match(text))]


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
    status = "failed" if ".NO_" in code or code == "wrapper.INERT" else v.status
    return v._replace(code=code, status=status)
