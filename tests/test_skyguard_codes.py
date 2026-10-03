"""Verdicts carry a stable reason code and an evaluation status, separate from the decision (Plan 4 R8/R9)."""
import json
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codes, shell_rules, write_rules  # noqa: E402
from skyguard.verdict import STATUSES, Verdict  # noqa: E402

CORPUS = REPO / "tests" / "guard_corpus" / "expected.jsonl"


def test_positional_construction_is_unchanged():
    assert Verdict("deny", "x") == ("deny", "x", "", "evaluated", "")
    assert Verdict()._fields[:3] == ("decision", "reason", "context")
    assert STATUSES == ("evaluated", "partial", "failed")


def _constants():
    for ns, mod in (("shell", shell_rules), ("write", write_rules)):
        for name, value in sorted(vars(mod).items()):
            if isinstance(value, str) and name.startswith(("DENY_", "ASK_", "ADV_", "NO_")) and not name.endswith("_LOG"):
                yield "{}.{}".format(ns, name), value


@pytest.mark.parametrize("code,template", list(_constants()), ids=[c for c, _ in _constants()])
def test_every_rule_constant_codes_to_itself(code, template):
    text = template.replace("{c}", "rm -rf X").replace("{p}", "C:/X/Data/a.ini")
    whole = code.split(".", 1)[1].startswith(("DENY_", "ASK_", "NO_"))
    assert codes.matches(text, whole) == [code]


def test_every_corpus_row_maps_to_exactly_one_code():
    """Runs over every recorded verdict text; an ambiguous template pair would show up here."""
    seen = 0
    # split on LF only: str.splitlines() also breaks on U+2028 etc., which some corpus rows contain.
    for line in CORPUS.read_text(encoding="utf-8").split(chr(10)):
        if not line.strip():
            continue
        row = json.loads(line)
        if row["decision"] == "allow" or not row["text"]:
            continue
        found = codes.matches(row["text"], row["decision"] in ("deny", "ask"))
        assert len(found) == 1, (row["id"], found)
        seen += 1
    assert seen >= 50, "corpus suspiciously small: {} coded rows".format(seen)


def test_inert_refusals_are_failed_not_evaluated():
    v = codes.classify(Verdict("deny", shell_rules.NO_COMMAND_DENY))
    assert (v.decision, v.status, v.code) == ("deny", "failed", "shell.NO_COMMAND_DENY")


def test_a_rule_deny_is_evaluated():
    v = codes.classify(Verdict("deny", write_rules.DENY_PLUGIN))
    assert (v.status, v.code) == ("evaluated", "write.DENY_PLUGIN")


def test_advice_is_coded_by_its_first_item():
    ctx = write_rules.ADV_PAPYRUS.format(p="a.psc") + " | " + write_rules.ADV_INSTALL.format(p="x")
    assert codes.classify(Verdict("allow", "", ctx)).code == "write.ADV_PAPYRUS"


def test_plain_allow_is_coded_allow():
    assert codes.classify(Verdict()).code == "allow"


def test_an_explicit_code_is_kept():
    assert codes.classify(Verdict("deny", "anything", code="plan5.X")).code == "plan5.X"


def test_unknown_text_is_coded_unknown_never_guessed():
    assert codes.classify(Verdict("deny", "some text no rule wrote")).code == "unknown"
