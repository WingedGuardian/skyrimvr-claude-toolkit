"""The message contract for skyguard.rules, and the registry's own bookkeeping.

Every template is held to the contract: no echoed command, at most 2 short lines. Task 13 emptied the legacy set, so
there is no exemption left in the registry; the only exemption anywhere is the wrappers' own GUARD INERT text (R13),
which is narrow and explicit (see WRAPPER_INERT below) and is itself tested not to widen.
"""
import json
import re
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import rules  # noqa: E402

# Task 13: the set of rules still carrying their pre-Plan-5 text is EMPTY, and stays empty.
LEGACY_ALLOWED = frozenset()
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


def test_the_echo_check_would_catch_a_legacy_template(monkeypatch):
    """A check must be able to fail. Task 9 rewrote the last templates that echoed the command, so none does now; a
    planted echoing template must be caught by the same test the real ones pass."""
    echoing = {i for i, r in rules.RULES.items() if any(e in r["template"] for e in ECHO)}
    assert echoing <= LEGACY_ALLOWED
    monkeypatch.setitem(rules.RULES, "S-PLANTED", {"decision": "advise", "hook": "protect-bash", "legacy": False,
                                                  "prefixes": [], "template": "Note: {c} did something."})
    with pytest.raises(AssertionError):
        test_no_template_echoes_the_command()


def test_message_shape():
    for rid in _checked_ids():
        lines = _render(rid).split("\n")
        assert len(lines) <= 2, rid
        assert all(len(ln) <= 200 for ln in lines), rid


def test_message_uses_the_basename_only():
    for rid in _checked_ids():
        assert "C:/a/b/" not in _render(rid), rid


def test_no_legacy_messages():
    assert [i for i, r in rules.RULES.items() if r["legacy"]] == []


def test_every_rule_has_a_mutation():
    import test_mutations
    ids = [p.id for p in test_mutations.MUTATIONS if getattr(p, "id", None)]
    for rid in _checked_ids():
        assert any(i.startswith("rule-" + rid + "-") for i in ids), rid


def test_message_rejects_unsupported_fields_and_legacy_ids(monkeypatch):
    monkeypatch.setitem(rules.RULES, "T-LEGACY", {"decision": "advise", "hook": "protect-bash", "legacy": True,
                                                 "prefixes": [], "template": "old {c}"})
    with pytest.raises(ValueError):
        rules.message("T-LEGACY")                         # legacy: must go through legacy_message
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


# ---- Task 13: the strict contract, proven over every corpus row -----------------------------------------------------
# R13: the wrappers' own GUARD INERT text (agent/guards/adapters/claude/*.sh) appears only when a wrapper itself fails.
# The exemption is ONE exact shape, applies to the line/length check only (the no-echo check still runs), and only to
# a `payload-*` row; test_the_wrapper_exemption_does_not_widen pins all three.
WRAPPER_INERT = re.compile(r"^GUARD INERT: [\w.\-]+ [^\n]*, so it evaluated NO rules and cannot report a decision\. "
                           r"Allowing silently would be indistinguishable from approving\.$")
MIN_RUN = 24


def _wrapper_exempt(row_id, text):
    return row_id.startswith("payload-") and bool(WRAPPER_INERT.match(text))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _segments(command):
    """The pieces of a command that may be quoted back as a bare name: split on whitespace, quotes, separators, slashes."""
    return [x for x in re.split(r"[\s'\"/\\=;|&<>()]+", command) if x]


# Folder basenames that contain spaces (a bare token split would shred them): naming the folder is naming a basename.
# Explicit and short on purpose; anything longer than one of these names is a path and still counts as an echo.
SPACED_BASENAMES = ("the elder scrolls v skyrim vr", "gog games", "my games", "skyrim vr")


def echoed_run(text, payload):
    """A >= MIN_RUN character run of `text` (case-insensitive) that also occurs in a string of the payload, unless that
    run sits inside ONE basename: a space-free token of the command, or a SPACED_BASENAMES folder name. Returns the
    run, or None."""
    body = payload.get("tool_input", payload) if isinstance(payload, dict) else payload
    text = text.lower()
    for cmd in _strings(body):
        cmd = cmd.lower()
        segs = _segments(cmd) + list(SPACED_BASENAMES)
        for i in range(0, max(0, len(text) - MIN_RUN + 1)):
            w = text[i:i + MIN_RUN]
            if w in cmd and not any(w in sg for sg in segs):
                return w
    return None


def contract_problems(row_id, text, payload):
    out = []
    lines = text.split("\n")
    if not _wrapper_exempt(row_id, text):
        if len(lines) > 2:
            out.append("%d lines" % len(lines))
        if any(len(ln) > 200 for ln in lines):
            out.append("a line over 200 characters")
    run = echoed_run(text, payload)
    if run:
        out.append("echoes the command: %r" % run)
    return out


def _corpus_rows():
    base = REPO / "tests" / "guard_corpus"
    rows = []
    for name in ("expected.jsonl", "intended.jsonl"):
        rows += [json.loads(ln) for ln in (base / name).read_text(encoding="utf-8").split("\n") if ln.strip()]
    return rows


