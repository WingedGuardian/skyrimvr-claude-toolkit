"""The user-facing count of rules that ASK must match the engine's registry (Plan 5 final review D2).

core.md, README and safety-philosophy said "two rules ask" while rules.RULES held five: an agent got prompts its
instructions said could not happen. Each document states the count in words; this reads it back and compares it
with the registry, and checks the deny rules the review found missing from core.md's DENIED list are described.
"""
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import rules  # noqa: E402

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _asks() -> int:
    return sum(1 for r in rules.RULES.values() if r["decision"] == "ask")


@pytest.mark.parametrize("rel,pattern", [
    ("agent/instructions/core.md", r"### ASKS THE USER -- (\w+) rules"),
    ("README.md", r"Only (\w+) rules ask \*you\*"),
    ("docs/safety-philosophy.md", r"(\w+) rules in the toolkit ask you"),
], ids=["core", "readme", "philosophy"])
def test_the_documented_ask_count_matches_the_registry(rel, pattern):
    text = (REPO / rel).read_text(encoding="utf-8")
    m = re.search(pattern, text, re.IGNORECASE)
    assert m, f"{rel}: the ask-count sentence is gone ({pattern})"
    assert WORDS.get(m.group(1).lower()) == _asks(), (rel, m.group(1), _asks())


@pytest.mark.parametrize("needle", [
    "Moving or renaming the install",          # S-MOVE-ROOT
    "Decoded text run as commands",            # S-SHELL-UNREAD
    "deleting everything in a folder it cannot place",   # S-DEL-UNKNOWN-DIR
    "Recycle-Bin delete",                      # D1: core.md's own rule is guarded
])
def test_core_md_describes_the_deny_rules(needle):
    assert needle in (REPO / "agent" / "instructions" / "core.md").read_text(encoding="utf-8")
