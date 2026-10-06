"""Verdicts carry a stable reason code and an evaluation status, separate from the decision (Plan 4 R8/R9)."""
import json
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codes, rules, shell_rules, write_rules  # noqa: E402
from skyguard.verdict import STATUSES, Verdict  # noqa: E402

# Merge of Plans 4+5: expected.jsonl is the FROZEN record of the old bash hooks; Plan 5 rewrote every message, so
# its texts are no longer anything the engine emits (legacy_rules.json maps them to rule ids). The texts the engine
# emits today are intended.jsonl's -- the hand-authored spec, checked byte for byte through the real wrapper.
CORPUS = REPO / "tests" / "guard_corpus" / "intended.jsonl"


def test_positional_construction_is_unchanged():
    # The first five positions are unchanged; Plan 6 final fix (lane A C1) appended `never` (default None).
    assert Verdict("deny", "x")[:5] == ("deny", "x", "", "evaluated", "")
    assert Verdict("deny", "x").never is None and Verdict()._fields[5:] == ("never",)
    assert Verdict()._fields[:3] == ("decision", "reason", "context")
    assert STATUSES == ("evaluated", "partial", "failed")


def _constants():
    for ns, mod in (("shell", shell_rules), ("write", write_rules)):
        for name, value in sorted(vars(mod).items()):
            if isinstance(value, str) and name.startswith(("DENY_", "ASK_", "ADV_", "NO_")) and not name.endswith("_LOG"):
                yield "{}.{}".format(ns, name), value


@pytest.mark.parametrize("code,template", list(_constants()), ids=[c for c, _ in _constants()])
def test_every_rule_constant_codes_to_itself(code, template):
    text = (template.replace("{c}", "rm -rf X").replace("{p}", "C:/X/Data/a.ini").replace("{file}", "a.ini")
            .replace("{op}", "op").replace("{save}", "s.ess").replace("{n}", "1"))
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


@pytest.mark.parametrize("rid", sorted(r for r, v in rules.RULES.items() if not v["legacy"]))
def test_every_registry_rule_codes_to_exactly_one_code(rid):
    """Plan 5 renders most verdicts straight from the rule registry (rules.message), not from a module constant.
    Each must still get its own code -- else the telemetry reads every Plan 5 deny as "unknown"."""
    rule = rules.RULES[rid]
    text = rules.message(rid, **{k: "x.esp" if k == "file" else "1" for k in rules.FIELDS
                                if "{" + k + "}" in rule["template"]})
    found = codes.matches(text, rule["decision"] in ("deny", "ask"))
    assert len(found) == 1, (rid, found)
    v = codes.classify(Verdict("allow" if rule["decision"] == "advise" else rule["decision"],
                               "" if rule["decision"] == "advise" else text,
                               text if rule["decision"] == "advise" else ""))
    assert v.code != "unknown"
    assert (v.status == "failed") == ("INERT" in rid), (rid, v)


def test_a_later_advice_item_cannot_code_the_first():
    """Advice is coded by its FIRST item: a field must not stretch across ' | ' into a later item's template."""
    first = rules.message("S-WRITE-LOADORDER", file="plugins.txt")
    later = rules.message("S-WRITE-INSTALL", file="x.ini")
    assert codes.matches(first + " | " + later, whole=False) == ["shell.S-WRITE-LOADORDER"]


def test_inert_refusals_are_failed_not_evaluated():
    v = codes.classify(Verdict("deny", shell_rules.NO_COMMAND_DENY))
    assert (v.decision, v.status, v.code) == ("deny", "failed", "shell.NO_COMMAND_DENY")


def test_a_rule_deny_is_evaluated():
    v = codes.classify(Verdict("deny", write_rules.DENY_PLUGIN))
    assert (v.status, v.code) == ("evaluated", "write.DENY_PLUGIN")


def test_advice_is_coded_by_its_first_item():
    ctx = write_rules.ADV_PAPYRUS.format(file="a.psc") + " | " + write_rules.ADV_INSTALL.format(file="x")
    assert codes.classify(Verdict("allow", "", ctx)).code == "write.ADV_PAPYRUS"


def test_plain_allow_is_coded_allow():
    assert codes.classify(Verdict()).code == "allow"


def test_an_explicit_code_is_kept():
    assert codes.classify(Verdict("deny", "anything", code="plan5.X")).code == "plan5.X"


def test_unknown_text_is_coded_unknown_never_guessed():
    assert codes.classify(Verdict("deny", "some text no rule wrote")).code == "unknown"


def test_plain_allow_is_evaluated_and_coded_allow():
    v = codes.classify(Verdict())
    assert (v.decision, v.status, v.code) == ("allow", "evaluated", "allow")


def test_unreadable_env_refusal_is_failed_and_coded(monkeypatch, tmp_path, capsys):
    """run.py's own inert refusal never passes through a rule module; it must still read as 'nothing checked'."""
    import io
    from skyguard import paths, run

    seen = []
    real = codes.classify
    monkeypatch.setattr(run.codes, "classify", lambda v: seen.append(real(v)) or seen[-1])

    def boom(*a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr(paths, "read_env", boom)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf X"}}).encode()

    class _In:
        buffer = io.BytesIO(payload)

    monkeypatch.setattr(run.sys, "stdin", _In)
    run.main(["claude", "protect-bash", "--root", str(tmp_path), "--project", str(tmp_path)])
    v = seen[-1]
    assert (v.decision, v.status, v.code) == ("deny", "failed", "run.ENV_UNREADABLE")


def test_no_deny_or_ask_is_built_outside_the_rule_modules_uncoded():
    """Scan: any Verdict("deny"/"ask", ...) in a non-rule module must name its own code= or use a rule constant."""
    import re

    pat = re.compile(r"Verdict\(\s*[\"'](deny|ask)[\"']([^\n]*)")
    bad = []
    for f in (REPO / "tools" / "skyguard").rglob("*.py"):
        # mcp_rules.py is Plan 5's third rule module: its texts come from the registry, which codes.py reads.
        if f.name in ("shell_rules.py", "write_rules.py", "mcp_rules.py", "codes.py", "verdict.py"):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = pat.search(line)
            if m and "code=" not in m.group(2):
                bad.append("{}:{}".format(f.name, n))
    assert bad == [], bad
