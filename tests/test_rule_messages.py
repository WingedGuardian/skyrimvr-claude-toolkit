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
    "S-INERT-NOCMD", "S-INERT-NOROOT", "S-DEL-CONFIG", "S-DEL-INSTALL", "S-REGISTRY", "S-PSC-OUT",
    "S-CHAMPOLLION", "S-DEL-ADVISE", "S-RELATIVE-UNKNOWN", "S-MOVE-INSTALL", "S-WRITE-INSTALL",
    "S-SED-WINPATH", "S-PLUGIN-REF", "S-LOADORDER-REF", "S-AUTOMOD-ESP", "S-AUTOMOD-NIF",
    "S-AUTOMOD-ARCHIVE", "S-RESAVER-APPLY", "S-EXTERNAL-OUT", "W-INERT-NOPATH", "W-PLUGIN", "W-INI",
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
        rules.message("S-DEL-CONFIG")                     # legacy: must go through legacy_message
    with pytest.raises(ValueError):
        rules.message("S-DEL-CONFIG", c="x")


def test_registry_entries_are_well_formed():
    for rid, r in rules.RULES.items():
        assert r["decision"] in ("deny", "ask", "advise"), rid
        assert r["hook"] in ("protect-bash", "protect-files", "protect-mcp"), rid
        assert isinstance(r["legacy"], bool) and isinstance(r["prefixes"], list), rid
        assert re.match(r"^[SW]-[A-Z0-9-]+$", rid), rid


def test_legacy_rules_json_maps_every_legacy_template_to_its_id():
    m = json.loads((REPO / "tests" / "guard_corpus" / "legacy_rules.json").read_text(encoding="utf-8"))
    legacy = {i: r["template"] for i, r in rules.RULES.items() if r["legacy"]}
    assert m == {t: i for i, t in legacy.items()}