@pytest.fixture(scope="module")
def fired_corpus():
    """Every legacy and intended row, fired through the REAL wrapper once. {row id: (decision, text, payload)}."""
    import importlib.util
    import tempfile
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    rec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rec)
    if rec.needs_missing_jq(REPO / ".claude" / "hooks"):
        pytest.skip("the shipped hooks still need jq, and it is not installed")
    out = {}
    with tempfile.TemporaryDirectory() as td:
        inst = {n: rec.make_install(Path(td), REPO / ".claude" / "hooks", n) for n in rec.LAYOUTS}
        for r in _corpus_rows():
            got = rec.fire_full(inst[r["layout"]], r)
            out[r["id"]] = (got["decision"], got["text"], rec.row_payload(r, inst[r["layout"]]))
    return out


def test_no_message_echoes_its_command(fired_corpus):
    """User directive: an ask/advise/deny is <= 2 plain lines and never quotes the command. Fire every legacy AND
    intended row and hold the text it really returns to the contract."""
    assert len(fired_corpus) >= 150
    spoken = 0
    bad = []
    for rid, (decision, text, payload) in sorted(fired_corpus.items()):
        if decision == "allow":
            assert text == "", rid
            continue
        spoken += 1
        bad += ["%s: %s" % (rid, p) for p in contract_problems(rid, text, payload)]
    assert spoken >= 100, spoken
    assert not bad, "\n".join(bad)


def test_the_contract_check_can_fail():
    """Each clause bites on a planted message: an echo, a third line, a long line."""
    cmd = {"tool_input": {"command": "bash tools/resaver-cli.sh reset-havok-for-the-whole-save.ess --apply"}}
    ok = "ReSaver will change a save.\nYes lets it write Existing.ess."
    assert contract_problems("x", ok, cmd) == []
    assert contract_problems("x", "Runs: bash tools/resaver-cli.sh reset-havok-for-the-whole-save.ess --apply", cmd)
    assert contract_problems("x", "a\nb\nc", cmd)
    assert contract_problems("x", "a" * 201, cmd)
    # a basename quoted back is allowed even when it is long; a path-sized run is not
    longname = "x" * 40 + ".esp"
    p = {"tool_input": {"file_path": "C:/GOG Games/Skyrim VR/Data/" + longname}}
    assert echoed_run("Blocked: " + longname + " is a plugin.", p) is None
    assert echoed_run("Blocked: C:/GOG Games/Skyrim VR/Data/" + longname, p)
    # a spaced folder basename is a basename; the path around it is not
    q = {"tool_input": {"command": "delete C:/GOG Games/The Elder Scrolls V Skyrim VR/Data"}}
    assert echoed_run("Blocked: deletes The Elder Scrolls V Skyrim VR here.", q) is None
    assert echoed_run("Blocked: GOG Games/The Elder Scrolls V Skyrim VR/Data", q)


def test_the_wrapper_exemption_does_not_widen():
    good = ("GUARD INERT: protect-bash.sh PreToolUse, so it evaluated NO rules and cannot report a decision. "
            "Allowing silently would be indistinguishable from approving.")
    assert _wrapper_exempt("payload-empty", good)
    assert not _wrapper_exempt("shell-delete", good)                   # only a payload-* row
    assert not _wrapper_exempt("payload-empty", good + " Extra.")      # exactly that text, nothing appended
    assert not _wrapper_exempt("payload-empty", good + "\nsecond")     # and not a longer message
    assert not _wrapper_exempt("payload-empty", "Blocked: something else.")
    # the exemption skips the shape check only, never the echo check
    p = {"tool_input": {"command": "remove C:/GOG Games/The Elder Scrolls V Skyrim VR/Data"}}
    echoing = good.replace("PreToolUse", "C:/GOG Games/The Elder Scrolls V Skyrim VR/Data")
    assert _wrapper_exempt("payload-x", echoing)                       # shaped like the wrapper text, so shape-exempt
    assert any("echoes" in x for x in contract_problems("payload-x", echoing, p))


def test_only_wrapper_failures_use_the_exemption(fired_corpus):
    """No corpus row outside payload-* returns the wrapper shape."""
    for rid, (decision, text, payload) in fired_corpus.items():
        if WRAPPER_INERT.match(text):
            assert rid.startswith("payload-"), rid


def test_message_fields_are_basenames():
    """A long full path in `file` renders as its basename for every template that takes {file}."""
    seen = 0
    for rid, r in rules.RULES.items():
        if "{file}" not in r["template"]:
            continue
        seen += 1
        for path in ("C:/GOG Games/The Elder Scrolls V Skyrim VR/Data/" + LONG_NAME,
                     "C:" + chr(92) + "GOG Games" + chr(92) + "Data" + chr(92) + LONG_NAME):
            out = rules.message(rid, file=path, op="op", save="Save1", n=3)
            assert LONG_NAME in out and "GOG Games" not in out and "Data/" + LONG_NAME not in out, rid
            assert "C:" not in out and chr(92) not in out, rid
            assert max(len(ln) for ln in out.split("\n")) <= 200, rid
    assert seen >= 3, seen
