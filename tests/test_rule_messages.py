"""The message contract for skyguard.rules, and the registry's own bookkeeping.

Every template is held to the contract (no echoed command, at most 2 short lines) unless its id is in
LEGACY_ALLOWED. That set is an explicit, shrinking list: Plan 5 empties it one rewritten rule at a time.
"""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import rules  # noqa: E402

# The rules still carrying their pre-Plan-5 text (which echoes the command). It may only shrink.
LEGACY_ALLOWED = {
    "S-INERT-NOCMD", "S-INERT-NOROOT", "S-REGISTRY", "S-PSC-OUT",
    "S-CHAMPOLLION", "S-MOVE-INSTALL",
    "S-SED-WINPATH", "S-AUTOMOD-ESP", "S-AUTOMOD-NIF",
    "S-AUTOMOD-ARCHIVE", "S-EXTERNAL-OUT", "W-INERT-NOPATH", "W-PLUGIN", "W-INI",
    "W-SKSE-INI", "W-LOADORDER", "W-PAPYRUS", "W-INSTALL",
}
ECHO = ("{c}", "{p}", "{command}")
LONG_NAME = "x" * 60 + ".esp"


def _checked_ids():
    return sorted(i for i in rules.RULES if i not in LEGACY_ALLOWED)


def _render(rid):
    return rules.message(rid, file="C:/a/b/" + LONG_NAME, op="op", save="Save1", n=3)


def test_no_template_echoes_the_command():
    for rid in _checked_ids():
        t = rules.RULES[rid]["template"]
        assert not any(e in t for e in ECHO), rid


def test_the_echo_check_would_catch_a_legacy_template():
    """A check must be able to fail: legacy templates do echo, and every one that does is exempted by id."""
    echoing = {i for i, r in rules.RULES.items() if any(e in r["template"] for e in ECHO)}
    assert echoing, "no legacy template echoes the command; the exemption set is stale"
    assert echoing <= LEGACY_ALLOWED


def test_message_shape():
    for rid in _checked_ids():
        lines = _render(rid).split("\n")
        assert len(lines) <= 2, rid
        assert all(len(ln) <= 200 for ln in lines), rid


def test_message_uses_the_basename_only():
    for rid in _checked_ids():
        assert "C:/a/b/" not in _render(rid), rid


def test_legacy_set_only_shrinks():
    still_legacy = {i for i, r in rules.RULES.items() if r["legacy"]}
    assert LEGACY_ALLOWED <= still_legacy, sorted(LEGACY_ALLOWED - still_legacy)
    assert still_legacy <= LEGACY_ALLOWED, sorted(still_legacy - LEGACY_ALLOWED)


def test_every_rule_has_a_mutation():
    import test_mutations
    ids = [p.id for p in test_mutations.MUTATIONS if getattr(p, "id", None)]
    for rid in _checked_ids():
        assert any(i.startswith("rule-" + rid + "-") for i in ids), rid


def test_message_rejects_unsupported_fields_and_legacy_ids():
    with pytest.raises(ValueError):
        rules.message("S-REGISTRY")                       # legacy: must go through legacy_message
    with pytest.raises(ValueError):
        rules.message("S-DEL-CONFIG", c="x")              # a non-legacy rule rejects a field outside FIELDS


def test_registry_entries_are_well_formed():
    for rid, r in rules.RULES.items():
        assert r["decision"] in ("deny", "ask", "advise"), rid
        assert r["hook"] in ("protect-bash", "protect-files", "protect-mcp"), rid
        assert isinstance(r["legacy"], bool) and isinstance(r["prefixes"], list), rid
        assert re.match(r"^[SW]-[A-Z0-9-]+$", rid), rid


def test_legacy_rules_json_maps_every_legacy_template_to_its_id():
    m = json.loads((REPO / "tests" / "guard_corpus" / "legacy_rules.json").read_text(encoding="utf-8"))
    legacy = {i: r["template"] for i, r in rules.RULES.items() if r["legacy"]}
    # legacy_rules.json is FROZEN: it keeps the old text of a rule a later task rewrote (the frozen corpus rows
    # still need it), so it may hold MORE than the still-legacy templates, but never fewer or a dead id.
    assert {t: i for i, t in legacy.items()}.items() <= m.items()
    extra = {t: i for t, i in m.items() if t not in legacy.values()}
    for t, i in extra.items():
        if i in rules.REMOVED:
            assert i not in rules.RULES, (i, "a removed id is back in the registry")
            continue
        assert i in rules.RULES and not rules.RULES[i]["legacy"], (i, "a frozen extra entry must be a REWRITTEN rule")


def test_removed_ids_are_frozen_legacy_ids():
    """REMOVED names only ids the frozen corpus knows (no typo can hide a live rule), and none is in RULES."""
    m = json.loads((REPO / "tests" / "guard_corpus" / "legacy_rules.json").read_text(encoding="utf-8"))
    assert rules.REMOVED <= set(m.values())
    assert not rules.REMOVED & set(rules.RULES)


def test_basename_splits_on_both_separators():
    assert rules._basename("C:/a" + chr(92) + "b/x.esp") == "x.esp"
    assert rules._basename("C:" + chr(92) + "a" + chr(92)) == "a"
    assert rules._basename("x.esp") == "x.esp"


def test_a_non_legacy_rule_renders_through_message(monkeypatch):
    monkeypatch.setitem(rules.RULES, "T-TEMP", {
        "decision": "advise", "hook": "protect-bash", "legacy": False, "prefixes": [],
        "template": "Note: {op} touches {file} (save {save}, {n} more).",
    })
    out = rules.message("T-TEMP", file="C:/a" + chr(92) + "b/x.esp", op="cp", save="S1", n=2)
    assert out == "Note: cp touches x.esp (save S1, 2 more)."


def test_every_deny_template_formats_without_error():
    """deny()/advise() call .format() on templates; a stray brace must not be able to crash the engine."""
    for rid, r in rules.RULES.items():
        if r["legacy"]:
            rules.legacy_message(rid, c="cmd", p="path")
        else:
            _render(rid)
